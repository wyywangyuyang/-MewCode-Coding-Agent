"""EvolutionManager — 自进化子系统门面。

负责：
- 接收配置，初始化所有进化组件
- 在每次任务结束后检查触发条件
- 非阻塞地执行进化循环
- 防重入保护
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from mewcode.harness.evolution.models import EvolutionRecord, SkipEvolutionError
from mewcode.harness.evolution.backup import BackupManager
from mewcode.harness.evolution.trace_store import ExecutionTraceStore, TraceCollector
from mewcode.harness.evolution.skill_meta import SkillMetaManager
from mewcode.harness.evolution.problem_classifier import ProblemClassifier
from mewcode.harness.evolution.skill_generator import SkillGenerator
from mewcode.harness.evolution.evaluator import EvolutionEvaluator
from mewcode.harness.evolution.decision_loop import EvolutionDecisionLoop
from mewcode.harness.evolution.success_detector import SuccessDetector
from mewcode.harness.evolution.success_generator import SuccessSkillGenerator
from mewcode.harness.evolution.skill_matcher import SkillMatcher

log = logging.getLogger(__name__)


class EvolutionManager:
    """自进化子系统门面。

    用法:
        mgr = EvolutionManager(
            harness_dir=Path("mewcode/harness"),
            config=evo_config,
            client_factory=client.stream,  # LLM 调用工厂
        )
        # 每次任务完成后:
        trace_id = mgr.trace_collector.start_task("user request")
        # ... agent works ...
        mgr.trace_collector.end_task(trace_id, success=True)
        # 任务后检查:
        await mgr.check_and_evolve()
    """

    def __init__(
        self,
        harness_dir: Path,
        config: Any,  # EvolutionConfig
        client_factory: Any = None,
        session_manager: Any = None,
        memory_manager: Any = None,
        skill_loader: Any = None,
    ) -> None:
        self._harness_dir = Path(harness_dir)
        self._config = config
        self._client_factory = client_factory
        self._session_manager = session_manager
        self._memory_manager = memory_manager
        self._skill_loader = skill_loader

        # 确保子目录存在
        self._trace_dir = self._harness_dir / getattr(config, "traces_dir", "traces")
        self._skills_dir = self._harness_dir / getattr(config, "skills_dir", "skills")
        self._backup_dir = self._harness_dir / getattr(config, "backup_dir", "backup")
        self._meta_path = self._harness_dir / getattr(config, "skill_meta_file", "skills/skill_meta.json")

        for d in (self._trace_dir, self._skills_dir, self._backup_dir):
            d.mkdir(parents=True, exist_ok=True)

        # 初始化组件
        self.trace_store = ExecutionTraceStore(self._trace_dir)
        self.trace_collector = TraceCollector(self.trace_store)
        self.backup_manager = BackupManager(self._backup_dir)
        self.skill_meta_manager = SkillMetaManager(self._meta_path)

        self.classifier = ProblemClassifier(client_factory=client_factory)
        self.skill_generator = SkillGenerator(
            client_factory=client_factory,
            skills_dir=self._skills_dir,
            min_recurrence=getattr(config, "min_failure_recurrence", 3),
        )
        self.evaluator = EvolutionEvaluator(
            token_increase_threshold=getattr(config, "token_increase_threshold", 0.15),
            agent_factory=client_factory,
        )

        # 成功经验路径组件
        self.success_enabled: bool = bool(getattr(config, "success_enabled", False))
        self.success_detector = SuccessDetector(
            iteration_threshold=getattr(config, "success_iteration_threshold", 8),
            tool_call_threshold=getattr(config, "success_tool_call_threshold", 10),
        )
        self.success_generator = SuccessSkillGenerator(
            client_factory=client_factory,
            skills_dir=self._skills_dir,
        )
        self.skill_matcher = SkillMatcher(
            client_factory=client_factory,
            skill_meta_manager=self.skill_meta_manager,
            skills_dir=self._skills_dir,
            timeout=getattr(config, "success_match_timeout", 8.0),
        )
        self._success_match_enabled = bool(getattr(config, "success_match_enabled", True))
        self._success_hit_failure_threshold = int(
            getattr(config, "success_hit_failure_threshold", 3)
        )

        self.decision_loop = EvolutionDecisionLoop(
            trace_store=self.trace_store,
            classifier=self.classifier,
            skill_generator=self.skill_generator,
            evaluator=self.evaluator,
            backup_manager=self.backup_manager,
            skill_meta_manager=self.skill_meta_manager,
            min_traces=getattr(config, "min_traces_trigger", 30),
            max_traces=getattr(config, "max_traces_per_evolution", 50),
            min_recurrence=getattr(config, "min_failure_recurrence", 3),
            success_detector=self.success_detector if self.success_enabled else None,
            success_generator=self.success_generator if self.success_enabled else None,
            skill_matcher=self.skill_matcher if self.success_enabled else None,
            success_promotion_recurrence=getattr(config, "success_promotion_recurrence", 2),
        )

        # 状态
        self._running: bool = False
        self._last_result: EvolutionRecord | None = None

        log.info(
            "[evolution] manager initialized: traces_dir=%s, skills_dir=%s, threshold=%d, success_enabled=%s",
            self._trace_dir, self._skills_dir, getattr(config, "min_traces_trigger", 30),
            self.success_enabled,
        )

    # ------------------------------------------------------------------
    # 进化检查（主要钩子）
    # ------------------------------------------------------------------

    async def check_and_evolve(self) -> EvolutionRecord | None:
        """非阻塞检查：如果条件满足，执行完整进化周期。

        失败路径与成功路径顺序执行，互不依赖、互不查重。

        Returns:
            EvolutionRecord 或 None。
        """
        if self._running:
            log.debug("[evolution] already running, skipping check")
            return None

        # 先处理 Skill 废弃检查（含命中失败累计降级）
        self._check_deprecations()

        self._running = True
        try:
            # 失败路径
            record = await self.decision_loop.run()
            self._last_result = record

            # 成功路径（与失败路径独立）
            if self.success_enabled:
                try:
                    success_record = await self.decision_loop.run_success_path()
                    if success_record is not None and record is None:
                        record = success_record
                except Exception:
                    log.exception("[evolution][success] cycle failed")

            return record
        except SkipEvolutionError:
            log.debug("[evolution] skipped — insufficient traces or no patterns")
            # 失败路径跳过时，成功路径仍可独立执行
            if self.success_enabled:
                try:
                    return await self.decision_loop.run_success_path()
                except Exception:
                    log.exception("[evolution][success] cycle failed")
            return None
        except Exception:
            log.exception("[evolution] cycle failed")
            return None
        finally:
            self._running = False

    async def run_if_ready(self) -> EvolutionRecord | None:
        """check_and_evolve 的别名（供工具调用）。"""
        return await self.check_and_evolve()

    # ------------------------------------------------------------------
    # Skill 废弃检查
    # ------------------------------------------------------------------

    def _check_deprecations(self) -> None:
        """检查并废弃超时未使用的 Skill，以及命中失败累计超阈值的成功型 Skill。"""
        # 所有活跃 Skill 的 counter ++
        self.skill_meta_manager.increment_tasks()

        threshold = getattr(self._config, "deprecation_task_threshold", 60)
        candidates = self.skill_meta_manager.check_deprecation_candidates(threshold)

        for name in candidates:
            self.skill_meta_manager.deprecate_skill(name)
            log.info("[evolution] deprecated skill: %s (not used for %d tasks)", name, threshold)

        # 成功型 Skill 命中失败累计降级
        hit_fail_candidates = self.skill_meta_manager.check_hit_failure_candidates(
            self._success_hit_failure_threshold
        )
        for name in hit_fail_candidates:
            self.skill_meta_manager.demote_skill(
                name, reason=f"hit failures >= {self._success_hit_failure_threshold}"
            )
            log.info("[evolution] demoted success skill: %s (hit failures)", name)

    # ------------------------------------------------------------------
    # 成功经验路径：注入匹配与命中评估（供 Agent 主循环调用）
    # ------------------------------------------------------------------

    async def match_skill_for_injection(
        self, task_description: str
    ) -> dict[str, Any] | None:
        """任务开始时匹配正式成功 Skill（供注入）。

        受 success_enabled 与 success_match_enabled 双重控制。
        """
        if not self.success_enabled or not self._success_match_enabled:
            return None
        try:
            return await self.skill_matcher.match_active(task_description)
        except Exception as e:
            log.warning("[evolution][success] injection match failed: %s", e)
            return None

    async def evaluate_hit(
        self, skill_name: str, trace_id: str
    ) -> None:
        """一次命中采纳后的任务结束时，评估降本并记录结果。

        - 任务失败 → hit_failures++，累计超阈值则降级
        - 任务成功 → 与历史基线对比，未降本则降级，降本则 hit_count++
        """
        if not self.success_enabled or not skill_name or not trace_id:
            return

        traces = self.trace_store.get_by_ids([trace_id])
        if not traces:
            return
        trace = traces[0]

        # 任务失败：记录命中失败
        if not trace.success:
            self.skill_meta_manager.record_hit_result(skill_name, success=False)
            if self.skill_meta_manager.check_hit_failure_candidates(
                self._success_hit_failure_threshold
            ):
                # 命中失败累计达标 → 立即降级
                self.skill_meta_manager.demote_skill(
                    skill_name, reason="hit failure threshold reached"
                )
            return

        # 任务成功：与历史基线对比降本
        recent = self.trace_store.get_latest(
            getattr(self._config, "max_traces_per_evolution", 50)
        )
        # 基线排除当前 trace
        recent = [t for t in recent if t.trace_id != trace_id]
        baseline = self.evaluator.compute_success_baseline(recent)

        result = self.evaluator.evaluate_success(
            task_iteration_count=trace.iteration_count,
            task_token_total=trace.total_tokens,
            baseline=baseline,
        )
        if result.decision == "keep":
            self.skill_meta_manager.record_hit_result(skill_name, success=True)
        else:
            self.skill_meta_manager.demote_skill(
                skill_name, reason=result.reason
            )
        log.info(
            "[evolution][success] hit eval for '%s': %s — %s",
            skill_name, result.decision, result.reason,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """获取当前状态摘要。"""
        last = self.skill_meta_manager.get_last_evolution()
        active_skills = self.skill_meta_manager.get_active()
        deprecated_skills = self.skill_meta_manager.get_deprecated()

        return {
            "running": self._running,
            "total_traces": self.trace_store.count(),
            "active_skills": len(active_skills),
            "deprecated_skills": len(deprecated_skills),
            "last_evolution": last,
            "skills": {
                "active": [s.get("name") for s in active_skills],
                "deprecated": [s.get("name") for s in deprecated_skills],
            },
        }

    def get_last_cycle(self) -> EvolutionRecord | None:
        return self._last_result

    def list_cycles(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.skill_meta_manager.get_evolution_records()[:limit]

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """清理：Flush 所有未完成的 trace + 清理旧文件。"""
        self.trace_collector.flush()
        self.trace_store.cleanup()
        self.backup_manager.prune_old()
        log.info("[evolution] cleanup complete")
