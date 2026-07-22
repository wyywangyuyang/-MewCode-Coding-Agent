"""成功经验驱动的自进化 —— 单元测试。

覆盖：复杂任务识别、候选 Skill 生成（含质量护栏）、Skill 状态机与晋升、
命中后降本评估。对应 checklist「成功经验驱动的自进化」节。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mewcode.harness.evolution.models import ExecutionTrace, SuccessSignal
from mewcode.harness.evolution.success_detector import SuccessDetector
from mewcode.harness.evolution.success_generator import SuccessSkillGenerator
from mewcode.harness.evolution.skill_meta import SkillMetaManager
from mewcode.harness.evolution.evaluator import EvolutionEvaluator
from mewcode.harness.evolution.skill_generator import (
    InsufficientEvidenceError,
)


# ---------------------------------------------------------------------------
# Mock LLM 客户端
# ---------------------------------------------------------------------------


def make_client(return_name: str | None = None):
    """生成 mock client_factory。生成路径会从 prompt 中提取 trace_id 嵌入内容以通过反编造门。"""

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


# ---------------------------------------------------------------------------
# SuccessDetector
# ---------------------------------------------------------------------------


class TestSuccessDetector:
    def test_simple_success_not_complex(self):
        d = SuccessDetector(iteration_threshold=8, tool_call_threshold=10)
        t = ExecutionTrace(task_description="simple", success=True, iteration_count=5, tool_call_count=4)
        assert d.detect(t) is None

    def test_iteration_threshold_triggers(self):
        d = SuccessDetector(iteration_threshold=8, tool_call_threshold=10)
        t = ExecutionTrace(task_description="complex", success=True, iteration_count=8, tool_call_count=3)
        sig = d.detect(t)
        assert sig is not None
        assert sig.iteration_count == 8

    def test_tool_call_threshold_triggers(self):
        d = SuccessDetector(iteration_threshold=8, tool_call_threshold=10)
        t = ExecutionTrace(task_description="complex", success=True, iteration_count=3, tool_call_count=10)
        assert d.detect(t) is not None

    def test_failed_task_not_detected(self):
        d = SuccessDetector(iteration_threshold=8, tool_call_threshold=10)
        t = ExecutionTrace(task_description="complex but failed", success=False, iteration_count=20, tool_call_count=20)
        assert d.detect(t) is None

    def test_had_retries_is_informational(self):
        d = SuccessDetector(iteration_threshold=8, tool_call_threshold=10)
        t = ExecutionTrace(task_description="complex", success=True, iteration_count=10, tool_call_count=3, had_retries=True)
        sig = d.detect(t)
        assert sig is not None
        assert sig.had_retries is True  # 含高成本成功仍纳入范围


# ---------------------------------------------------------------------------
# SuccessSkillGenerator
# ---------------------------------------------------------------------------


class TestSuccessSkillGenerator:
    @pytest.mark.asyncio
    async def test_generate_candidate_skill(self, tmp_path: Path):
        gen = SuccessSkillGenerator(client_factory=make_client(), skills_dir=tmp_path)
        signal = SuccessSignal(
            trace_id="trace_aaaaaaaaaaaa",
            task_description="refactor module X",
            iteration_count=10,
            tool_call_count=5,
            key_steps=["Task: refactor module X", "Tools used: Bash, Edit"],
            tools_used=["Bash", "Edit"],
        )
        result = await gen.generate(signal)
        assert result.success, result.errors
        assert result.skill_name.startswith("auto-success-")
        assert Path(result.skill_path).exists()

    @pytest.mark.asyncio
    async def test_insufficient_evidence_raises(self, tmp_path: Path):
        gen = SuccessSkillGenerator(client_factory=make_client(), skills_dir=tmp_path)
        signal = SuccessSignal(trace_id="trace_bbbbbbbbbbbb", key_steps=["only one step"])
        with pytest.raises(InsufficientEvidenceError):
            await gen.generate(signal)

    @pytest.mark.asyncio
    async def test_fabricated_content_rejected(self, tmp_path: Path):
        """LLM 返回不含任何证据引用的内容 → 校验失败，success=False。"""

        async def bad_client(prompt: str, system: str = "") -> str:
            return """---
name: auto-success-bad
description: fabricated
---
# no evidence referenced here, totally made up content without trace id
"""

        gen = SuccessSkillGenerator(client_factory=bad_client, skills_dir=tmp_path)
        signal = SuccessSignal(
            trace_id="trace_cccccccccccc",
            task_description="some unique task description here",
            iteration_count=10,
            tool_call_count=5,
            key_steps=["Task: some unique task description here", "Tools used: Bash"],
            tools_used=["Bash"],
        )
        result = await gen.generate(signal)
        assert not result.success
        assert any("fabricated" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_template_fallback_without_client(self, tmp_path: Path):
        gen = SuccessSkillGenerator(client_factory=None, skills_dir=tmp_path)
        signal = SuccessSignal(
            trace_id="trace_dddddddddddd",
            task_description="template task",
            iteration_count=10,
            tool_call_count=5,
            key_steps=["Task: template task", "Tools used: Bash"],
            tools_used=["Bash"],
        )
        result = await gen.generate(signal)
        assert result.success


# ---------------------------------------------------------------------------
# SkillMetaManager 状态机
# ---------------------------------------------------------------------------


class TestSkillMetaStateMachine:
    def test_candidate_promotion_flow(self, tmp_path: Path):
        meta = SkillMetaManager(tmp_path / "skill_meta.json")
        meta.add_skill("auto-success-x", description="test", status="candidate", path="success")
        meta.increment_recurrence("auto-success-x")  # creation -> 1

        assert len(meta.get_candidates()) == 1
        assert len(meta.get_active_success_skills()) == 0

        r = meta.increment_recurrence("auto-success-x")
        assert r == 2
        meta.promote_to_active("auto-success-x")
        assert len(meta.get_active_success_skills()) == 1
        assert len(meta.get_candidates()) == 0

    def test_hit_failure_accumulation_demotes(self, tmp_path: Path):
        meta = SkillMetaManager(tmp_path / "skill_meta.json")
        meta.add_skill("auto-success-y", description="test", status="active", path="success")
        for _ in range(3):
            meta.record_hit_result("auto-success-y", success=False)
        candidates = meta.check_hit_failure_candidates(3)
        assert "auto-success-y" in candidates
        meta.demote_skill("auto-success-y", reason="hit failures")
        assert len(meta.get_active_success_skills()) == 0

    def test_backward_compat_missing_status_fields(self, tmp_path: Path):
        """旧条目（无 status/path 字段）加载后应回填默认值。"""
        import json
        path = tmp_path / "skill_meta.json"
        path.write_text(json.dumps({
            "version": 1,
            "skills": {"old-skill": {"name": "old-skill", "disabled": False}},
            "evolution_records": {},
        }), encoding="utf-8")
        meta = SkillMetaManager(path)
        data = meta.load()
        skill = data["skills"]["old-skill"]
        assert skill["status"] == "active"
        assert skill["path"] == "failure"
        assert skill["recurrence"] == 0


# ---------------------------------------------------------------------------
# EvolutionEvaluator 成功路径
# ---------------------------------------------------------------------------


class TestEvaluatorSuccess:
    def test_keep_when_iteration_reduced_and_token_acceptable(self):
        ev = EvolutionEvaluator(token_increase_threshold=0.15)
        baseline = {"sample_count": 6, "avg_iteration_count": 20.0, "avg_tokens": 200.0}
        r = ev.evaluate_success(task_iteration_count=10, task_token_total=210, baseline=baseline)
        assert r.decision == "keep"

    def test_rollback_when_no_iteration_reduction(self):
        ev = EvolutionEvaluator(token_increase_threshold=0.15)
        baseline = {"sample_count": 6, "avg_iteration_count": 20.0, "avg_tokens": 200.0}
        r = ev.evaluate_success(task_iteration_count=20, task_token_total=210, baseline=baseline)
        assert r.decision == "rollback"

    def test_rollback_when_token_increase_exceeds(self):
        ev = EvolutionEvaluator(token_increase_threshold=0.15)
        baseline = {"sample_count": 6, "avg_iteration_count": 20.0, "avg_tokens": 200.0}
        r = ev.evaluate_success(task_iteration_count=10, task_token_total=300, baseline=baseline)
        assert r.decision == "rollback"

    def test_insufficient_baseline_keeps_without_demotion(self):
        ev = EvolutionEvaluator(token_increase_threshold=0.15)
        baseline = {"sample_count": 2, "avg_iteration_count": 20.0, "avg_tokens": 200.0}
        r = ev.evaluate_success(task_iteration_count=20, task_token_total=300, baseline=baseline)
        assert r.decision == "keep"

    def test_compute_success_baseline(self):
        ev = EvolutionEvaluator()
        traces = [
            ExecutionTrace(task_description="t", success=True, iteration_count=20, tokens_input=100, tokens_output=100)
            for _ in range(6)
        ]
        b = ev.compute_success_baseline(traces)
        assert b["sample_count"] == 6
        assert b["avg_iteration_count"] == 20.0
