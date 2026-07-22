"""进化效果评估器。

运行历史失败用例，自动计算：
- 任务成功率
- Token 消耗
- 代码执行耗时

判定规则：新版本任务成功率提升，并且 Token 增幅不超过 15% 才保留新版本。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from mewcode.harness.evolution.models import (
    EvalResult,
    ExecutionTrace,
)

log = logging.getLogger(__name__)

# 单次重放超时（秒）
REPLAY_TIMEOUT = 120
# 最多重放的 trace 数量
MAX_REPLAY_TRACES = 10

# 成功经验路径评估阈值
SUCCESS_ITERATION_REDUCTION_THRESHOLD = 0.20  # 迭代数降幅 ≥ 20% 才算降本
SUCCESS_BASELINE_MIN_SAMPLES = 5  # 基线样本不足此数时不做降级


class EvolutionEvaluator:
    """进化效果评估器。

    通过重放历史失败用例，对比新旧版本的性能指标，
    做出 keep/rollback 决策。
    """

    def __init__(
        self,
        token_increase_threshold: float = 0.15,
        agent_factory: Any = None,
    ) -> None:
        self._threshold = token_increase_threshold
        self._agent_factory = agent_factory  # 用于重放（可选）

    async def evaluate(
        self,
        traces_before: list[ExecutionTrace],
        new_skills: list[str] | None = None,
        replay_enabled: bool = False,
    ) -> EvalResult:
        """评估进化效果。

        Args:
            traces_before: 进化前的基线执行轨迹（至少包含失败记录）。
            new_skills: 新创建的 Skill 名称列表。
            replay_enabled: 是否实际重放（需要 agent_factory）。

        Returns:
            EvalResult 包含对比指标和 keep/rollback 决策。
        """
        if not traces_before:
            return EvalResult(
                decision="rollback",
                reason="No baseline traces for evaluation",
            )

        # Step 1: 计算基线指标
        baseline = self._compute_metrics(traces_before)
        failure_traces = [t for t in traces_before if not t.success]

        log.info(
            "[evaluator] baseline: success_rate=%.2f, avg_tokens=%.0f",
            baseline["success_rate"], baseline["avg_tokens"],
        )

        # Step 2: 重放（如果启用）
        if replay_enabled and self._agent_factory is not None and failure_traces:
            replay_metrics = await self._replay_failures(failure_traces[:MAX_REPLAY_TRACES])
        else:
            # 无重放能力时，使用基线中的失败记录做保守估计
            replay_metrics = self._estimate_without_replay(traces_before)

        # Step 3: 对比计算
        token_change_pct = self._calc_change_pct(
            baseline["avg_tokens"], replay_metrics["avg_tokens"]
        )

        success_rate_before = baseline["success_rate"]
        success_rate_after = replay_metrics["success_rate"]

        # Step 4: 决策
        success_improved = success_rate_after > success_rate_before
        token_acceptable = token_change_pct <= self._threshold

        if success_improved and token_acceptable:
            decision = "keep"
            reason = (
                f"Success rate improved ({success_rate_before:.2f} → {success_rate_after:.2f}), "
                f"token change {token_change_pct:+.1%} within threshold ({self._threshold:.0%})"
            )
        elif not success_improved:
            decision = "rollback"
            reason = (
                f"Success rate did not improve ({success_rate_before:.2f} → {success_rate_after:.2f})"
            )
        else:
            decision = "rollback"
            reason = (
                f"Token increase {token_change_pct:+.1%} exceeds threshold ({self._threshold:.0%})"
            )

        result = EvalResult(
            success_rate_before=success_rate_before,
            success_rate_after=success_rate_after,
            avg_tokens_before=baseline["avg_tokens"],
            avg_tokens_after=replay_metrics["avg_tokens"],
            token_change_pct=token_change_pct,
            avg_exec_time_before=baseline["avg_exec_time_ms"],
            avg_exec_time_after=replay_metrics["avg_exec_time_ms"],
            decision=decision,
            reason=reason,
            details={
                "baseline_trace_count": len(traces_before),
                "failure_trace_count": len(failure_traces),
                "replayed_trace_count": replay_metrics.get("replayed_count", 0),
                "new_skills": new_skills or [],
            },
            replayed_traces=replay_metrics.get("replayed_trace_ids", []),
        )

        log.info("[evaluator] decision=%s reason=%s", decision, reason)
        return result

    # ------------------------------------------------------------------
    # 成功经验路径评估
    # ------------------------------------------------------------------

    def compute_success_baseline(
        self,
        traces: list[ExecutionTrace],
    ) -> dict[str, Any]:
        """从同类任务（未命中 Skill 时）的轨迹计算历史基线。

        基线 = 最近 N 次同类成功任务的迭代数/token 均值。
        样本不足 SUCCESS_BASELINE_MIN_SAMPLES 时返回 sample_count=0，
        供调用方判定「不做降级」。
        """
        successes = [t for t in traces if t.success]
        if not successes:
            return {"sample_count": 0, "avg_iteration_count": 0.0, "avg_tokens": 0.0}

        recent = successes[-SUCCESS_BASELINE_MIN_SAMPLES * 2:]  # 多取一些再截断
        recent = recent[-SUCCESS_BASELINE_MIN_SAMPLES * 2:]
        avg_iter = sum(t.iteration_count for t in recent) / len(recent)
        avg_tokens = sum(t.total_tokens for t in recent) / len(recent)
        return {
            "sample_count": len(recent),
            "avg_iteration_count": avg_iter,
            "avg_tokens": avg_tokens,
        }

    def evaluate_success(
        self,
        task_iteration_count: int,
        task_token_total: int,
        baseline: dict[str, Any],
    ) -> EvalResult:
        """成功型 Skill 命中采纳后的降本评估。

        判定规则（与失败型 token 阈值一致）：
        - 基线样本不足 → keep（不降级，维持 active）
        - 迭代数降幅 ≥ 20% 且 token 增幅 ≤ 15% → keep
        - 否则 → rollback（降级）

        Args:
            task_iteration_count: 命中采纳后该次任务的迭代轮数。
            task_token_total: 命中采纳后该次任务的 token 总量。
            baseline: compute_success_baseline 的返回值。
        """
        sample_count = baseline.get("sample_count", 0)
        if sample_count < SUCCESS_BASELINE_MIN_SAMPLES:
            return EvalResult(
                decision="keep",
                reason=(
                    f"Insufficient baseline samples ({sample_count} < "
                    f"{SUCCESS_BASELINE_MIN_SAMPLES}), keeping active without demotion"
                ),
                details={
                    "path": "success",
                    "baseline_sample_count": sample_count,
                    "task_iteration_count": task_iteration_count,
                    "task_token_total": task_token_total,
                },
            )

        baseline_iter = float(baseline.get("avg_iteration_count", 0.0))
        baseline_tokens = float(baseline.get("avg_tokens", 0.0))

        iter_reduction = self._calc_reduction_pct(baseline_iter, task_iteration_count)
        token_change = self._calc_change_pct(baseline_tokens, task_token_total)

        iter_improved = iter_reduction >= SUCCESS_ITERATION_REDUCTION_THRESHOLD
        token_acceptable = token_change <= self._threshold

        if iter_improved and token_acceptable:
            decision = "keep"
            reason = (
                f"Iteration reduced {iter_reduction:.0%} (>= {SUCCESS_ITERATION_REDUCTION_THRESHOLD:.0%}), "
                f"token change {token_change:+.1%} (<= {self._threshold:.0%})"
            )
        elif not iter_improved:
            decision = "rollback"
            reason = (
                f"Iteration reduction {iter_reduction:.0%} below threshold "
                f"({SUCCESS_ITERATION_REDUCTION_THRESHOLD:.0%})"
            )
        else:
            decision = "rollback"
            reason = (
                f"Token increase {token_change:+.1%} exceeds threshold "
                f"({self._threshold:.0%})"
            )

        result = EvalResult(
            avg_tokens_before=baseline_tokens,
            avg_tokens_after=task_token_total,
            token_change_pct=token_change,
            decision=decision,
            reason=reason,
            details={
                "path": "success",
                "baseline_sample_count": sample_count,
                "baseline_avg_iteration_count": baseline_iter,
                "task_iteration_count": task_iteration_count,
                "iteration_reduction": iter_reduction,
                "task_token_total": task_token_total,
            },
        )
        log.info(
            "[evaluator][success] iter_reduction=%.0f%% token_change=%+.1f%% decision=%s",
            iter_reduction * 100, token_change * 100, decision,
        )
        return result

    @staticmethod
    def _calc_reduction_pct(baseline: float, actual: float) -> float:
        """计算降幅百分比（baseline - actual）/ baseline。"""
        if baseline <= 0:
            return 0.0
        return (baseline - actual) / baseline

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_metrics(traces: list[ExecutionTrace]) -> dict[str, Any]:
        """从一组轨迹中计算聚合指标。"""
        if not traces:
            return {
                "success_rate": 0.0,
                "avg_tokens": 0.0,
                "avg_exec_time_ms": 0.0,
                "total_count": 0,
                "success_count": 0,
                "failure_count": 0,
            }

        total = len(traces)
        successes = sum(1 for t in traces if t.success)
        failures = total - successes
        total_tokens = sum(t.total_tokens for t in traces)
        total_time = sum(t.execution_time_ms for t in traces)

        return {
            "success_rate": successes / total if total > 0 else 0.0,
            "avg_tokens": total_tokens / total if total > 0 else 0.0,
            "avg_exec_time_ms": total_time / total if total > 0 else 0.0,
            "total_count": total,
            "success_count": successes,
            "failure_count": failures,
        }

    # ------------------------------------------------------------------
    # 重放
    # ------------------------------------------------------------------

    async def _replay_failures(
        self,
        failure_traces: list[ExecutionTrace],
    ) -> dict[str, Any]:
        """重放历史失败用例。"""
        results: list[dict[str, Any]] = []
        for trace in failure_traces:
            try:
                result = await self._replay_single(trace)
                results.append(result)
            except Exception as e:
                log.warning("[evaluator] replay failed for %s: %s", trace.trace_id, e)
                results.append({
                    "success": False,
                    "tokens": trace.total_tokens,
                    "exec_time_ms": trace.execution_time_ms,
                })

        if not results:
            return {
                "success_rate": 0.0,
                "avg_tokens": 0.0,
                "avg_exec_time_ms": 0.0,
                "replayed_count": 0,
                "replayed_trace_ids": [],
            }

        successes = sum(1 for r in results if r["success"])
        total_tokens = sum(r["tokens"] for r in results)
        total_time = sum(r["exec_time_ms"] for r in results)

        return {
            "success_rate": successes / len(results),
            "avg_tokens": total_tokens / len(results),
            "avg_exec_time_ms": total_time / len(results),
            "replayed_count": len(results),
            "replayed_trace_ids": [t.trace_id for t in failure_traces[:len(results)]],
        }

    async def _replay_single(
        self,
        trace: ExecutionTrace,
    ) -> dict[str, Any]:
        """重放单条失败 trace。"""
        if self._agent_factory is None:
            return {
                "success": False,
                "tokens": trace.total_tokens,
                "exec_time_ms": trace.execution_time_ms,
            }

        start = time.monotonic()
        try:
            # 使用 agent_factory 创建一个子 Agent 执行相同任务
            result = await self._agent_factory(
                prompt=trace.task_description,
                timeout=REPLAY_TIMEOUT,
            )
            elapsed = (time.monotonic() - start) * 1000

            # 从结果中提取信息
            success = self._infer_success_from_result(result)
            tokens = self._extract_tokens_from_result(result) or trace.total_tokens

            return {
                "success": success,
                "tokens": tokens,
                "exec_time_ms": elapsed,
            }
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            log.debug("[evaluator] replay exception: %s", e)
            return {
                "success": False,
                "tokens": trace.total_tokens,
                "exec_time_ms": elapsed,
            }

    # ------------------------------------------------------------------
    # 无重放时的估计
    # ------------------------------------------------------------------

    def _estimate_without_replay(
        self,
        traces: list[ExecutionTrace],
    ) -> dict[str, Any]:
        """无法重放时的保守估计。

        假定新 Skill 能将部分失败转为成功（基于失败模式的相似性），
        同时小幅增加 Token 消耗。
        """
        # 保守假设：失败 trace 中有 30% 可能因新 Skill 而成功
        failures = [t for t in traces if not t.success]
        successes = [t for t in traces if t.success]

        estimated_new_successes = int(len(failures) * 0.3)
        total = len(traces)
        new_success_rate = (len(successes) + estimated_new_successes) / total if total > 0 else 0.0

        # 保守假设 Token 增加 10%
        avg_tokens_before = sum(t.total_tokens for t in traces) / total if total > 0 else 0
        avg_tokens_after = avg_tokens_before * 1.10

        return {
            "success_rate": new_success_rate,
            "avg_tokens": avg_tokens_after,
            "avg_exec_time_ms": sum(t.execution_time_ms for t in traces) / total if total > 0 else 0,
            "replayed_count": 0,
            "replayed_trace_ids": [],
            "estimated": True,
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_change_pct(before: float, after: float) -> float:
        """计算变化百分比。"""
        if before == 0:
            return 0.0 if after == 0 else float("inf")
        return (after - before) / before

    @staticmethod
    def _infer_success_from_result(result: Any) -> bool:
        """从重放结果推断是否成功。"""
        if result is None:
            return False
        if isinstance(result, bool):
            return result
        if isinstance(result, dict):
            return not result.get("is_error", False)
        if isinstance(result, str):
            return "error" not in result.lower()[:500]
        return True

    @staticmethod
    def _extract_tokens_from_result(result: Any) -> int | None:
        """从重放结果提取 Token 用量。"""
        if isinstance(result, dict):
            tokens = result.get("total_tokens") or result.get("tokens")
            if isinstance(tokens, int):
                return tokens
            # 也可能是分开的
            in_t = result.get("input_tokens", 0)
            out_t = result.get("output_tokens", 0)
            if in_t or out_t:
                return in_t + out_t
        return None
