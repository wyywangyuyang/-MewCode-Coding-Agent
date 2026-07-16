import asyncio
import json
from pathlib import Path

import pytest

from mewcode.workflow.context import BudgetExhaustedError, WorkflowContext
from mewcode.workflow.engine import WorkflowEngine, WorkflowNotFoundError
from mewcode.workflow.journal import Journal
from mewcode.workflow.models import AgentCallRecord, BudgetInfo


def _write_workflow(work_dir: Path, name: str, source: str) -> None:
    path = work_dir / ".mewcode" / "workflows" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_discovers_workflow_metadata_without_execution(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "sample",
        '"""Fallback description."""\n'
        'META = {"name": "display-name", "description": "Explicit", "phases": ["one", "two"]}\n'
        "async def run(ctx):\n    return 'ok'\n",
    )

    definitions = WorkflowEngine(str(tmp_path)).list_workflows()

    assert [(item.name, item.description, item.phases) for item in definitions] == [
        ("display-name", "Explicit", ["one", "two"])
    ]


@pytest.mark.asyncio
async def test_executes_workflow_and_records_usage(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "execute_once",
        "async def run(ctx, args):\n"
        "    ctx.phase('implementation')\n"
        "    return await ctx.agent(args['prompt'], label='worker')\n",
    )
    calls = []

    async def agent_factory(**kwargs):
        calls.append(kwargs)
        return "completed", {"input_tokens": 10, "output_tokens": 4}

    engine = WorkflowEngine(str(tmp_path), agent_factory=agent_factory)
    result = await engine.execute(
        "execute_once", {"prompt": "do work"}, budget_total=20, resume=False
    )

    assert result == "completed"
    assert calls[0]["prompt"] == "do work"
    state = engine.get_active_runs()[0]
    assert state.status == "completed"
    run_id = engine.get_workflow_runs("execute_once")[0]
    journal = Journal.load(str(tmp_path), "execute_once", run_id)
    assert journal is not None
    records = journal.get_all_records()
    journal.close()
    assert len(records) == 1
    assert records[0].status == "completed"
    assert records[0].input_tokens == 10
    assert records[0].output_tokens == 4


@pytest.mark.asyncio
async def test_context_reuses_completed_journal_entry(tmp_path: Path) -> None:
    journal = Journal.create(str(tmp_path), "cached", "run-1")
    calls = 0

    async def agent_factory(**kwargs):
        nonlocal calls
        calls += 1
        return "first", {"input_tokens": 2, "output_tokens": 1}

    context = WorkflowContext(
        workflow_name="cached",
        run_id="run-1",
        journal=journal,
        agent_factory=agent_factory,
    )

    assert await context.agent("same prompt") == "first"
    assert await context.agent("same prompt") == "first"
    journal.close()
    assert calls == 1


@pytest.mark.asyncio
async def test_pipeline_parallel_and_budget_failure(tmp_path: Path) -> None:
    journal = Journal.create(str(tmp_path), "patterns", "run-1")

    async def agent_factory(**kwargs):
        return "result", {"input_tokens": 3, "output_tokens": 2}

    context = WorkflowContext(
        workflow_name="patterns",
        run_id="run-1",
        journal=journal,
        budget=BudgetInfo(total=5),
        agent_factory=agent_factory,
    )

    async def double(value):
        return value * 2

    async def label(value, original, index):
        return f"{index}:{original}:{value}"

    async def fail():
        raise RuntimeError("expected")

    assert await context.pipeline([2, 3], double, label) == ["0:2:4", "1:3:6"]
    assert await context.parallel([lambda: double(2), fail]) == [4, None]
    assert await context.agent("consume budget") == "result"
    with pytest.raises(BudgetExhaustedError):
        await context.agent("over budget")
    journal.close()


@pytest.mark.asyncio
async def test_missing_workflow_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(WorkflowNotFoundError, match="not found"):
        await WorkflowEngine(str(tmp_path)).execute("missing")
