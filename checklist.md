# MewCode Loop Engineering + Harness Engineering 验收清单

> 每一项必须可勾选、可观测。检查方法写在括号里。

---

## Workflow 编排引擎

### 数据模型 & Journal
- [ ] `WorkflowDef` 包含字段：name, description, phases, entry_function（`grep -c "class WorkflowDef" mewcode/workflow/models.py` 返回 1）
- [ ] `AgentCallRecord` 包含 call_id (uuid)、prompt_sha256、opts_sha256、status (running/completed/failed)、result_json、started_at、completed_at（`grep -c "prompt_sha256" mewcode/workflow/models.py` 返回 1）
- [ ] Journal 文件写入路径为 `.mewcode/workflows/journals/{workflow_name}/{run_id}.jsonl`（执行任意 workflow 后 `ls .mewcode/workflows/journals/` 可见对应目录和文件）
- [ ] Journal 写入是追加模式——`open(file, "a")` 而非 `"w"`（`grep "mode.*a" mewcode/workflow/journal.py` 返回 ≥1）
- [ ] `lookup(prompt_sha256, opts_sha256)` 在无匹配时返回 None（单元测试断言 `assert journal.lookup("nonexistent", "nonexistent") is None`）
- [ ] `lookup()` 在找到已完成的记录时返回对应的 AgentCallRecord 且 result_json 非空（单元测试断言 `assert result.result_json is not None`）
- [ ] `lookup()` 在找到 running 状态的记录时返回 None（视为未完成）
- [ ] `prune(max_bytes=10485760)` 后 journal 文件 ≤ 10MB（`du -b journal.jsonl` 返回 ≤ 10485760）
- [ ] `prune()` 不删除 status=running 的记录（单元测试：有 100 条已完成的 + 1 条 running，prune 到 1KB，running 记录仍在）
- [ ] Journal 支持 flush() 强制刷盘（`grep "\.flush()" mewcode/workflow/journal.py` 返回 ≥1）

### WorkflowContext 原语
- [ ] `ctx.agent("test prompt")` 返回字符串类型的结果（单元测试 `assert isinstance(result, str)`）
- [ ] `ctx.agent("test", schema=MyModel)` 返回 MyModel 类型的 Pydantic 实例（单元测试 `assert isinstance(result, MyModel)`）
- [ ] `ctx.agent()` 不传 schema 时，prompt 中包含 "return your final answer as plain text" 类指令（检查 agent 调用的 system prompt 内容）
- [ ] `ctx.agent()` 传 schema 时，system prompt 包含 "You MUST respond with a valid JSON object matching this schema" + JSON Schema 文本（检查 agent 调用的 system prompt 内容）
- [ ] schema 校验失败时自动重试，最多 3 次（日志中可见 "[structured output] validation failed, retry 1/3"）
- [ ] 3 次重试后仍失败则抛出 `StructuredOutputError`（单元测试用永远返回错误格式的 mock client，断言抛出）
- [ ] `ctx.pipeline(items, stage1, stage2)` 对 3 个 items × 2 个 stages 产生 6 次 agent 调用（检查 journal 记录数 = 6）
- [ ] pipeline 中 item A 进入 stage2 时 item B 仍在 stage1——两阶段可同时运行（通过日志时间戳验证：stage2 某 item 的 start 时间 < stage1 另一 item 的 end 时间）
- [ ] pipeline 中某个 stage 抛出异常 → 对应 item 结果为 None，不影响其他 items（3 items 中第 2 个 item 的 stage1 抛异常 → 结果数组为 `[R1, None, R3]`）
- [ ] `ctx.parallel([lambda1, lambda2, lambda3])` 并发执行 3 个任务，全部完成后返回（通过日志时间戳验证：3 个任务的 end 时间接近，而非串行累加）
- [ ] parallel 中单个 thunk 抛异常 → 对应槽位为 None，不取消其他任务（`asyncio.TaskGroup` 中某 task 异常 → 其他 task 继续）
- [ ] `ctx.phase("Review")` 后发起的 agent() 调用，在 journal 中记录的 phase 字段为 "Review"（`grep '"phase": "Review"' journal.jsonl` 返回 ≥1）
- [ ] `ctx.log("processing item 3/10")` 触发进度回调（TUI 中可见该文本输出）
- [ ] 并发上限为 `min(16, os.cpu_count() - 2)`（日志中可见 `[workflow] concurrency cap: N`）
- [ ] 超过并发上限的 agent() 调用排队等待而非失败（提交 20 个并发 agent 调用，全部成功完成）

### Token Budget
- [ ] `ctx.budget.total` 在未设置时为 None（表示无限预算）（单元测试 `assert ctx.budget.total is None`）
- [ ] `ctx.budget.total` 在设置后返回设置值（`ctx.budget.total = 100000; assert ctx.budget.total == 100000`）
- [ ] `ctx.budget.spent()` 初始为 0（单元测试 `assert ctx.budget.spent() == 0`）
- [ ] 每次 agent() 调用完成后 `spent()` 增加对应 token 数（调用 agent → 检查 spent() 增量 ≈ 该次调用的 input_tokens + output_tokens）
- [ ] `ctx.budget.remaining()` = max(0, total - spent())（设置 total=100 → spent=30 → remaining=70 → spent=120 → remaining=0）
- [ ] 当 `remaining() == 0` 时，后续 `ctx.agent()` 调用抛出 `BudgetExhaustedError`（单元测试断言异常类型）
- [ ] `BudgetExhaustedError` 的错误消息包含已消耗 token 数和预算上限（如 "Budget exhausted: spent 100,000/100,000 tokens"）

### Loop Patterns
- [ ] `loop_until_count(target=3, fn)` 当 fn 每次返回 1 个结果时，执行恰好 3 轮（检查 journal 记录数 = 3）
- [ ] `loop_until_count(target=5, fn)` 当 fn 返回空列表时，连续 3 轮空结果后停止（dry 保护，检查 journal 记录数 = 3）
- [ ] `loop_until_budget(fn)` 当 budget 剩余 50000 token 时，每轮消耗约 20000 → 执行 2 轮后因 `remaining() < 50000` 停止
- [ ] `loop_until_budget` 的默认 min_budget_per_call 为 50000（`grep "50000" mewcode/workflow/patterns.py` 返回 ≥1）
- [ ] `loop_until_dry(fn, dry_threshold=2)` 在连续 2 轮无新结果后停止（第 1 轮返回 3 条 → 第 2 轮返回 0 条 → 第 3 轮返回 0 条 → 停止，journal 记录数为 3）
- [ ] `loop_until_dry(fn, dry_threshold=1)` 在连续 1 轮无新结果后停止（第 1 轮 3 条 → 第 2 轮 0 条 → 停止）

### 断点恢复
- [ ] 执行 workflow 到第 3 次 agent() 调用 → 模拟中断（进程 kill） → 重新执行同一 workflow → 前 3 次 agent() 调用命中缓存，跳过实际 LLM 调用
- [ ] 缓存命中时 journal 不写入新记录（恢复后 journal 总行数 = 中断前行数 + 新调用的行数）
- [ ] 缓存命中时日志输出 `[resume] cache hit: agent call #N`（`grep "cache hit"` 日志返回 ≥1）
- [ ] 缓存未命中时日志输出 `[resume] cache miss: agent call #N, executing`（`grep "cache miss"` 日志返回 ≥1）
- [ ] Workflow 函数代码被修改后，重新执行 → 对于修改点之前的相同 (prompt, opts) 仍命中缓存
- [ ] Workflow 函数代码被修改后，修改点之后的 agent() 调用序列可能不同 → 原缓存失效，新调用正常执行
- [ ] 检查 journal 中某 run 是否存在未完成记录的方法：`journal.get_incomplete_runs()` 返回列表（单元测试：有 running 记录的 run_id 出现在列表中）
- [ ] `ResumeManager.resume_or_create(workflow_name)` 在无未完成 run 时返回新的 WorkflowContext（fresh start）
- [ ] `ResumeManager.resume_or_create(workflow_name)` 在有未完成 run 时加载 journal 并设置缓存映射

---

## 调度系统

### Cron 表达式
- [ ] `CronExpression.parse("0 9 * * 1-5")` 正确解析为 "每周一至周五 9:00"（单元测试验证 next_fire 返回的时间）
- [ ] `CronExpression.parse("*/5 * * * *")` 正确解析为 "每 5 分钟"（单元测试验证两次 next_fire 间隔 = 300 秒）
- [ ] `CronExpression.parse("0 */2 * * *")` 正确解析为 "每 2 小时"（单元测试验证 next_fire 的时间差 = 2 小时）
- [ ] `CronExpression.parse("30 14 28 2 *")` 为一次性触发（2 月 28 日 14:30 后 next_fire 返回 None 或 year+1）
- [ ] `CronExpression.parse("invalid")` 抛出 `CronParseError`（单元测试断言异常）
- [ ] CronExpression 支持 5 字段格式（minute hour dom month dow）（`grep -c "len.*== 5" mewcode/scheduler/cron.py` 返回 ≥1）

### 任务持久化
- [ ] `CronStore.add(task)` 将任务写入 `.mewcode/scheduled_tasks.json`（文件存在且内容有效 JSON）
- [ ] `CronStore.list()` 返回所有活跃任务（不包括已完成的非周期性任务）（单元测试：add 3 个 → list 返回 3 个 → 其中一个触发后完成 → list 返回 2 个）
- [ ] `CronStore.remove(task_id)` 从文件中移除对应任务（再次 list 不包含该任务）
- [ ] `CronStore.get_due(now)` 返回所有 `next_fire <= now` 的任务（单元测试：2 个到期 + 1 个未到期 → get_due 返回 2 个）
- [ ] 持久化文件为格式正确的 JSON 数组，每个元素包含 id/cron/prompt/recurring/durable/created_at/last_fired_at（`jq '.[0] | keys' scheduled_tasks.json` 包含全部 7 个字段）
- [ ] 文件损坏（非 JSON 格式）时，加载返回空列表并备份损坏文件为 `.scheduled_tasks.json.corrupted.{timestamp}`
- [ ] durable=false 的任务在 MewCode 进程重启后消失（重启后 list 不包含该任务）
- [ ] durable=true 的任务在重启后依然存在（重启后 list 仍包含该任务）

### 调度运行时
- [ ] SchedulerRuntime 每 60 秒检查一次到期任务（日志中 `[scheduler] checking due tasks` 间隔 ≈ 60s）
- [ ] 一次性任务触发后自动标记完成并从活跃列表移除（第二次 check 时不返回该任务）
- [ ] 周期性任务触发后更新 last_fired_at 并计算 next_fire（连续触发 2 次，每次间隔符合 cron 表达式）
- [ ] 任务触发时以系统消息注入当前 Agent 会话（对话中可见 `<system-reminder>` 包裹的 cron prompt）
- [ ] 无活跃会话时（无 Agent 在运行），调度器仅记录 "skipped: no active session" 日志（`grep "no active session"` 日志返回 ≥1）
- [ ] 调度器在 MewCode App 关闭时通过 `shutdown()` 优雅停止（无 asyncio.CancelledError 泄露）

### ScheduleWakeup
- [ ] `wakeup_scheduler.schedule(delay_seconds=60, reason="test", prompt="wake up")` 后约 60 秒触发（日志时间戳差值 60±5 秒）
- [ ] delay_seconds < 60 时被 clamp 到 60（日志中 `[wakeup] delay clamped: 30 → 60`）
- [ ] delay_seconds > 3600 时被 clamp 到 3600（日志中 `[wakeup] delay clamped: 7200 → 3600`）
- [ ] 300 秒内的 wakeup 日志包含 "cache warm" 标记（`grep "cache warm"` 日志返回 ≥1）
- [ ] 超过 300 秒的 wakeup 日志包含 "cache cold" 标记（`grep "cache cold"` 日志返回 ≥1）

### Cron Agent Tools
- [ ] `CronCreateTool` 注册在 ToolRegistry 中，category 为 "write"（`grep CronCreateTool` → category 字段为 write）
- [ ] `CronDeleteTool` 注册在 ToolRegistry 中（`grep CronDeleteTool` → 能找到工具注册代码）
- [ ] `CronListTool` 注册在 ToolRegistry 中，category 为 "read"（`grep CronListTool` → category 字段为 read）
- [ ] Agent 调用 CronCreate 创建一次性任务 → `.mewcode/scheduled_tasks.json` 中出现新条目（`jq '.[] | select(.recurring==false)'` 返回结果）

---

## Harness 增强

### 动态上下文管理
- [ ] 配置项 `compact.utilization_threshold` 默认值为 0.85（`grep "utilization_threshold.*0.85" mewcode/config.py` 返回 ≥1）
- [ ] 模型上下文窗口为 200000 token 时，compact 触发阈值为 170000（200000 × 0.85）（日志中 `[compact] threshold: 170000/200000 (85.0%)`）
- [ ] 模型上下文窗口为 128000 token 时，compact 触发阈值为 108800（128000 × 0.85）（日志中 `[compact] threshold: 108800/128000 (85.0%)`）
- [ ] `compact.min_keep_messages` 默认值为 3（`grep "min_keep_messages.*3" mewcode/config.py` 返回 ≥1）
- [ ] Compact 后保留的 tail 消息数 ≥ min_keep_messages（单元测试：历史 20 条消息 → compact → messages 列表长度 ≥ 3）
- [ ] 语义边界识别：compact 切割点落在 Message.role == "user" 的消息边界上（日志中 `[compact] split boundary at message #N (role: user)`）
- [ ] `utilization_threshold` 可在运行时通过 UpdateConfigTool 修改（调用 UpdateConfig 设置新值 → 下一轮 compact 使用新阈值）

### Completeness Critic
- [ ] 配置项 `critic.enabled` 默认值为 false（`grep "critic.*enabled.*false\|critic.*enabled.*False" mewcode/config.py` 返回 ≥1）
- [ ] 当 `critic.enabled = false` 时，Agent 无工具调用后不触发 Critic（日志中无 `[critic]` 输出）
- [ ] 当 `critic.enabled = true` 时，Agent 无工具调用后触发 Critic，日志 `[critic] checking completeness...`
- [ ] Critic 超时设置为 8 秒（`grep "timeout.*8" mewcode/context/critic.py` 返回 ≥1）
- [ ] Critic 返回 `clean` 时无后续动作（日志 `[critic] result: clean`）
- [ ] Critic 返回 `suggestions` 时，建议以 system reminder 形式注入下一轮对话（对话中出现 "Here are some suggestions to consider:" 开头的系统消息）
- [ ] 连续 3 次 Critic 返回相同建议 → 不再注入（日志中 `[critic] suppressed: same suggestion repeated 3 times`）
- [ ] Critic 使用独立的轻量模型调用（如 haiku 或 gpt-4o-mini），不影响主会话的模型选择（日志中 `[critic] model: haiku` 或 `[critic] model: gpt-4o-mini`）

### 权限审计日志
- [ ] 审计日志文件路径为 `.mewcode/audit/decisions.jsonl`（首次执行工具调用后该文件存在）
- [ ] 每条审计记录包含 8 个字段：timestamp、tool_name、params_summary、decision、source_layer、rule_id、latency_ms、session_id（`head -1 .mewcode/audit/decisions.jsonl | jq 'keys | length'` 返回 8）
- [ ] `params_summary` 截断到 200 字符（`head -1 decisions.jsonl | jq '.params_summary | length'` ≤ 200）
- [ ] `source_layer` 取值来自 safe_readonly/dangerous/sandbox/rule_engine/mode/hitl/plan_mode 之一
- [ ] `decision` 取值来自 allow/deny/ask 之一
- [ ] 执行 `ls -la` 命令（safe read-only 自动允许）→ 审计记录中 source_layer=safe_readonly, decision=allow
- [ ] 执行 `rm -rf /` 命令（dangerous 拦截）→ 审计记录中 source_layer=dangerous, decision=deny
- [ ] 用户通过 HITL 弹窗 clicking "Allow Always" → 审计记录中 source_layer=hitl, decision=allow
- [ ] `AuditLogTool` 注册在 ToolRegistry 中，category 为 "read"（Agent 可查询自身审计日志）
- [ ] 审计文件超过 50MB 时自动 rotate（旧文件命名为 `.mewcode/audit/decisions.{date}.jsonl`）
- [ ] 保留最近 10 个归档文件（超过 10 个时删除最旧的）

### 工具级速率限制
- [ ] `rate_limit.enabled` 默认值为 true（`grep "rate_limit.*enabled.*True\|rate_limit.*enabled.*true" mewcode/config.py` 返回 ≥1）
- [ ] `rate_limit.default_max_per_minute` 默认值为 30（`grep "default_max_per_minute.*30" mewcode/config.py` 返回 ≥1）
- [ ] `rate_limit.per_tool` 默认值包含 `{"bash": 10, "write_file": 20}`（`grep -A5 "per_tool" mewcode/config.py` 包含 bash 和 write_file）
- [ ] 在 1 分钟内连续调用 Bash 工具 11 次 → 第 11 次返回 Decision.deny，reason 包含 "rate limit exceeded: bash (10/10 per minute)"
- [ ] 限流在滑动窗口内计算——第 1 次调用在 0:00，第 10 次在 0:59 → 0:59 之前第 11 次被拒，1:01 后第 11 次放行
- [ ] `rate_limit.enabled = false` 时，所有工具调用不限流（Bash 调用 50 次全部通过）
- [ ] 新会话开始时速率限制计数器归零（新 session → Bash 调用了 10 次 → 切换到新 session → Bash 仍可调用 10 次）

---

## 可观测性

### 全链路 Trace
- [ ] TraceNode 新增字段：input_summary、output_summary、tool_calls_detail、token_usage（`grep -c "input_summary" mewcode/agents/trace.py` ≥1 等 4 个字段）
- [ ] `tool_calls_detail` 为列表，每项包含 name、params_summary、result_summary、success（`grep "params_summary" mewcode/agents/trace.py` ≥1）
- [ ] `params_summary` 截断到 200 字符（`grep "200" mewcode/agents/trace.py` 在 params_summary 上下文附近）
- [ ] `result_summary` 截断到 500 字符（`grep "500" mewcode/agents/trace.py` 在 result_summary 上下文附近）
- [ ] Trace 持久化到 `.mewcode/traces/{session_id}/{timestamp}_{agent_name}.json`（检查文件存在）
- [ ] 启动时自动清理 30 天前的 trace 文件（修改某 trace 文件的 mtime 为 31 天前 → 重启 MewCode → 该文件被删除）
- [ ] `trace_manager.replay_context(trace_node)` 返回可传给 Agent 的等价上下文（单元测试断言返回的 prompt 包含 trace_node.input_summary 的关键文本）

### 性能指标
- [ ] `MetricsCollector` 在会话结束时输出统计数据到 `.mewcode/metrics/{session_id}.json`
- [ ] 统计包含：total_tokens、total_tool_calls、avg_tool_latency_ms、p50_tool_latency_ms、p95_tool_latency_ms、cache_hit_rate、compact_count（`jq 'keys' metrics.json` 输出至少 7 个字段）
- [ ] `cache_hit_rate` = prompt_cache_hits / total_requests（值为 0~1 的浮点数）
- [ ] `avg_tool_latency_ms` 为所有工具调用的平均延迟（毫秒，整数）
- [ ] 无会话时 metrics 目录保持干净（不产生空文件）

---

## Agent 自配置

### Harness 工具集
- [ ] `AddHookTool` 注册在 ToolRegistry 中（`grep AddHookTool mewcode/harness/tools.py` → 能找到类定义和注册代码）
- [ ] `RemoveHookTool` 注册在 ToolRegistry 中
- [ ] `ListHooksTool` 注册在 ToolRegistry 中，category 为 "read"
- [ ] `UpdateConfigTool` 注册在 ToolRegistry 中
- [ ] `AddPermissionRuleTool` 注册在 ToolRegistry 中
- [ ] `RemovePermissionRuleTool` 注册在 ToolRegistry 中
- [ ] `ManageMemoryTool` 注册在 ToolRegistry 中
- [ ] 所有 Harness 工具的 category 为 "harness"（`grep "harness" mewcode/harness/tools.py` 返回 ≥7）
- [ ] `allow_self_modification` 配置项默认值为 false（`grep "allow_self_modification.*False\|allow_self_modification.*false" mewcode/config.py` 返回 ≥1）
- [ ] 当 `allow_self_modification = false` 时，Agent 调用任何 Harness 工具 → PermissionChecker 返回 Decision.deny（reason 包含 "self modification is disabled"）
- [ ] 当 `allow_self_modification = true` 时，Agent 可以成功调用 AddHookTool 添加一个新 hook
- [ ] 调用 `AddHookTool` 添加的 hook 在下一次对应事件触发时生效（添加 post_tool_use hook → 执行一次工具调用 → hook 动作执行）
- [ ] 调用 `RemoveHookTool` 删除某 hook → 再次触发对应事件时该 hook 不再执行
- [ ] 调用 `UpdateConfigTool` 修改 `permission_mode` → 下次权限检查使用新模式
- [ ] 调用 `AddPermissionRuleTool` 添加规则 → 规则持久化到 `.mewcode/permissions.local.yaml`（文件内容包含新规则）
- [ ] 调用 `ManageMemoryTool` 添加记忆 → `.mewcode/memories.md` 文件末尾出现新条目
- [ ] 所有 Harness 工具调用写入审计日志（source_layer 为 harness 标识）

---

## Workflow Agent Tool

- [ ] `WorkflowTool` 注册在 ToolRegistry 中，tool name 为 "workflow"（`grep "workflow" mewcode/workflow/tool.py` class 定义中的 name 字段）
- [ ] WorkflowTool 参数包含：workflow_name（必填 str）、args（可选 dict）、background（默认 false）（检查 Pydantic params_model 的字段定义）
- [ ] 调用 `workflow(name="review-changes", background=false)` → 阻塞等待 workflow 完成 → 返回结果文本
- [ ] 调用 `workflow(name="migrate", background=true)` → 立即返回 task_id → workflow 在后台执行 → 完成后以通知形式注入结果
- [ ] `workflow.list_workflows()` 方法扫描 `.mewcode/workflows/*.py` 文件（目录下有 3 个 .py 文件 → list 返回 3 个 workflow 名称）
- [ ] Workflow 文件不含 `META` 或 `__doc__` 时，使用文件名作为 workflow 名称（`my_workflow.py` → name="my_workflow"）

---

## 端到端验证

### 基础功能端到端
- [ ] **E2E-1**：创建一个简单的 workflow `.mewcode/workflows/hello.py`，其中 `ctx.agent("say hello")` 执行 1 次 → 对话中输入 `/workflow hello` → Agent 调用 WorkflowTool → workflow 执行 → 返回 "hello" 相关内容到对话中
- [ ] **E2E-2**：创建一次性 cron 任务 → `!mewcode -p "create a cron job to remind me in 2 minutes"` → Agent 调用 CronCreateTool → 等待约 2 分钟 → 对话中出现系统提醒消息
- [ ] **E2E-3**：开启 `critic.enabled = true` → 与 Agent 对话后让它自然结束 → 观察对话中是否出现 Critic 的追问建议

### 向后兼容端到端
- [ ] **COMPAT-1**：`mewcode -p "list files in current directory"` 的行为与改造前完全一致——输出包含当前目录的文件列表
- [ ] **COMPAT-2**：现有的 `.mewcode/config.yaml` 文件在改造后可直接加载，不报配置解析错误
- [ ] **COMPAT-3**：现有的 MEWCODE.md 指令文件在改造后仍被正确加载和应用（Agent 行为受指令约束）
- [ ] **COMPAT-4**：现有 Skill 系统正常工作（`/review` 等 slash command 仍可触发对应 Skill）
- [ ] **COMPAT-5**：现有权限模式切换（Shift+Tab）仍正常工作
- [ ] **COMPAT-6**：现有 MCP 工具在改造后仍可正常使用（MCP server 连接、工具调用、结果返回均正常）

### 压力端到端
- [ ] **STRESS-1**：创建包含 10 个 items × 2 个 stages 的 pipeline workflow → 全部 20 次 agent 调用成功完成 → journal 记录完整
- [ ] **STRESS-2**：提交 100 个并发 cron 任务 → 调度器正常运行，不崩溃，不丢任务（list 返回 100 个）
- [ ] **STRESS-3**：模拟 1000 次工具调用 → 审计日志正常写入 → 文件大小合理（< 5MB）→ rotate 机制正常

### 恢复端到端
- [ ] **RECOVER-1**：执行 workflow 到一半 → 强制终止进程（Ctrl+C 或 kill） → 重新启动 MewCode → 再次执行同一 workflow → 前半部分从缓存恢复，仅执行后半部分 → 最终结果与一次性完整执行一致
- [ ] **RECOVER-2**：Journal 文件存在但部分记录损坏（某行非 JSON 格式）→ 加载时跳过损坏行 → 其余记录正常加载和命中 → 日志中 `[journal] skipping corrupted line N`

### 安全端到端
- [ ] **SEC-1**：`allow_self_modification = false` → Agent 执行 "add a hook to log all tool calls" → Agent 尝试调用 AddHookTool → 权限拒绝 → Agent 被告知无法执行
- [ ] **SEC-2**：Bash 工具限流为 10/min → Agent 执行 15 次 Bash 命令 → 前 10 次成功，后 5 次被限流拒绝 → Agent 被告知速率限制原因
- [ ] **SEC-3**：Agent 尝试通过 `UpdateConfigTool` 将 `allow_self_modification` 改为 true → 该操作本身被权限检查拦截（不允许递归提升权限）

---

## 成功经验驱动的自进化

> 阈值/默认值集中在此节作为验收项。每项可勾选、可观测，检查方法写在括号里。

### 配置项与开关
- [ ] `evolution.success.enabled` 默认值为 false（`grep "success.*enabled.*False\|success.*enabled.*false" mewcode/config.py` 返回 ≥1）
- [ ] `evolution.success.enabled = false` 时，成功路径完全不触发——无候选生成、无匹配注入（日志中无 `[evolution][success]` 输出）
- [ ] 成功路径受 `allow_self_evolution` 控制：该开关为 false 时即使 `success.enabled=true` 也不执行（日志中 `[evolution][success] skipped: self-evolution disabled`）
- [ ] 新增配置项经 `validator.py` 校验：非法类型/越界值启动报错（`grep "success" mewcode/validator.py` 返回 ≥1）

### 复杂任务识别
- [ ] 迭代数阈值默认 8（`grep "iteration.*8\|8.*iteration" mewcode/harness/evolution/success_detector.py` 上下文命中）
- [ ] 工具调用数阈值默认 10（`grep "tool_call.*10\|10.*tool_call" mewcode/harness/evolution/success_detector.py` 上下文命中）
- [ ] 迭代数 < 8 且工具调用数 < 10 的任务即使成功也不产出 SuccessSignal（单元测试：5 轮迭代 / 4 次工具调用 → `detect()` 返回 None）
- [ ] 迭代数 ≥ 8（即使工具调用数 < 10）的成功任务产出 SuccessSignal（单元测试：8 轮迭代 / 3 次工具调用 → `detect()` 非 None）
- [ ] 失败任务不产出 SuccessSignal（单元测试：标记 success=False → `detect()` 返回 None）
- [ ] SuccessSignal 含 `had_retries` 字段：发生重试的任务成功后仍产出信号（验证「含高成本成功」纳入范围）

### 候选 Skill 生成
- [ ] 复杂成功任务产出候选 Skill，写入 skills 目录且 `skill_meta.json` 中 `status="candidate"`（`jq '.skills[] | select(.status=="candidate")' .mewcode/skill_meta.json` 返回结果）
- [ ] 候选 Skill 为指南型 SKILL.md（文件含「步骤/经验/指引」语义，非工具调用序列日志）
- [ ] 证据不足时抛出 `InsufficientEvidenceError`，不生成 Skill（单元测试：key_steps 过少 → 断言抛出）
- [ ] 检测到捏造内容时抛出 `FabricatedContentError`，不生成 Skill（单元测试：mock 返回含未出现于 trace 的步骤 → 断言抛出）
- [ ] 生成前 skills 目录被 BackupManager 备份（日志中 `[evolution][success] backup created`）

### 候选晋升
- [ ] 晋升阈值默认 2：同类复杂成功复发 ≥ 2 次后候选升为 active（`grep "recurrence.*2\|2.*recurrence" mewcode/harness/evolution/skill_meta.py` 上下文命中）
- [ ] 首次成功生成候选后 `recurrence=1`，状态仍为 candidate（`jq` 验证 status=candidate, recurrence=1）
- [ ] 同类第二次成功匹配到已有候选 → `recurrence=2` → 状态升为 active（`jq` 验证 status=active）
- [ ] 「同类」判定走 SkillMatcher.match_candidates：非同类的新成功生成新候选而非误累加（单元测试：两个语义不同的复杂任务 → 两条候选记录，各自 recurrence=1）
- [ ] candidate 状态的 Skill 不参与任务开始的注入匹配（任务 19 的 match_active 扫描结果不含 candidate）

### 语义匹配与注入
- [ ] `SkillMatcher.match_active(task_desc)` 只返回 `status=active` 的 Skill（单元测试：1 active + 2 candidate → 仅返回 active）
- [ ] 匹配为轻量 LLM 侧路调用，超时默认 8 秒（`grep "timeout.*8" mewcode/harness/evolution/skill_matcher.py` 返回 ≥1）
- [ ] 匹配超时/失败时静默跳过，不阻塞主循环、不抛异常到 Agent.run（单元测试：mock 匹配超时 → 主循环正常继续）
- [ ] 命中 active Skill 时，其内容以「可用经验」形式注入上下文（对话/trace 中可见注入的 SKILL.md 摘要）
- [ ] 注入内容含「由 Agent 自主判断是否采纳」说明，不强制执行（检查注入文本）
- [ ] Agent 二次校验拒绝采纳时，常规流程不受影响（trace 中 Skill 未被引用、任务正常完成）
- [ ] 无 active Skill 或未命中时，任务开始无注入、行为与未开启功能时一致

### 命中后降本评估
- [ ] 降本判定：迭代数降幅 ≥ 20% 且 token 增幅 ≤ 15% 才判为降本（`grep "0.2\|20" mewcode/harness/evolution/evaluator.py` 与 `grep "0.15\|15" mewcode/harness/evolution/evaluator.py` 上下文命中）
- [ ] 历史基线样本数默认 5：同类任务最近 5 次未命中时的迭代数/token 均值（`grep "baseline.*5\|5.*baseline" mewcode/harness/evolution/evaluator.py` 上下文命中）
- [ ] 基线样本不足（< 5）时不做降级，维持 active（单元测试：仅 2 条历史 → 评估返回 keep，不降级）
- [ ] 命中采纳后降本 → 维持 active，记录 `eval=keep`（`jq '.evolution_records[] | select(.path=="success" and .eval=="keep")'` 返回结果）
- [ ] 命中采纳后未降本 → 降级（记录 `eval=demote`）
- [ ] 命中被采纳但任务失败 → 记一次 hit_failure；累计 ≥ 3 次自动降级废弃（`grep "hit_failure.*3\|3.*hit_failure" mewcode/harness/evolution/skill_meta.py` 上下文命中）
- [ ] 成功型 Skill 沿用「60 任务未用即废弃」机制（`check_deprecation_candidates` 覆盖 success 路径 Skill）

### 双路独立与可观测
- [ ] 同一会话 `check_and_evolve` 同时跑失败路径与成功路径，两路均写入进化记录且 `path` 字段区分（`jq '.evolution_records | group_by(.path) | map({path: .[0].path, n: length})'` 含 success 与 failure 两组）
- [ ] 成功路径与失败路径不互相查重合并（同一任务既有失败补救 Skill 又有成功经验 Skill 时，两条记录共存）
- [ ] `ListAutoSkillsTool` 可列出成功型 Skill 及其 status/recurrence/hit_count（调用工具 → 返回含 candidate/active 标记的列表）
- [ ] `ListEvolutionsTool` / `GetEvolutionDetailTool` 可查看成功路径进化记录（调用工具 → 返回 path=success 的记录）

### 端到端验证
- [ ] **EVO-S1**：跑一轮简单任务（< 阈值）成功 → `skill_meta.json` 无新候选（`jq '.skills | length'` 不变）
- [ ] **EVO-S2**：跑两轮同类复杂成功任务 → 第一轮生成候选（recurrence=1）→ 第二轮晋升 active（recurrence=2, status=active）
- [ ] **EVO-S3**：第三轮同类任务开始时命中 active Skill 并注入 → Agent 采纳 → 任务成功且迭代数/token 低于历史基线 → 评估 keep
- [ ] **EVO-S4**：命中注入后 Agent 拒绝采纳（任务与 Skill 差异较大）→ 常规流程完成、不被记为 hit_failure
- [ ] **EVO-S5**：连续 3 次命中采纳但任务失败 → 该 Skill 自动降级废弃（`jq` 验证 status=deprecated）
- [ ] **EVO-S6**：`allow_self_evolution=false` 时，即使复杂成功多次复发也不生成候选、不注入（`skill_meta.json` 无 success 路径记录）
