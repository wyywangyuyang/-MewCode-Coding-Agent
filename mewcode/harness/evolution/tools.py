"""暴露给 Agent 的自进化工具。

提供以下工具：
- TriggerEvolution: 手动触发一轮进化检查
- ListEvolutions: 列出进化历史
- GetEvolutionDetail: 查看某次进化的详细信息
- ListAutoSkills: 列出自动生成的 Skill 及状态
- DeprecateSkill: 手动废弃某个自动生成的 Skill
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from mewcode.tools.base import Tool, ToolResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. TriggerEvolutionTool
# ---------------------------------------------------------------------------


class TriggerEvolutionParams(BaseModel):
    pass


class TriggerEvolutionTool(Tool):
    """手动触发一轮进化检查。"""

    name = "TriggerEvolution"
    description = (
        "Manually trigger a self-evolution check. "
        "The system will read execution traces, classify failure patterns, "
        "auto-generate Skills if needed, evaluate, and keep or rollback changes."
    )
    params_model = TriggerEvolutionParams
    category = "harness"
    is_concurrency_safe = False  # 防止并发触发

    def __init__(self, evolution_manager: Any = None) -> None:
        self._mgr = evolution_manager

    async def execute(self, params: TriggerEvolutionParams) -> ToolResult:
        if self._mgr is None:
            return ToolResult(output="Evolution manager not initialized.", is_error=True)

        result = await self._mgr.run_if_ready()
        if result is None:
            return ToolResult(
                output="Evolution check completed: insufficient traces (< 30) or no actionable patterns. No changes made."
            )
        return ToolResult(
            output=(
                f"Evolution cycle completed.\n"
                f"ID: {result.evolution_id}\n"
                f"Decision: {result.decision}\n"
                f"Skills created: {', '.join(result.skills_created) if result.skills_created else 'none'}\n"
                f"Skills deprecated: {', '.join(result.skills_deprecated) if result.skills_deprecated else 'none'}\n"
                f"Traces analyzed: {len(result.traces_analyzed)}\n"
                f"Problems found: {result.problems_found}"
            )
        )


# ---------------------------------------------------------------------------
# 2. ListEvolutionsTool
# ---------------------------------------------------------------------------


class ListEvolutionsParams(BaseModel):
    limit: int = Field(default=20, description="Max number of evolution records to return")


class ListEvolutionsTool(Tool):
    """列出进化历史。"""

    name = "ListEvolutions"
    description = "List recent self-evolution history including decisions and skills created."
    params_model = ListEvolutionsParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, evolution_manager: Any = None) -> None:
        self._mgr = evolution_manager

    async def execute(self, params: ListEvolutionsParams) -> ToolResult:
        if self._mgr is None:
            return ToolResult(output="Evolution manager not initialized.", is_error=True)

        records = self._mgr.list_cycles(limit=params.limit)
        if not records:
            return ToolResult(output="No evolution records found.")

        lines = [f"Evolution history ({len(records)} records):"]
        for r in records:
            evo_id = r.get("evolution_id", "?")
            decision = r.get("decision", "?")
            skills = r.get("skills_created", [])
            ts = r.get("timestamp", 0)
            import time
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"
            lines.append(
                f"  [{ts_str}] {evo_id} — {decision}"
                + (f" — skills: {', '.join(skills)}" if skills else "")
            )

        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# 3. GetEvolutionDetailTool
# ---------------------------------------------------------------------------


class GetEvolutionDetailParams(BaseModel):
    evolution_id: str = Field(..., description="Evolution record ID")


class GetEvolutionDetailTool(Tool):
    """查看某次进化的详细信息。"""

    name = "GetEvolutionDetail"
    description = "Get detailed information about a specific evolution cycle."
    params_model = GetEvolutionDetailParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, evolution_manager: Any = None) -> None:
        self._mgr = evolution_manager

    async def execute(self, params: GetEvolutionDetailParams) -> ToolResult:
        if self._mgr is None:
            return ToolResult(output="Evolution manager not initialized.", is_error=True)

        records = self._mgr.list_cycles(limit=200)
        target = None
        for r in records:
            if r.get("evolution_id") == params.evolution_id:
                target = r
                break

        if target is None:
            return ToolResult(
                output=f"Evolution record '{params.evolution_id}' not found.",
                is_error=True,
            )

        import json
        return ToolResult(output=json.dumps(target, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 4. ListAutoSkillsTool
# ---------------------------------------------------------------------------


class ListAutoSkillsParams(BaseModel):
    include_deprecated: bool = Field(default=False, description="Include deprecated skills")


class ListAutoSkillsTool(Tool):
    """列出自动生成的 Skill 及状态。"""

    name = "ListAutoSkills"
    description = "List auto-generated skills with their invocation statistics and deprecation status."
    params_model = ListAutoSkillsParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, skill_meta_manager: Any = None) -> None:
        self._meta_mgr = skill_meta_manager

    async def execute(self, params: ListAutoSkillsParams) -> ToolResult:
        if self._meta_mgr is None:
            return ToolResult(output="Skill meta manager not initialized.", is_error=True)

        active = self._meta_mgr.get_active()
        all_skills = list(active)
        if params.include_deprecated:
            all_skills.extend(self._meta_mgr.get_deprecated())

        if not all_skills:
            return ToolResult(output="No auto-generated skills found.")

        lines = [f"Auto-generated skills ({len(all_skills)}):"]
        for s in all_skills:
            name = s.get("name", "?")
            call_count = s.get("call_count", 0)
            tasks_since = s.get("tasks_since_last_call", 0)
            disabled = s.get("disabled", False)
            path = s.get("path", "failure")
            life_status = s.get("status", "active")
            if disabled:
                status = "DEPRECATED"
            elif path == "success":
                status = life_status.upper()  # CANDIDATE / ACTIVE
            else:
                status = "ACTIVE"
            base = (
                f"  [{status}] {name} — called {call_count} times, "
                f"{tasks_since} tasks since last use"
            )
            if path == "success":
                recurrence = s.get("recurrence", 0)
                hit_count = s.get("hit_count", 0)
                hit_failures = s.get("hit_failures", 0)
                base += (
                    f" | path=success recurrence={recurrence} "
                    f"hits={hit_count} hit_failures={hit_failures}"
                )
            lines.append(base)

        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# 5. DeprecateSkillTool
# ---------------------------------------------------------------------------


class DeprecateSkillParams(BaseModel):
    skill_name: str = Field(..., description="Name of the auto-generated skill to deprecate")


class DeprecateSkillTool(Tool):
    """手动废弃某个自动生成的 Skill。"""

    name = "DeprecateSkill"
    description = (
        "Manually deprecate an auto-generated skill. "
        "Updates skill_meta.json and the SKILL.md document."
    )
    params_model = DeprecateSkillParams
    category = "harness"
    is_concurrency_safe = True

    def __init__(self, skill_meta_manager: Any = None) -> None:
        self._meta_mgr = skill_meta_manager

    async def execute(self, params: DeprecateSkillParams) -> ToolResult:
        if self._meta_mgr is None:
            return ToolResult(output="Skill meta manager not initialized.", is_error=True)

        success = self._meta_mgr.deprecate_skill(params.skill_name)
        if success:
            return ToolResult(output=f"Skill '{params.skill_name}' has been deprecated.")
        else:
            return ToolResult(
                output=f"Skill '{params.skill_name}' not found or already deprecated.",
                is_error=True,
            )
