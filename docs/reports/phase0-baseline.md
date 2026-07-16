# Phase 0 测试基线报告

日期：2026-07-15  
基线提交：`b579939`  
验证环境：Windows、Python 3.12.10、uv 0.11.8

## 结论

Phase 0 已建立可重复执行的本地质量基线：锁文件安装、pytest 配置、低争议 Ruff
门禁、Windows/Linux CI 矩阵，以及 Workflow、Scheduler、Harness、Evolution 和配置
合并的关键测试均已落地。

最终本地验证结果：

- `pytest -q -rs`：570 passed，1 skipped，28.95 秒。
- `ruff check mewcode tests`：通过。
- 唯一跳过项是 Windows 环境没有可用于符号链接越界测试的系统文件；该用例保留，
  不以跳过掩盖功能失败。

## 初始基线问题

初次全量测试无法形成稳定绿线，主要问题分为三类：

1. 测试契约过期或依赖单一平台：硬编码 `/tmp`、旧 `/do` 命令、旧 MCP 配置结构、
   旧提示词常量和终端后端假设。
2. 测试隔离不足：异步 Hook 测试启动真实长时间进程，事件循环和临时状态未完全隔离。
3. 产品缺陷：配置层覆盖丢失、Workflow 元数据阶段无法解析、Cron 到期计算错误、
   Harness 权限类别缺失、运行时 Hook 接口不完整、Worktree 切换未清文件缓存，以及
   Plan 模式和沙箱边界行为不符合契约。

## 本轮修复与新增保护

- 配置先合并原始层再统一校验；显式 `false`、默认值、嵌套字典、MCP 同名服务和 Hook
  追加均有回归测试。
- 修复 Workflow `phases` 元数据解析、Journal 缓存、串行/并行/预算路径测试。
- 修复 Cron 任务基于上次触发时间计算到期；覆盖持久化、触发、唤醒和关闭。
- Harness 增加独立权限类别，补齐 Hook 管理、配置修改、权限规则、审计和限流测试。
- 应用启动入口显式注入完整 `AppConfig`，确保运行时 `UpdateConfig` 使用真实配置对象。
- Evolution 覆盖 Trace 持久化、失败筛选、采集、跳过和受控失败路径。
- 现有测试改为跨平台临时目录和可控异步同步原语，不再依赖真实休眠。

## CI 基线

GitHub Actions 使用锁文件执行以下矩阵：

- 操作系统：`ubuntu-latest`、`windows-latest`
- Python：3.11、3.12
- 门禁：`uv sync --locked --dev`、Ruff、全量 pytest

Ruff 在 Phase 0 只启用语法错误、无效结构和未定义名称等低争议规则。未在本阶段进行
全仓风格清理，避免把无关格式改动混入可靠性基线。

## 后续风险

本地结果不能替代远端双平台 CI 的实际运行；分支推送后应以四个矩阵任务全部通过作为
Phase 0 的最终合并条件。完成该条件后，再进入 RuntimeBuilder/AgentLoop 的 Phase 1
重构。
