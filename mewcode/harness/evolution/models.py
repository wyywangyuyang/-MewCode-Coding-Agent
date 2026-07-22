"""自进化系统数据模型。

定义所有核心数据结构：执行轨迹、失败模式、评估结果、进化周期等。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 执行轨迹
# ---------------------------------------------------------------------------


@dataclass
class ExecutionTrace:
    """单次任务执行记录。"""

    trace_id: str = ""  # 唯一标识，自动生成
    session_id: str = ""  # 所属会话 ID
    timestamp: float = 0.0  # Unix 时间戳
    task_description: str = ""  # 任务描述（截取前 200 字）
    success: bool = True  # 任务是否成功
    error_info: dict[str, Any] | None = None  # {error_type, stack_trace, message, file_path}
    tools_used: list[str] = field(default_factory=list)
    tokens_input: int = 0
    tokens_output: int = 0
    execution_time_ms: float = 0.0
    files_modified: list[str] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    # 成功经验路径所需的复杂度计数字段
    iteration_count: int = 0  # Agent 主循环迭代轮数
    tool_call_count: int = 0  # 工具调用总次数（非去重）
    had_retries: bool = False  # 是否发生过重试/绕路（信息性）

    def __post_init__(self) -> None:
        if not self.trace_id:
            self.trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        if self.timestamp <= 0:
            self.timestamp = time.time()

    @property
    def total_tokens(self) -> int:
        return self.tokens_input + self.tokens_output

    @property
    def error_type(self) -> str:
        if self.error_info is None:
            return ""
        return str(self.error_info.get("error_type", ""))

    @property
    def error_message(self) -> str:
        if self.error_info is None:
            return ""
        return str(self.error_info.get("message", ""))

    @property
    def error_stacktrace(self) -> str:
        if self.error_info is None:
            return ""
        return str(self.error_info.get("stack_trace", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "task_description": self.task_description,
            "success": self.success,
            "error_info": self.error_info,
            "tools_used": self.tools_used,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "execution_time_ms": self.execution_time_ms,
            "files_modified": self.files_modified,
            "skills_used": self.skills_used,
            "iteration_count": self.iteration_count,
            "tool_call_count": self.tool_call_count,
            "had_retries": self.had_retries,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionTrace:
        return cls(
            trace_id=d.get("trace_id", ""),
            session_id=d.get("session_id", ""),
            timestamp=d.get("timestamp", 0.0),
            task_description=d.get("task_description", ""),
            success=d.get("success", True),
            error_info=d.get("error_info"),
            tools_used=d.get("tools_used", []),
            tokens_input=d.get("tokens_input", 0),
            tokens_output=d.get("tokens_output", 0),
            execution_time_ms=d.get("execution_time_ms", 0.0),
            files_modified=d.get("files_modified", []),
            skills_used=d.get("skills_used", []),
            iteration_count=d.get("iteration_count", 0),
            tool_call_count=d.get("tool_call_count", 0),
            had_retries=d.get("had_retries", False),
        )


# ---------------------------------------------------------------------------
# 问题分类
# ---------------------------------------------------------------------------


class ProblemCategory(str, Enum):
    MISSING_CAPABILITY = "missing_capability"  # Agent 缺少某项能力
    PATTERN_REPETITION = "pattern_repetition"  # 重复犯相同错误
    TOOL_MISUSE = "tool_misuse"  # 工具使用不当
    KNOWLEDGE_GAP = "knowledge_gap"  # 知识缺口
    NO_ISSUE = "no_issue"  # 无系统性问题


# ---------------------------------------------------------------------------
# Skill 状态机（成功经验路径）
# ---------------------------------------------------------------------------


class SkillStatus(str, Enum):
    """自动生成 Skill 的生命周期状态（成功经验路径）。"""

    CANDIDATE = "candidate"  # 首次成功生成，等待复发晋升
    ACTIVE = "active"  # 已晋升，可被匹配注入
    DEPRECATED = "deprecated"  # 已废弃（命中失败累计或长期未用）


@dataclass
class FailurePattern:
    """一个系统性的失败模式。"""

    pattern_id: str = ""
    error_type: str = ""  # 错误类型（ImportError, SyntaxError 等）
    stack_signature: str = ""  # 去参数化的堆栈签名
    occurrence_count: int = 0
    trace_ids: list[str] = field(default_factory=list)
    common_files: list[str] = field(default_factory=list)
    root_cause_summary: str = ""  # LLM 总结的根因
    missing_capability: str = ""  # 缺失的能力描述
    confidence: float = 0.0  # 0.0 ~ 1.0

    def __post_init__(self) -> None:
        if not self.pattern_id:
            self.pattern_id = f"pattern_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "error_type": self.error_type,
            "stack_signature": self.stack_signature,
            "occurrence_count": self.occurrence_count,
            "trace_ids": self.trace_ids,
            "common_files": self.common_files,
            "root_cause_summary": self.root_cause_summary,
            "missing_capability": self.missing_capability,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FailurePattern:
        return cls(
            pattern_id=d.get("pattern_id", ""),
            error_type=d.get("error_type", ""),
            stack_signature=d.get("stack_signature", ""),
            occurrence_count=d.get("occurrence_count", 0),
            trace_ids=d.get("trace_ids", []),
            common_files=d.get("common_files", []),
            root_cause_summary=d.get("root_cause_summary", ""),
            missing_capability=d.get("missing_capability", ""),
            confidence=d.get("confidence", 0.0),
        )


# ---------------------------------------------------------------------------
# Skill 生成结果
# ---------------------------------------------------------------------------


@dataclass
class SkillGenResult:
    """Skill 生成操作的结果。"""

    skill_name: str = ""
    skill_path: str = ""  # 磁盘上的路径
    content: str = ""  # SKILL.md 完整内容
    based_on_traces: list[str] = field(default_factory=list)
    success: bool = False
    errors: list[str] = field(default_factory=list)
    evidence_quoted: list[str] = field(default_factory=list)  # 引用的证据片段


# ---------------------------------------------------------------------------
# 成功信号（成功经验路径）
# ---------------------------------------------------------------------------


@dataclass
class SuccessSignal:
    """一次「复杂且成功」任务的信号，用于沉淀成功经验 Skill。

    只有 success=True 且复杂度达标（迭代数或工具调用数超阈值）的任务
    才会产出本信号。had_retries 仅为信息性字段，不作为过滤条件——
    「含高成本成功」（重试/绕路后完成）同样纳入沉淀范围。
    """

    trace_id: str = ""
    task_description: str = ""
    iteration_count: int = 0  # Agent 主循环迭代轮数
    tool_call_count: int = 0  # 工具调用总次数（非去重）
    token_total: int = 0
    had_retries: bool = False  # 是否发生过重试/绕路（信息性，不参与过滤）
    key_steps: list[str] = field(default_factory=list)  # 关键步骤摘要
    tools_used: list[str] = field(default_factory=list)  # 去重后的工具名
    files_modified: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_description": self.task_description,
            "iteration_count": self.iteration_count,
            "tool_call_count": self.tool_call_count,
            "token_total": self.token_total,
            "had_retries": self.had_retries,
            "key_steps": self.key_steps,
            "tools_used": self.tools_used,
            "files_modified": self.files_modified,
        }


# ---------------------------------------------------------------------------
# 进化评估结果
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """进化效果的量化评估结果。"""

    success_rate_before: float = 0.0
    success_rate_after: float = 0.0
    avg_tokens_before: float = 0.0
    avg_tokens_after: float = 0.0
    token_change_pct: float = 0.0
    avg_exec_time_before: float = 0.0
    avg_exec_time_after: float = 0.0
    decision: str = "rollback"  # "keep" | "rollback"
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    replayed_traces: list[str] = field(default_factory=list)

    @property
    def success_rate_improved(self) -> bool:
        return self.success_rate_after > self.success_rate_before

    @property
    def token_increase_acceptable(self) -> bool:
        return self.token_change_pct <= 0.15

    @property
    def should_keep(self) -> bool:
        """综合判定：成功率提升 且 Token 增幅不超过阈值。"""
        return self.success_rate_improved and self.token_increase_acceptable

    def to_dict(self) -> dict[str, Any]:
        return {
            "success_rate_before": self.success_rate_before,
            "success_rate_after": self.success_rate_after,
            "avg_tokens_before": self.avg_tokens_before,
            "avg_tokens_after": self.avg_tokens_after,
            "token_change_pct": self.token_change_pct,
            "avg_exec_time_before": self.avg_exec_time_before,
            "avg_exec_time_after": self.avg_exec_time_after,
            "decision": self.decision,
            "reason": self.reason,
            "details": self.details,
            "replayed_traces": self.replayed_traces,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalResult:
        return cls(
            success_rate_before=d.get("success_rate_before", 0.0),
            success_rate_after=d.get("success_rate_after", 0.0),
            avg_tokens_before=d.get("avg_tokens_before", 0.0),
            avg_tokens_after=d.get("avg_tokens_after", 0.0),
            token_change_pct=d.get("token_change_pct", 0.0),
            avg_exec_time_before=d.get("avg_exec_time_before", 0.0),
            avg_exec_time_after=d.get("avg_exec_time_after", 0.0),
            decision=d.get("decision", "rollback"),
            reason=d.get("reason", ""),
            details=d.get("details", {}),
            replayed_traces=d.get("replayed_traces", []),
        )


# ---------------------------------------------------------------------------
# 进化周期
# ---------------------------------------------------------------------------


class EvolutionStatus(str, Enum):
    IDLE = "idle"
    READING = "reading"
    CLASSIFYING = "classifying"
    WRITING = "writing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class EvolutionRecord:
    """一次完整的进化周期记录。"""

    evolution_id: str = ""
    timestamp: float = 0.0
    traces_analyzed: list[str] = field(default_factory=list)
    problems_found: int = 0
    patterns: list[dict[str, Any]] = field(default_factory=list)
    skills_created: list[str] = field(default_factory=list)
    skills_deprecated: list[str] = field(default_factory=list)
    eval_result: dict[str, Any] = field(default_factory=dict)
    decision: str = "skipped"  # "kept" | "rolled_back" | "skipped"
    status: str = "completed"
    error_message: str = ""
    path: str = "failure"  # "failure" | "success" —— 进化路径来源

    def __post_init__(self) -> None:
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.timestamp or time.time()))
        if not self.evolution_id:
            self.evolution_id = f"evol_{ts}_{uuid.uuid4().hex[:6]}"
        if self.timestamp <= 0:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "evolution_id": self.evolution_id,
            "timestamp": self.timestamp,
            "traces_analyzed": self.traces_analyzed,
            "problems_found": self.problems_found,
            "patterns": self.patterns,
            "skills_created": self.skills_created,
            "skills_deprecated": self.skills_deprecated,
            "eval_result": self.eval_result,
            "decision": self.decision,
            "status": self.status,
            "error_message": self.error_message,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvolutionRecord:
        return cls(
            evolution_id=d.get("evolution_id", ""),
            timestamp=d.get("timestamp", 0.0),
            traces_analyzed=d.get("traces_analyzed", []),
            problems_found=d.get("problems_found", 0),
            patterns=d.get("patterns", []),
            skills_created=d.get("skills_created", []),
            skills_deprecated=d.get("skills_deprecated", []),
            eval_result=d.get("eval_result", {}),
            decision=d.get("decision", "skipped"),
            status=d.get("status", "completed"),
            error_message=d.get("error_message", ""),
            path=d.get("path", "failure"),
        )


# ---------------------------------------------------------------------------
# 进化周期（运行中状态）
# ---------------------------------------------------------------------------


@dataclass
class EvolutionCycle:
    """单次进化运行时的状态追踪。"""

    cycle_id: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    status: EvolutionStatus = EvolutionStatus.IDLE
    traces_read: int = 0
    patterns_found: list[FailurePattern] = field(default_factory=list)
    skills_generated: list[SkillGenResult] = field(default_factory=list)
    eval_result: EvalResult | None = None
    was_kept: bool = False
    backup_id: str = ""

    def __post_init__(self) -> None:
        if not self.cycle_id:
            self.cycle_id = f"cycle_{uuid.uuid4().hex[:8]}"
        if self.started_at <= 0:
            self.started_at = time.time()


# ---------------------------------------------------------------------------
# SkipEvolution 异常
# ---------------------------------------------------------------------------


class SkipEvolutionError(Exception):
    """进化被跳过（迹不足、无问题模式等）。"""
    pass
