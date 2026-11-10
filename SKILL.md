---
name: curryflow
description: >-
  多机开发流程的唯一入口。给一个项目引导出流程：探测机器、和人定验收阶段与判据、定存储规矩，从模板生成项目自己的
  ci/（accept、machines、coord、executor）、AGENTS.md 和 .gitignore，建中枢 git 仓库和镜像，在每台机器装
  executor；写清协调器的通用规矩（任务生命周期、只看 diff 的独立审查、一次汇报、按任务挑模型、卡住就停）；
  管机器池（机器清单、每分钟心跳、每台机器一个服务所有项目的 executor、拿槽位的 flow run / flow hold、curryPlan 上的
  "机器"页）；往 curryPlan 发页面（方案页、论文页、学习页、笔记、状态页，约定在 curryplan.md）。触发于："用 curryFlow
  引导 X"、"给项目搭多机流程"、"起协调器"、"加一台机器"、"看机器负载 / 槽位"、"在机器池上跑个作业 / 占个槽位"、
  "发到 curryPlan"、"写个方案页 / 学习页 / 论文页"、"给项目开个 curryPlan"、"更新状态页"。
---

# curryFlow

curryFlow 只做引导，本身不带运行时代码，也不知道具体项目。运行时代码都在 `templates/` 里，引导时拷进项目的
`ci/`，提交进项目仓库；从那以后，项目里的 `ci/` 就是全部真相，curryFlow 不再参与。换项目就再引导一次。
对人的一切叙述用中文。

```
中枢 hub（小 VPS）    bare 仓库；任务、认领、验收队列、结论、租约全是 git ref（见 ci/cflib.py 开头）
  └─ 备用中枢         post-receive 每次 ref 更新镜像一份；主中枢连不上时 ci/ 按顺序用它
协调器（任何地方）    clone + 中枢 key + ci/coord；无状态；租约保证同一时间只有一个
executor（每台机器）  ci/executor run；只往外连中枢；领任务开 agent 容器，领验收作业跑槽位
curryPlan             人看的状态页（每分钟读中枢）和方案页；讨论和拍板在会话里，不上页面
```

机器之间只走 git，不挂共享盘；协调器从不登录执行机；token 只放执行机，不过中枢；GitHub 只收 main，只由人推。

## 引导

按顺序做，每步做完核对再往下。需要人拍板的只有第 2 步的判据和第 1 步里"共享机器用多少"；其余自己定，
定了在最后的汇报里说一句。

1. **探测机器。** 对每个 ssh 别名：`ssh <别名> python3 - probe < templates/ci/executor`，读输出的 JSON
   （L3 域、NUMA 节点和各自负载、内存、GPU、磁盘、容器后端、agentctl、herdr）。切槽位：
   - sim = 一个 L3 域：8 个物理核加它们的超线程；一个 L3 不到 8 核（Zen2 的 CCX 是 4 核）就拼同一 NUMA
     节点里相邻的两个。probe 的 `sim_slots` 已经按这个规则切好。
   - synth = 一个 NUMA 节点；gpu = 一张卡（槽位加 `"gpu": N`）；build 可超额（不和别的槽位抢核）。
   - agent 容器是单独的席位，每个 2–4 核，不占上面几类；核不够分的机器（box-c 16 核）席位写成个数、不钉核，
     并在汇报里说明它和仿真槽共用核。
   - 共享机器标 `"shared": true`，只占人给的份额，选 probe 里最闲的 NUMA 节点；精确计时的阶段优先放独占机器。
   - 容器后端：有 incus 就用 incus（agent 容器靠 agentbox，见 agentbox skill；先确认 `agentctl` 和
     `agentbox-base` 镜像在）；没有容器就是 bare：只跑验收，用 `taskset` + `systemd-run --user -p MemoryMax=` 钉核限内存。
   - 根分区小的机器，incus 存储池和缓存目录放到大盘（/home）上。
   - 列出跨槽位共享的可写状态：浮动 license 的并发数（它决定 synth 槽位最多几个）、工具写死的输出路径、
     需要先生成的输入。说不清是否能并行的，先只给一个槽位，跑起来再加。
   - 连中枢 22 端口不稳的机器（国内）：`"hub_proxy": "127.0.0.1:7890"`，git 经本机代理连中枢，agent 容器里同样。
2. **定验收（和人一起）。** 列阶段：名字、槽位类、命令、判据。用例优先来自真实 bug——问人最近踩过的 bug
   （死锁、超时、时序为负……），每个做成一个能复现的用例，写清 why。按风险分级（`TIERS`）：只改文档、工具的走
   快阶段，动核心的走全套。填进 `ci/accept` 的 `STAGES`、`TIERS`。判据由人拍板，不替人定。
   结果和时序有关、偶尔才出的失败（仿真里的死锁、竞争），判据里写跑几遍。
3. **定存储。** 派生物只准在 `.build/`（`ci/accept` 每个阶段前后扫一遍工作区，别处多了或改了文件就判失败）；
   `.gitignore` 写 `.build/`。可共享的派生物（工具链、编好的模型）在阶段的 `cache` 字段里声明输入路径、取工具
   版本的命令、构建命令：键 = 输入的 git tree hash + 工具版本，executor 先建到临时目录再 rename，只读挂进
   容器的 `/cache`，每机预算（默认 100 GB）按最近使用淘汰。验收结论永久在中枢；大文件留执行机 `keep_days`
   （默认 7）天，`queue --pin` 的基线留到有新基线。容器根盘有 incus 限额。
4. **生成 ci/。** `cp templates/ci/{accept,coord,executor,cflib.py} <项目>/ci/`，写 `ci/machines`（下面的格式），
   `AGENTS.md`（从 `templates/project/AGENTS.md` 起，只放项目专属的：测试怎么跑、哪些不能改，保持短），
   `.gitignore`（`templates/project/gitignore`）。本地跑一遍 `ci/accept` 全绿再提交。
5. **建中枢。** 主中枢 `ci` 用户下：
   ```sh
   ssh <主中枢> 'git init -q --bare -b main <项目>.git'
   scp templates/hub/post-receive <主中枢>:<项目>.git/hooks/ && ssh <主中枢> 'chmod +x <项目>.git/hooks/post-receive'
   ssh <主中枢> 'git -C <项目>.git config curryflow.mirror ci@<备用中枢>:<项目>.git'
   ssh <主中枢> 'git -C <项目>.git config core.sshCommand "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"'
   ```
   备用中枢同样有 `ci` 用户和 bare 仓库；主中枢 `ci` 的公钥进备用中枢的 authorized_keys；每台执行机、要起协调器的
   机器、curryPlan 机的公钥进两边。`git push <主中枢> main`，核对备用中枢的 main 几秒内跟上。
6. **装 executor。** 每台机器 clone 到 `ci/machines` 里的 `dir`，然后
   `ci/executor install --machine <名>`（建钉核的槽位容器、挂缓存、查 agentctl）和 `ci/executor start --machine <名>`
   （在本机 herdr 里起，人能 attach 看）。冒烟：协调器 `ci/coord queue smoke --commit main`，`ci/coord wait` 出 PASS 后
   `ci/coord drop smoke-1`。
7. **开 curryPlan 项目。** 按 `curryplan.md` 注册项目；状态页用 `templates/curryplan/status/`（把 `__PROJECT__`
   换成项目名），在 curryPlan 机上另 clone 一份，cron 每分钟 `ci/coord status --json` 写到页面目录的 `status.json`；
   方案页从 `templates/curryplan/plan/` 起，只写最终形态。

`ci/machines`（JSON）：

```json
{"project": "toy", "hubs": ["ci@<主中枢>:toy.git", "ci@<备用中枢>:toy.git"],
 "curryPlan": {"host": "user@<curryPlan 机>", "page": "/var/www/plan/toy/status", "clone": "~/curryflow/toy-status"},
 "machines": {
  "box-c": {"ssh": "box-c", "dir": "~/curryflow/toy", "backend": "incus", "shared": false, "hub_proxy": "127.0.0.1:7890",
            "incus": {"image": "agentbox-base", "pool": "agentbox"},
            "slots": {"sim0": {"class": "sim", "cpus": "0-7,16-23", "mem": "24GiB", "disk": "20GiB"}},
            "agents": {"seats": ["4", "4"], "mem": "16GiB", "disk": "30GiB", "token": 1, "agentctl": "agentctl"},
            "cache": {"dir": "~/.cache/curryflow", "budget_gb": 100}, "keep_days": 7},
  "box-a": {"ssh": "box-a", "dir": "~/curryflow/toy", "backend": "bare", "shared": true,
              "slots": {"sim0": {"class": "sim", "cpus": "16-23,144-151", "mem": "16G"}},
              "cache": {"dir": "~/.cache/curryflow", "budget_gb": 100}}}}
```

`seats` 每项是一个 incus `limits.cpu`：写个数就不钉核，写 `"48-49,176-177"` 就钉在这几个核上。

## 协调：怎么分活

- **协调器自己做定位和判断。** 读 diff、二分是哪个改动引起的、对比输出、改几行，都在协调器自己的上下文里做：
  它看过前面每一步，交出去还得让别人从头建上下文。交给 agent 的是耗时长、目标清楚的实现。
- **按目标切任务，不按代码区域。** 一个任务是一个可核对的结果（"这组 kernel 的寄存器数追平参照"），要动的代码都
  归它；不说"你只管调度器"，否则边界上的问题谁都不做。并行的任务挑改动面不重叠的目标；两个目标要动同一块代码，
  就排先后或并成一个任务。
- **任务说明只写目标、证据和判据。** 通用规矩在 TASK.md 模板和 AGENTS.md 里，不往每份说明里抄。证据写具体：
  失败日志的位置、对比出来的差异、已经排除的可能。
- **修复和返工开新任务。** 和原任务同一个问题的小跟进用 `task msg` 续原会话；换了问题、或原会话已经很长，
  就开一个只带这个问题和证据的新任务：续长会话每一轮都要重读全部上下文，慢而贵。
- **产物要留得住。** 容器和工作区收尾就删。agent 造的、后面还要用的工具（对比脚本、语料提取）提交进仓库；
  大的派生数据声明成阶段的 `cache`；数字写进汇报。只放在 agent 自己 `.build/` 里的东西，任务一关就没了。

## 协调：一个任务怎么走完

1. **拿租约。** `ci/coord lease hold` 放后台跑（Claude Code 用 Bash 的 run_in_background；codex 用
   `nohup … &`）。没有租约，改中枢的命令都会拒绝。起它的会话没了，心跳就停，60 秒后别处的协调器能接手。
   换地方起协调器：clone、`lease hold`、`ci/coord status`，就接上了——状态全在中枢，本地什么都不用带。
2. **建任务。** `ci/coord task new <id> --goal-file <md> --accept <判据> --budget <预算> [--agent claude] [--model M]
   [--effort E] [--machine 名]`。判据写可核对的证据；连续指标（性能、面积、时序）写清对象、口径和对照基线。长目标一律写成 md 文件用 `--goal-file`：executor 把它放进容器的 `~/.cf/TASK.md`，
   agent 只收到一句"读 TASK.md"（长提示词直接塞给 agent 会被截断、吞回车，已观测）。
3. **等终态。** `ci/coord wait`：阻塞到某个任务汇报完或某个候选出全部结论，打印事件后退出。只为终态事件花一个
   回合；不轮询、不挂心跳循环。退出码 4 是租约丢了：先查 hold。
4. **审查。** `ci/coord review <id>` 把 diff 落到 `.build/coord/review/<id>.diff`。另起一个独立审查者
   （`codex exec -s read-only` 或 `claude -p`，不是写这段代码的那个），只给它 diff 和判据，不给你的看法；最多 2–3 轮。
   要 agent 改：`ci/coord task msg <id> --file <md>`，executor 在同一个容器里 resume 同一个会话（只用于同一问题的
   小跟进，见上）。小改动（冲突、几行修正）协调器自己改：在 `.build/coord/merge/<id>` 里改完提交，
   `ci/coord queue <id> --commit <sha>`。
5. **验收、合并。** `ci/coord queue <id>` 合成候选（main 合上 `wip/<id>`），按 `TIERS` 每个阶段一个作业；executor
   在钉核的槽位里跑候选自己的 `ci/accept --stage`。`wait` 出 PASS 就 `ci/coord merge <cid>`（只做快进，main 动过
   就重新 queue）；FAIL 看结论和 `reports/`，再 msg 或自己修；不要了就 `ci/coord drop <cid>`。
6. **收尾。** `ci/coord task close <id>`，executor 删掉容器和它的构建。告诉人中枢 main 到了哪个提交——推 GitHub 是人的事。

规矩：

- **只汇报一次，在最后。** agent 是这样（TASK.md 里写了），你对人也是：一件事做完报一次，不发进度、不发"在等"。
- **按任务挑模型。** 小改动用快而便宜的；跨模块、难查的 bug 用强的、effort 高（codex 最高是 `xhigh`，没有 ultra）。
- **卡住就停下分类。** 同一个问题 2–3 轮没有进展，停，归类：环境/工具坏了、规格不清、实现错、用例本身错、超预算。
  环境问题修机器；规格问题问人；其余换思路、换模型重开任务，不在原地再磨。
- **不信自称。** agent 汇报里的"验证通过"只是线索，合并只认 executor 在槽位里跑出的结论。
- **跑检查之前先问**：它能发现什么具体的失败？发现了我会做什么不同的事？答不上就不跑。
- **不替人拍板，也不卡着等人。** 要人定的事写清选项和你的建议，其余任务照推。
- **协调器不登录执行机。** 排错看中枢上的 executor 心跳（`ci/coord status`）、任务 ref 里的报错和验收的 `reports/`。
- **只动自己起的进程。** 结束进程只按自己记下的 PID；不用 `pkill -f`、`pgrep -f` 找进程或写等待循环——它们会
  匹配到发命令的 shell 自己。等后台作业就等它的 PID。
- token、密码不打印、不写日志、不提交。

## 机器池

槽位归机器，不归项目：几个项目共用同一批机器，机器清单只有一份，每台机器一个 executor 管它的槽位。重活（仿真、综合、
验收阶段）不自己 `taskset`、不自己 ssh 进容器跑，向机器池要槽位。两条命令，在任何一台机器的 `~/curryflow/machines` 里：

```sh
# 提交一个作业并等结论：打印日志最后 40 行、结论和完整日志的位置，退出码就是作业的退出码
~/curryflow/machines/flow run --project curryGPU --here -- ci/accept --stage func
# 占用一个槽位若干小时（最长 8）：打印机器、槽位、核、容器、目录；用完 flow release <id>，到期自动放
~/curryflow/machines/flow hold --hours 2 --machine box-b
```

- `flow run --project <名> [--commit <提交或中枢分支> | --here] [--threads N] [--mem G] [--gpu] [--machine M] [--slot S]
  [--prio gate|normal|night] [--timeout 秒] [--tag synth] [--no-wait] -- <命令>`。不给 `--commit`、`--here` 就用中枢 main；
  `--commit` 可以是提交、项目中枢上的分支 / tag / 任意 ref（`HEAD`、`refs/pull/7/head`、带中文或 `@`、`#` 的分支名都行），
  或当前仓库解析得了的名字，flow 都先解析成 40 位提交再交上去；
  `--here` 用当前仓库的 HEAD，中枢上没有分支指着它时推到项目中枢的临时 ref `refs/cf/tmp/<id>`（不建分支，executor
  取到后删）。命令只有一个词时用 `bash -c` 跑（`-- "make -j && ctest"`）。默认超时 4 小时，最长 24 小时。退出码：作业
  自己的；124 超时，143 被取消，99 executor 没能把它跑起来（取不到提交，或请求不合规），255 丢了（跑它的机器心跳和
  executor 自报的存活信号都停了，或它的 executor 没在管它，十分钟后由别的 executor 判；判的 executor 自己读不到中枢的
  那段时间不算；不在判的那台机器 `machines.json` 里的机器也一样要心跳停了才判）。255 不是终局：作业也许还在跑，它自己
  的结论回来后会替换掉 255，`flow wait <id>` 取到的就是替换后的。254 是 `flow wait` 不等了：认领它的机器 20 分钟没有
  心跳也没有存活信号（flow 自己读不到中枢的时间不算；作业也许还在跑，结论回来后 `flow wait <id>` 能取到）。
  `--no-wait` 只打印 id，之后 `flow wait <id>`。
- `flow ls`：排队、在跑、最近完成。`flow cancel <id>`：没开始的撤掉（写 143 的结论，中枢上已经有结论时不盖它），在跑的
  结束它的全部进程。排队时撤掉和 executor 认领撞上时，认领的 executor 看到中枢上已有的结论（取消、不合规）就结束作业、
  不推自己的结论；只有已有的结论是"丢了"时才接着跑。
- 作业的环境：工作目录是这个槽位里该项目的常驻工作树，checkout 到指定提交；`.gitignore` 里的东西（`.build`）留着做
  增量构建，别的未跟踪文件清掉。环境变量有 `CF_DEPS_CACHE`、`PYTHONDONTWRITEBYTECODE=1`、`TMPDIR=<工作树>/.build/tmp`、
  `CF_SLOT`、`CF_CPUS`、`CF_THREADS`（并行度用它）、`CF_JOB`；`HOME` 在容器里是 `<槽位目录>/.home`，不用容器时是真实的
  家目录。没有容器的机器上是 `taskset` 钉核加带内存上限的 systemd 用户 scope；有容器的机器上在槽位的容器里以宿主机
  用户的 uid/gid 跑，写出的文件属主是宿主机用户，不是 root（容器里这个用户名被别的 uid 占了时，用户名是 `cf<uid>`）。
  准备阶段的 git 在宿主机上跑，也钉在槽位的核上。
- 排队规则：先按优先级（gate > normal > night），同优先级里提交人轮转，再按提交时间；一个作业占一个槽位；`--tag synth`
  的作业一台机器同时只跑一个。槽位可排 = 没有 executor 自己的作业或占用，并且实测忙线程不到 20%；共享机上别人直接用过
  的槽位要安静 5 分钟才算空闲。**要在槽位的核上手工跑东西，先 `flow hold`**：不占用的话 executor 只能靠负载猜。
  hold 期间直接在 `<槽位目录>/<项目>` 工作树里改的东西，下一个作业的 `checkout -f` 和 `clean` 会清掉（`.gitignore` 里的
  除外）：要留的先提交或拷走。
- 谁提的：`$CF_WHO`，没设就是 `用户@主机:分支`。

中枢上的 `machines.git` 放机器池的全部东西，每台机器 clone 到 `~/curryflow/machines`：

- `machines.json`：每台机器的槽位（钉的核、内存、容器名、有没有 NV 显卡 `gpu`）、要报告的挂载点、`executor: true`、依赖缓存
  `deps_cache`、给作业加的环境变量 `env`；国内机器写 `hub_proxy`（和 `host`）。`projects` 是项目名到中枢地址的表：`seed` 是
  每台机器上已有的 clone（镜像从它建），`seed_only` 表示不许从中枢全量取（中枢小，大仓库都要写）。
- `beat`、`status`、`executor`、`flow` 从 `templates/pool/` 拷来。
- 状态全是 ref（JSON blob，格式见 `executor` 开头）：`refs/hb/<机器>` 心跳；`refs/q/<id>` 请求；`refs/run/<id>` 认领，用
  "该 ref 必须不存在"的 push 建，只写这一次，两个 executor 抢同一个请求只有一个成功；`refs/done/<id>` 结论（退出码、
  最后一行、用时、日志最后 40 行、完整日志在哪台机器的哪个路径），结束 7 天后清；`refs/cancel/<id>` 取消在跑的。作业
  活着靠机器心跳：beat 把 executor 手上的请求 id 带进 `refs/hb/<机器>`，beat 停了时由 executor 推 `refs/alive/<机器>`
  顶上，不按作业推心跳。不合规的请求（字段类型或取值不对、半小时没有哪个槽位满足）由先看到的 executor 写一个 99 的
  结论并撤掉；`need` 里本机不认识的键或 tag 算"本机跑不了"，不算不合规；"半小时没有槽位满足"只由运行着本机 clone 里
  那版 executor、并且 clone 里的 `machines.json` 和中枢 main 上一样的 executor 判（clone 落后的不判）。项目仓库上只碰
  `refs/cf/tmp/<id>`：flow 推，executor 取到提交后删；flow 没交上请求（失败或 Ctrl-C）时自己删；剩下的 executor 每小时清一次（没有请求在用、推了
  一小时以上的）。
- 每台机器两行 cron（装的时候保留已有的行；远端用脚本整段重写，不在 `set -e` 下 `crontab -l | grep -v`）：
  ```
  * * * * * cd ~/curryflow/machines && flock -n .git/beat.lock timeout 55 python3 beat --machine <名> >/dev/null 2>&1  # curryFlow 机器心跳
  * * * * * cd ~/curryflow/machines && flock -n .git/executor.lock python3 executor run --machine <名> >/dev/null 2>&1  # curryFlow executor
  ```
  beat 每分钟把本机状态推成心跳：各 NUMA 节点和各槽位的忙线程、内存、磁盘、显卡、incus 容器、谁在占 CPU、每个槽位里的
  作业或占用；每 10 分钟自己 `git pull`，心跳里带 clone 的版本和上次 pull 的结果（`pull`），机器页上显示版本，pull 失败
  标红。executor 每 15 秒经一条复用的 ssh 连接看一次队列（取项目的提交另开连接）；文件变了且手上没有作业时自己换成新
  版本；被结束了一分钟内由 cron 拉起，接着等在跑的作业，不重跑。beat 停了或卡在慢的探测（nvidia-smi、incus）里时，
  手上有作业的 executor 每 2.5 分钟自己推一次 `refs/alive/<机器>`（它手上的请求 id），作业不会因为 beat 坏了被判丢。
- 机器上的布局：`~/curryflow/mirror/<项目>.git` 每个项目一份 bare 镜像，只对中枢增量取；槽位目录是 `~/<容器名>`（有容器，
  容器里同路径）或 `~/curryflow/slots/<槽位>`，里面 `<项目>/` 是工作树，`.cf/<id>/` 是作业目录（`log` 是完整日志）；
  executor 自己的日志和状态在 `~/curryflow/exec/<机器>/`。
- curryPlan 机 clone 到 `~/curryflow/machines-status`，cron 每分钟取心跳、队列和结论，`python3 status --history hist.json`
  写出 `/var/www/plan/curryplan/machines/status.json`；页面是 `templates/curryplan/machines/index.html`：槽位表的"作业"列、
  "排队"和"最近完成"两张表。重发这个页面只 scp `index.html` 和 `meta.json`（`bin/planpub` 会清掉目录里的 `status.json`）。
- 改槽位、加项目：改 `machines.json` 推到 `machines.git`，各机器十分钟内自己拉到。加机器：加一项，在那台机器上 clone、
  装两行 cron。

通用容器（incus 机器）：同一个镜像（基础系统加构建用的包），钉在一个槽位的核上，依赖缓存只读挂入，工作目录是宿主机
`~/<容器名>` 同路径挂入（`shift=true`，容器里的 uid 和宿主机一一对应）；会变的工具（仿真器）走依赖缓存，不进镜像。

## curryPlan

人看的页面都发到 curryPlan。页面约定、发布（`bin/planpub`）、注册项目、状态页的做法在 `curryplan.md`，发之前先读它；
页面骨架在 `templates/curryplan/`（`page/` 通用、`plan/` 方案、`status/` 项目状态、`machines/` 机器）。

## 加一台机器

`probe` → 在 `ci/machines` 加一项 → 提交推中枢 → 那台机器 clone、`install`、`start`。改了 `ci/` 以后各机要
`ci/executor stop && git pull && ci/executor start --machine <名>`：executor 不会自己更新。

## 排错

| 现象 | 原因 / 做法 |
|---|---|
| 任务一直 open | 没有带空闲 agent 席位的 executor 在线（`ci/coord status` 看心跳），或 `--machine` 指了别的机器 |
| executor 日志：没有权限连 incus | herdr 服务端是用户加进 incus-admin 之前起的；`ci/executor start` 已经用 `sg incus-admin` 包了，手动跑也要包 |
| git 卡在 banner exchange、连中枢超时 | 那台机器到中枢 22 端口不稳：配 `hub_proxy` |
| `agentctl new` 卡在 Claude token 探测 | `agents.token` 指定号（codex 任务不用 Claude 号） |
| 验收判 FAIL "写到了 .build/ 以外" | 阶段在仓库里写了派生物（常见：Python 的 `__pycache__`，用 `PYTHONDONTWRITEBYTECODE=1`） |
| `wait` 退出码 4 | hold 进程停了或被别人接手：`ci/coord lease show` |
| 主中枢挂了 | `ci/` 自动用备用中枢；主中枢恢复后先在备用中枢上 `git push --mirror <主中枢>` 再切回 |
