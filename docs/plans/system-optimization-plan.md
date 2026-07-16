# MewCode 系统优化与完善计划

> 状态：Draft  
> 适用版本：0.2.0（基线提交 `b579939`）  
> 目标：先把现有能力收敛为稳定、可验证、可维护的 Agent Runtime，再扩展高级能力。

## 1. 背景与结论

MewCode 已具备多模型协议、工具调用、MCP、上下文压缩、Skill、子 Agent、Agent Team、Workflow、Scheduler、Harness 和实验性自进化等能力。当前主要矛盾已经不是“功能不足”，而是功能增长速度超过了架构、测试和运行保障的完善速度。

本轮优化遵循以下顺序：

1. 建立可信基线：测试可重复、关键路径有覆盖、错误可诊断。
2. 收敛运行内核：交互和非交互共用同一套装配与 Agent 循环。
3. 加固安全与持久化：工具执行、后台任务和状态文件具有清晰契约。
4. 建立可观测和评测体系：用数据判断上下文、多 Agent 和自进化是否有效。
5. 最后产品化：安装、初始化、诊断、文档和发布流程完整。

## 2. 当前基线

### 2.1 已确认的结构性问题

| 编号 | 问题 | 影响 | 优先级 |
|---|---|---|---|
| B1 | `MewCodeApp` 同时承担 UI、依赖装配、后台任务和业务协调，文件超过 2,000 行 | 修改任一子系统都可能影响 UI 生命周期 | P1 |
| B2 | TUI 与 `-p` 分别手工装配 Runtime | 非交互模式缺少 MCP、Skill、Workflow、Scheduler、Harness、完整会话与记忆能力 | P0 |
| B3 | `Agent.run()` 与 `run_to_completion()` 分别实现 Agent 循环 | 权限、Hook、限流、指标、压缩行为容易漂移 | P0 |
| B4 | Harness/Evolution 配置通过构造后写入 `app._xxx` 私有属性 | 依赖不显式，漏注入时只能在运行期发现 | P1 |
| B5 | `_merge_config()` 未完整合并 Compact、Critic、RateLimit、Evolution 等新增字段 | 多层配置覆盖结果与文档不一致 | P0 |
| B6 | Workflow、Scheduler、Harness、Evolution 缺少对应测试模块 | 新能力无法证明稳定，也无法安全重构 | P0 |
| B7 | 多处裸 `except Exception`、静默 `pass` 和未集中管理的 `create_task()` | 故障可能被隐藏，退出时可能遗留后台任务 | P0 |
| B8 | 文件型持久化分散在多个模块，缺少统一版本与原子写契约 | 崩溃、并发写入或格式升级时存在损坏风险 | P1 |
| B9 | 自进化已有生成、评估和回滚代码，但缺少稳定 Eval 基线 | 无法客观判断“改进”是否真实有效 | P2 |

### 2.2 本轮非目标

- 不重写为 LangChain、LangGraph 或其他 Agent 框架。
- 不引入数据库、消息队列、微服务等当前规模不需要的基础设施。
- 不在稳定性和评测闭环完成前扩大自修改范围。
- 不同时重做 TUI 视觉设计；本计划只调整 UI 与 Runtime 的边界。
- 不追求一次性“大重构”，所有阶段均以可独立合并、可回滚的小 PR 实施。

## 3. 目标架构

```text
CLI / Textual TUI
        │
        ▼
RuntimeBuilder ── 解析配置、创建依赖、统一交互/非交互装配
        │
        ▼
AppRuntime ────── 会话生命周期、后台任务、MCP、Scheduler、关闭流程
        │
        ├── AgentLoop ───── 唯一的模型→工具→结果→模型循环
        │       ├── Conversation / Context
        │       ├── LLMClient
        │       └── ToolExecutionPipeline
        │               ├── Hook
        │               ├── RateLimit
        │               ├── Permission / Sandbox
        │               ├── Validation
        │               ├── Execute
        │               └── Audit / Metrics
        │
        ├── ExtensionRegistry
        │       ├── Built-in Tools
        │       ├── MCP
        │       ├── Skills
        │       ├── Sub-Agent / Team
        │       └── Workflow
        │
        ├── TaskSupervisor ─ 后台任务创建、取消、等待、异常上报
        └── StateStore ───── 会话、Journal、审计、指标的文件持久化契约
```

关键约束：

- TUI 只负责输入输出和用户交互，不创建业务子系统。
- 交互和非交互入口都调用同一个 `RuntimeBuilder`。
- 系统只能存在一个 Agent 循环实现；不同使用方式通过事件消费者适配。
- 所有后台任务必须由 `TaskSupervisor` 持有，禁止散落的 fire-and-forget。
- 所有工具调用必须经过同一条执行管线，不能由不同入口绕过安全层。
- 所有持久化格式包含 `schema_version`，关键写入采用临时文件加原子替换。

## 4. 分阶段实施计划

## Phase 0：建立可信基线

建议投入：3～5 人日。

### 工作项

1. 固化开发和测试环境
   - 增加项目级 pytest 配置，显式设置 asyncio 模式、超时和测试标记。
   - 增加 Linux 与 Windows CI，使用锁文件安装依赖。
   - 把需要真实网络、终端后端或长时间运行的测试标记为 integration/slow。
   - 记录当前测试数量、耗时、失败用例和环境依赖。

2. 修复现有测试
   - 逐个定位当前失败用例，区分代码回归、平台差异和测试隔离问题。
   - 禁止通过跳过失败测试实现“全绿”。
   - 清理测试留下的任务、临时目录、环境变量和全局状态。

3. 补齐新增模块的最小测试集
   - `test_workflow_engine.py`：串行、并行、预算、结构化输出。
   - `test_workflow_resume.py`：Journal、缓存命中和中断恢复。
   - `test_scheduler.py`：Cron 解析、存储、触发和关闭。
   - `test_context_critic.py`：Critic 开关和建议注入。
   - `test_permissions_audit.py`：审计写入、查询和轮转。
   - `test_rate_limit.py`：窗口计数与超限拒绝。
   - `test_harness_tools.py`：元权限与配置修改。
   - `test_evolution.py`：生成失败、评估拒绝和回滚路径。

4. 修正配置合并
   - 为每个配置字段定义明确的“替换/追加/按名称合并”语义。
   - 修复 Harness 和 Evolution 字段未进入 `_merge_config()` 的问题。
   - 增加三层配置覆盖的参数化测试。

5. 建立静态质量门禁
   - 引入 Ruff，仅启用明确且低争议的规则。
   - 第一阶段不强制全量 mypy；先为新增 Runtime 接口补类型。

### 验收标准

- Linux、Windows CI 均能从干净环境运行。
- 单元测试全绿且无未回收 asyncio task 警告。
- Workflow、Scheduler、Harness、Evolution 至少覆盖成功路径和一个失败路径。
- 配置三层覆盖行为由测试固定。
- `git status` 在测试后保持干净。

## Phase 1：统一 Runtime 与 Agent 循环

建议投入：7～10 人日。

### 工作项

1. 引入显式 Runtime 配置
   - 将 Compact、Critic、RateLimit、Evolution 等作为构造参数传递。
   - 删除 `__main__.py` 对 `app._compact_config` 等私有属性的事后注入。
   - 配置对象在装配完成后只读，运行时修改通过专门服务完成。

2. 提取 `RuntimeBuilder`
   - 统一创建 Client、Conversation、PermissionChecker、Registry、MCP、Skill、Worktree、Team、Workflow、Scheduler 和 Harness。
   - TUI 和 `-p` 只决定事件如何展示、是否允许交互审批，不决定装配哪些能力。
   - 为确实不适合非交互模式的能力定义显式 capability flag，并给出清晰错误。

3. 合并 Agent 双循环
   - 提取唯一 `AgentLoop.execute()` 异步事件流。
   - TUI 消费事件并渲染；`run_to_completion()` 只作为收集事件的薄适配器。
   - 权限、Hook、限流、审计、指标、压缩和未知工具保护只实现一次。

4. 提取工具执行管线
   - 按固定顺序执行：查找 → 启用检查 → Hook → 限流 → 权限 → 参数校验 → 工具执行 → 快照 → 审计/指标。
   - 交互审批通过回调接口注入，非交互模式使用拒绝或明确授权策略。
   - 不在管线内部静默吞异常。

5. 缩减 `MewCodeApp`
   - 保留 Textual Widget、事件绑定、渲染和用户审批。
   - 将服务初始化、会话控制和后台任务管理迁入 Runtime。
   - 第一阶段目标不是追求文件行数，而是移除业务依赖创建代码。

### 验收标准

- 同一输入、同一 mock 模型下，TUI 与 `-p` 的工具、Hook、权限和上下文行为一致。
- `Agent.run_to_completion()` 不再包含独立工具循环。
- `MewCodeApp` 不直接实例化 Workflow、Scheduler、Harness、TeamManager。
- 原有 Agent、权限、上下文、MCP、Skill 和团队测试无需大规模重写即可通过。

## Phase 2：生命周期、安全与持久化加固

建议投入：5～8 人日。

### 工作项

1. 集中管理后台任务
   - 新增 `TaskSupervisor`，统一创建、命名、取消和等待 asyncio task。
   - 未处理异常写 error 日志并关联任务名、会话 ID、Agent ID。
   - 关闭顺序固定为：停止接收输入 → 取消 Agent → 停 Scheduler → 停 MCP/Team → 刷盘 → 关闭 Hook。

2. 整理错误边界
   - 内部配置、状态和数据契约违反时 fail-fast。
   - 仅在第三方 API、可选 Hook、MCP 单服务器等系统边界允许降级。
   - 清理无日志的 `except Exception: pass`，保留降级处必须记录上下文。

3. 加固工具安全
   - 为文件工具统一使用解析后的绝对路径并重新校验工作区边界。
   - 为命令工具增加超时、取消、输出上限和进程树清理测试。
   - 明确 Plan、Default、Accept-edits、Bypass 对每类工具的决策表。
   - MCP 工具同样经过权限、限流、审计和超时管线。

4. 统一文件持久化契约
   - 为 Session、Workflow Journal、Cron、Audit、Metrics、Team 状态添加版本字段。
   - JSON/YAML 快照使用原子写；JSONL 使用 append、flush 和损坏尾行恢复。
   - 引入单进程文件锁或明确禁止同文件多写者，避免伪并发安全。
   - 增加格式迁移入口，不在读取失败时返回空状态掩盖损坏。

5. Worktree 与文件历史联动
   - 明确文件快照属于主工作区还是当前 Worktree。
   - 删除 Worktree 前检查未提交和未推送修改。
   - 将恢复、清理和失败回滚做成集成测试。

### 验收标准

- 正常退出、Ctrl+C、模型调用取消三种路径均无遗留子进程和后台 task。
- 所有允许降级的异常都有 error 日志和上下文。
- 状态文件写入中断后能够恢复，无法恢复时给出明确错误而非空状态。
- 路径逃逸、危险命令、MCP 超时和 Worktree 未提交变更均有自动化测试。

## Phase 3：可观测性与 Eval 体系

建议投入：5～7 人日。

### 工作项

1. 统一事件与 Trace 模型
   - 定义 RuntimeEvent：LLM 请求、工具调用、权限决策、Agent 委派、Workflow 阶段、调度触发。
   - 每次执行携带 session_id、run_id、agent_id、parent_agent_id 和 tool_call_id。
   - TUI、JSONL Trace 和指标从同一事件源消费。

2. 定义核心指标
   - 任务成功率、平均轮次、总 Token、缓存命中、压缩次数。
   - 工具成功率、P50/P95 延迟、权限拒绝率、未知工具率。
   - 子 Agent/Workflow 的并发收益与失败传播。
   - 上下文压缩后的恢复成功率。

3. 建立离线 Eval 数据集
   - 先收录 20～30 个可重复任务：代码探索、单文件修改、多文件修改、测试修复、受限操作、长上下文。
   - 使用固定 mock/replay 验证执行逻辑，使用真实模型的小样本验证端到端质量。
   - 保存期望文件差异、必须调用/禁止调用的工具和最大预算。

4. 为架构能力建立基准
   - Deferred Tool：比较 Schema Token 节省量和工具发现成功率。
   - Context Compact：比较压缩前后 Token、任务完成率和关键信息保留率。
   - Multi-Agent：比较串行与并行耗时、Token 和冲突率。
   - Workflow Resume：比较中断恢复后的重复调用数。

### 验收标准

- 任意一次 Agent 执行可以从 Trace 还原关键调用链。
- CI 中运行确定性 Eval；真实模型 Eval 可手动或定时运行。
- README 中的性能或效果结论均有可复现脚本和原始结果。
- 回归阈值明确，例如成功率下降或 Token 增长超过阈值时阻止合并。

## Phase 4：高级能力收敛

建议投入：6～10 人日。

### 工作项

1. 统一子 Agent、Team 和 Workflow 的执行上下文
   - 共用 AgentFactory、预算、Trace、取消令牌和事件模型。
   - 不强行合并三个领域概念：Sub-Agent 负责委派，Team 负责协作，Workflow 负责确定性编排。
   - 删除重复的 Client、PermissionChecker 和工具注册构造逻辑。

2. 完善 Workflow
   - 增加可取消、超时、失败策略和结构化错误。
   - 对 Python Workflow 的加载边界进行安全说明；默认只执行用户明确创建的本地 Workflow。
   - TUI 先提供只读进度视图，不引入拖拽编辑器。

3. 完善 Scheduler
   - 调度触发生成独立 Run，而不是只向当前对话插入提醒。
   - 明确进程关闭期间错过任务的补偿策略。
   - 如果未来需要无人值守，再拆独立 daemon；当前阶段保持单进程。

4. 限制性开放自进化
   - 自进化只能修改约定的 Skill 目录，不能直接修改 Runtime 核心代码。
   - 所有候选变更先在隔离目录生成，再运行 Eval。
   - 只有指标超过基线且无安全回归时才接受，否则自动回滚。
   - 保留人工审核开关和完整变更差异。

### 验收标准

- 子 Agent、Team、Workflow 共用相同的预算、Trace 和取消语义。
- Scheduler 任务可以跨会话独立追踪执行结果。
- 自进化的每次接受或拒绝都有输入轨迹、Diff、评测结果和回滚记录。
- 自进化无法越过允许修改的目录边界。

## Phase 5：产品化与发布

建议投入：3～5 人日。

### 工作项

1. CLI 完善
   - `mewcode init`：生成最小配置和 `.gitignore` 建议。
   - `mewcode doctor`：检查 Python、API Key、Git、MCP 和终端后端。
   - `mewcode run -p`：统一非交互命令语义和退出码。
   - `mewcode version`：只从包元数据读取版本，消除 UI 硬编码版本。

2. 发布工程
   - 建立语义化版本、Changelog、构建和发布 CI。
   - 产出 Wheel，并验证全新虚拟环境安装。
   - 提供配置 Schema、示例配置和升级说明。

3. 项目展示
   - README 首页保留核心价值、快速开始和架构图。
   - 增加 1～2 分钟演示：安全编辑、长上下文、多 Agent/Worktree。
   - 提供 Eval 报告，而不是只罗列功能。

### 验收标准

- 新用户从安装到第一次成功对话不超过 5 分钟。
- `doctor` 能定位缺少配置、无效 Key、Git/MCP 环境异常。
- 发布包、README 版本和程序显示版本一致。
- 至少提供一份可复现的端到端演示和一份 Eval 报告。

## 5. 推荐 PR 顺序

每个 PR 只处理一个可验证主题：

1. `test: stabilize suite and CI baseline`
2. `test: cover workflow scheduler and harness`
3. `fix: complete layered config merge`
4. `refactor: introduce runtime builder`
5. `refactor: unify agent execution loop`
6. `refactor: centralize tool execution pipeline`
7. `refactor: supervise background tasks and shutdown`
8. `fix: version and atomically persist runtime state`
9. `feat: unify runtime events and traces`
10. `test: add deterministic agent eval suite`
11. `refactor: share execution context across agent orchestration`
12. `feat: gate skill evolution with eval and directory sandbox`
13. `feat: add init doctor and release workflow`

合并原则：前一个 PR 的测试和验收未通过，不启动依赖它的后续重构；禁止把 Runtime 重构、UI 改版和功能新增混在同一 PR。

## 6. 成功指标

| 维度 | 建议指标 |
|---|---|
| 正确性 | 单元测试全绿；确定性 Eval 成功率 100% |
| 稳定性 | 关键端到端任务成功率不低于 90% |
| 测试 | 核心 Runtime/权限/上下文/持久化分支覆盖率不低于 80% |
| 性能 | 无工具首 Token 延迟不因重构显著回退；回退阈值 10% |
| 上下文 | 压缩后关键事实保留率不低于基线，平均输入 Token 有可测下降 |
| 工具 | 未知工具率、参数校验失败率和工具异常率均可观测 |
| 生命周期 | 测试与正常退出后无遗留 task、MCP 进程和 Worktree |
| 安全 | 路径逃逸、危险命令、越权自修改测试全部通过 |
| 可维护性 | TUI 不再负责服务构造；Agent 循环和工具执行管线各只有一个实现 |

指标用于发现回归，不应为了数字而大量编写低价值测试。覆盖率只对核心模块设置门槛，UI 展示代码以关键交互测试为主。

## 7. 风险与控制

| 风险 | 控制措施 |
|---|---|
| Runtime 重构引起行为变化 | 先补 characterization tests，再逐步迁移装配代码 |
| 双循环合并破坏 TUI 流式体验 | 以现有 AgentEvent 为兼容边界，先做适配器再删除旧循环 |
| 多平台终端测试不稳定 | 单元测试使用 fake backend，真实 tmux/iTerm2 放入平台集成测试 |
| 文件格式升级损坏用户数据 | 原子写、版本字段、迁移前备份、损坏时 fail-fast |
| Eval 受模型随机性影响 | 确定性 replay 作为 CI 门禁，真实模型 Eval 只做趋势判断 |
| 自进化产生危险修改 | 限定目录、隔离生成、评测门禁、人工审核和自动回滚 |

## 8. 首个迭代建议

首个迭代只做 Phase 0，不开始架构重构。建议交付物为：

1. 一份可重复的测试基线报告。
2. 修复后的全绿测试集。
3. Workflow、Scheduler、Harness、Evolution 的最小关键测试。
4. 完整的配置覆盖测试与修复。
5. Linux/Windows CI 和 Ruff 门禁。

完成这些以后，才能用测试保护 RuntimeBuilder 和 AgentLoop 的重构。首个迭代结束时重新评估 Phase 1 的拆分，不提前为 Phase 4/5 编写抽象。
