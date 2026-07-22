"""执行轨迹存储与收集。

ExecutionTraceStore: 按日分片的 JSONL 持久化存储。
TraceCollector: 在会话中被动收集执行轨迹，会话结束时写入。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from mewcode.harness.evolution.models import ExecutionTrace

log = logging.getLogger(__name__)

MAX_TRACE_FILE_AGE_DAYS = 90


class ExecutionTraceStore:
    """执行轨迹的持久化存储。

    按日分片存储为 JSONL 文件，自动清理超过 90 天的旧文件。
    """

    def __init__(self, traces_dir: Path) -> None:
        self._traces_dir = Path(traces_dir)
        self._traces_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def append(self, trace: ExecutionTrace) -> None:
        """追加一条执行轨迹。"""
        date_str = time.strftime("%Y%m%d", time.localtime(trace.timestamp))
        file_path = self._traces_dir / f"{date_str}.jsonl"
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            log.error("[trace_store] failed to append trace %s: %s", trace.trace_id, e)

    def append_batch(self, traces: list[ExecutionTrace]) -> None:
        """批量追加执行轨迹。"""
        for trace in traces:
            self.append(trace)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_latest(self, n: int = 50) -> list[ExecutionTrace]:
        """读取最近 N 条轨迹。

        从最新的日期文件开始向前读取，直到收集 N 条或读完所有文件。
        """
        collected: list[ExecutionTrace] = []
        date_files = sorted(self._list_date_files(), reverse=True)

        for fp in date_files:
            if len(collected) >= n:
                break
            # 从文件末尾向前读
            traces = self._read_file_reverse(fp, n - len(collected))
            collected.extend(traces)

        # 按时间戳降序排序
        collected.sort(key=lambda t: t.timestamp, reverse=True)
        return collected[:n]

    def get_failures(self, n: int = 50) -> list[ExecutionTrace]:
        """读取最近 N 条失败记录。"""
        all_traces = self.get_latest(n * 2)  # 多读一些以防成功记录过多
        failures = [t for t in all_traces if not t.success]
        return failures[:n]

    def count(self) -> int:
        """总轨迹数（统计所有文件行数）。"""
        total = 0
        for fp in self._list_date_files():
            total += self._count_lines(fp)
        return total

    def count_since(self, timestamp: float) -> int:
        """自某时间点以来的轨迹数量。"""
        count = 0
        for fp in self._list_date_files():
            for trace in self._read_file_forward(fp):
                if trace.timestamp >= timestamp:
                    count += 1
        return count

    def load_all(self) -> list[ExecutionTrace]:
        """加载所有轨迹（注意：可能非常大）。"""
        all_traces: list[ExecutionTrace] = []
        for fp in sorted(self._list_date_files()):
            all_traces.extend(self._read_file_forward(fp))
        return all_traces

    def get_by_ids(self, trace_ids: list[str]) -> list[ExecutionTrace]:
        """按 trace_id 批量获取。"""
        id_set = set(trace_ids)
        results: list[ExecutionTrace] = []
        for fp in self._list_date_files():
            for trace in self._read_file_forward(fp):
                if trace.trace_id in id_set:
                    results.append(trace)
        return results

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------

    def cleanup(self, max_age_days: int = MAX_TRACE_FILE_AGE_DAYS) -> int:
        """清理超过指定天数的旧文件。"""
        cutoff_date = time.strftime(
            "%Y%m%d",
            time.localtime(time.time() - max_age_days * 86400),
        )
        removed = 0
        for fp in self._list_date_files():
            if fp.stem < cutoff_date:
                try:
                    fp.unlink()
                    removed += 1
                except OSError:
                    pass
        if removed:
            log.info("[trace_store] cleaned up %d old trace files", removed)
        return removed

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _list_date_files(self) -> list[Path]:
        """列出所有 JSONL 日期文件。"""
        if not self._traces_dir.is_dir():
            return []
        files = sorted(self._traces_dir.glob("*.jsonl"))
        return [f for f in files if f.stem.isdigit() and len(f.stem) == 8]

    @staticmethod
    def _read_file_forward(file_path: Path) -> list[ExecutionTrace]:
        """从文件头顺序读取所有轨迹。"""
        traces: list[ExecutionTrace] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        traces.append(ExecutionTrace.from_dict(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            pass
        return traces

    @staticmethod
    def _read_file_reverse(file_path: Path, limit: int) -> list[ExecutionTrace]:
        """从文件尾反向读取最多 limit 条轨迹。"""
        traces: list[ExecutionTrace] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return traces

        for line in reversed(lines):
            if len(traces) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                traces.append(ExecutionTrace.from_dict(data))
            except (json.JSONDecodeError, TypeError):
                continue
        return traces

    @staticmethod
    def _count_lines(file_path: Path) -> int:
        try:
            with open(file_path, encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0


# ---------------------------------------------------------------------------
# TraceCollector — 会话中被动收集
# ---------------------------------------------------------------------------


class TraceCollector:
    """在 Agent 执行过程中被动收集执行轨迹。

    用法：
        collector = TraceCollector(trace_store)
        trace_id = collector.start_task("implement login feature")
        # ... agent executes ...
        collector.record_tool_use(trace_id, "Bash")
        collector.record_skill_use(trace_id, "my-skill")
        collector.end_task(trace_id, success=True)
        collector.flush()  # 会话结束时调用
    """

    def __init__(self, trace_store: ExecutionTraceStore) -> None:
        self._store = trace_store
        self._active: dict[str, ExecutionTrace] = {}
        self._completed_count: int = 0

    def start_task(
        self,
        description: str,
        session_id: str = "",
        start_time: float | None = None,
    ) -> str:
        """开始记录一个任务。

        Args:
            description: 任务描述。
            session_id: 所属会话 ID。
            start_time: 开始时间戳（默认当前时间）。

        Returns:
            trace_id 用于后续记录。
        """
        trace = ExecutionTrace(
            session_id=session_id,
            timestamp=start_time or time.time(),
            task_description=description[:200],
        )
        self._active[trace.trace_id] = trace
        return trace.trace_id

    def end_task(
        self,
        trace_id: str,
        success: bool = True,
        error_info: dict[str, Any] | None = None,
    ) -> None:
        """结束任务记录。

        Args:
            trace_id: start_task 返回的 trace_id。
            success: 任务是否成功。
            error_info: 失败时的错误信息。
        """
        trace = self._active.get(trace_id)
        if trace is None:
            log.warning("[trace_collector] unknown trace_id: %s", trace_id)
            return

        trace.success = success
        trace.error_info = error_info
        trace.execution_time_ms = (time.time() - trace.timestamp) * 1000

        self._store.append(trace)
        del self._active[trace_id]
        self._completed_count += 1

    def record_tool_use(self, trace_id: str, tool_name: str) -> None:
        """记录一次工具使用。

        tools_used 保留去重后的工具名清单；tool_call_count 累计总调用次数
        （供成功经验路径判定任务复杂度）。
        """
        trace = self._active.get(trace_id)
        if not trace:
            return
        trace.tool_call_count += 1
        if tool_name not in trace.tools_used:
            trace.tools_used.append(tool_name)

    def record_iteration(self, trace_id: str) -> None:
        """记录一次 Agent 主循环迭代（供成功经验路径判定复杂度）。"""
        trace = self._active.get(trace_id)
        if trace:
            trace.iteration_count += 1

    def record_retry(self, trace_id: str) -> None:
        """标记本轮任务发生过重试/绕路（信息性，不影响复杂度判定）。"""
        trace = self._active.get(trace_id)
        if trace:
            trace.had_retries = True

    def record_skill_use(self, trace_id: str, skill_name: str) -> None:
        """记录一次 Skill 使用。"""
        trace = self._active.get(trace_id)
        if trace and skill_name not in trace.skills_used:
            trace.skills_used.append(skill_name)

    def record_file_modify(self, trace_id: str, file_path: str) -> None:
        """记录一次文件修改。"""
        trace = self._active.get(trace_id)
        if trace and file_path not in trace.files_modified:
            trace.files_modified.append(file_path)

    def record_tokens(self, trace_id: str, input_tokens: int, output_tokens: int) -> None:
        """记录 Token 使用量。"""
        trace = self._active.get(trace_id)
        if trace:
            trace.tokens_input += input_tokens
            trace.tokens_output += output_tokens

    def flush(self) -> int:
        """将所有进行中的 trace 强制写入磁盘。

        Returns:
            已完成的 trace 总数。
        """
        for trace in list(self._active.values()):
            trace.execution_time_ms = (time.time() - trace.timestamp) * 1000
            trace.success = False
            if trace.error_info is None:
                trace.error_info = {"error_type": "interrupted", "message": "Session ended without explicit completion"}
            self._store.append(trace)
        count = len(self._active)
        self._active.clear()
        self._completed_count += count
        return self._completed_count

    @property
    def completed_count(self) -> int:
        return self._completed_count

    @property
    def active_count(self) -> int:
        return len(self._active)
