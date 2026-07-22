"""成功经验 Skill 生成器。

将 SuccessSignal 总结为指南型 SKILL.md，供 Agent 下次遇到同类任务时
读取并按指引弹性执行。复用失败路径的质量护栏（证据不足 / 捏造内容校验），
禁止从单次成功轨迹中臆造步骤。

与失败路径的 SkillGenerator 区别：
- 语义为「总结为何成功、可复用的经验」，而非「补救失败」
- 产物为指南型 Skill（强调弹性适配），初始状态为 candidate
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from mewcode.harness.evolution.models import (
    ExecutionTrace,
    SkillGenResult,
    SuccessSignal,
)
from mewcode.harness.evolution.skill_generator import (
    FabricatedContentError,
    InsufficientEvidenceError,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 成功经验生成 Prompt
# ---------------------------------------------------------------------------

SUCCESS_GENERATION_SYSTEM_PROMPT = """\
你正在为一个 AI 编程助手生成指南型 SKILL.md 文件，总结一次复杂任务的成功经验。

**完全基于**下面提供的成功执行证据来编写。禁止想象证据中没有的步骤或决策。
每条经验都必须能追溯到下面提供的具体证据。产物是「指南」而非「脚本」——
Agent 读取后应能弹性适配同类但有差异的任务。

## 输出格式

以 YAML frontmatter 开头：

---
name: auto-success-{经验简称}
description: Reusable experience distilled from a successful complex task — {任务简述}
mode: inline
allowedTools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
---

## 正文格式

# {Skill 标题}

## When to Apply

明确列出适用场景（从证据中提取的任务特征）

## Proven Approach

从证据中总结的关键步骤、工具选择、决策点（每步可追溯到证据）

## Pitfalls Avoided

本次执行中绕过的弯路 / 踩过的坑（若有，基于证据）

## Evidence References

列出本 Skill 所基于的证据 trace ID

## Constraints

- 必须根据实际任务差异弹性调整，不要机械重放
- 关键决策点需验证后再执行
"""

SUCCESS_GENERATION_USER_PROMPT = """\
--- 成功证据 ---
任务描述: {task_description}
迭代轮数: {iteration_count}
工具调用数: {tool_call_count}
Token 消耗: {token_total}
是否含重试/绕路: {had_retries}

关键步骤摘要:
{key_steps}

详细证据（关联 trace 的具体信息）:

{evidence_details}

--- 上下文 ---
这些证据来自 AI 编程助手一次复杂任务的成功执行轨迹。请生成一个完整的指南型 SKILL.md，
帮助 AI 助手在未来遇到同类任务时复用这次的成功经验。

要求：
1. name 必须以 "auto-success-" 开头，必须是小写字母、数字、连字符
2. 正文中的每个步骤都必须能追溯到上面的证据
3. 如果证据中没有显示某个做法有效，不要包含它
4. 不要包含通用的"也可以尝试"之类的建议
5. 强调弹性适配——同类任务可能有差异

生成完整的 SKILL.md 内容（frontmatter + body）：
"""


class SuccessSkillGenerator:
    """从 SuccessSignal 生成指南型 SKILL.md。

    关键安全约束：
    1. 必须有可引用的真实证据（key_steps 非空 + 关联 trace）
    2. LLM prompt 明确禁止编造
    3. 生成后做格式与证据引用校验
    4. 原子写入（temp + os.replace）
    """

    KNOWN_TOOLS = {
        "Bash", "Read", "Write", "Edit", "Glob", "Grep",
        "Agent", "TaskCreate", "TaskUpdate", "TaskList",
        "Skill", "WebFetch", "WebSearch", "AskUserQuestion",
        "EnterPlanMode", "ExitPlanMode",
    }

    def __init__(
        self,
        client_factory: Any = None,
        skills_dir: Path | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._skills_dir = Path(skills_dir) if skills_dir else Path("harness/skills")

    async def generate(
        self,
        signal: SuccessSignal,
        trace: ExecutionTrace | None = None,
    ) -> SkillGenResult:
        """从成功信号生成指南型 SKILL.md。

        Args:
            signal: 成功信号。
            trace: 关联的详细执行轨迹（提供额外证据）。

        Returns:
            SkillGenResult。

        Raises:
            InsufficientEvidenceError: 证据不足。
            FabricatedContentError: 生成内容可能含编造。
        """
        # Step 1: 验证证据充分性
        if not signal.key_steps or len(signal.key_steps) < 2:
            raise InsufficientEvidenceError(
                f"Only {len(signal.key_steps)} key steps, need >= 2"
            )

        # Step 2: 生成 Skill 名称
        skill_name = self._derive_skill_name(signal)

        # Step 3: 构建证据文本
        evidence_text = self._build_evidence_text(signal, trace)

        # Step 4: 调用 LLM 生成
        if self._client_factory is not None:
            try:
                content = await self._generate_with_llm(signal, evidence_text)
            except Exception as e:
                log.warning("[success_gen] LLM generation failed: %s, using template", e)
                content = self._generate_template(signal, evidence_text)
        else:
            content = self._generate_template(signal, evidence_text)

        # Step 5: 校验
        errors = self._validate(content, signal, trace)
        if errors:
            return SkillGenResult(
                skill_name=skill_name,
                content=content,
                based_on_traces=[signal.trace_id] if signal.trace_id else [],
                success=False,
                errors=errors,
            )

        # Step 6: 写入磁盘
        skill_path = self._write_skill(skill_name, content)

        return SkillGenResult(
            skill_name=skill_name,
            skill_path=str(skill_path),
            content=content,
            based_on_traces=[signal.trace_id] if signal.trace_id else [],
            success=True,
            errors=[],
            evidence_quoted=self._extract_evidence_refs(content),
        )

    # ------------------------------------------------------------------
    # LLM 生成
    # ------------------------------------------------------------------

    async def _generate_with_llm(
        self,
        signal: SuccessSignal,
        evidence_text: str,
    ) -> str:
        prompt = SUCCESS_GENERATION_USER_PROMPT.format(
            task_description=signal.task_description[:200],
            iteration_count=signal.iteration_count,
            tool_call_count=signal.tool_call_count,
            token_total=signal.token_total,
            had_retries=signal.had_retries,
            key_steps="\n".join(f"- {s}" for s in signal.key_steps),
            evidence_details=evidence_text,
        )

        response = await self._client_factory(
            prompt=prompt,
            system=SUCCESS_GENERATION_SYSTEM_PROMPT,
        )
        return str(response).strip()

    # ------------------------------------------------------------------
    # 模板生成（降级方案）
    # ------------------------------------------------------------------

    def _generate_template(
        self,
        signal: SuccessSignal,
        evidence_text: str,
    ) -> str:
        skill_name = self._derive_skill_name(signal)
        description = (
            f"Reusable experience distilled from a successful complex task — "
            f"{signal.task_description[:80]}"
        )
        steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(signal.key_steps[:8]))
        tools = ", ".join(signal.tools_used) if signal.tools_used else "N/A"

        return f"""---
name: {skill_name}
description: {description}
mode: inline
allowedTools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
---

# {skill_name}

## When to Apply

Use this skill when handling a task similar to: **{signal.task_description[:200]}**

Triggered after a confirmed complex success (iterations={signal.iteration_count},
tool_calls={signal.tool_call_count}).

## Proven Approach

{steps}

Tools that proved effective: {tools}

## Pitfalls Avoided

{"- This task involved retries/detours before succeeding; re-validate key decisions." if signal.had_retries else "- No significant detours recorded."}

## Evidence References

Based on trace: {signal.trace_id or 'unknown'}

## Constraints

- Adapt steps to the actual task differences; do not replay mechanically.
- Verify key decisions before executing.
- If the task diverges significantly, fall back to the normal workflow.
"""

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def _validate(
        self,
        content: str,
        signal: SuccessSignal,
        trace: ExecutionTrace | None,
    ) -> list[str]:
        """校验生成的 SKILL.md 内容。"""
        errors: list[str] = []

        if not content.startswith("---"):
            errors.append("Missing YAML frontmatter")
            return errors

        end = content.find("---", 3)
        if end == -1:
            errors.append("Unclosed YAML frontmatter")
            return errors

        fm_block = content[3:end]
        body = content[end + 3:]

        # name 格式校验
        name_match = re.search(r'^name:\s*(\S+)', fm_block, re.MULTILINE)
        if not name_match:
            errors.append("Missing 'name' in frontmatter")
        else:
            name = name_match.group(1)
            if not re.match(r'^[a-z][a-z0-9\-]*$', name):
                errors.append(f"Invalid skill name: {name}")
            if not name.startswith("auto-"):
                errors.append(f"Skill name must start with 'auto-': {name}")

        # 内容不能为空
        if len(body.strip()) < 50:
            errors.append("Skill body too short")

        # 反编造 gate：必须引用证据（trace_id 或文件路径）
        evidence_refs = self._extract_evidence_refs(content)
        if not evidence_refs:
            referenced = self._check_content_references_evidence(body, signal, trace)
            if not referenced:
                errors.append(
                    "Generated content does not reference any evidence from the success trace. "
                    "This skill may contain fabricated logic."
                )

        return errors

    def _check_content_references_evidence(
        self,
        content: str,
        signal: SuccessSignal,
        trace: ExecutionTrace | None,
    ) -> bool:
        """检查生成内容是否引用了证据中的具体信息。"""
        if signal.trace_id and signal.trace_id in content:
            return True
        for fp in signal.files_modified:
            if fp and fp in content:
                return True
        if trace is not None:
            for fp in trace.files_modified:
                if fp and fp in content:
                    return True
        # 检查任务描述的关键片段
        if signal.task_description and len(signal.task_description) > 20:
            key_phrase = signal.task_description[10:50]
            if key_phrase and key_phrase in content:
                return True
        return False

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_skill_name(signal: SuccessSignal) -> str:
        """从任务描述推导 Skill 名称。"""
        base = signal.task_description or "complex-task"
        slug = re.sub(r'[^a-z0-9-]', '-', base.lower())[:30].strip("-")
        slug = re.sub(r'-+', '-', slug)
        return f"auto-success-{slug or 'task'}"

    @staticmethod
    def _build_evidence_text(
        signal: SuccessSignal,
        trace: ExecutionTrace | None,
    ) -> str:
        """构建证据文本。"""
        parts: list[str] = [
            f"### Success Signal ({signal.trace_id or 'n/a'})",
            f"- Task: {signal.task_description[:200]}",
            f"- Iterations: {signal.iteration_count}",
            f"- Tool calls: {signal.tool_call_count}",
            f"- Tokens: {signal.token_total}",
            f"- Had retries: {signal.had_retries}",
            f"- Tools used: {', '.join(signal.tools_used)}",
        ]
        if signal.files_modified:
            parts.append(f"- Files modified: {', '.join(signal.files_modified)}")
        parts.append("- Key steps:")
        for s in signal.key_steps:
            parts.append(f"  - {s}")

        if trace is not None:
            parts.append(f"- Session: {trace.session_id}")
            parts.append(f"- Execution time (ms): {trace.execution_time_ms:.0f}")

        return "\n".join(parts)

    def _write_skill(self, skill_name: str, content: str) -> Path:
        """写入 SKILL.md 到磁盘（原子写入）。"""
        skill_dir = self._skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"

        tmp_path = Path(str(skill_path) + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(str(tmp_path), str(skill_path))

        log.info("[success_gen] wrote %s", skill_path)
        return skill_path

    @staticmethod
    def _extract_evidence_refs(content: str) -> list[str]:
        """从生成内容中提取证据引用（trace_id）。"""
        refs: list[str] = []
        for match in re.finditer(r'trace_[a-f0-9]{12}', content):
            refs.append(match.group(0))
        return refs
