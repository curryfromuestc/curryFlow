# curryflow

给一个项目引导出多机开发流程的 Claude Code / codex skill。curryflow 只做引导：探测机器、和人定验收阶段，
从 `templates/` 生成项目自己的 `ci/`（`accept`、`machines`、`coord`、`executor`、`cflib.py`）、`AGENTS.md`
和 `.gitignore`，建中枢 git 仓库和镜像，在每台机器装 executor，开 curryplan 项目。生成之后流程跟着项目仓库走，
和 curryflow 无关。全部约定见 [SKILL.md](SKILL.md)。

```
templates/
  ci/accept            分阶段验收的骨架（项目填 STAGES、TIERS）
  ci/coord             协调器 CLI：租约、任务、审查、排队、合并、状态、等待
  ci/executor          每台机器的常驻进程：probe、install、start、stop、run
  ci/cflib.py          两者共用：读 ci/machines，和中枢之间读写 ref
  hub/post-receive     主中枢镜像到备用中枢
  project/AGENTS.md    项目规则骨架；project/gitignore
  curryplan/plan/      方案页骨架；curryplan/status/ 读中枢数据的状态页
```

只用 Python 3 标准库、git、ssh；incus 机器上的 agent 容器靠 [agentbox](https://github.com/curryfromuestc/agentbox)
的 `agentctl`，herdr 让人能 attach 到 executor 和容器里的 agent。

## 安装

```sh
git clone git@github.com:curryfromuestc/curryflow.git ~/code/curryflow
ln -s ~/code/curryflow ~/.claude/skills/curryflow
ln -s ~/code/curryflow ~/.codex/skills/curryflow
```
