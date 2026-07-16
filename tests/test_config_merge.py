from pathlib import Path

import pytest

from mewcode.config import load_config
from mewcode.validator import ConfigError


PROVIDER = """
providers:
  - name: base
    protocol: openai
    base_url: https://api.example.com/v1
    model: test-model
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_loads_harness_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        PROVIDER
        + """
compact:
  utilization_threshold: 0.7
  min_keep_messages: 8
critic:
  enabled: true
rate_limit:
  enabled: false
  default_max_per_minute: 12
  per_tool:
    Bash: 3
allow_self_modification: true
allow_self_evolution: true
evolution:
  enabled: true
  min_traces_trigger: 40
""",
    )

    config = load_config(config_path)

    assert config.compact.utilization_threshold == 0.7
    assert config.compact.min_keep_messages == 8
    assert config.critic.enabled is True
    assert config.rate_limit.enabled is False
    assert config.rate_limit.default_max_per_minute == 12
    assert config.rate_limit.per_tool == {"Bash": 3}
    assert config.allow_self_modification is True
    assert config.allow_self_evolution is True
    assert config.evolution.enabled is True
    assert config.evolution.min_traces_trigger == 40


def test_three_layers_merge_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("mewcode.config.Path.home", lambda: home)
    monkeypatch.chdir(project)

    _write(
        home / ".mewcode" / "config.yaml",
        PROVIDER
        + """
permission_mode: bypassPermissions
enable_fork: true
compact:
  utilization_threshold: 0.7
  min_keep_messages: 4
critic:
  enabled: true
rate_limit:
  per_tool:
    Bash: 5
    WriteFile: 8
mcp_servers:
  - name: shared
    command: first-command
  - name: user-only
    command: user-command
hooks:
  - event: startup
    action:
      type: prompt
      message: user
""",
    )
    _write(
        project / ".mewcode" / "config.yaml",
        """
permission_mode: default
enable_fork: false
compact:
  min_keep_messages: 6
critic:
  enabled: false
rate_limit:
  per_tool:
    Bash: 2
mcp_servers:
  - name: shared
    url: https://project.example.com/mcp
hooks:
  - event: shutdown
    action:
      type: prompt
      message: project
""",
    )
    _write(
        project / ".mewcode" / "config.local.yaml",
        """
compact:
  utilization_threshold: 0.8
rate_limit:
  enabled: false
""",
    )

    config = load_config()

    assert config.permission_mode == "default"
    assert config.enable_fork is False
    assert config.compact.utilization_threshold == 0.8
    assert config.compact.min_keep_messages == 6
    assert config.critic.enabled is False
    assert config.rate_limit.enabled is False
    assert config.rate_limit.per_tool == {"Bash": 2, "WriteFile": 8}
    assert [server.name for server in config.mcp_servers] == ["shared", "user-only"]
    assert config.mcp_servers[0].url == "https://project.example.com/mcp"
    assert [hook["event"] for hook in config.raw_hooks] == ["startup", "shutdown"]


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("compact:\n  utilization_threshold: 1.2", "compact.utilization_threshold"),
        ("critic:\n  enabled: 1", "critic.enabled"),
        ("rate_limit:\n  default_max_per_minute: 0", "rate_limit.default_max_per_minute"),
        ("rate_limit:\n  per_tool:\n    Bash: -1", "rate_limit.per_tool"),
    ],
)
def test_rejects_invalid_harness_configuration(
    tmp_path: Path, section: str, message: str
) -> None:
    config_path = tmp_path / "config.yaml"
    _write(config_path, PROVIDER + section + "\n")

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)
