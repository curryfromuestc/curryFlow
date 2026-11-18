---
name: curryflow
description: >-
  多机开发流程的唯一入口。给一个项目引导出流程：探测机器、和人定验收阶段与判据、定存储规矩，生成项目的 ci/accept、
  docs/pages.json、tools/publish-docs、AGENTS.md 和 .gitignore，建中枢 git 仓库和镜像，接入机器池；写清协调器的通用规矩
  （派任务前问人用哪些机器、本地 subagent 只写代码不建多工作树、合并后主会话统一跑重活、发布件按版本号发、不跟提交走）；
  管机器池（machines.git、每分钟心跳 beat、每台机器一个服务所有项目的 executor、拿槽位的 flow run / flow hold、curryPlan 上的
  "机器"页）；管 curryPlan 页面（仓库 docs/*.html 与 curryPlan 同源自动发布、图表与表格优先、禁大段文字）。触发于："用 curryFlow
  引导 X"、"给项目搭多机流程"、"加一台机器"、"看机器负载 / 槽位"、"在机器池上跑个作业 / 占个槽位"、"发到 curryPlan"、
  "写个方案页 / 学习页 / 论文页"、"给项目开个 curryPlan"。
---

# curryFlow

curryFlow 是多机开发、机器池调度和 curryPlan 文档的统一入口。对人的一切叙述用中文。

```
中枢 hub（只放 git 仓库的小服务器，选各台机器直连都稳的位置）
  ├─ machines.git         机器池仓库；心跳 refs/hb/*、队列 refs/q/*、认领 refs/run/*、结论 refs/done/*
  └─ <项目>.git           项目 bare 仓库（带备用中枢 post-receive 镜像）；存 main 和 wip/* 分支
机器池（每台机器一份 ~/curryflow/machines）
  ├─ beat                 每分钟采集 NUMA/槽位负载、内存、磁盘、GPU、容器，推 refs/hb/<机器>
  ├─ executor             每台机器一个，管本机全部槽位（incus 容器或 taskset + systemd MemoryMax）
  └─ flow                 提交作业（flow run）或占用槽位（flow hold），任何机器上都能调
项目仓库（~/code/<项目>）
  ├─ ci/accept            分阶段验收入口（配合 ci/stage.sh），按改动分级（--plan）
  ├─ docs/                docs/*/index.html（设计）与 todo.html（待办），由 tools/publish-docs 同步到 curryPlan
  └─ AGENTS.md            项目专属规矩
```

机器之间只走 git，不挂共享盘；token 和机器地址只放本机 `~/.config/curryflow/local.env`，不进仓库；GitHub 只收 `main`。

## 引导新项目

按顺序做，每步做完核对再往下。需要人拍板的是验收判据和允许使用哪些机器/槽位。

1. **探测机器并配进机器池。** 对新机器跑 `ssh <别名> python3 - --probe < templates/pool/beat`，读输出的 JSON（NUMA 节点、L3 域、内存、GPU、磁盘、incus 容器）：
   - 一个仿真槽位 = 一个 L3 域（8 个物理核加超线程；L3 不足 8 核则拼同一 NUMA 节点相邻的 L3）。
   - 共享机器标 `"shared": true`，只占人给的份额，选最闲的 NUMA 节点；有 incus 的机器用通用容器（钉核、限内存、只读挂依赖缓存），没有容器的机器用裸槽位（`executor` 自动用 `taskset` 钉核 + `systemd-run --user -p MemoryMax=` 限内存）。
   - 直连中枢不稳的机器配 `"hub_proxy": "127.0.0.1:7890"`。
   - 在 `machines.git` 的 `machines.json` 里登记项目（`projects.<项目>.hub`、大仓库设 `seed_only: true` 和各机 `seed` 路径）和机器槽位，推上中枢。
2. **定验收（和人一起）。** 列阶段：名字、命令、判据。用例来自真实 bug，按改动文件的风险分级（`TIERS`）填进 `ci/accept`。
   - **共享机资源铁律**：项目的验收和构建脚本（如 `ci/stage.sh`）取 CPU 和内存时，必须用槽位注入的 `$CF_CPUS`、`$CF_THREADS`、`$CF_CORES`（物理核数）、`$CF_MEM_GB`，或读进程自己的 `os.sched_getaffinity(0)` 与当前 cgroup 路径上最小的 `memory.max`；**严禁读整机的 `/proc/meminfo`（`MemTotal`）或全局 `/sys/fs/cgroup/cpuset.cpus.effective`**（在裸槽位上那会读出整机几百 GB 内存和全部核，导致任务越界或被 cgroup OOM 杀死）。
3. **定存储。** 派生物只准在 `.build/`（`ci/accept` 每阶段前后扫描工作区，写到 `.build/` 以外直接判失败）；`.gitignore` 写 `.build/`。外部工具链与测试数据写进项目的 `deps.json`，由 `tools/provision` 按版本和 sha256 拉到 `.build/third_party`（机器上只留只读缓存 `$CF_DEPS_CACHE`）。
4. **生成项目文件。**
   - `cp templates/ci/accept <项目>/ci/accept`
   - `cp templates/project/publish-docs <项目>/tools/publish-docs`
   - 从 `templates/project/AGENTS.md` 和 `templates/project/gitignore` 生成 `AGENTS.md`、`.gitignore`
   - 建 `docs/pages.json`、设计文档 `docs/<模块>/index.html` 和待办 `docs/<模块>/todo.html`（格式见 `curryplan.md`），把 `tools/publish-docs --check` 放进 `style` 验收阶段。
5. **建项目中枢。** 主中枢 `ci` 用户下建 `<项目>.git` bare 仓库，装 `templates/hub/post-receive` 镜像到备用中枢，推 `main`。
   备用中枢上的同名仓库装 `templates/hub/pre-receive`：只收主中枢的镜像推送，别的推送拒掉并打印主中枢的地址
   （否则直接推到备用中枢的分支会被下一次镜像删掉）。两个钩子要设的 `git config` 写在各自的文件头里。
6. **开 curryPlan 自动同步。** 按 `curryplan.md` 注册项目；在 curryPlan 机上建项目的稀疏 clone（只检出 `docs/` 和 `tools/publish-docs`），cron 每分钟拉中枢 `main` 跑 `tools/publish-docs`。

## 协调：怎么分活与跑完一个任务

1. **派活或用机器前先问人。** 指派 agent 或向机器池提交作业、申请 `flow hold` 之前，先和人确认本次用哪些机器、哪些槽位或容器。已经被别的会话占用、正在跑作业、或别人声明在用的机器（例如 `aimax`），绝不派任务或抢占槽位；不能占用槽位以外的宿主机 CPU。
2. **本地 subagent 只写代码，重活归主会话。**
   - 除非人明确指定某个 subagent 去跑测试/综合，否则本地 subagent **只做代码阅读、静态分析和修改代码**。
   - **禁止建一堆 git worktree**：多个按文件切开的机械修改任务共用同一个工作树（每个 worktree 的 `.build/` 动辄几十 GB，建十几个会把几 TB 磁盘吃光）；用完的工作树及时清理。
   - **禁止 subagent 起无限阻塞的后台进程**（如 `tail -F`、`watch`、没超时的轮询循环），否则 subagent 交完报告后还会因为残留子进程挂着几个小时不退。
   - 编译、综合、形式化等价证明、RTL 仿真、分阶段验收这些耗 CPU/内存的重活，等代码在工作树里合好后，由主会话统一通过 `flow run` 提交到机器池槽位执行。
3. **按目标切任务，自己做定位和审查。**
   - 读 diff、二分定位、对比输出、解决合并冲突、几行小修，协调器自己在主会话里做。
   - 任务说明只写目标、证据和判据；修复和返工如果换了问题或原会话已很长，直接开新任务，不续长会话。
   - subagent 交回改动后，协调器自己读 diff 审核、修正细节并合入集成分支，不需要开多轮独立审查循环。
4. **验收。** 用 `ci/accept --plan` 算出改动涉及的阶段，通过 `~/curryflow/machines/flow run` 提交到确认可用的机器槽位跑：
   ```sh
   ~/curryflow/machines/flow run --project <项目> --commit <中枢分支或SHA> --machine <机器> --prio gate --no-wait -- ci/accept --stage <阶段>
   ```
   合并只认机器池槽位里跑出的 `accept: PASS`。
5. **合并（每次推 main 两步）。** 验收全绿后，按顺序执行：
   ```
   (1) 推中枢 main        git push hub main
   (2) 推 GitHub main     git push origin main
   ```
   **发版不跟提交走。** main 上的提交不必出发布件；版本是仓库根的 `VERSION` 文件（`X.Y.Z`）。要发版时：
   ```
   (1) 在 main 上提交一个把 VERSION 升上去的提交，全部验收阶段在它上面通过（不只是 --plan 算出的那几个）
   (2) 在机器池跑 make release，校验 sha256
   (3) gh release create v<版本>，传 <名字>-<版本>-<平台>.tar.gz、.sha256、.manifest.json
   ```
   下游仓库在 `deps.json` 里钉版本和 sha256，没有新版本就继续用旧的；发布件不替换，改了就升版本。
   各机器上给人看的主目录（`~/code/<项目>`）不用手工同步：登记在 `machines.json` 的 `projects.<项目>.home` 里，beat 每
   10 分钟把它的 main 快进到中枢的 main（见“机器池”）。

通用规矩：

- **只汇报一次，在最后。** 一件事做完报一次结论和关键数字表，不发中间轮询进度。
- **卡住就停下分类。** 同一个问题 2–3 轮没进展就停：环境坏了报给用户修机器；规格不清问人；其余换思路。
- **跑检查前先问**：它能发现什么具体的失败？真出现了下一步会做什么不同的事？答不上就不跑（不写多层冗余验证和无意义的变异测试）。
- **只动自己起的进程。** 结束进程只按自己记下的 PID，不用 `pkill -f` / `pgrep -f` 盲杀。
- token、密码、机器 IP 不打印、不写日志、不进仓库（留在 `~/.config/curryflow/local.env`）。

## 机器池

槽位归机器，不归项目：几个项目共用同一批机器，机器清单只有一份，每台机器一个 executor 管它的槽位。重活（仿真、综合、
验收阶段、打包发布件）向机器池要槽位。两条命令，在任何一台机器的 `~/curryflow/machines` 里：

```sh
# 提交一个作业并等结论：打印日志最后 40 行、结论和完整日志的位置，退出码就是作业的退出码
~/curryflow/machines/flow run --project curryGPU --here -- ci/accept --stage func
# 占用一个槽位若干小时（最长 8）：打印机器、槽位、核、容器、目录；用完 flow release <id>，到期自动放
~/curryflow/machines/flow hold --hours 2 --machine box-b
```

- `flow run --project <名> [--commit <提交或中枢分支> | --here] [--threads N] [--mem G] [--gpu] [--machine M] [--slot S]
  [--prio gate|normal|night] [--timeout 秒] [--tag synth] [--clean] [--no-wait] -- <命令>`。不给 `--commit`、`--here` 就用中枢 main；
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
- 作业之间传目录：`flow get --project <名> <名字> <新目录>`。以 0 结束的作业留在工作树 `.build/cf/share/<名字>/` 里的每个目录，
  executor 在推结论之前先推到项目中枢的 `refs/cf/share/<名字>/<id>`（一个没有父提交的提交，树就是那个目录），别的机器用
  `flow get` 取；一个名字只有一份内容（后来的作业顶掉先前的），留 14 天。中枢上没有这个名字时 `flow get` 退出码 3。`flow get`
  要用本机到中枢的通路，容器槽位里的作业用不了（它们只能往外共享）。
- `flow ls`：排队、在跑、最近完成。`flow cancel <id>`：没开始的撤掉（写 143 的结论，中枢上已经有结论时不盖它），在跑的
  结束它的全部进程。排队时撤掉和 executor 认领撞上时，认领的 executor 看到中枢上已有的结论（取消、不合规）就结束作业、
  不推自己的结论；只有已有的结论是"丢了"时才接着跑。
- 作业的环境与 `.build/` 清理规则：工作目录是这个槽位里该项目的常驻工作树，checkout 到指定提交；未跟踪文件清掉。
  `.build/` 的清理分三层：
  1. 每次作业开始前，executor 必清 `.build/accept`、`.build/synth`、`.build/release`、`.build/downstream`，防止复用上一个候选留下的报告、综合网表或发布包；
  2. executor 在 `.build/.cf-env.json` 记录当前 `deps.json` 内容与槽位环境（`$CF_DEPS_CACHE`、容器名）；只要 `deps.json` 或槽位环境变了，或者 `flow run` 带了 `--clean`，整个 `.build/` 全部清空；一致时保留其余编译目录做增量构建；
  3. 整个槽位目录不超过 50 GiB：作业准备好后和结束后各量一次，超了就按 `.cf-env.json` 的修改时间从旧到新整个删掉各工作树的 `.build/`（没有这个文件的最先删），降到 50 GiB 以内为止。作业前不删本作业项目的；作业后它排最后，只有它自己就让槽位超限时才删（要共享的 `.build/cf/share` 留着）。`.cf/`、`.home/` 算在总量里但不删；删完还超只在作业日志里记警告，作业照常跑。
  环境变量有 `CF_DEPS_CACHE`、`PYTHONDONTWRITEBYTECODE=1`、`TMPDIR=<工作树>/.build/tmp`、`CF_SLOT`、`CF_CPUS`、
  `CF_THREADS`（逻辑线程数）、`CF_CORES`（物理核数）、`CF_MEM_GB`（槽位内存上限 GiB）、`CF_JOB`；`HOME` 在容器里是
  `<槽位目录>/.home`，不用容器时是真实的家目录。没有容器的机器上是 `taskset` 钉核加带内存上限的 systemd 用户 scope；
  有容器的机器上在槽位的容器里以宿主机用户的 uid/gid 跑，写出的文件属主是宿主机用户，不是 root（容器里这个用户名被别的
  uid 占了时，用户名是 `cf<uid>`）。准备阶段的 git 在宿主机上跑，也钉在槽位的核上。
- 排队规则：先按优先级（gate > normal > night），同优先级里提交人轮转，再按提交时间；一个作业占一个槽位；`--tag synth`
  的作业一台机器同时只跑一个。槽位可排 = 没有 executor 自己的作业或占用，并且实测忙线程不到 20%；共享机上别人直接用过
  的槽位要安静 5 分钟才算空闲。**要在槽位的核上手工跑东西，先 `flow hold`**：不占用的话 executor 只能靠负载猜。
  hold 期间直接在 `<槽位目录>/<项目>` 工作树里改的东西，下一个作业的 `checkout -f` 和 `clean` 会清掉（`.gitignore` 里的
  除外）：要留的先提交或拷走。
- 谁提的：`$CF_WHO`，没设就是 `用户@主机:分支`。

中枢上的 `machines.git` 放机器池的全部东西，每台机器 clone 到 `~/curryflow/machines`：

- `machines.json`：每台机器的槽位（钉的核、内存、容器名、有没有 NV 显卡 `gpu`）、要报告的挂载点、`executor: true`、依赖缓存
  `deps_cache`、给作业加的环境变量 `env`；直连中枢不稳的机器写 `hub_proxy`（和 `host`）。`projects` 是项目名到中枢地址的表：`seed` 是
  每台机器上已有的 clone（镜像从它建），`seed_only` 表示不许从中枢全量取（中枢小，大仓库都要写）；`home` 是每台机器上给人
  看的主目录（`{"<机器>": "~/code/<项目>"}`），beat 让它的 main 跟着中枢的 main。
- `beat`、`status`、`executor`、`flow`、`mac-agents` 从 `templates/pool/` 拷来。
- 状态全是 ref（JSON blob，格式见 `executor` 开头）：`refs/hb/<机器>` 心跳；`refs/q/<id>` 请求；`refs/run/<id>` 认领，用
  "该 ref 必须不存在"的 push 建，只写这一次，两个 executor 抢同一个请求只有一个成功；`refs/done/<id>` 结论（退出码、
  最后一行、用时、日志最后 40 行、完整日志在哪台机器的哪个路径），结束 7 天后清；`refs/cancel/<id>` 取消在跑的。作业
  活着靠机器心跳：beat 把 executor 手上的请求 id 带进 `refs/hb/<机器>`，beat 停了时由 executor 推 `refs/alive/<机器>`
  顶上，不按作业推心跳。不合规的请求（字段类型或取值不对、半小时没有哪个槽位满足）由先看到的 executor 写一个 99 的
  结论并撤掉；`need` 里本机不认识的键或 tag 算"本机跑不了"，不算不合规；"半小时没有槽位满足"只由运行着本机 clone 里
  那版 executor、并且 clone 里的 `machines.json` 和中枢 main 上一样的 executor 判（clone 落后的不判）。项目仓库上只碰
  `refs/cf/tmp/<id>` 和 `refs/cf/share/<名字>/<id>`（上一条）：flow 推，executor 取到提交后删；flow 没交上请求（失败或 Ctrl-C）时自己删；剩下的 executor 每小时清一次（没有请求在用、推了
  一小时以上的）。
- 每台机器两行 cron（装的时候保留已有的行；远端用脚本整段重写，不在 `set -e` 下 `crontab -l | grep -v`）：
  ```
  * * * * * cd ~/curryflow/machines && flock -n .git/beat.lock timeout 55 python3 beat --machine <名> >/dev/null 2>&1  # curryFlow 机器心跳
  * * * * * cd ~/curryflow/machines && flock -n .git/executor.lock python3 executor run --machine <名> >/dev/null 2>&1  # curryFlow executor
  ```
  beat 每分钟把本机状态推成心跳：各 NUMA 节点和各槽位的忙线程、内存、磁盘、显卡、incus 容器、谁在占 CPU（容器内进程按
  持有该容器槽位的提交人归属）、每个槽位里的作业或占用；每 10 分钟自己 `git pull`，心跳里带 clone 的版本和上次 pull 的结果
  （`pull`），机器页上显示版本，pull 失败标红。同样每 10 分钟，beat 对本机登记了 `home` 的项目从中枢取 main（记成
  `refs/remotes/hub/main`）并快进本地的 main：主目录正检出 main 时连工作区一起快进（有本地改动挡着时 git 拒绝，目录不动），
  检出的是别的分支时只动 main 这个分支；不切分支、不合并、不回退，main 上有自己的提交就原样留着，子模块不更新。看到的
  结果在心跳的 `homes` 里（分支、main、落后和超前的提交数、错误），机器页上落后、同步失败或一小时没看过标红。beat 每次
  运行先删掉 clone 里长度为零的松散对象（机器异常重启会把最后写的那个对象留成空文件，之后 beat 和 executor 的 push、fetch
  全部失败）；别的进程正在写的 `tmp_obj_*` 不动。executor 每 15 秒经一条复用的 ssh 连接看一次队列（取项目的提交另开连接）；
  文件变了且手上没有作业时自己换成新版本；被结束了一分钟内由 cron 拉起，接着等在跑的作业，不重跑。beat 停了或卡在慢的探测
  （nvidia-smi、incus）里时，手上有作业的 executor 每 2.5 分钟自己推一次 `refs/alive/<机器>`（它手上的请求 id），作业不会
  因为 beat 坏了被判丢。
- 机器上的布局：`~/curryflow/mirror/<项目>.git` 每个项目一份 bare 镜像，只对中枢增量取；槽位目录是 `~/<容器名>`（有容器，
  容器里同路径）或 `~/curryflow/slots/<槽位>`，里面 `<项目>/` 是工作树，`.cf/<id>/` 是作业目录（`log` 是完整日志）；
  executor 自己的日志和状态在 `~/curryflow/exec/<机器>/`。
- curryPlan 机 clone 到 `~/curryflow/machines-status`，cron 每分钟取心跳、队列和结论，`python3 status --history hist.json`
  写出 `/var/www/plan/curryplan/machines/status.json`；页面是 `templates/curryplan/machines/index.html`：槽位表的"作业"列、
  "排队"和"最近完成"两张表。重发这个页面只 scp `index.html` 和 `meta.json`（`bin/planpub` 会清掉目录里的 `status.json`）。
- 改槽位、加项目：改 `machines.json` 推到 `machines.git`，各机器十分钟内自己拉到。加机器：`beat --probe` → 在 `machines.json`
  加一项 → 在那台机器上 clone `machines.git`、装两行 cron（Mac 上是 `./mac-agents install <名>`）。
- **macOS 机器**（不能钉核，没有 cgroup、`/proc` 和 cron 用的 `flock`）：

  | 方面 | Linux | macOS |
  |---|---|---|
  | 槽位 | 一组钉住的核 | 线程数配额加内存预算：`cpus` 写 `0-<N-1>`，只算个数 |
  | 槽位空闲 | 槽位那几个核的忙线程不到 20% | 整机负载占满槽位以外的核之后，伸进槽位份额的部分不到 20% |
  | 作业的进程 | 监管进程（child subreaper）的进程树 | 监管进程是独立的 launchd agent `cf.<id>`，作业的进程 = 它那个联盟（coalition）里的进程，脱离会话的也跑不出去 |
  | 内存上限 | systemd scope 的 `MemoryMax` 或容器 | 监管进程每几秒加一次联盟里进程的内存，超了结束作业，结论 137 并注明 |
  | 常驻 | 两行 cron + `flock` | 两个 LaunchAgent（`mac-agents install`）：beat 每分钟一次，executor 常驻；只在这个用户登录着时跑 |

  机器标 `"named_only": true`：只接点名给它的请求（`--machine <名>`），因为项目的 Linux 工具链在上面跑不了；不点名的作业和
  占用不会落到它上面。连中枢要走代理的写 `hub_proxy`，作业的 `PATH`、`LANG` 写在 `env` 里（默认的是 Linux 的）。

通用容器（incus 机器）：同一个镜像（基础系统加构建用的包），钉在一个槽位的核上，依赖缓存只读挂入，工作目录是宿主机
`~/<容器名>` 同路径挂入（`shift=true`，容器里的 uid 和宿主机一一对应）；会变的工具（仿真器）走依赖缓存，不进镜像。

## curryPlan

项目的架构文档（`docs/*/index.html`）和待办（`docs/*/todo.html`）直接存在项目仓库里，由 `docs/pages.json` 和 `tools/publish-docs`
与 curryPlan 自动保持同源。页面排版规范（图表与表格优先、禁止大段文字、`<details>` 折叠历史排查明细）、发布与通知做法见 `curryplan.md`。
