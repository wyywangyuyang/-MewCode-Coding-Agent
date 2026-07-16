from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mewcode.config import EvolutionConfig
from mewcode.harness.evolution.manager import EvolutionManager
from mewcode.harness.evolution.models import ExecutionTrace, SkipEvolutionError
from mewcode.harness.evolution.trace_store import ExecutionTraceStore, TraceCollector


def test_trace_store_round_trip_and_failure_filter(tmp_path: Path) -> None:
    store = ExecutionTraceStore(tmp_path / "traces")
    success = ExecutionTrace(trace_id="ok", task_description="ok", success=True)
    failure = ExecutionTrace(
        trace_id="failed",
        task_description="failed",
        success=False,
        error_info={"error_type": "RuntimeError", "message": "boom"},
    )
    store.append_batch([success, failure])

    assert store.count() == 2
    assert {trace.trace_id for trace in store.load_all()} == {"ok", "failed"}
    assert [trace.trace_id for trace in store.get_failures()] == ["failed"]


def test_trace_collector_flushes_completed_task(tmp_path: Path) -> None:
    store = ExecutionTraceStore(tmp_path / "traces")
    collector = TraceCollector(store)
    trace_id = collector.start_task("implement feature")
    collector.record_tool_use(trace_id, "ReadFile")
    collector.record_tokens(trace_id, input_tokens=4, output_tokens=2)
    collector.end_task(trace_id, success=True)
    collector.flush()

    trace = store.load_all()[0]
    assert trace.tools_used == ["ReadFile"]
    assert trace.total_tokens == 6


@pytest.mark.asyncio
async def test_manager_skips_without_enough_traces(tmp_path: Path) -> None:
    manager = EvolutionManager(tmp_path / "harness", EvolutionConfig())
    manager.decision_loop.run = AsyncMock(side_effect=SkipEvolutionError())

    assert await manager.check_and_evolve() is None
    assert manager._running is False
    assert manager.get_last_cycle() is None


@pytest.mark.asyncio
async def test_manager_contains_cycle_failure(tmp_path: Path) -> None:
    manager = EvolutionManager(tmp_path / "harness", EvolutionConfig())
    manager.decision_loop.run = AsyncMock(side_effect=RuntimeError("cycle failed"))

    assert await manager.check_and_evolve() is None
    assert manager._running is False
