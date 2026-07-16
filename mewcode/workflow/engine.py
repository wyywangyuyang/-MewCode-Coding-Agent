"""WorkflowEngine — 加载、执行、恢复 workflow。"""

from __future__ import annotations

import importlib.util
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from mewcode.workflow.context import BudgetExhaustedError, WorkflowContext
from mewcode.workflow.journal import JOURNALS_DIR, Journal
from mewcode.workflow.models import BudgetInfo, WorkflowDef, WorkflowState

log = logging.getLogger(__name__)

WORKFLOWS_DIR = ".mewcode/workflows"


class WorkflowError(Exception):
    """Workflow 执行错误。"""

    def __init__(self, message: str, workflow_name: str = "") -> None:
        self.workflow_name = workflow_name
        super().__init__(message)


class WorkflowNotFoundError(WorkflowError):
    """Workflow 未找到。"""

    pass


class WorkflowEngine:
    """Workflow 的执行引擎。

    负责：
    - 扫描 .mewcode/workflows/ 目录发现 workflow
    - 加载 Python 模块并提取入口函数
    - 创建 WorkflowContext 并执行
    - 管理 Journal 和断点恢复
    """

    def __init__(
        self,
        work_dir: str,
        *,
        agent_factory: Callable[..., Any] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_phase_change: Callable[[str], None] | None = None,
        on_agent_start: Callable[[str, str], None] | None = None,
        on_agent_complete: Callable[[str, str], None] | None = None,
    ) -> None:
        self._work_dir = work_dir
        self._agent_factory = agent_factory
        self._on_log = on_log
        self._on_phase_change = on_phase_change
        self._on_agent_start = on_agent_start
        self._on_agent_complete = on_agent_complete

        self._active_runs: dict[str, WorkflowState] = {}

    # ------------------------------------------------------------------
    # 发现 workflow
    # ------------------------------------------------------------------

    def list_workflows(self) -> list[WorkflowDef]:
        """扫描 .mewcode/workflows/ 目录，返回所有可用 workflow 的元信息。"""
        workflows_dir = Path(self._work_dir) / WORKFLOWS_DIR
        if not workflows_dir.exists():
            return []

        definitions: list[WorkflowDef] = []
        for py_file in sorted(workflows_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            wf_def = self._extract_meta(py_file)
            definitions.append(wf_def)

        return definitions

    def _extract_meta(self, py_file: Path) -> WorkflowDef:
        """从 .py 文件中提取 META 信息（不执行模块代码）。

        策略：
        1. 静态解析文件顶部的 META = {...} 字典（如果存在）
        2. 回退到模块的 __doc__
        3. 回退到文件名
        """
        name = py_file.stem
        description = ""
        phases: list[str] = []

        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception:
            return WorkflowDef(
                name=name,
                description=description,
                phases=phases,
                source_path=str(py_file),
            )

        # 简单解析 META = {...} 块
        import ast
        try:
            tree = ast.parse(source)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "META":
                            if isinstance(node.value, ast.Dict):
                                for key, val in zip(node.value.keys, node.value.values):
                                    if not isinstance(key, ast.Constant):
                                        continue
                                    if key.value == "phases" and isinstance(val, ast.List):
                                        phases = [
                                            str(element.value)
                                            for element in val.elts
                                            if isinstance(element, ast.Constant)
                                        ]
                                    elif isinstance(val, ast.Constant):
                                        if key.value == "name":
                                            name = str(val.value)
                                        elif key.value == "description":
                                            description = str(val.value)
        except SyntaxError:
            pass

        # 回退到 __doc__
        if not description:
            doc = _extract_docstring(source)
            if doc:
                description = doc.split("\n")[0].strip()

        return WorkflowDef(
            name=name,
            description=description,
            phases=phases,
            source_path=str(py_file),
        )

    # ------------------------------------------------------------------
    # 执行 workflow
    # ------------------------------------------------------------------

    async def execute(
        self,
        workflow_name: str,
        args: Any = None,
        *,
        budget_total: int | None = None,
        resume: bool = True,
    ) -> Any:
        """执行指定 workflow。

        Args:
            workflow_name: workflow 名称（文件名去 .py）。
            args: 传递给 workflow 函数的参数。
            budget_total: token 预算上限（None = 无限）。
            resume: 是否尝试从 journal 恢复未完成的 run。

        Returns:
            workflow 函数的返回值。

        Raises:
            WorkflowNotFoundError: workflow 不存在。
            WorkflowError: 执行错误。
            BudgetExhaustedError: token 预算耗尽。
        """
        # 加载模块
        module = self._load_module(workflow_name)
        if module is None:
            raise WorkflowNotFoundError(
                f"Workflow '{workflow_name}' not found in {WORKFLOWS_DIR}/",
                workflow_name=workflow_name,
            )

        entry_fn = self._find_entry(module, workflow_name)
        if entry_fn is None:
            raise WorkflowError(
                f"No async entry function found in workflow '{workflow_name}'. "
                f"Define an async function named 'run' or '{workflow_name}'.",
                workflow_name=workflow_name,
            )

        # 确定 run_id（新建或恢复）
        run_id = None
        journal = None

        if resume:
            # 检查是否有未完成的 run
            existing_runs = Journal.list_journals(self._work_dir, workflow_name)
            for rid in existing_runs:
                j = Journal.load(self._work_dir, workflow_name, rid)
                if j is not None:
                    incomplete = j.get_incomplete_runs()
                    if incomplete:
                        run_id = rid
                        journal = j
                        log.info(
                            "[workflow] resuming incomplete run %s for '%s'",
                            run_id, workflow_name,
                        )
                        break
                    j.close()

        if run_id is None:
            run_id = uuid.uuid4().hex[:12]
            journal = Journal.create(self._work_dir, workflow_name, run_id)
            log.info(
                "[workflow] starting new run %s for '%s'",
                run_id, workflow_name,
            )

        if journal is None:
            raise WorkflowError(
                f"Failed to create journal for workflow '{workflow_name}'",
                workflow_name=workflow_name,
            )

        # 创建上下文
        budget = BudgetInfo(total=budget_total)
        ctx = WorkflowContext(
            workflow_name=workflow_name,
            run_id=run_id,
            journal=journal,
            budget=budget,
            agent_factory=self._agent_factory,
            on_log=self._on_log,
            on_phase_change=self._on_phase_change,
            on_agent_start=self._on_agent_start,
            on_agent_complete=self._on_agent_complete,
        )

        # 追踪状态
        state = WorkflowState(
            run_id=run_id,
            workflow_name=workflow_name,
            status="running",
            started_at=_now_iso(),
        )
        self._active_runs[run_id] = state

        # 执行
        try:
            result = await entry_fn(ctx, args) if args is not None else await entry_fn(ctx)
            state.status = "completed"
            state.completed_at = _now_iso()
            return result
        except BudgetExhaustedError:
            state.status = "interrupted"
            state.completed_at = _now_iso()
            raise
        except Exception:
            state.status = "failed"
            state.completed_at = _now_iso()
            raise
        finally:
            journal.close()
            # 保留 state 在 _active_runs 中供查询

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load_module(self, workflow_name: str) -> Any:
        """动态加载 workflow 模块。"""
        workflows_dir = Path(self._work_dir) / WORKFLOWS_DIR
        py_file = workflows_dir / f"{workflow_name}.py"

        if not py_file.exists():
            return None

        # 使用唯一的模块名避免冲突
        mod_name = f"mewcode_workflow_{workflow_name}"

        # 如果已加载，重新加载
        if mod_name in sys.modules:
            return sys.modules[mod_name]

        spec = importlib.util.spec_from_file_location(mod_name, py_file)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _find_entry(module: Any, workflow_name: str) -> Any:
        """在模块中查找入口函数。

        优先级：
        1. `run` 函数
        2. 与 workflow 同名的函数
        3. 第一个 async 函数
        """
        import inspect

        # 优先查找 run
        run_fn = getattr(module, "run", None)
        if run_fn and inspect.iscoroutinefunction(run_fn):
            return run_fn

        # 按名称查找
        named_fn = getattr(module, workflow_name, None)
        if named_fn and inspect.iscoroutinefunction(named_fn):
            return named_fn

        # 查找第一个 async 函数
        for name in dir(module):
            if name.startswith("_"):
                continue
            obj = getattr(module, name)
            if inspect.iscoroutinefunction(obj):
                return obj

        return None

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_run_state(self, run_id: str) -> WorkflowState | None:
        """获取指定 run 的状态。"""
        return self._active_runs.get(run_id)

    def get_active_runs(self) -> list[WorkflowState]:
        """获取所有活跃 run 的状态。"""
        return list(self._active_runs.values())

    def get_workflow_runs(self, workflow_name: str) -> list[str]:
        """获取某个 workflow 的所有 run_id。"""
        return Journal.list_journals(self._work_dir, workflow_name)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _extract_docstring(source: str) -> str | None:
    """从 Python 源码中提取模块级 docstring。"""
    import ast
    try:
        tree = ast.parse(source)
        doc = ast.get_docstring(tree)
        return doc
    except SyntaxError:
        return None
