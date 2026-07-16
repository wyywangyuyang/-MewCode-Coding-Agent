"""运行时配置管理器。

允许 Agent 在运行时读取和修改配置项。
部分修改立即生效（如权限模式），部分需下一轮生效（如模型切换）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# 允许 Agent 运行时修改的配置项白名单
ALLOWED_CONFIG_KEYS = {
    "permission_mode",
    "compact.utilization_threshold",
    "compact.min_keep_messages",
    "critic.enabled",
    "rate_limit.enabled",
    "rate_limit.default_max_per_minute",
    "max_iterations",
}


@dataclass
class ConfigChange:
    """一次配置变更记录。"""

    key: str
    old_value: Any
    new_value: Any
    immediate: bool  # 是否立即生效


class ConfigManager:
    """运行时配置读写。"""

    def __init__(self, app_config: Any = None) -> None:
        self._config = app_config
        self._changes: list[ConfigChange] = []

    def bind_config(self, app_config: Any) -> None:
        """注入配置引用。"""
        self._config = app_config

    def get_config(self, key: str) -> Any | None:
        """读取配置项。"""
        if self._config is None:
            return None

        # 支持点号分隔的嵌套键
        parts = key.split(".")
        value: Any = self._config
        for part in parts:
            if hasattr(value, part):
                value = getattr(value, part)
            elif isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    def set_config(self, key: str, value: Any) -> tuple[bool, str]:
        """修改配置项。

        Args:
            key: 配置项键名（支持点号分隔）。
            value: 新值。

        Returns:
            (成功标志, 消息)
        """
        if key not in ALLOWED_CONFIG_KEYS:
            return False, (
                f"Configuration key '{key}' is not allowed for runtime modification. "
                f"Allowed keys: {', '.join(sorted(ALLOWED_CONFIG_KEYS))}"
            )

        if self._config is None:
            return False, "Config not initialized"

        old_value = self.get_config(key)

        # 立即生效的键
        immediate_keys = {"permission_mode", "critic.enabled",
                          "rate_limit.enabled", "max_iterations"}

        try:
            parts = key.split(".")
            target: Any = self._config
            for part in parts[:-1]:
                if hasattr(target, part):
                    target = getattr(target, part)
                else:
                    return False, f"Cannot navigate to '{part}' in config"

            last = parts[-1]
            if hasattr(target, last):
                setattr(target, last, value)
            else:
                return False, f"Config has no attribute '{last}'"

        except Exception as e:
            return False, f"Failed to set config: {e}"

        immediate = key in immediate_keys
        change = ConfigChange(
            key=key,
            old_value=old_value,
            new_value=value,
            immediate=immediate,
        )
        self._changes.append(change)

        log.info(
            "[harness] config changed: %s = %s (was %s, immediate=%s)",
            key, value, old_value, immediate,
        )

        return True, (
            f"Configuration '{key}' updated: {old_value} → {value}"
            + (" (effective immediately)" if immediate else " (effective next turn)")
        )

    def get_allowed_keys(self) -> set[str]:
        """返回允许修改的配置项列表。"""
        return set(ALLOWED_CONFIG_KEYS)

    def get_changes(self) -> list[ConfigChange]:
        """返回所有变更记录。"""
        return list(self._changes)
