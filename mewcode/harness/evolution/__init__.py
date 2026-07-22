"""MewCode Agent 自进化子系统。

实现真正的自主进化能力：
- 执行轨迹收集与持久化
- 失败模式自动分类
- 失败驱动的 Skill 自动生成
- Skill 元数据管理与自动废弃
- 文件备份与回滚
- 基于量化指标的进化评估
- 6 阶段自主进化决策循环
"""

from mewcode.harness.evolution.models import (
    EvalResult,
    EvolutionCycle,
    EvolutionRecord,
    EvolutionStatus,
    ExecutionTrace,
    FailurePattern,
    ProblemCategory,
    SkillGenResult,
    SkillStatus,
    SuccessSignal,
)
from mewcode.harness.evolution.backup import BackupManager
from mewcode.harness.evolution.trace_store import ExecutionTraceStore, TraceCollector
from mewcode.harness.evolution.skill_meta import SkillMetaManager
from mewcode.harness.evolution.problem_classifier import ProblemClassifier
from mewcode.harness.evolution.skill_generator import SkillGenerator
from mewcode.harness.evolution.evaluator import EvolutionEvaluator
from mewcode.harness.evolution.decision_loop import EvolutionDecisionLoop
from mewcode.harness.evolution.manager import EvolutionManager
from mewcode.harness.evolution.success_detector import SuccessDetector
from mewcode.harness.evolution.success_generator import SuccessSkillGenerator
from mewcode.harness.evolution.skill_matcher import SkillMatcher
from mewcode.harness.evolution.tools import (
    TriggerEvolutionTool,
    ListEvolutionsTool,
    GetEvolutionDetailTool,
    ListAutoSkillsTool,
    DeprecateSkillTool,
)

__all__ = [
    # Models
    "EvalResult",
    "EvolutionCycle",
    "EvolutionRecord",
    "EvolutionStatus",
    "ExecutionTrace",
    "FailurePattern",
    "ProblemCategory",
    "SkillGenResult",
    "SkillStatus",
    "SuccessSignal",
    # Core
    "BackupManager",
    "ExecutionTraceStore",
    "TraceCollector",
    "SkillMetaManager",
    # Intelligence
    "ProblemClassifier",
    "SkillGenerator",
    "EvolutionEvaluator",
    "SuccessDetector",
    "SuccessSkillGenerator",
    "SkillMatcher",
    # Orchestration
    "EvolutionDecisionLoop",
    "EvolutionManager",
    # Tools
    "TriggerEvolutionTool",
    "ListEvolutionsTool",
    "GetEvolutionDetailTool",
    "ListAutoSkillsTool",
    "DeprecateSkillTool",
]
