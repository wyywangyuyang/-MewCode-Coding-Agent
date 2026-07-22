"""成功信号识别器。

在任务结束时判定是否为「复杂且成功」的任务——只有满足条件的任务
才会产出 SuccessSignal，进入成功经验沉淀流程。

复杂判定：迭代数 ≥ iteration_threshold 或 工具调用数 ≥ tool_call_threshold。
成功判定：trace.success 为 True。had_retries 仅为信息性，不参与过滤——
「含高成本成功」（重试/绕路后完成）同样纳入范围。
"""

from __future__ import annotations

import logging
from typing import Any

from mewcode.harness.evolution.models import ExecutionTrace, SuccessSignal

log = logging.getLogger(__name__)

# 复杂度阈值默认值（可由 EvolutionConfig 覆盖）
DEFAULT_ITERATION_THRESHOLD = 8
DEFAULT_TOOL_CALL_THRESHOLD = 10


class SuccessDetector:
    """识别复杂成功任务，产出 SuccessSignal。"""

    def __init__(
        self,
        iteration_threshold: int = DEFAULT_ITERATION_THRESHOLD,
        tool_call_threshold: int = DEFAULT_TOOL_CALL_THRESHOLD,
    ) -> None:
        self._iter_threshold = iteration_threshold
        self._tool_threshold = tool_call_threshold

    def is_complex(self, trace: ExecutionTrace) -> bool:
        """判定任务是否复杂（迭代数或工具调用数超阈值）。"""
        return (
            trace.iteration_count >= self._iter_threshold
            or trace.tool_call_count >= self._tool_threshold
        )

    def detect(self, trace: ExecutionTrace) -> SuccessSignal | None:
        """判定一条 trace 是否为「复杂且成功」，产出 SuccessSignal。

        Returns:
            SuccessSignal（复杂且成功时），否则 None。
        """
        if not trace.success:
            return None

        if not self.is_complex(trace):
            log.debug(
                "[success_detector] trace %s not complex (iter=%d, tools=%d)",
                trace.trace_id, trace.iteration_count, trace.tool_call_count,
            )
            return None

        signal = SuccessSignal(
            trace_id=trace.trace_id,
            task_description=trace.task_description,
            iteration_count=trace.iteration_count,
            tool_call_count=trace.tool_call_count,
            token_total=trace.total_tokens,
            had_retries=trace.had_retries,
            key_steps=self._extract_key_steps(trace),
            tools_used=list(trace.tools_used),
            files_modified=list(trace.files_modified),
        )
        log.info(
            "[success_detector] complex success detected: trace=%s iter=%d tools=%d retries=%s",
            trace.trace_id, signal.iteration_count, signal.tool_call_count,
            signal.had_retries,
        )
        return signal

    @staticmethod
    def _extract_key_steps(trace: ExecutionTrace) -> list[str]:
        """从 trace 中提取关键步骤摘要（供生成器作为证据）。"""
        steps: list[str] = []
        if trace.task_description:
            steps.append(f"Task: {trace.task_description[:200]}")
        if trace.tools_used:
            steps.append(f"Tools used: {', '.join(trace.tools_used)}")
        if trace.files_modified:
            steps.append(f"Files modified: {', '.join(trace.files_modified[:10])}")
        steps.append(f"Iterations: {trace.iteration_count}, tool calls: {trace.tool_call_count}")
        if trace.had_retries:
            steps.append("Task involved retries / detours before succeeding.")
        return steps
