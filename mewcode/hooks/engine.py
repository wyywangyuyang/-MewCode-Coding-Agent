from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from mewcode.hooks.executors import execute_action
from mewcode.hooks.conditions import parse_condition
from mewcode.hooks.models import Hook, HookContext, ToolRejectedError
from mewcode.hooks.models import Action

log = logging.getLogger(__name__)


@dataclass
class HookNotification:
    hook_id: str
    event: str
    output: str
    success: bool


class HookEngine:
    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks: list[Hook] = hooks or []
        self._prompt_messages: list[str] = []
        self._notifications: list[HookNotification] = []


    def find_matching_hooks(self, event: str, ctx: HookContext) -> list[Hook]:
        matched: list[Hook] = []
        for hook in self.hooks:
            if hook.event != event:
                continue
            if not hook.should_run():
                continue
            if hook.condition is not None and not hook.condition.evaluate(ctx):
                continue
            matched.append(hook)
        return matched


    async def run_hooks(self, event: str, ctx: HookContext) -> None:
        matched = self.find_matching_hooks(event, ctx)
        for hook in matched:
            hook.mark_executed()
            if hook.async_exec:
                asyncio.ensure_future(self._run_single(hook, ctx))
            else:
                await self._run_single(hook, ctx)


    async def _run_single(self, hook: Hook, ctx: HookContext) -> None:
        try:
            result = await execute_action(hook.action, ctx)
            if hook.action.type == "prompt" and result.success:
                self._prompt_messages.append(result.output)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event=hook.event,
                    output=result.output,
                    success=result.success,
                )
            )
            if not result.success:
                log.warning(
                    "Hook '%s' action failed: %s", hook.id, result.output
                )
        except Exception as e:
            log.warning("Hook '%s' execution error: %s", hook.id, e)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event=hook.event,
                    output=str(e),
                    success=False,
                )
            )


    async def run_pre_tool_hooks(
        self, ctx: HookContext
    ) -> ToolRejectedError | None:
        matched = self.find_matching_hooks("pre_tool_use", ctx)
        for hook in matched:
            hook.mark_executed()
            try:
                result = await execute_action(hook.action, ctx)
                self._notifications.append(
                    HookNotification(
                        hook_id=hook.id,
                        event="pre_tool_use",
                        output=result.output,
                        success=result.success,
                    )
                )
                if hook.reject:
                    return ToolRejectedError(
                        tool=ctx.tool_name,
                        reason=result.output,
                        hook_id=hook.id,
                    )
            except Exception as e:
                log.warning("Hook '%s' execution error: %s", hook.id, e)
        return None

    def get_prompt_messages(self) -> list[str]:
        messages = list(self._prompt_messages)
        self._prompt_messages.clear()
        return messages


    def drain_notifications(self) -> list[HookNotification]:
        notifications = list(self._notifications)
        self._notifications.clear()
        return notifications

    def register_runtime_hook(
        self,
        *,
        hook_id: str,
        event: str,
        action_type: str,
        action_config: dict,
        condition: str = "",
        once: bool = False,
    ) -> None:
        action = Action(type=action_type, **action_config)
        parsed_condition = parse_condition(condition) if condition else None
        self.hooks.append(
            Hook(
                id=hook_id,
                event=event,
                action=action,
                condition=parsed_condition,
                once=once,
            )
        )

    def unregister_runtime_hook(self, hook_id: str) -> bool:
        for index, hook in enumerate(self.hooks):
            if hook.id == hook_id:
                del self.hooks[index]
                return True
        return False

    def list_runtime_hooks(self) -> list[dict]:
        return [
            {
                "id": hook.id,
                "event": hook.event,
                "action_type": hook.action.type,
                "once": hook.once,
                "executed": hook.executed,
            }
            for hook in self.hooks
        ]
