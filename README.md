# curryflows

[herdr](https://herdr.dev) + codex/kimi + Claude Code `/loop` 的 herdr 注入器:把长跑
worker 起在 herdr workspace 里,注入契约、事件唤醒、心跳巡检、驭偏、回收——只提供机制,
不预设流程。

## 它怎么工作

- 每个用本 skill 的 Claude Code session,角色由你开场注入:派构建者、派评审位、裁决
  合并,或小任务全包。典型用法是拆两个 session——一个专职派工 + 裁决,另一个专职
  `/loop` 盯完工 + 起评审位,互不挤占上下文。
- 起线程:每线程独立 git 分支 + worktree + herdr workspace,worker 长跑(默认 codex,
  `model_reasoning_effort=max`;可点名换 kimi k3,契约不走 /goal、以普通消息注入)。
- 只读位:评审、审计类角色用只读 argv(codex `-s read-only` / kimi `--plan` / claude
  `--permission-mode plan`),要跑代码的复验位在 detached-HEAD 副本里跑。
- 完成靠 `herdr agent wait` 事件唤醒(阻塞 wait 挂后台任务,退出即唤醒);`/loop` 心跳
  放稀到 20-30 分钟,只兜 worker 卡死不动的底——拦问题靠合并门禁,不靠加密巡检。
- 合并门禁:协调器独立复跑不读 worker 自称,机器验布尔量、人验连续量,绿了才 rebase
  合本地 main。推送远端永远归人类。
- 需要人类裁决时:workspace 改名标记 + herdr 弹通知;其余线程照推,不等人。

窗口排布:session 创建的一切 workspace(构建线程、评审编组同款)挂它自己名下、排在
其正后方:

```
herdr workspaces
  w1  curryGPU            [claude ● idle]     ← 派工 session
  w2    cfx rtl-smem      [codex ▶ working]
  w3    cfx compiler      [codex ▶ working]
  w4  curryGPU_rv         [claude ● idle]     ← 评审 session
  w5    cfx-rv rtl-smem   [rv-* 一位一 tab,只读]
```

## 安装

```bash
git clone <repo-url> ~/.claude/skills/curryflows
```

依赖:[herdr](https://herdr.dev) ≥0.8、codex CLI、kimi CLI、git ≥2.5(worktree)、Claude Code。
0.8 重排了 CLI(`agent send`/顶层 `wait` 已废,改 `agent prompt`/`agent wait`),配方按 0.8 写。

## 用法

```text
/curryflows 你是派工位:把 parser 迁移拆线程发出去,完工改 label 标待评审
/curryflows 你是评审位:/loop 盯 cfx 线程,谁完工就起只读位评审,findings 落文件通知我
```

## 设计取向

没有看板文件、没有辅助脚本、没有预设流程——机制在 skill,角色由你注入,状态活在 herdr,
唯一的持久物是 git 分支上的产物。前提是状态全部外置(契约落文件、事实落仓库文档、进度落
git)——协调器上下文会被压缩,记在脑子里的一定丢。全部约定见 [SKILL.md](SKILL.md),127 行读完。
