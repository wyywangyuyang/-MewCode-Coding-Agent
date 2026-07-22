"""成功经验 Skill 语义匹配器 —— 单元测试。

覆盖：候选/正式匹配范围、超时静默、Agent 二次校验拒绝路径。
对应 checklist「语义匹配与注入」节。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mewcode.harness.evolution.skill_matcher import SkillMatcher
from mewcode.harness.evolution.skill_meta import SkillMetaManager


# ---------------------------------------------------------------------------
# Mock 客户端
# ---------------------------------------------------------------------------


async def echo_client_factory(name: str | None = "none", delay: float = 0.0):
    async def client(prompt: str, system: str = "") -> str:
        if delay:
            await asyncio.sleep(delay)
        return name

    return client


def _seed_meta(tmp_path: Path):
    meta = SkillMetaManager(tmp_path / "skill_meta.json")
    meta.add_skill("auto-success-cand-a", description="refactor module A", status="candidate", path="success")
    meta.add_skill("auto-success-active-b", description="migrate database B", status="active", path="success")
    meta.add_skill("auto-success-active-c", description="refactor module C", status="active", path="success")
    meta.add_skill("auto-fix-failure", description="fix import errors", status="active", path="failure")
    return meta


# ---------------------------------------------------------------------------
# 匹配范围
# ---------------------------------------------------------------------------


class TestMatchRange:
    @pytest.mark.asyncio
    async def test_match_active_only_scans_active_success_skills(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        m = SkillMatcher(client_factory=await echo_client_factory("auto-success-active-c"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        result = await m.match_active("refactor module C task")
        assert result is not None
        assert result["name"] == "auto-success-active-c"

    @pytest.mark.asyncio
    async def test_match_active_excludes_candidates(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        # 即便 client 返回候选名称，match_active 也不应返回候选
        m = SkillMatcher(client_factory=await echo_client_factory("auto-success-cand-a"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        result = await m.match_active("refactor module A")
        # 候选不在 active 列表中，名称无法匹配 → None
        assert result is None

    @pytest.mark.asyncio
    async def test_match_active_excludes_failure_path(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        m = SkillMatcher(client_factory=await echo_client_factory("auto-fix-failure"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        result = await m.match_active("fix import errors")
        assert result is None  # failure 路径 skill 不参与注入

    @pytest.mark.asyncio
    async def test_match_candidates_only_scans_candidates(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        m = SkillMatcher(client_factory=await echo_client_factory("auto-success-cand-a"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        result = await m.match_candidates("refactor module A")
        assert result is not None
        assert result["name"] == "auto-success-cand-a"

    @pytest.mark.asyncio
    async def test_no_skills_returns_none(self, tmp_path: Path):
        meta = SkillMetaManager(tmp_path / "skill_meta.json")
        m = SkillMatcher(client_factory=await echo_client_factory("auto-success-x"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        assert await m.match_active("any task") is None
        assert await m.match_candidates("any task") is None


# ---------------------------------------------------------------------------
# 超时静默
# ---------------------------------------------------------------------------


class TestMatcherTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_none_silently(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        # 客户端 sleep 2s，匹配器超时 0.1s
        m = SkillMatcher(
            client_factory=await echo_client_factory("auto-success-active-c", delay=2.0),
            skill_meta_manager=meta,
            skills_dir=tmp_path,
            timeout=0.1,
        )
        result = await m.match_active("refactor module C")
        assert result is None  # 超时静默跳过，不抛异常

    @pytest.mark.asyncio
    async def test_exception_returns_none_silently(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)

        async def bad_client(prompt: str, system: str = "") -> str:
            raise RuntimeError("LLM down")

        m = SkillMatcher(client_factory=bad_client, skill_meta_manager=meta, skills_dir=tmp_path)
        result = await m.match_active("any task")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_client_factory_returns_none(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        m = SkillMatcher(client_factory=None, skill_meta_manager=meta, skills_dir=tmp_path)
        assert await m.match_active("any task") is None


# ---------------------------------------------------------------------------
# 注入内容
# ---------------------------------------------------------------------------


class TestInjectionContent:
    @pytest.mark.asyncio
    async def test_matched_active_skill_returns_content(self, tmp_path: Path):
        meta = SkillMetaManager(tmp_path / "skill_meta.json")
        meta.add_skill("auto-success-x", description="some task", status="active", path="success")
        # 写入 SKILL.md 文件
        skill_dir = tmp_path / "auto-success-x"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# skill content here", encoding="utf-8")

        m = SkillMatcher(client_factory=await echo_client_factory("auto-success-x"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        result = await m.match_active("some task")
        assert result is not None
        assert result["content"] == "# skill content here"

    @pytest.mark.asyncio
    async def test_none_response_returns_none(self, tmp_path: Path):
        meta = _seed_meta(tmp_path)
        m = SkillMatcher(client_factory=await echo_client_factory("none"),
                         skill_meta_manager=meta, skills_dir=tmp_path)
        assert await m.match_active("unrelated task") is None
