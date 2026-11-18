# curryFlow

多机开发流程、机器池调度与 curryPlan 文档的统一 skill。全部约定见 [SKILL.md](SKILL.md) 与 [curryplan.md](curryplan.md)。

```
templates/
  ci/accept              分阶段验收骨架（项目填 STAGES、TIERS）
  project/AGENTS.md      项目规则骨架
  project/gitignore      .gitignore 骨架
  project/publish-docs   docs/*.html 检查（--check）与自动发布到 curryPlan
  curryplan/page/        通用页面骨架（图表、表格、指标格、折叠明细）
  curryplan/plan/        方案页骨架
  curryplan/machines/    机器页
  curryplan/chat/        聊天页（本地模型）
  pool/beat              每台机器每分钟一份心跳和告警（含 --probe 拓扑探测）
  pool/status            把心跳汇成机器页的 status.json
  pool/executor          在一个槽位里起一个作业；不常驻，flow 经 ssh 调它
  pool/flow              在定好的机器和槽位上跑作业：run、wait、log、cancel、ls
  pool/mac-agents        Mac 上的 beat（LaunchDaemon）和 executor 要的 sudo 规则：install <名>、remove、status
bin/planpub              把一个独立页面目录发布到 curryPlan
curryplan.md             curryPlan 的排版铁律、同源自动发布、注册项目、通知
site/                    curryPlan 站点本身：shell/（页面目录和公共文件）、server/（评论、提问、通知）、chat/（聊天页后端与 Mac 模型节点）、test/push/、test/chat/（端到端测试）
```

## 机器池

先和人定好机器和槽位，再在 `~/curryflow/machines` 里：

```sh
flow run --machine aimax --slot p1 --project sdk --here -- ci/accept --stage func   # 等结束，退出码 = 作业的
flow run ... --no-wait -- <命令>     # 打印 机器:作业号 就返回
flow wait aimax:<作业号>             # 接着等
flow cancel aimax:<作业号>
flow ls                              # 各机器的作业和告警
```

规则、布局和装法见 SKILL.md 的"机器池"一节；负载和告警在 curryPlan 的"机器"页上。

## 安装

```sh
git clone git@github.com:curryfromuestc/curryFlow.git ~/code/curryFlow
ln -s ~/code/curryFlow ~/.claude/skills/curryflow
ln -s ~/code/curryFlow ~/.codex/skills/curryflow
```
