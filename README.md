# curryflows

curryflows 使用 [herdr](https://herdr.dev) 编排用户指定的交互式 worker CLI。它负责创建隔离
worktree、启动任务、等待与巡检、组织只读复核、验证、合并到本地 `main`，以及回收资源。

## 功能

- 每个可写 worker 使用独立 `cfx/*` 分支、git worktree 和 Herdr workspace。
- 获得用户明确授权后，以各 worker 的全权限模式启动可写线程。
- 支持 Herdr 可识别的 agent，也支持通过 pane 控制未识别的交互式 CLI。
- 长任务说明存放在 worktree 外，通过短消息发送文件路径。
- Herdr 可识别的 worker 使用 `agent wait` 检测完成；其他 worker 通过心跳读取 pane。
- 评审位使用只读参数；需要执行代码时使用 detached-HEAD worktree。
- 协调器独立检查 diff 和验证结果，通过后才合并到本地 `main`。
- 需要用户处理时更新 workspace label 并发送 Herdr 通知。
- 不推送远端。

可写 worker 参数：

| worker | 参数 |
|---|---|
| `codex` / `tcodex` | `--dangerously-bypass-approvals-and-sandbox` |
| `claude` / `tclaude` | `--dangerously-skip-permissions` |
| `kimi` | `--yolo` |
| `codebuddy` | `--dangerously-skip-permissions` |

## 依赖

- Herdr 0.8 或更高版本
- 至少一个 worker CLI
- Git 2.5 或更高版本
- Claude Code

## 安装

```bash
git clone <repo-url> ~/.claude/skills/curryflows
```

## 用法

```text
/curryflows 你是派工位：把 parser 迁移拆成并行线程，完成后标记为待评审
/curryflows 你是评审位：巡检 cfx 线程，完成后启动只读评审并汇报 findings
/curryflows 完整推进这个任务：派工、验证、合并到本地 main，但不要推送
```

具体操作见 [SKILL.md](SKILL.md)。
