# curryFlow

多机开发流程、机器池调度与 curryPlan 文档的统一 skill。全部约定见 [SKILL.md](SKILL.md) 与 [curryplan.md](curryplan.md)。

```
templates/
  ci/accept              分阶段验收骨架（项目填 STAGES、TIERS）
  hub/post-receive       主中枢镜像到备用中枢
  project/AGENTS.md      项目规则骨架
  project/gitignore      .gitignore 骨架
  project/publish-docs   docs/*.html 检查（--check）与自动发布到 curryPlan
  curryplan/page/        通用页面骨架（图表、表格、指标格、折叠明细）
  curryplan/plan/        方案页骨架
  curryplan/machines/    机器页
  curryplan/chat/        聊天页（本地模型）
  pool/beat              每台机器每分钟一份心跳（含 --probe 拓扑探测）
  pool/status            把心跳、队列和结论汇成机器页的 status.json
  pool/executor          机器级 executor：每台机器一个，管这台机器的槽位，服务所有项目
  pool/flow              拿槽位的命令：flow run（提交作业并等结论）、flow hold（占用槽位）、ls、cancel、release
bin/planpub              把一个独立页面目录发布到 curryPlan
test/pool/e2e            机器池的端到端测试：本机上的假中枢、两台假机器，跑完写出 results.tsv
curryplan.md             curryPlan 的排版铁律、同源自动发布、注册项目、通知
site/                    curryPlan 站点本身：shell/（页面目录和公共文件）、server/（评论、提问、通知）、chat/（聊天页后端与 Mac 模型节点）、test/push/、test/chat/（端到端测试）
```

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
