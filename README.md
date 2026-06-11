# MewCode Coding-Agent

轻量级终端 Coding Agent，基于 **ReAct** 与 **Plan Mode** 双模式驱动 LLM 自主完成编程任务。采用交互、引擎、工具、记忆、安全五层分层架构，兼容 **Anthropic**、**OpenAI** 双协议，支持 MCP 工具扩展、Skill 技能包、跨会话记忆、多 Agent 并行协作。

## 核心特性

- **🔄 ReAct + Plan Mode 双模式** — 默认 ReAct 推理-行动循环，支持 Plan Mode 先规划再执行，灵活应对不同复杂度任务
- **🏗️ 五层分层架构** — 交互层（TUI）、引擎层（Agent 主循环）、工具层（Tool Registry）、记忆层（Memory）、安全层（Permissions），职责清晰、可扩展
- **🤖 Multi-Agent 协作** — 内置子 Agent 分发（Fork / SubAgent）、Team 团队协作（Coordinator 模式），支持多 Agent 并行执行
- **🧩 MCP 协议扩展** — 兼容 Model Context Protocol，可对接外部 MCP Server 动态扩展工具能力
- **📦 Skill 技能包** — 内置 commit、review、test 等技能，支持自定义 Skill 扩展，一键激活
- **🧠 跨会话记忆** — 自动提取与持久化长期记忆，支持会话摘要与恢复（compact），跨会话知识复用
- **🛡️ 多维安全机制** — 权限模式（Default / AcceptEdits / Plan / Bypass）、危险命令检测、路径沙箱、权限规则引擎
- **🪝 Hook 钩子系统** — 支持 session / turn / tool 生命周期 Hook，可自定义前置/后置行为
- **🌲 Worktree 隔离** — 基于 Git worktree 的任务隔离执行，避免影响主工作区
- **💬 终端 TUI 界面** — 基于 Textual 的现代化终端 UI，支持流式输出、文件引用（@）、命令补全

## 架构设计

```
┌─────────────────────────────────────────────────┐
│                   交互层 (TUI)                    │
│         ChatInput / ToolCallBlock / Stream       │
├─────────────────────────────────────────────────┤
│                  引擎层 (Agent)                   │
│     ReAct Loop / Plan Mode / Context Compact     │
├─────────────────────────────────────────────────┤
│                  工具层 (Tools)                   │
│   ReadFile / WriteFile / Bash / MCP / Skill / …  │
├─────────────────────────────────────────────────┤
│                 记忆层 (Memory)                   │
│    Session / Auto Memory / Instructions / Hook   │
├─────────────────────────────────────────────────┤
│                 安全层 (Permissions)              │
│   Mode Matrix / Sandbox / Rule Engine / Detector │
└─────────────────────────────────────────────────┘
```

### ReAct 主循环

Agent 采用标准 ReAct（Reasoning + Acting）循环：

1. **Think** — LLM 推理当前上下文，决定下一步行动
2. **Act** — 调用工具执行操作（读文件、写代码、运行命令等）
3. **Observe** — 收集工具执行结果
4. **Loop** — 将观察结果输入 LLM，继续推理，直至任务完成或达到最大轮次

期间自动触发：**权限检查** → **Hook 回调** → **上下文压缩** → **记忆提取**。

### Plan Mode 规划模式

开启 Plan Mode 后，Agent 先制定详细计划（保存为 Markdown 文件），经用户确认后再逐步执行，适合复杂、高风险任务。

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
| `Agent` | 派发子 Agent 任务 |
| `TeamCreate` / `TeamDelete` | 创建/删除 Agent 团队 |
| `TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate` | 任务管理 |
| `LoadSkill` | 激活 Skill 技能包 |
| `ToolSearch` | 延迟加载工具搜索 |
| `AskUser` | 向用户提问 |
| `EnterWorktree` / `ExitWorktree` | 工作树隔离 |
| `ExitPlanMode` | 退出规划模式 |
| `SyntheticOutput` | 协调者模式结构化输出 |
| `SendMessage` | 团队内消息通信 |

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
| `verification` | 验证 Agent，专注审查和验证代码 |

Agent 定义存放在 `~/.mewcode/agents/` 和 `<project>/.mewcode/agents/`。

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
    command: ruff check $FILE_PATH
  - id: format-on-write
    event: post_tool_use
    tool_name: WriteFile
    command: ruff format $FILE_PATH
```

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
MewCode-Agent/
├── mewcode/
│   ├── agent.py              # Agent 主循环（ReAct / Plan Mode）
│   ├── app.py                # Textual TUI 应用
│   ├── client.py             # LLM 客户端（Anthropic / OpenAI）
│   ├── config.py             # 配置加载与合并
│   ├── conversation.py       # 对话管理
│   ├── prompts.py            # 系统提示词构建
│   ├── agents/               # 子 Agent 系统
│   │   ├── builtins/         # 内置 Agent 定义
│   │   ├── loader.py         # Agent 加载器
│   │   └── task_manager.py   # 任务管理器
│   ├── commands/             # 斜杠命令系统
│   │   └── handlers/         # 内置命令处理器
│   ├── context/              # 上下文窗口管理（compact）
│   ├── hooks/                # Hook 钩子系统
│   ├── mcp/                  # MCP 协议支持
│   ├── memory/               # 记忆系统
│   │   ├── auto_memory.py    # 自动记忆提取
│   │   ├── session.py        # 会话管理
│   │   └── recall.py         # 记忆召回
│   ├── permissions/          # 权限系统
│   │   ├── checker.py        # 权限检查器
│   │   ├── dangerous.py      # 危险命令检测
│   │   ├── modes.py          # 权限模式矩阵
│   │   └── rules.py          # 规则引擎
│   ├── skills/               # Skill 技能包系统
│   │   ├── builtins/         # 内置技能
│   │   ├── loader.py         # 技能加载器
│   │   └── executor.py       # 技能执行器
│   ├── teams/                # 多 Agent 团队协作
│   ├── tools/                # 工具注册与实现
│   └── worktree/             # Git worktree 隔离
├── tests/                    # 测试文件
├── pyproject.toml            # 项目配置
└── README.md
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
