"""成功经验驱动的自进化 —— 端到端测试。

覆盖完整生命周期：复杂成功识别 → 候选生成 → 复发晋升 → 任务开始命中注入 →
降本保留 / 命中失败降级。对应 checklist 的 EVO-S* 验收项。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mewcode.config import EvolutionConfig
from mewcode.harness.evolution.models import ExecutionTrace
from mewcode.harness.evolution.manager import EvolutionManager


def _make_client(return_name: str | None = None):
    async def client(prompt: str, system: str = "") -> str:
        if "最匹配" in prompt or "候选 Skill" in prompt:
            return return_name or "none"
        m = re.search(r"(trace_[a-f0-9]{12})", prompt)
        tid = m.group(1) if m else "trace_unknown000000"
        return f"""---
name: auto-success-mock
description: Reusable experience distilled from a successful complex task
mode: inline
allowedTools:
  - Bash
  - Read
---
# auto-success-mock

## When to Apply
Use when handling a task similar to the recorded successful complex task.

## Proven Approach
1. Identify the task scope
2. Apply the proven steps from trace {tid}

## Evidence References
Based on trace: {tid}

## Constraints
- Adapt to actual task differences
"""

    return client


def _complex_success(trace_id: str, desc: str = "refactor module X") -> ExecutionTrace:
    return ExecutionTrace(
        trace_id=trace_id,
        task_description=desc,
        success=True,
        iteration_count=10,
        tool_call_count=5,
        tokens_input=100,
        tokens_output=100,
    )


# ---------------------------------------------------------------------------
# E2E 生命周期
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_simple_task_no_candidate(tmp_path: Path):
    """EVO-S1：简单任务（< 阈值）成功 → 不生成候选。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    # 简单任务：5 轮迭代 / 4 次工具调用
    mgr.trace_store.append(ExecutionTrace(task_description="simple", success=True,
                                          iteration_count=5, tool_call_count=4))
    await mgr.check_and_evolve()
    assert len(mgr.skill_meta_manager.get_candidates()) == 0
    assert len(mgr.skill_meta_manager.get_active_success_skills()) == 0


@pytest.mark.asyncio
async def test_e2e_candidate_then_promotion(tmp_path: Path):
    """EVO-S2：两轮同类复杂成功 → 第一轮生成候选 → 第二轮晋升正式。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          success_promotion_recurrence=2,
                          min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())

    # 第一轮：无候选 → 生成候选
    mgr.trace_store.append(_complex_success("trace_aaaaaaaaaaaa"))
    await mgr.check_and_evolve()
    cands = mgr.skill_meta_manager.get_candidates()
    assert len(cands) == 1, "第一轮应生成 1 个候选"
    assert cands[0]["recurrence"] == 1

    # 第二轮：matcher 返回候选名 → 增加复发 → 晋升
    cand_name = cands[0]["name"]
    mgr.skill_matcher._client_factory = _make_client(return_name=cand_name)
    mgr.trace_store.append(_complex_success("trace_bbbbbbbbbbbb", "refactor module X again"))
    await mgr.check_and_evolve()
    actives = mgr.skill_meta_manager.get_active_success_skills()
    assert len(actives) == 1, "第二轮应晋升为正式"
    assert actives[0]["status"] == "active"
    assert actives[0]["recurrence"] == 2


@pytest.mark.asyncio
async def test_e2e_injection_match_returns_active_skill(tmp_path: Path):
    """EVO-S3：正式 Skill 在任务开始被命中注入（match_skill_for_injection 返回内容）。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True, success_match_enabled=True,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    # 手动创建一个正式 Skill + SKILL.md
    mgr.skill_meta_manager.add_skill("auto-success-x", description="refactor module X",
                                     status="active", path="success")
    skill_dir = mgr._skills_dir / "auto-success-x"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# proven approach for module X", encoding="utf-8")

    mgr.skill_matcher._client_factory = _make_client(return_name="auto-success-x")
    result = await mgr.match_skill_for_injection("refactor module X task")
    assert result is not None
    assert result["name"] == "auto-success-x"
    assert "proven approach" in result["content"]


@pytest.mark.asyncio
async def test_e2e_match_disabled_when_config_off(tmp_path: Path):
    """success_match_enabled=False → 不做注入匹配。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True, success_match_enabled=False,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    mgr.skill_meta_manager.add_skill("auto-success-x", description="x", status="active", path="success")
    result = await mgr.match_skill_for_injection("any task")
    assert result is None


@pytest.mark.asyncio
async def test_e2e_hit_failure_demotes(tmp_path: Path):
    """EVO-S5：连续命中失败 → Skill 自动降级废弃。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          success_hit_failure_threshold=3,
                          min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    mgr.skill_meta_manager.add_skill("auto-success-x", description="x", status="active", path="success")

    # 模拟三次命中采纳但任务失败
    for _ in range(3):
        mgr.skill_meta_manager.record_hit_result("auto-success-x", success=False)
    # check_and_evolve 会触发命中失败降级检查
    await mgr.check_and_evolve()
    assert len(mgr.skill_meta_manager.get_active_success_skills()) == 0
    # 已废弃
    all_skills = mgr.skill_meta_manager.get_all()
    assert all_skills["auto-success-x"]["disabled"] is True


@pytest.mark.asyncio
async def test_e2e_evaluate_hit_keep_on_cost_reduction(tmp_path: Path):
    """EVO-S3 降本侧：命中采纳后任务成功且降本 → 维持正式 + hit_count++。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          success_baseline_samples=5,
                          min_traces_trigger=1, min_traces_per_evolution=1,
                          max_traces_per_evolution=50)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    mgr.skill_meta_manager.add_skill("auto-success-x", description="x", status="active", path="success")

    # 建立基线：6 次同类成功任务，迭代数 20
    for i in range(6):
        mgr.trace_store.append(ExecutionTrace(
            trace_id=f"trace_base{i:012d}"[:17],
            task_description="baseline task", success=True,
            iteration_count=20, tool_call_count=5, tokens_input=100, tokens_output=100,
        ))
    # 本次命中采纳后迭代数 10（降 50%），token 210（+5%）→ keep
    hit_trace = ExecutionTrace(
        trace_id="trace_hithithit00",
        task_description="baseline task", success=True,
        iteration_count=10, tool_call_count=5, tokens_input=110, tokens_output=100,
    )
    mgr.trace_store.append(hit_trace)
    await mgr.evaluate_hit("auto-success-x", hit_trace.trace_id)
    skill = mgr.skill_meta_manager.get_stats("auto-success-x")
    assert skill["hit_count"] == 1
    assert not skill["disabled"]


@pytest.mark.asyncio
async def test_e2e_failure_and_success_paths_independent(tmp_path: Path):
    """双路独立：成功路径与失败路径在同一 check_and_evolve 内都执行，path 字段区分。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=True,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          min_failure_recurrence=2, min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    # 一条复杂成功 trace
    mgr.trace_store.append(_complex_success("trace_aaaaaaaaaaaa"))
    await mgr.check_and_evolve()
    records = mgr.skill_meta_manager.get_evolution_records()
    paths = {r.get("path", "failure") for r in records}
    assert "success" in paths


@pytest.mark.asyncio
async def test_e2e_success_path_disabled_when_config_off(tmp_path: Path):
    """EVO-S6：success_enabled=False → 成功路径完全不触发。"""
    cfg = EvolutionConfig(enabled=True, success_enabled=False,
                          success_iteration_threshold=8, success_tool_call_threshold=10,
                          min_traces_trigger=1, min_traces_per_evolution=1)
    mgr = EvolutionManager(harness_dir=tmp_path / "harness", config=cfg,
                           client_factory=_make_client())
    mgr.trace_store.append(_complex_success("trace_aaaaaaaaaaaa"))
    await mgr.check_and_evolve()
    assert len(mgr.skill_meta_manager.get_candidates()) == 0
    # 注入匹配也不触发
    assert await mgr.match_skill_for_injection("any") is None
