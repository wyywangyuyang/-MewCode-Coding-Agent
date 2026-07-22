# MewCode Loop Engineering + Harness Engineering 实施任务清单

## 任务总览

共 15 个任务，分 4 个阶段。每个任务标注影响文件、前置依赖、参考资料定位。

---

## 阶段一：Workflow 编排引擎核心

### 任务 1：Workflow 数据模型 + Journal 系统

**描述**：定义 workflow 的核心数据结构，实现追加式 Journal 日志系统（写入、查询、缓存命中判定）。

**影响文件**：
- `mewcode/workflow/__init__.py` — 新建，空包
- `mewcode/workflow/models.py` — 新建，定义 WorkflowDef、AgentCallRecord、JournalEntry、WorkflowState、BudgetInfo
- `mewcode/workflow/journal.py` — 新建，Journal 类：append()、lookup(prompt_hash, opts_hash)、prune(max_size)、flush()

**前置依赖**：无

**参考资料定位**：
- 现有 `agents/trace.py` — TraceNode 的树结构可参考
- 现有 `memory/session.py` — SessionRecord 的 JSONL 序列化方式可参考
- 现有 `context/manager.py` — CompactBoundary 的持久化模式

**关键设计点**：
- Journal 文件路径：`.mewcode/workflows/journals/{workflow_name}/{run_id}.jsonl`
- 每条记录包含：`call_id`、`prompt_sha256`、`opts_sha256`、`status`（running/completed/failed）、`result_json`（可为 null）、`started_at`、`completed_at`
- `lookup()` 返回 None 表示未命中（需实际执行），返回 AgentCallRecord 表示命中（可跳过）
- `prune(max_bytes)` 当文件超过限制时删除最旧的非 running 条目

---

### 任务 2：WorkflowContext — 核心原语实现

**描述**：实现 WorkflowContext 类，提供 `agent()`、`pipeline()`、`parallel()`、`phase()`、`log()` 五个核心原语和 `budget` 属性。

**影响文件**：
- `mewcode/workflow/context.py` — 新建，WorkflowContext 类
- `mewcode/workflow/engine.py` — 新建，WorkflowEngine 类（骨架：加载模块、执行函数、传入 context）

**前置依赖**：任务 1

**参考资料定位**：
- 现有 `agent.py` — `Agent.run()` 方法中 LLM 调用流程（`client.stream()` → `StreamCollector` → `ToolBatch` 执行）——`agent()` 原语实质上是这个流程的封装
- 现有 `agents/task_manager.py` — `TaskManager.spawn()` 的并发控制模式（`asyncio.Semaphore`）
- 现有 `tools/agent_tool.py` — `AgentTool.execute()` 中 sub-agent 的创建和参数传递
- 现有 `conversation.py` — `ConversationManager` 的创建和管理

**关键设计点**：
- `agent(prompt, *, schema=None, label=None, phase=None, model=None, effort=None, isolation=None)` — 每次调用创建一个新的 Agent 实例，使用新的 ConversationManager，执行完毕后返回结果（有 schema 时返回校验后的 Pydantic 对象，无 schema 时返回字符串）
- `pipeline(items, *stages)` — 用 `asyncio.Queue` 连接各 stage，每项独立流经所有 stage，项间无屏障
- `parallel(thunks)` — `asyncio.TaskGroup` 并发执行，单个失败 → 对应槽位为 None
- `phase(title)` — 设置当前 phase 标记，后续 agent() 调用自动归属
- `log(message)` — 通过回调通知 TUI 显示进度文本
- `budget` — 暴露 BudgetInfo 对象（total: int|None, spent(): int, remaining(): int）
- 并发上限：`min(16, os.cpu_count() - 2)`

---

### 任务 3：Structured Output + Token Budget 追踪

**描述**：实现 agent() 的 schema 校验（Pydantic → JSON Schema → 强制模型输出结构化数据）和 token 预算实时追踪。

**影响文件**：
- `mewcode/workflow/context.py` — 增强 agent() 方法：schema 参数处理、校验失败重试逻辑
- `mewcode/workflow/engine.py` — 增强 WorkflowEngine：维护全局 token 计数器，每次 agent 调用完成后累加 usage
- `mewcode/workflow/models.py` — 新增 StructuredOutputConfig、BudgetInfo

**前置依赖**：任务 2

**参考资料定位**：
- 现有 `client.py` — 各 LLM 客户端的 `stream()` 方法返回的 `StreamEnd` 事件包含 `usage`（input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens）
- 现有 `serialization.py` — `build_messages()` 可参考来在 system prompt 中注入 "你必须输出 JSON" 指令
- 现有 `agents/tool_filter.py` — 工具过滤模式，schema 模式下需要注入特殊的 `StructuredOutput` 工具

**关键设计点**：
- Schema 注入方式：在 system prompt 中追加 "You MUST respond with a valid JSON object matching this schema: {json_schema}"，同时注册一个 `__structured_output__` 工具，其 input_schema 即为目标 schema
- 校验失败重试：最多重试 3 次，每次将校验错误信息反馈给模型
- Token 追踪：`budget.spent()` 实时聚合所有 agent 调用的 `usage.input_tokens + usage.output_tokens`，`budget.remaining()` = max(0, total - spent())
- 当 `remaining() == 0` 时，后续 `agent()` 调用抛出 `BudgetExhaustedError`

---

### 任务 4：Loop Patterns + 断点恢复

**描述**：实现 `loop_until_count`、`loop_until_budget`、`loop_until_dry` 三种内建循环模式，以及 Workflow 断点恢复逻辑。

**影响文件**：
- `mewcode/workflow/patterns.py` — 新建，三种 loop_until 辅助函数
- `mewcode/workflow/resume.py` — 新建，ResumeManager：检测已有 journal、回放命中、定位断点
- `mewcode/workflow/engine.py` — 增强：执行前检查是否存在未完成的 run，若存在则进入恢复模式

**前置依赖**：任务 3

**参考资料定位**：
- 现有 `context/manager.py` — `CompactCircuitBreaker`（连续失败计数器）——loop_until_dry 的 dry 计数器参考
- 现有 `memory/session.py` — `ResumeResult` 的数据结构可参考
- Journal 文件格式来自任务 1

**关键设计点**：
- `loop_until_count(target, fn)` — 循环调用 `fn()`，累积结果，直到结果数 ≥ target 或连续 3 轮无新结果
- `loop_until_budget(fn)` — 循环调用 `fn()`，直到 `budget.remaining() < min_budget_per_call` (默认 50000)
- `loop_until_dry(fn, dry_threshold=2)` — 循环调用 `fn()`，直到连续 `dry_threshold` 轮无新结果（新结果判定由调用方提供的 `is_new(result, seen)` 函数决定）
- 断点恢复流程：`ResumeManager` 加载已有 journal → 构建 (prompt_hash → result) 映射 → Workflow 函数正常执行 → 每个 `agent()` 调用先查映射 → 命中则直接返回缓存结果，未命中则实际执行并写入 journal
- 检测 run 状态：journal 中存在 `status: running` 且无 `completed_at` 的记录 → 该 run 未完成

---

## 阶段二：调度系统

### 任务 5：Cron 调度核心 + 持久化

**描述**：实现 Cron 表达式解析器、任务存储、后台调度循环。

**影响文件**：
- `mewcode/scheduler/__init__.py` — 新建
- `mewcode/scheduler/cron.py` — 新建，CronExpression 类：parse()、next_fire()、validate()
- `mewcode/scheduler/store.py` — 新建，CronStore 类：add()、remove()、list()、load()、save()、get_due()
- `mewcode/scheduler/runtime.py` — 新建，SchedulerRuntime 类：后台 asyncio 循环，每分钟检查到期任务

**前置依赖**：无（独立模块）

**参考资料定位**：
- 现有 `config.py` — `load_config()` 中多文件合并模式，Store 的 JSON 文件读写参考
- 现有 `hooks/conditions.py` — fnmatch 模式匹配

**关键设计点**：
- Cron 表达式支持 5 字段：`minute hour day-of-month month day-of-week`
- `next_fire(after_dt)` 返回下一次触发时间（本地时区）
- 持久化文件：`.mewcode/scheduled_tasks.json`，JSON 数组格式
- 任务属性：`id`（uuid）、`cron`（表达式字符串）、`prompt`（触发时注入的提示词）、`recurring`（bool）、`durable`（bool）、`created_at`、`last_fired_at`
- 调度循环：每 60 秒唤醒一次，查询所有到期任务，逐一触发
- 一次性任务触发后自动标记 `fired` 并从活跃列表移除
- 周期性任务记录 `last_fired_at` 并计算下一次触发时间
- 抖动：在计算出的触发时间上叠加 ±(0~90) 秒随机偏移（只对分钟级精度）

---

### 任务 6：ScheduleWakeup 自步进 + Agent Tools

**描述**：实现动态 Wakeup 调度（用于 Agent 在 Loop 中声明"N 秒后唤醒我"），以及 Cron 相关的 Agent 工具。

**影响文件**：
- `mewcode/scheduler/wakeup.py` — 新建，WakeupScheduler：schedule(delay_seconds, reason, prompt)、cancel()
- `mewcode/scheduler/tools.py` — 新建，CronCreateTool、CronDeleteTool、CronListTool、ScheduleWakeupTool
- `mewcode/scheduler/runtime.py` — 增强：集成 wakeup 到期检测

**前置依赖**：任务 5

**参考资料定位**：
- 现有 `tools/ask_user.py` — AskUserTool 的工具定义模式（params_model、execute）
- 现有 `tools/base.py` — Tool ABC 的注册和 get_schema 模式

**关键设计点**：
- `ScheduleWakeup` 的 delay 参数 clamp 到 [60, 3600] 秒
- 缓存感知：300 秒内 wakeup 保持 prompt 缓存热度，超过 300 秒接受缓存失效
- Wakeup 触发时：将 prompt 作为系统消息注入当前会话
- Cron Tools 的权限模式：CronCreate 标记为 write 类（需用户确认），CronList 标记为 read 类
- CronCreate 参数校验：表达式合法性、prompt 非空、recurring 默认 true、durable 默认 false

---

## 阶段三：Harness 增强

### 任务 7：动态上下文管理改造

**描述**：改造现有 Compact 逻辑——从固定字符阈值改为动态利用率阈值，实现语义分段压缩。

**影响文件**：
- `mewcode/context/manager.py` — 改造：`compute_compact_threshold()` 改为基于模型上下文窗口利用率计算；`_compute_keep_start_index()` 改为识别语义边界
- `mewcode/config.py` — 新增配置项：`compact.utilization_threshold`（默认 0.85）、`compact.min_keep_messages`（默认 3）

**前置依赖**：无（独立改造）

**参考资料定位**：
- 现有 `context/manager.py` — `auto_compact()` 的完整逻辑（第 80-250 行区域），`_compute_keep_start_index()` 的当前实现（基于字符数截断）
- 现有 `conversation.py` — `ConversationManager` 的 `current_tokens()` 和 `record_usage_anchor()` 方法
- 现有 `validator.py` — 模型上下文窗口的 builtin mapping（1m→1000000, gpt-4o→128000, claude→200000）

**关键设计点**：
- 动态阈值：`threshold_tokens = context_window * utilization_threshold`，而非当前硬编码的某个固定值
- 语义边界识别：按 Message 角色边界切割（user message 开始新语义段，tool_result 和下一轮 user 之间是天然边界）
- 保留最小 tail：至少保留 `min_keep_messages` 条最近消息（不受压缩影响）
- `utilization_threshold` 可通过 harness 自配置工具在运行时调整

---

### 任务 8：Completeness Critic

**描述**：实现轻量级 Critic——在 Agent 无工具调用即将结束轮次时，检查是否遗漏了关键维度。

**影响文件**：
- `mewcode/context/critic.py` — 新建，CompletenessCritic 类：`check(conversation, last_response)` → CriticResult
- `mewcode/agent.py` — 改造：在 Loop 检测到无工具调用、即将 break 之前，调用 Critic

**前置依赖**：任务 7

**参考资料定位**：
- 现有 `agent.py` — Agent.run() 中 "no tool calls → break" 的逻辑位置（约在流式响应处理完成后的判断分支）
- 现有 `memory/recall.py` — 侧路 LLM 查询模式（独立小模型调用，有超时限制）

**关键设计点**：
- Critic 使用独立的轻量 LLM 调用（推荐用 haiku 或 gpt-4o-mini），设置 8 秒超时
- Critic 检查维度：未使用的工具类别（有 Bash 没用过？）、未验证的假设（提到过某文件但没读过？）、未确认的副作用（修改了代码但没运行测试？）
- 结果分两级：`clean`（无遗漏）/ `suggestions`（有建议追问）
- 当结果为 `suggestions` 时，将建议作为 system reminder 注入下一轮对话
- Critic 可配置开关：`config.critic.enabled`（默认 false，用户手动开启）
- 连续 3 次 Critic 都返回相同建议 → 停止追问（防死循环）

---

### 任务 9：权限审计日志

**描述**：为所有工具执行决策添加结构化审计日志记录。

**影响文件**：
- `mewcode/permissions/audit.py` — 新建，AuditLogger 类：`log_decision()`、`query()`、`rotate()`
- `mewcode/permissions/checker.py` — 改造：在 `check()` 方法返回 Decision 后调用 `audit_logger.log_decision()`
- `mewcode/permissions/__init__.py` — 改造：导出 AuditLogger

**前置依赖**：无（独立增强）

**参考资料定位**：
- 现有 `permissions/checker.py` — `PermissionChecker.check()` 的完整调用链（6 层决策，每层返回 Decision）
- 现有 `permissions/rules.py` — RuleEngine 的规则匹配日志
- Journal 模块（任务 1）的 JSONL 追加模式可复用

**关键设计点**：
- 审计日志文件：`.mewcode/audit/decisions.jsonl`
- 每条记录：`timestamp`、`tool_name`、`params_summary`（截断到 200 字符）、`decision`（allow/deny/ask）、`source_layer`（safe_readonly/dangerous/sandbox/rule_engine/mode/hitl）、`rule_id`（若来自规则引擎则记录规则标识）、`latency_ms`、`session_id`
- 自动 rotate：单文件超过 50MB 时归档为 `decisions.{date}.jsonl`，保留最近 10 个归档
- 提供 `AuditLogTool` 让 Agent 查询自身审计日志（read 类工具）

---

### 任务 10：工具级速率限制

**描述**：实现对单工具的调用频率限制，防止 Agent 疯狂调用。

**影响文件**：
- `mewcode/permissions/rate_limit.py` — 新建，RateLimiter 类：`acquire(tool_name)` → bool、`reset(tool_name)`
- `mewcode/permissions/checker.py` — 改造：在 Layer 0（plan mode）之后、Layer 1（safe readonly）之前插入速率检查层
- `mewcode/config.py` — 新增配置项：`rate_limit.enabled`（默认 true）、`rate_limit.default_max_per_minute`（默认 30）、`rate_limit.per_tool`（dict，如 `{"bash": 10, "write_file": 20}`）

**前置依赖**：无

**参考资料定位**：
- 现有 `permissions/checker.py` — `check()` 方法的层级插入点（plan_mode_exception 之后）
- 现有 `tools/base.py` — Tool.name 属性用于限流 key
- 现有 `cache.py` — FileCache 的线程安全模式（threading.Lock）可参考用于滑动窗口的并发控制

**关键设计点**：
- 算法：滑动窗口（1 分钟窗口），用 `collections.deque` 存储时间戳
- 超限时：PermissionChecker 返回 Decision.deny，reason 中包含 "rate limit exceeded: {tool_name} ({count}/{max} per minute)"
- 重置：新会话开始时清空所有限流状态
- `per_tool` 配置优先于 `default_max_per_minute`

---

### 任务 11：全链路 Trace 增强 + 回放

**描述**：增强现有 TraceManager，记录每个 Agent 调用的完整输入/输出，支持从 Trace 节点回放。

**影响文件**：
- `mewcode/agents/trace.py` — 增强：TraceNode 新增 `input_summary`、`output_summary`、`tool_calls_detail`、`token_usage`、`latency_ms` 字段；新增 `export_json()` 和 `replay_context()` 方法
- `mewcode/agents/metrics.py` — 新建，MetricsCollector：聚合 token 效率、工具延迟分位数、缓存命中率
- `mewcode/agent.py` — 改造：在 `_execute_tool()` 和 `run()` 的关键节点调用 `trace_manager.record_detail()`

**前置依赖**：无

**参考资料定位**：
- 现有 `agents/trace.py` — TraceNode 的当前字段（agent_name, parent, children, status, started_at, completed_at）
- 现有 `agent.py` — StreamCollector 中的 usage 信息（StreamEnd 事件携带 token 数据）

**关键设计点**：
- `tool_calls_detail` 存储每次工具调用的 name、params_summary（截断 200 字符）、result_summary（截断 500 字符）、success（bool）
- Trace 持久化：`.mewcode/traces/{session_id}/{timestamp}_{agent_name}.json`
- 自动清理：每次启动时删除 30 天前的 trace 文件
- `replay_context(trace_node)` — 从 trace 节点提取输入上下文，构造等价的 Agent 调用（用于回归测试/对比）
- 指标聚合：`MetricsCollector` 在会话结束时输出统计摘要到 `.mewcode/metrics/{session_id}.json`

---

### 任务 12：Agent 自配置工具集

**描述**：实现让 Agent 在运行时修改自身 Harness 行为的工具集——Hook 管理、配置更新、权限规则管理、Memory 管理。

**影响文件**：
- `mewcode/harness/__init__.py` — 新建
- `mewcode/harness/hook_manager.py` — 新建，HookManager：add_hook()、remove_hook()、list_hooks()、update_hook()
- `mewcode/harness/config_manager.py` — 新建，ConfigManager：get_config()、set_config()、reload_config()
- `mewcode/harness/permission_manager.py` — 新建，PermissionManager：add_rule()、remove_rule()、list_rules()
- `mewcode/harness/memory_tools.py` — 新建，MemoryTools：add_memory()、update_memory()、delete_memory()、search_memory()
- `mewcode/harness/tools.py` — 新建，暴露给 Agent 的工具：AddHookTool、RemoveHookTool、ListHooksTool、UpdateConfigTool、AddPermissionRuleTool、RemovePermissionRuleTool、ManageMemoryTool

**前置依赖**：任务 9（审计日志——自配置操作需被审计）

**参考资料定位**：
- 现有 `hooks/engine.py` — HookEngine 的 `add_hook()` / `remove_hook()`（需新增）
- 现有 `hooks/loader.py` — Hook 配置格式
- 现有 `config.py` — AppConfig 的字段结构
- 现有 `permissions/rules.py` — RuleEngine 的 `add_rule()` / `remove_rule()`（需新增）
- 现有 `memory/auto_memory.py` — MemoryManager 的文件写入模式

**关键设计点**：
- 所有自配置工具标记为 `category: "harness"`，受独立权限层控制
- 新增权限模式检查："是否允许 Agent 修改自身 Harness"（`allow_self_modification` 配置项，默认 false）
- Hook 修改立即生效（写入内存 + 持久化到配置文件的 hooks 段）
- 配置修改部分立即生效（模型切换等需下一轮生效）、部分需重启
- 权限规则修改：写入 `.mewcode/permissions.local.yaml`（最高优先级文件）
- Memory 管理：直接操作 `.mewcode/memories.md` 文件

---

## 阶段四：集成与验证

### 任务 13：Workflow Agent Tool — 暴露给主 Agent

**描述**：实现 Workflow 工具，让主 Agent 可以在对话中调用 workflow。支持同步（等待结果）和后台（异步通知）两种模式。

**影响文件**：
- `mewcode/workflow/tool.py` — 新建，WorkflowTool（继承 Tool）：列出可用 workflow、执行指定 workflow、返回结果
- `mewcode/tools/__init__.py` — 改造：`create_default_registry()` 中注册 WorkflowTool
- `mewcode/workflow/engine.py` — 增强：`list_workflows()` 方法（扫描 `.mewcode/workflows/` 目录）

**前置依赖**：任务 4、任务 2

**参考资料定位**：
- 现有 `tools/agent_tool.py` — AgentTool 的 execute() 模式（同步 sub-agent 执行，结果返回到主 Agent 上下文）
- 现有 `tools/load_skill.py` — LoadSkill 的模式（加载外部定义并激活）
- 现有 `agents/task_manager.py` — 后台任务启动 + 完成通知机制

**关键设计点**：
- WorkflowTool 参数：`workflow_name`（必填）、`args`（可选 dict）、`background`（默认 false）
- 同步模式：阻塞等待 workflow 执行完毕，将结果文本注入当前对话
- 后台模式：通过 TaskManager 启动后台任务，返回 task_id；完成后以 Teammate 通知形式注入结论
- `list_workflows()` 扫描 `.mewcode/workflows/*.py`，解析每个文件的 `__doc__` 或模块级 `META` dict 获取名称和描述
- Workflow 执行使用独立于主 Agent 的 token 预算（默认不限制，可配置上限）

---

### 任务 14：主循环集成

**描述**：将 Workflow 引擎、调度器、Completeness Critic、审计日志、速率限制、Trace 增强全部接入 Agent 主循环和 TUI。

**影响文件**：
- `mewcode/agent.py` — 核心改造：
  - `Agent.__init__()` 接收新的依赖：`workflow_engine`、`scheduler_runtime`、`critic`、`audit_logger`、`rate_limiter`
  - `run()` / `run_to_completion()` 中：每轮开始检查调度到期任务、无工具调用时触发 Critic
- `mewcode/app.py` — 改造：
  - `MewCodeApp.__init__()` 初始化所有新组件
  - 显示 workflow 执行进度（phase 分组树、agent 调用状态）
  - 显示 cron 任务状态
- `mewcode/__main__.py` — 改造：
  - `_run_prompt()` 中初始化新组件并传入 Agent
  - 启动 SchedulerRuntime 后台循环
  - 优雅关闭：信号处理中 `await scheduler_runtime.shutdown()`

**前置依赖**：任务 1-13 全部

**参考资料定位**：
- 现有 `agent.py` — `Agent.__init__()` 的参数列表（当前：client, registry, protocol, work_dir, max_iterations, permission_checker, context_window, instructions_content, memory_manager, hook_engine）
- 现有 `app.py` — `MewCodeApp.__init__()` 的组件初始化顺序
- 现有 `__main__.py` — `main()` 和 `_run_prompt()` 的流程

**关键设计点**：
- Agent 构造函数参数新增约 5 个依赖——使用可选参数保持向后兼容（默认值 = None 时创建默认实例）
- TUI 进度展示：在 `TeammateTree` 旁新增 `WorkflowProgress` widget，显示 active workflow 的 phase 树和 agent 调用状态
- 调度器生命周期：`SchedulerRuntime` 在 App 启动时创建后台 `asyncio.Task`，关闭时 cancel
- 所有新功能默认不改变 Agent 行为——Critic 默认关闭、调度器无任务时静默、workflow 未被调用时不加载

---

### 任务 15：端到端验证

**描述**：编写覆盖所有新增功能的端到端验证场景，确保改造后的 MewCode 功能完整且向后兼容。

**影响文件**：
- `tests/test_workflow_engine.py` — 新建，测试：pipeline/parallel 执行、structured output 校验、journal 写入/恢复
- `tests/test_workflow_resume.py` — 新建，测试：中断后恢复、缓存命中、幂等性
- `tests/test_scheduler.py` — 新建，测试：cron 解析、任务存储、到期触发
- `tests/test_context_critic.py` — 新建，测试：动态阈值、Critic 建议注入
- `tests/test_permissions_audit.py` — 新建，测试：审计日志写入/查询/rotate
- `tests/test_rate_limit.py` — 新建，测试：滑动窗口、超限拒绝
- `tests/test_harness_tools.py` — 新建，测试：自配置工具的权限控制、Hook 增删
- `tests/test_e2e_backcompat.py` — 新建，测试：现有 CLI 接口不变、现有配置文件可正常加载

**前置依赖**：任务 14

**参考资料定位**：
- 现有项目测试目录（若有）的结构
- 现有 `pyproject.toml` 中的 pytest 配置
- 各模块的公开 API（从任务 1-12 的模块导入）

**关键验证场景**（详见 checklist.md）：
1. 基本 workflow 执行：pipeline 3 项 × 2 stage → 6 次 agent 调用，验证并发和结果正确
2. 断点恢复：执行 workflow → 模拟中断 → 重新执行 → 验证缓存命中数
3. Cron 调度：创建一次性任务 → 等待触发 → 验证系统消息注入
4. 向后兼容：`mewcode -p "hello"` 的行为与改造前一致
5. 自配置安全：禁用 `allow_self_modification` 时 Agent 无法调用 Harness 工具

---

## 阶段五：成功经验驱动的自进化扩展

> 增量扩展，不改动阶段一~四已有任务。复用现有 `harness/evolution/` 子系统，新增「成功路径」与失败路径并列。

### 任务 16：成功信号识别 + 数据模型

**描述**：在任务结束时识别「复杂且成功」的任务，产出成功信号；扩展进化数据模型支持成功路径与 Skill 状态机。

**影响文件**：
- `mewcode/harness/evolution/models.py` — 增强：新增 `SuccessSignal`（task_summary、iteration_count、tool_call_count、token_total、key_steps、had_retries）、`SkillStatus` 枚举（candidate/active/deprecated）、扩展 `EvolutionRecord` 增加 `path` 字段（success/failure）
- `mewcode/harness/evolution/success_detector.py` — 新建，`SuccessDetector.detect(trace) -> SuccessSignal | None`：判定成功且复杂
- `mewcode/harness/evolution/trace_store.py` — 增强：`TraceCollector.end_task()`（`:245`）补充记录 `success` 与复杂度计数字段，供 detector 读取

**前置依赖**：无（基于现有 evolution 子系统）

**参考资料定位**：
- 现有 `harness/evolution/trace_store.py:245` `TraceCollector.end_task()` —— 当前已记录 task 边界，需扩展成功/复杂度字段
- 现有 `harness/evolution/trace_store.py:271` `record_tool_use()`、`:289` `record_tokens()` —— 工具调用数与 token 累计的数据来源
- 现有 `harness/evolution/models.py` —— `ExecutionTrace / EvolutionRecord` 现有结构

**关键设计点**：
- 复杂判定阈值（具体值见 checklist）：迭代数 ≥ 8 **或** 工具调用数 ≥ 10 即复杂
- `success` 判定：end_task 时任务未被标记为失败、且产出了最终响应
- `had_retries`：本轮是否发生过重试/绕路（用于「含高成本成功」纳入范围，不作为过滤条件）
- 成功信号不在此阶段落盘为 Skill，仅产出数据对象供下游使用

---

### 任务 17：成功经验生成器

**描述**：将成功信号总结为指南型 SKILL.md，复用现有 SkillGenerator 的生成与质量护栏。

**影响文件**：
- `mewcode/harness/evolution/success_generator.py` — 新建，`SuccessSkillGenerator.generate(signal) -> SkillGenResult`：调用 LLM 总结成功经验为指南型 Skill
- `mewcode/harness/evolution/skill_generator.py` — 增强：抽出可复用的「写 SKILL.md + 注册 meta」公共逻辑，供成功路径调用

**前置依赖**：任务 16

**参考资料定位**：
- 现有 `harness/evolution/skill_generator.py:166` `SkillGenerator.generate()` —— 失败路径的生成流程，成功路径复用其写文件与 meta 注册
- 现有 `harness/evolution/skill_generator.py:128` `InsufficientEvidenceError`、`:133` `FabricatedContentError` —— 质量护栏，成功路径必须复用
- 现有 `harness/evolution/backup.py` `BackupManager` —— 生成前备份 skills 目录的既有机制

**关键设计点**：
- 生成 prompt 语义为「总结这次复杂任务为何成功、关键步骤与决策点、可复用的经验」，区别于失败路径的「补救」语义
- 产物为指南型 SKILL.md（非工具调用序列），强调「Agent 读取后按指引弹性执行」
- 证据不足（如关键步骤过少）或检测到捏造内容时抛出既有异常，不生成 Skill
- 生成后写入 `skill_meta`，初始状态为 `candidate`

---

### 任务 18：Skill 状态机 + 候选晋升

**描述**：为 Skill 增加 candidate/active 状态机，实现「同类复杂成功复发达阈值则候选晋升为正式」。

**影响文件**：
- `mewcode/harness/evolution/skill_meta.py` — 增强：`SkillMetaManager` 支持 `status` 字段、`increment_recurrence()`、`promote_to_active()`；`add_skill()`（`:84`）接受初始状态参数
- `mewcode/harness/evolution/decision_loop.py` — 增强：在成功路径阶段，新成功信号先尝试匹配已有候选，命中则累加复发计数，达阈值晋升

**前置依赖**：任务 16、17

**参考资料定位**：
- 现有 `harness/evolution/skill_meta.py:84` `add_skill()`、`:114` `deprecate_skill()`、`:169` `increment_tasks()` —— 状态变更的既有模式
- 现有 `harness/evolution/skill_meta.py:190` `check_deprecation_candidates()` —— 既有废弃计数机制可参照
- 现有 `harness/evolution/decision_loop.py:178` Phase 2 Classify —— 成功路径的「先匹配已有候选」逻辑插入位置

**关键设计点**：
- 晋升阈值（具体值见 checklist）：同类复杂成功复发 ≥ 2 次晋升为 active
- 「同类」判定复用任务 19 的语义匹配器（候选 Skill ↔ 新成功信号）
- candidate 状态的 Skill **不参与**任务开始的注入匹配（只有 active 才注入）
- 晋升/降级/废弃均写入 `skill_meta.json` 进化记录，`path` 字段标记为 success

---

### 任务 19：语义匹配器（晋升 + 注入两用）

**描述**：实现基于语义的 Skill 匹配器，两处复用——成功路径匹配候选 Skill 做晋升、任务开始匹配正式 Skill 做注入。

**影响文件**：
- `mewcode/harness/evolution/skill_matcher.py` — 新建，`SkillMatcher.match_candidates(task_desc)` 与 `match_active(task_desc)`：LLM 侧路调用判断任务与 Skill 的语义相似度
- `mewcode/agent.py` — 改造：在主循环每轮开始（`turn_start`/`pre_send` hook 位置，参考 `agent.py:441` `Agent.run()`）调用匹配器，命中 active Skill 则注入其内容到上下文
- `mewcode/harness/evolution/decision_loop.py` — 增强：成功路径调用 `match_candidates` 做晋升判定

**前置依赖**：任务 18

**参考资料定位**：
- 现有 `harness/evolution/problem_classifier.py:76` `ProblemClassifier.classify()` —— LLM 侧路调用 + 结构化输出的既有模式
- 现有 `agent.py:441` `Agent.run()` —— 每轮迭代触发 `turn_start`/`pre_send` hook 的位置，注入点在此之后、构建 system prompt 之前
- 现有 `app.py:1188` `_make_evolution_client()` —— 进化子系统轻量 LLM 客户端工厂，匹配器复用

**关键设计点**：
- 匹配为轻量 LLM 调用，设独立超时（具体值见 checklist，默认 8 秒）；超时/失败静默跳过，不阻塞主循环
- 注入语义：命中的 active Skill 内容以「可用经验」形式注入上下文，并附说明「由 Agent 自主判断是否采纳」——不强制执行
- Agent 二次校验：Agent 可在差异较大时拒绝采纳并按常规流程执行，拒绝不影响后续匹配
- 匹配范围：`match_active` 只扫描 `status=active` 的 Skill；`match_candidates` 只扫描 `status=candidate`

---

### 任务 20：命中后降本评估

**描述**：正式 Skill 被匹配采纳后，对比本次任务实际迭代数/token 与同类任务历史基线，判定是否降本以决定保留或降级。

**影响文件**：
- `mewcode/harness/evolution/evaluator.py` — 增强：`EvolutionEvaluator.evaluate()`（`:45`）新增成功型评估分支，输入为「命中采纳后的任务指标 + 历史基线」
- `mewcode/harness/evolution/skill_meta.py` — 增强：记录每次命中采纳的结果（降本/未降本/任务失败），累计命中失败超阈值自动降级废弃

**前置依赖**：任务 19

**参考资料定位**：
- 现有 `harness/evolution/evaluator.py:45` `EvolutionEvaluator.evaluate()` —— 失败型评估（成功率提升且 token 增幅 ≤15% 才 keep），成功型评估规则与之同构
- 现有 `harness/evolution/skill_meta.py:190` `check_deprecation_candidates()` —— 「60 任务未用即废弃」的既有淘汰机制

**关键设计点**：
- 降本判定阈值（具体值见 checklist）：迭代数降幅 ≥ 20% **且** token 增幅 ≤ 15% 才判为降本（token 规则与失败型一致）
- 历史基线：同类任务（未命中 Skill 时）最近 N 次的迭代数/token 均值（N 见 checklist，默认 5）；基线样本不足时不做降级，维持 active
- 命中被采纳但任务最终失败：记一次「命中失败」，累计超阈值（见 checklist，默认 3 次）自动降级废弃
- 评估结果写入进化记录，`path=success`

---

### 任务 21：接入主流程 + 进化决策循环

**描述**：将成功路径接入现有 `EvolutionDecisionLoop` 与 App 退出清理流程，使会话结束时自动跑「失败 + 成功」双路进化检查；任务开始时自动跑 Skill 注入匹配。

**影响文件**：
- `mewcode/harness/evolution/manager.py` — 改造：`check_and_evolve()`（`:113`）在失败路径后追加成功路径阶段；`_check_deprecations()`（`:150`）覆盖成功型 Skill 的降级/废弃
- `mewcode/harness/evolution/decision_loop.py` — 改造：`run()`（`:91`）新增成功路径阶段（与现有 6 阶段并列，不替换）
- `mewcode/app.py` — 改造：`_init_evolution`（`:1137`）实例化新增组件并注入；退出清理（`:2162-2169`）的 `check_and_evolve` 自动覆盖双路
- `mewcode/agent.py` — 改造：主循环注入任务 19 的匹配结果
- `mewcode/config.py` — 增强：新增成功经验相关配置项（开关、阈值，见 checklist）
- `mewcode/validator.py` — 增强：校验新增配置项

**前置依赖**：任务 16~20

**参考资料定位**：
- 现有 `app.py:1137` `_init_evolution()`、`:1157` EvolutionManager 创建、`:1168-1174` 进化工具注册、`:2162-2169` 退出清理调用 `check_and_evolve`
- 现有 `manager.py:113` `check_and_evolve()`、`:150` `_check_deprecations()`
- 现有 `decision_loop.py:91` `run()` 6 阶段结构
- 现有 `config.py:177` `allow_self_modification`、`validator.py:280` 校验模式 —— 成功经验开关复用 `allow_self_evolution`，不新增元权限

**关键设计点**：
- 成功路径整体受 `allow_self_evolution` 控制，关闭时完全不执行（不识别、不生成、不匹配、不注入）
- 成功路径与失败路径在同一 `check_and_evolve` 调用内顺序执行，互不依赖、互不查重
- 注入匹配（任务开始）与进化检查（会话结束）解耦：匹配在 Agent 主循环内即时进行，进化检查在退出时批量进行
- 单组件实例化失败不阻塞其他组件（沿用 `app.py:1071` 异常隔离模式）

---

### 任务 22：端到端验证

**描述**：覆盖成功经验路径全生命周期的端到端验证——识别、生成、晋升、匹配注入、降本评估、降级废弃。

**影响文件**：
- `tests/test_evolution_success.py` — 新建，测试：复杂成功识别、候选生成、复发晋升、命中注入、降本保留、命中失败降级
- `tests/test_evolution_matcher.py` — 新建，测试：候选/正式匹配范围、超时静默、Agent 二次校验拒绝路径
- `tests/test_evolution_e2e.py` — 新建，端到端：跑两轮同类复杂成功任务 → 验证候选生成与晋升 → 第三轮任务开始命中注入 → 验证降本保留

**前置依赖**：任务 21

**参考资料定位**：
- 现有 `harness/evolution/` 各组件公开 API（任务 16~20 产出）
- 现有项目测试目录结构、`pyproject.toml` 的 pytest 配置

**关键验证场景**（详见 checklist.md「成功经验驱动的自进化」节）：
1. 简单任务（迭代数 < 阈值）成功 → 不生成候选
2. 复杂任务成功 → 生成候选；同类复发 → 晋升正式
3. 正式 Skill 在任务开始被命中注入；Agent 拒绝采纳时常规流程不受影响
4. 命中采纳后降本 → 维持正式；命中失败累计 → 降级废弃
5. `allow_self_evolution=false` 时成功路径完全不触发
