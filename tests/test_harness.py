from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from mewcode.config import AppConfig, ProviderConfig
from mewcode.harness.config_manager import ConfigManager
from mewcode.harness.hook_manager import HookManager
from mewcode.harness.permission_manager import PermissionManager
from mewcode.harness.tools import (
    AddHookParams,
    AddHookTool,
    AddPermissionRuleParams,
    AddPermissionRuleTool,
    RemoveHookParams,
    RemoveHookTool,
    UpdateConfigParams,
    UpdateConfigTool,
)
from mewcode.hooks import HookEngine
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.permissions.audit import AuditLogger
from mewcode.permissions.rate_limit import RateLimiter
from mewcode.tools.base import Tool, ToolResult


def make_app_config() -> AppConfig:
    return AppConfig(
        providers=[
            ProviderConfig(
                name="test",
                protocol="openai",
                base_url="https://api.example.com/v1",
                model="test-model",
            )
        ]
    )


class EmptyParams(BaseModel):
    pass


class HarnessTool(Tool):
    name = "HarnessTool"
    description = "test"
    params_model = EmptyParams
    category = "harness"

    async def execute(self, params: EmptyParams) -> ToolResult:
        return ToolResult(output="ok")


def test_harness_tools_follow_permission_modes(tmp_path: Path) -> None:
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmp_path)),
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )
    tool = HarnessTool()

    assert checker.check(tool, {}).effect == "ask"
    checker.mode = PermissionMode.PLAN
    assert checker.check(tool, {}).effect == "deny"
    checker.mode = PermissionMode.BYPASS
    assert checker.check(tool, {}).effect == "allow"


@pytest.mark.asyncio
async def test_runtime_hook_tools_add_and_remove_hook() -> None:
    engine = HookEngine()
    manager = HookManager(engine)
    add_tool = AddHookTool(manager)
    remove_tool = RemoveHookTool(manager)

    added = await add_tool.execute(
        AddHookParams(
            event="post_tool_use",
            action_type="prompt",
            action_config={"message": "remember this"},
        )
    )

    assert added.is_error is False
    hooks = manager.list_hooks()
    assert len(hooks) == 1
    removed = await remove_tool.execute(RemoveHookParams(id=hooks[0]["id"]))
    assert removed.is_error is False
    assert manager.list_hooks() == []


@pytest.mark.asyncio
async def test_runtime_config_tool_changes_allowlisted_value() -> None:
    config = make_app_config()
    manager = ConfigManager()
    manager.bind_config(config)

    result = await UpdateConfigTool(manager).execute(
        UpdateConfigParams(key="critic.enabled", value="true")
    )

    assert result.is_error is False
    assert config.critic.enabled is True
    rejected = await UpdateConfigTool(manager).execute(
        UpdateConfigParams(key="providers", value="none")
    )
    assert rejected.is_error is True


def test_app_keeps_runtime_config_for_harness() -> None:
    from mewcode.app import MewCodeApp

    config = make_app_config()
    app = MewCodeApp(providers=config.providers, app_config=config)

    assert app._app_config is config


@pytest.mark.asyncio
async def test_permission_rule_tool_persists_rule(tmp_path: Path) -> None:
    manager = PermissionManager(str(tmp_path))
    tool = AddPermissionRuleTool(manager)

    result = await tool.execute(
        AddPermissionRuleParams(tool_name="Bash", pattern="git status*", effect="allow")
    )

    assert result.is_error is False
    assert manager.list_rules() == [
        {"rule": "Bash(git status*)", "effect": "allow"}
    ]


def test_audit_query_and_rate_limit_window(tmp_path: Path) -> None:
    audit = AuditLogger(str(tmp_path), session_id="session-1")
    audit.log_decision(
        tool_name="Bash",
        params_summary="git status",
        decision="allow",
        source_layer="mode",
    )
    audit.log_decision(
        tool_name="WriteFile",
        params_summary="file.py",
        decision="deny",
        source_layer="sandbox",
    )
    assert [item["tool_name"] for item in audit.query(decision="deny")] == ["WriteFile"]

    limiter = RateLimiter(default_max_per_minute=2, per_tool_limits={})
    with patch("mewcode.permissions.rate_limit.time.monotonic", side_effect=[0, 1, 2, 61]):
        assert limiter.acquire("ReadFile") is True
        assert limiter.acquire("ReadFile") is True
        assert limiter.acquire("ReadFile") is False
        assert limiter.acquire("ReadFile") is True
