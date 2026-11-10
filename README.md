# curryFlow

给一个项目引导出多机开发流程的 Claude Code / codex skill。curryFlow 只做引导：探测机器、和人定验收阶段，
从 `templates/` 生成项目自己的 `ci/`（`accept`、`machines`、`coord`、`executor`、`cflib.py`）、`AGENTS.md`
和 `.gitignore`，建中枢 git 仓库和镜像，在每台机器装 executor，开 curryPlan 项目。生成之后流程跟着项目仓库走，
和 curryFlow 无关。全部约定见 [SKILL.md](SKILL.md)。

```
templates/
  ci/accept            分阶段验收的骨架（项目填 STAGES、TIERS）
  ci/coord             协调器 CLI：租约、任务、审查、排队、合并、状态、等待
  ci/executor          每台机器的常驻进程：probe、install、start、stop、run
  ci/cflib.py          两者共用：读 ci/machines，和中枢之间读写 ref
  hub/post-receive     主中枢镜像到备用中枢
  project/AGENTS.md    项目规则骨架；project/gitignore
  curryplan/plan/      方案页骨架；curryplan/status/ 读中枢数据的状态页
  curryplan/page/      通用页面骨架；curryplan/machines/ 机器页
  pool/beat            每台机器每分钟一份心跳；pool/status 把心跳、队列和结论汇成机器页的 status.json
  pool/executor        机器级 executor：每台机器一个，管这台机器的槽位，服务所有项目
  pool/flow            拿槽位的命令：flow run（提交作业并等结论）、flow hold（占用槽位）、ls、cancel、release
bin/planpub            把一个页面目录发布到 curryPlan
test/pool/e2e          机器池的端到端测试：本机上的假中枢、两台假机器，跑完写出 results.tsv
curryplan.md           curryPlan 的页面约定、发布、注册项目、状态页
site/                  curryPlan 站点本身：shell/（页面目录和公共文件）、server/（评论、提问、通知）、test/push/（通知的端到端测试）
```

只用 Python 3 标准库、git、ssh；incus 机器上的 agent 容器靠 [agentbox](https://github.com/curryfromuestc/agentbox)
的 `agentctl`，herdr 让人能 attach 到 executor 和容器里的 agent。

## 机器池：两条命令

几个项目共用同一批机器；每台机器的 `~/curryflow/machines`（中枢 `machines.git` 的 clone）里有 `flow`：

```sh
# 提交一个作业并等结论（退出码 = 作业的退出码；124 超时、143 取消、99 没跑起来、255 丢了；254 认领它的机器没了心跳，flow 不等了）
~/curryflow/machines/flow run --project curryGPU --here -- ci/accept --stage func
~/curryflow/machines/flow run --project curryGPU --commit main --machine box-b --tag synth --timeout 28800 -- ci/accept --stage synth
# 占用一个槽位若干小时（最长 8 小时，到期自动放），打印机器、槽位、核、容器、目录
~/curryflow/machines/flow hold --hours 2 --machine box-b
~/curryflow/machines/flow release <id>
# 排队、在跑、最近完成；取消
~/curryflow/machines/flow ls
~/curryflow/machines/flow cancel <id>
```

作业落在哪个槽位、谁提的、跑了多久、排队情况都在 curryPlan 的"机器"页上。规则、布局和装法见 SKILL.md 的"机器池"一节。

## 安装

```sh
git clone git@github.com:curryfromuestc/curryFlow.git ~/code/curryFlow
ln -s ~/code/curryFlow ~/.claude/skills/curryflow
ln -s ~/code/curryFlow ~/.codex/skills/curryflow
```
