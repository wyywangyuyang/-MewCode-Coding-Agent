"""语义 Skill 匹配器。

两处复用：
- 成功路径晋升：新成功信号匹配已有候选 Skill（match_candidates）
- 任务开始注入：当前任务匹配正式 Skill（match_active）

匹配为轻量 LLM 侧路调用，设独立超时；超时/失败时静默跳过，绝不阻塞主循环。
命中的正式 Skill 内容以「可用经验」形式返回，由 Agent 自主判断是否采纳。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 匹配侧路调用超时（秒）
DEFAULT_MATCH_TIMEOUT = 8.0

MATCH_SYSTEM_PROMPT = """\
你是一个任务匹配助手。判断当前任务与下列已有经验 Skill 中哪一个语义上是「同类任务」。

规则：
- 只返回最匹配的一个 Skill 名称，如果没有真正同类的则返回 "none"
- 严格基于任务语义判断，不要勉强匹配
- 只输出 Skill 名称本身（或 none），不要输出任何解释或多余文字
"""

MATCH_USER_PROMPT = """\
当前任务:
{task}

候选 Skill 列表（name — description）:
{candidates}

最匹配的 Skill 名称（或 none）:
"""


class SkillMatcher:
    """基于语义的 Skill 匹配器。"""

    def __init__(
        self,
        client_factory: Any = None,
        skill_meta_manager: Any = None,
        skills_dir: Path | None = None,
        timeout: float = DEFAULT_MATCH_TIMEOUT,
    ) -> None:
        self._client_factory = client_factory
        self._meta_mgr = skill_meta_manager
        self._skills_dir = Path(skills_dir) if skills_dir else Path("harness/skills")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def match_candidates(self, task_description: str) -> dict[str, Any] | None:
        """在候选 Skill 中查找同类（用于晋升判定）。

        Returns:
            命中的候选 Skill 元数据 dict（含 name），或 None。
        """
        if self._meta_mgr is None:
            return None
        candidates = self._meta_mgr.get_candidates()
        if not candidates:
            return None
        return await self._match(task_description, candidates, with_content=False)

    async def match_active(self, task_description: str) -> dict[str, Any] | None:
        """在正式成功 Skill 中查找同类（用于任务开始注入）。

        Returns:
            命中的 Skill dict（含 name, description, content），或 None。
        """
        if self._meta_mgr is None:
            return None
        actives = self._meta_mgr.get_active_success_skills()
        if not actives:
            return None
        return await self._match(task_description, actives, with_content=True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _match(
        self,
        task_description: str,
        skills: list[dict[str, Any]],
        with_content: bool,
    ) -> dict[str, Any] | None:
        """对一组 Skill 做语义匹配，返回命中的 Skill 或 None。"""
        # 仅取 name + description 构建 candidate 文本
        candidate_lines: list[str] = []
        name_to_skill: dict[str, dict[str, Any]] = {}
        for s in skills:
            name = s.get("name", "")
            if not name:
                continue
            desc = s.get("description", "") or ""
            candidate_lines.append(f"- {name} — {desc[:120]}")
            name_to_skill[name] = s

        if not name_to_skill:
            return None

        # 无 client_factory 时无法做语义匹配，静默返回
        if self._client_factory is None:
            log.debug("[skill_matcher] no client_factory, skipping match")
            return None

        prompt = MATCH_USER_PROMPT.format(
            task=task_description[:500],
            candidates="\n".join(candidate_lines),
        )

        try:
            response = await asyncio.wait_for(
                self._client_factory(prompt=prompt, system=MATCH_SYSTEM_PROMPT),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            log.info("[skill_matcher] match timed out (%.1fs), skipping", self._timeout)
            return None
        except Exception as e:
            log.warning("[skill_matcher] match failed: %s, skipping", e)
            return None

        matched_name = str(response).strip().strip("`").strip()
        if not matched_name or matched_name.lower() == "none":
            return None

        skill = name_to_skill.get(matched_name)
        if skill is None:
            # LLM 可能返回了近似名称；做一次宽松包含匹配
            for name, s in name_to_skill.items():
                if matched_name.lower() == name.lower():
                    skill = s
                    break
        if skill is None:
            log.debug("[skill_matcher] matched name '%s' not in candidates", matched_name)
            return None

        result: dict[str, Any] = {
            "name": skill.get("name", ""),
            "description": skill.get("description", ""),
        }
        if with_content:
            result["content"] = self._read_skill_content(result["name"])
        log.info("[skill_matcher] matched skill '%s' for task", result["name"])
        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _read_skill_content(self, skill_name: str) -> str:
        """读取 SKILL.md 内容（注入用）。"""
        for candidate in (
            self._skills_dir / skill_name / "SKILL.md",
            self._skills_dir / f"{skill_name}.md",
        ):
            try:
                if candidate.exists():
                    return candidate.read_text(encoding="utf-8")
            except OSError as e:
                log.debug("[skill_matcher] failed to read %s: %s", candidate, e)
        return ""
