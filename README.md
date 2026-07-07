# MewCode Coding-Agent

轻量级终端 Coding Agent，基于 **ReAct** 与 **Plan Mode** 双模式驱动 LLM 自主完成编程任务。采用交互、引擎、工具、记忆、安全五层分层架构，引入 **Loop Engineering + Harness Engineering** 双工程体系；兼容 **Anthropic**、**OpenAI** 双协议，支持 MCP 工具扩展、Skill 技能包、跨会话记忆、多 Agent 并行协作。

## 核心特性

- **🔄 ReAct + Plan Mode 双模式** — 默认 ReAct 推理-行动循环，支持 Plan Mode 先规划再执行，灵活应对不同复杂度任务
- **🏗️ 五层分层架构** — 交互层（TUI）、引擎层（Agent 主循环）、工具层（Tool Registry）、记忆层（Memory）、安全层（Permissions），职责清晰、可扩展
- **🔁 Loop Engineering** — Workflow Engine 工作流编排引擎，支持 phase-based 分阶段执行、Journal 断点恢复、预算控制循环（loop_until_budget）和 Cron 定时调度系统
- **⚙️ Harness Engineering** — 标准化运行基座，提供完整性校验（CompletenessCritic）、审计日志（AuditLogger）、速率限制（RateLimiter）、指标收集（MetricsCollector）四大增强组件
- **🤖 Multi-Agent 协作** — 内置子 Agent 分发（Fork / SubAgent）、Team 团队协作（Coordinator 模式），支持多 Agent 并行执行
- ** MCP 协议扩展** — 兼容 Model Context Protocol，可对接外部 MCP Server 动态扩展工具能力
- **📦 Skill 技能包** — 内置 commit、review、test 等技能，支持自定义 Skill 扩展，一键激活
- **🧠 跨会话记忆** — 自动提取与持久化长期记忆（用户偏好/纠正反馈/项目知识/参考资料），支持会话摘要与恢复（compact），跨会话知识复用
- **🛡️ 七层权限拦截** — Plan 模式例外 → 只读命令放行 → 危险命令检测 → 路径沙箱 → 规则引擎 → 权限模式 → 人工确认，保障 Agent 全自动安全运行
- ** Hook 钩子系统** — 支持 session / turn / tool 生命周期 Hook，可自定义前置/后置行为
- **🌲 Worktree 隔离** — 基于 Git worktree 的任务隔离执行，避免影响主工作区
- **💬 终端 TUI 界面** — 基于 Textual 的现代化终端 UI，支持流式输出、文件引用（@）、命令补全

## 架构设计

```
┌─────────────────────────────────────────────────┐
│                   交互层 (TUI)                   │
│         ChatInput / ToolCallBlock / Stream      │
├─────────────────────────────────────────────────┤
│                  引擎层 (Agent)                  │
│     ReAct Loop / Plan Mode / Context Compact    │
│     Loop Engineering: Workflow/Cron/Scheduler   │
├─────────────────────────────────────────────────┤
│                  工具层 (Tools)                  │
│   ReadFile / WriteFile / Bash / MCP / Skill / … │
│   ToolRegistry 延迟加载机制                       │
├─────────────────────────────────────────────────┤
│                 记忆层 (Memory)                  │
│    Session / Auto Memory / Instructions / Hook  │
│    JSONL 持久化 + RecoveryState 快照              │
├─────────────────────────────────────────────────┤
│                 安全层 (Permissions)             │
│   七层拦截 / Sandbox / Rule Engine / Detector    │ 
│   Harness: Audit/RateLimit/Metrics/Critic       │
└─────────────────────────────────────────────────┘
```

### ReAct 主循环

Agent 采用标准 ReAct（Reasoning + Acting）循环：

1. **Think** — LLM 推理当前上下文，决定下一步行动
2. **Act** — 调用工具执行操作（读文件、写代码、运行命令等）
3. **Observe** — 收集工具执行结果
4. **Loop** — 将观察结果输入 LLM，继续推理，直至任务完成或达到最大轮次

期间自动触发：**权限检查（7 层）** → **Hook 回调** → **上下文压缩（双层渐进式）** → **记忆提取**。

### Plan Mode 规划模式

开启 Plan Mode 后，Agent 先制定详细计划（保存为 Markdown 文件），经用户确认后再逐步执行，适合复杂、高风险任务。

### Loop Engineering - 循环工程体系

- **WorkflowEngine**：phase-based 工作流编排引擎，支持 Journal 断点恢复、预算追踪（BudgetInfo）
- **循环控制**：`loop_until_count()` / `loop_until_budget()` 提供带干跑保护（dry_protection=3）的循环模式
- **调度系统**：CronStore + SchedulerRuntime + WakeupScheduler 支持 Cron 表达式定时任务与唤醒调度
- **执行追踪**：AgentCallRecord 记录每次调用的完整信息（prompt_hash、opts_hash、状态、耗时、token 用量）

### Harness Engineering - 标准化运行基座

- **四大增强组件**：
  - CompletenessCritic：完整性审查器
  - AuditLogger：Session 级别审计日志（JSONL 格式）
  - RateLimiter：单工具粒度限流（默认 30 次/分钟，per-tool 可配置）
  - MetricsCollector：全链路指标采集（token 用量、执行时长等）
  
- **三大运行时管理器**：
  - HookManager：管理生命周期 Hook（session/turn/tool）
  - ConfigManager：动态配置管理
  - PermissionManager：运行时权限规则管理
  
- **Harness 工具集**：暴露 AddHook/RemoveHook/ListHooks/UpdateConfig/AddPermissionRule/RemovePermissionRule/ManageMemory 等工具供 Agent 自主调整运行规则

### 双层渐进式上下文压缩

- **Layer 1 - 工具结果落盘**：单条 >50KB 或聚合 >200KB 时自动持久化至 `.mewcode/session/tool-results/`，替换为预览 + 文件路径
- **ContentReplacementState**：决策冻结机制保证 prompt cache 一致性，支持 fork 子 agent 继承父 agent 替换状态
- **Layer 2 - 全对话摘要**：触发阈值（context_window - 13K safety margin）时调用 LLM 生成结构化摘要
- **保留策略**：尾部 10K tokens / 5 条消息原文保留，通过 `_align_keep_start_to_tool_pair()` 确保 tool_use↔tool_result 配对不被拆散
- **RecoveryState**：压缩时保留最近读取的文件内容（最多 5 个，每文件 5K tokens）和激活的 Skill SOP（总预算 25K tokens）

### 七层权限拦截模型

1. **Layer 0**：Plan 模式例外放行（允许 Agent/ToolSearch/AskUserQuestion/ExitPlanMode）
2. **Layer 1**：安全的只读命令自动放行
3. **Layer 1b**：危险命令黑名单检测（DangerousCommandDetector）
4. **Layer 2**：路径沙箱检查（PathSandbox，限定工作目录 + 临时目录）
5. **Layer 3**：规则引擎匹配（RuleEngine，支持 user/project/local 三级 YAML 配置）
6. **Layer 4**：权限模式兜底判定（default/acceptEdits/plan/bypass）
7. **Layer 5**：触发人工确认（HITL）

任一环节返回 DENY 即终止操作，保障 Agent 全自动安全运行。

## 快速开始

### 环境要求

- Python >= 3.11
- 推荐使用 [uv](https://docs.astral.sh/uv/) 管理依赖

### 安装

```bash
# 克隆仓库
git clone <repo-url> && cd MewCode-Agent

# 创建虚拟环境
uv venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/macOS

# 安装依赖
uv pip install -e .
```

### 配置 API Key

```bash
# Anthropic
set ANTHROPIC_API_KEY=your-key-here       # Windows
export ANTHROPIC_API_KEY=your-key-here    # Linux/macOS

# OpenAI
set OPENAI_API_KEY=your-key-here
export OPENAI_API_KEY=your-key-here
```

### 创建配置文件

在项目目录下创建 `.mewcode/config.yaml`（或全局 `~/.mewcode/config.yaml`）：

```yaml
providers:
  - name: claude
    protocol: anthropic
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-5-20250929
    api_key: ${ANTHROPIC_API_KEY}
    thinking: true

permission_mode: default

mcp_servers: []

enable_fork: false
enable_verification_agent: false
teammate_mode: ""
enable_coordinator_mode: false

hooks: []

worktree:
  symlink_directories:
    - node_modules
    - .venv
  stale_cleanup_interval: 3600
  stale_cutoff_hours: 24
```

### Harness Engineering 配置

```yaml
compact: 
    utilization_threshold: 0.85 
    min_keep_messages: 3
critic: 
    enabled: false
rate_limit: 
    enabled: true 
    default_max_per_minute: 30 
    per_tool: 
      Bash: 10 
    WriteFile: 20
allow_self_modification: false
```


### 启动

```bash
mewcode
```

### 命令行模式

```bash
mewcode -p "帮我写一个 Python 的快速排序函数"
```

## 权限模式

| 模式 | 读取 | 写入 | 命令 | 说明 |
|------|------|------|------|------|
| `default` | ✓ 允许 | 🔔 询问 | 🔔 询问 | 默认安全模式 |
| `acceptEdits` | ✓ 允许 | ✓ 允许 | 🔔 询问 | 自动接受文件编辑 |
| `plan` | ✓ 允许 | 🔔 询问 | 🔔 询问 | 规划模式 |
| `bypass` | ✓ 允许 | ✓ 允许 | ✓ 允许 | 跳过所有权限检查 |

使用 `Shift+Tab` 在 TUI 中快速切换权限模式。

## 内置工具

| 工具 | 功能 |
|------|------|
| `ReadFile` | 读取文件内容 |
| `WriteFile` | 创建或覆写文件 |
| `EditFile` | 精确编辑文件（search/replace） |
| `Bash` | 执行 Shell 命令 |
| `Glob` | 文件模式匹配搜索 |
| `Grep` | 正则表达式内容搜索 |
| `Agent` | 派发子 Agent 任务（支持同步/异步/Git Worktree 隔离三种模式） |
| `TeamCreate` / `TeamDelete` | 创建/删除 Agent 团队 |
| `TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate` | 任务管理 |
| `LoadSkill` | 激活 Skill 技能包 |
| `ToolSearch` | 延迟加载工具搜索（按需拉取 schema） |
| `AskUser` | 向用户提问 |
| `EnterWorktree` / `ExitWorktree` | 工作树隔离 |
| `ExitPlanMode` | 退出规划模式 |
| `SyntheticOutput` | 协调者模式结构化输出 |
| `SendMessage` | 团队内消息通信 |

### Harness 工具（需 allow_self_modification=true）

| 工具 | 功能 |
|------|------|
| `AddHook` | 添加生命周期 Hook |
| `RemoveHook` | 删除 Hook |
| `ListHooks` | 列出已注册的 Hook |
| `UpdateConfig` | 更新运行时配置 |
| `AddPermissionRule` | 添加权限规则 |
| `RemovePermissionRule` | 删除权限规则 |
| `ManageMemory` | 管理长期记忆 |

## 内置 Skill 技能包

| 技能 | 说明 |
|------|------|
| `commit` | 分析 git diff 并生成规范 commit |
| `review` | 多维度代码审查（逻辑/安全/性能/风格/可维护性） |
| `test` | 自动生成测试用例 |
| `backend-interview` | 后端面试知识问答 |

Skills 存放在 `~/.mewcode/skills/` 和 `<project>/.mewcode/skills/`，支持自定义扩展。

## 内置子 Agent

| Agent 类型 | 说明 |
|------------|------|
| `general-purpose` | 通用子 Agent，拥有全部工具 |
| `explore` | 代码探索 Agent，专注搜索和理解代码 |
| `plan` | 规划 Agent，专注制定执行计划 |
| `verification` | 验证 Agent，专注审查和验证代码（需 enable_verification_agent=true） |

Agent 定义存放在 `~/.mewcode/agents/` 和 `<project>/.mewcode/agents/`，支持热重载。

## MCP 工具扩展

配置 MCP Server 自动接入外部工具：



```yaml
mcp_servers:
  - name: filesystem
    command: npx
    args:
      - -y
      - @modelcontextprotocol/server-filesystem
      - /path/to/allowed/dir
  - name: github
    command: npx
    args:
      - -y
      - @modelcontextprotocol/server-github
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: ${GITHUB_TOKEN}
```

## Hook 钩子系统

在配置中定义 Hook，拦截 Agent 生命周期事件：

```yaml
hooks:
  - id: lint-on-write
    event: post_tool_use
    tool_name: WriteFile
    condition: "file_path.endswith('.py')"
    command: ruff check $FILE_PATH
  - id: format-on-write
    event: post_tool_use
    tool_name: WriteFile
    condition: "file_path.endswith('.py')"
    command: ruff format $FILE_PATH
  - id: notify-on-error
    event: error
    action_type: http
    url: https://hooks.example.com/alert
    method: POST
    body: '{"error": "$ERROR", "tool": "$TOOL_NAME"}'
```

支持的变量占位符：`$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR`、`$TOOL_ARGS.<key>`

## TUI 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 |
| `Shift+Enter` / `Ctrl+J` | 换行 |
| `Tab` | 命令/文件补全 |
| `Shift+Tab` | 切换权限模式 |
| `Ctrl+O` | 展开/折叠工具调用详情 |
| `Ctrl+C` | 退出 |
| `Escape` | 取消当前操作 |
| `@` | 引用文件（自动补全路径） |
| `/` | 输入斜杠命令 |

## 项目结构

```
MewCode Coding-Agent/
├── mewcode/                          # 项目核心源码目录
│   ├── agent.py                      # Agent 主循环实现，支持 ReAct / Plan Mode 双推理模式
│   ├── app.py                        # Textual TUI 终端交互应用入口
│   ├── client.py                     # LLM 统一客户端，兼容 Anthropic / OpenAI 双协议
│   ├── config.py                     # 配置加载、多源配置合并逻辑
│   ├── conversation.py               # 对话消息生命周期管理
│   ├── prompts.py                    # 系统提示词动态构建模块
│   ├── agents/                       # 子 Agent 多智能体核心模块
│   │   ├── builtins/                 # 内置预置 Agent 定义文件
│   │   ├── loader.py                 # Agent 加载器，三级优先级：项目 > 用户 > 内置
│   │   ├── task_manager.py           # 多子任务调度管理器
│   │   ├── tool_filter.py            # Agent 工具权限过滤器
│   │   └── metrics.py                # MetricsCollector 全链路指标收集器
│   ├── commands/                     # 斜杠交互命令系统
│   │   └── handlers/                 # 各类内置命令处理器实现
│   ├── context/                      # 上下文窗口管理与压缩模块
│   │   ├── manager.py                # 双层渐进式上下文压缩、状态冻结管理
│   │   └── critic.py                 # CompletenessCritic 会话完整性审查器
│   ├── hooks/                        # 全局 Hook 生命周期钩子系统
│   │   ├── engine.py                 # Hook 执行调度引擎
│   │   ├── models.py                 # Hook/Action/HookContext 数据模型定义
│   │   ├── conditions.py             # 钩子触发条件表达式解析器
│   │   └── executors.py              # 钩子动作执行器（命令/HTTP/提示词/子Agent）
│   ├── mcp/                          # MCP 工具协议扩展支持
│   │   ├── client.py                 # MCP 服务客户端
│   │   ├── manager.py                # MCP 服务生命周期管理器
│   │   └── tool_wrapper.py           # MCP 工具标准化包装转换层
│   ├── memory/                       # 跨会话持久记忆系统
│   │   ├── auto_memory.py            # 异步记忆提取，自动分类四类业务记忆
│   │   ├── session.py                # JSONL 会话持久化、压缩边界控制
│   │   └── recall.py                 # 历史记忆检索召回逻辑
│   ├── permissions/                  # 权限安全沙箱体系
│   │   ├── checker.py                # 串联式多级权限校验器
│   │   ├── dangerous.py              # 高危命令识别检测模块
│   │   ├── modes.py                  # 权限模式矩阵定义
│   │   ├── rules.py                  # 三级YAML自定义规则引擎
│   │   ├── sandbox.py                # 文件路径沙箱，限定工作目录访问范围
│   │   ├── audit.py                  # AuditLogger 会话级操作审计日志
│   │   └── rate_limit.py             # RateLimiter 单工具粒度限流控制器
│   ├── skills/                       # Skill 自定义技能包体系
│   │   ├── builtins/                 # 官方内置技能集合
│   │   ├── loader.py                 # 技能动态加载器
│   │   └── executor.py               # 技能执行调度器
│   ├── teams/                        # 多Agent团队并行协作模块
│   │   ├── coordinator.py            # Coordinator 调度主Agent，负责任务拆分与结果汇总
│   │   ├── mailbox.py                # Agent 间消息队列通信模型
│   │   ├── manager.py                # 多团队生命周期管理器
│   │   ├── spawn_tmux.py             # 启动独立 tmux 终端面板 Worker
│   │   ├── spawn_iterm2.py           # 启动独立 iTerm2 终端面板 Worker
│   │   └── spawn_inprocess.py        # 进程内轻量 Worker 启动逻辑
│   ├── tools/                        # 工具注册表与工具实现层
│   │   ├── base.py                   # Tool 基类、LLM 流式输出事件标准定义
│   │   ├── agent_tool.py             # Agent 内置工具，同步/异步/Git隔离三种执行模式
│   │   └── impl/                     # 各类业务工具具体实现
│   ├── workflow/                     # Loop Engineering 工作流循环引擎
│   │   ├── engine.py                 # WorkflowEngine 分阶段闭环引擎
│   │   ├── patterns.py               # 通用循环模板（次数限制/预算限制循环）
│   │   ├── journal.py                # Journal 会话断点持久化与恢复
│   │   └── models.py                 # 工作流、预算、调用记录数据模型
│   ├── scheduler/                    # Cron 定时调度系统
│   │   ├── runtime.py                # 调度运行时内核
│   │   ├── store.py                  # Cron 任务持久化存储
│   │   ├── wakeup.py                 # 休眠唤醒调度器
│   │   └── tools.py                  # 定时任务配套管理工具
│   ├── harness/                      # Harness Engineering 标准化运行基座
│   │   ├── hook_manager.py           # HookManager 钩子运行时管理
│   │   ├── config_manager.py         # ConfigManager 动态配置管理
│   │   ├── permission_manager.py     # PermissionManager 权限运行时管控
│   │   └── tools.py                  # Harness 内置自调控工具集（共7类）
│   └── worktree/                     # Git Worktree 代码隔离沙箱
│       ├── manager.py                # Worktree 生命周期管理器
│       ├── integration.py            # Worktree 与Agent执行集成逻辑
│       └── cleanup.py                # 过期临时分支自动清理
├── tests/                            # 单元测试、集成测试目录
├── pyproject.toml                    # Python 项目依赖、打包配置
└── README.md                         # 项目说明文档
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python >= 3.11 |
| TUI 框架 | Textual >= 2.1.0 |
| LLM API | Anthropic >= 0.42.0 / OpenAI >= 1.60.0 |
| 数据验证 | Pydantic >= 2.0 |
| MCP 协议 | mcp >= 1.12.0 |
| 配置解析 | PyYAML >= 6.0 |
| HTTP 客户端 | HTTPX >= 0.27.0 |
| 构建工具 | Hatchling |
| 测试框架 | pytest >= 9.0.3 |

## 开发

```bash
# 安装开发依赖
uv sync

# 运行
uv run mewcode

# 测试
uv run pytest
```

## License

[MIT](LICENSE)
