---
name: curryflow
description: >-
  多机开发流程的唯一入口。给一个项目引导出流程：探测机器、和人定验收阶段与判据、定存储规矩，生成项目的 ci/accept、
  docs/pages.json、tools/publish-docs、AGENTS.md 和 .gitignore，接入机器池；写清协调器的通用规矩
  （派任务前和人定好用哪台机器哪个槽位、本地 subagent 只写代码不建多工作树、合并后主会话统一跑重活、发布件按版本号发、不跟提交走）；
  管机器池（machines 仓库、每分钟心跳 beat 与内存/换页告警、在指定槽位上跑作业的 flow run、curryPlan 上的"机器"页）；
  管 curryPlan 页面（仓库 docs/*.html 与 curryPlan 同源自动发布、图表与表格优先、禁大段文字）。触发于："用 curryFlow
  引导 X"、"给项目搭多机流程"、"加一台机器"、"看机器负载 / 槽位"、"在机器池上跑个作业"、"发到 curryPlan"、
  "写个方案页 / 学习页 / 论文页"、"给项目开个 curryPlan"。
---

# curryFlow

curryFlow 是多机开发、机器池和 curryPlan 文档的统一入口。对人的一切叙述用中文。

```
GitHub（唯一的代码仓库）
  ├─ 各项目仓库             main 和 wip/* 分支；合入只推 GitHub main
  └─ curryflow-machines     机器池仓库：machines.json 和下面这些脚本
机器池（每台机器一份 ~/curryflow/machines）
  ├─ beat                   每分钟采集负载、内存、换页、磁盘、GPU、容器、各槽位的作业，算告警，写到 curryPlan 机
  ├─ executor               不常驻：flow 经 ssh 调它，在一个槽位里起一个作业
  └─ flow                   在人定好的机器和槽位上跑作业（flow run），看作业（ls / wait / log / cancel）
curryPlan 机（阿里云）      收心跳、出"机器"页；也是 109 到 aimax 的 ssh 中转
项目仓库（~/code/<项目>）
  ├─ ci/accept              分阶段验收入口（配合 ci/stage.sh），按改动分级（--plan）
  ├─ docs/                  docs/*/index.html（设计）与 todo.html（待办），由 tools/publish-docs 同步到 curryPlan
  └─ AGENTS.md              项目专属规矩
```

机器之间不挂共享盘。GitHub token 在每台机器的 `~/.config/curryflow/github-token`（权限 600），不进仓库、不打印。

## 引导新项目

按顺序做，每步做完核对再往下。需要人拍板的是验收判据和用哪些机器、槽位。

1. **探测机器并配进机器池。** 对新机器跑 `ssh <别名> python3 - --probe < templates/pool/beat`，读输出的 JSON（NUMA 节点、L3 域、内存、GPU、磁盘、incus 容器）：
   - 一个仿真槽位 = 一个 L3 域（8 个物理核加超线程；L3 不足 8 核则拼同一 NUMA 节点相邻的 L3）。
   - Linux 机器的槽位都是 incus 通用容器（钉核、限内存）；Mac 不用容器，槽位是一个目录。共享机标 `"shared": true`，只占人给的份额。
   - 在 machines 仓库的 `machines.json` 里登记机器槽位和项目（`projects.<项目>.repo` 是 GitHub 地址），推 GitHub。
2. **定验收（和人一起）。** 列阶段：名字、命令、判据。用例来自真实 bug，按改动文件的风险分级（`TIERS`）填进 `ci/accept`。
   - **共享机资源铁律**：验收和构建脚本取 CPU 和内存时，用槽位注入的 `$CF_CPUS`、`$CF_THREADS`、`$CF_MEM_GB`，或读进程自己的 `os.sched_getaffinity(0)` 与当前 cgroup 的 `memory.max`；**严禁读整机的 `/proc/meminfo` 或全局 cpuset**。
3. **定存储。** 派生物只准在 `.build/`（`ci/accept` 每阶段前后扫描工作区，写到 `.build/` 以外直接判失败）；`.gitignore` 写 `.build/`。外部工具链与测试数据写进项目的 `deps.json`，由 `tools/provision` 按版本和 sha256 拉进机器的依赖缓存 `$CF_DEPS_CACHE`，再链到 `.build/third_party`。
4. **生成项目文件。**
   - `cp templates/ci/accept <项目>/ci/accept`
   - `cp templates/project/publish-docs <项目>/tools/publish-docs`
   - 从 `templates/project/AGENTS.md` 和 `templates/project/gitignore` 生成 `AGENTS.md`、`.gitignore`
   - 建 `docs/pages.json`、设计文档 `docs/<模块>/index.html` 和待办 `docs/<模块>/todo.html`（格式见 `curryplan.md`），把 `tools/publish-docs --check` 放进 `style` 验收阶段。
5. **开 curryPlan 自动同步。** 按 `curryplan.md` 注册项目；在 curryPlan 机上建项目的稀疏 clone（只检出 `docs/` 和 `tools/publish-docs`，远端是 GitHub），cron 每分钟拉 `main` 跑 `tools/publish-docs`。

## 协调：怎么分活与跑完一个任务

1. **跑之前和人定好放哪。** 每个作业在哪台机器、哪个槽位跑，先和人确认，再 `flow run --machine <机器> --slot <槽位>`。没有排队和自动分配：槽位正在跑作业时 flow 直接报错。别人声明在用的机器不派；不占用槽位以外的宿主机 CPU。
2. **本地 subagent 只写代码，重活归主会话。**
   - 除非人明确指定某个 subagent 去跑测试/综合，否则本地 subagent **只做代码阅读、静态分析和修改代码**。
   - **禁止建一堆 git worktree**：多个按文件切开的机械修改任务共用同一个工作树；用完的工作树及时清理。
   - **禁止 subagent 起无限阻塞的后台进程**（`tail -F`、`watch`、没超时的轮询循环）。
   - 编译、综合、RTL 仿真、分阶段验收这些耗 CPU/内存的重活，等代码合好后，由主会话通过 `flow run` 放到定好的槽位执行。
3. **按目标切任务，自己做定位和审查。** 读 diff、二分定位、对比输出、解决合并冲突、几行小修，协调器自己做；subagent 交回改动后，协调器自己读 diff 审核、修正并合入。
4. **验收。** 用 `ci/accept --plan` 算出改动涉及的阶段，放到定好的槽位跑：
   ```sh
   ~/curryflow/machines/flow run --machine <机器> --slot <槽位> --project <项目> --here -- ci/accept --stage <阶段>
   ```
   合并只认机器池槽位里跑出的 `accept: PASS`。
5. **合并。** 验收全绿后快进推 GitHub main（`git push origin main`）。
   **发版不跟提交走。** 版本是仓库根的 `VERSION` 文件（`X.Y.Z`）。要发版时：
   ```
   (1) 在 main 上提交一个把 VERSION 升上去的提交，全部验收阶段在它上面通过
   (2) 在机器池跑 make release，校验 sha256
   (3) gh release create v<版本>，传 <名字>-<版本>-<平台>.tar.gz、.sha256、.manifest.json
   ```
   下游仓库在 `deps.json` 里钉版本和 sha256；发布件不替换，改了就升版本。各机器上给人看的主目录（`~/code/<项目>`）登记在
   `machines.json` 的 `projects.<项目>.home` 里，beat 每 10 分钟把它的 main 快进到 GitHub 的 main。

通用规矩：

- **只汇报一次，在最后。** 一件事做完报一次结论和关键数字表，不发中间轮询进度。
- **卡住就停下分类。** 同一个问题 2–3 轮没进展就停：环境坏了报给用户修机器；规格不清问人；其余换思路。
- **跑检查前先问**：它能发现什么具体的失败？真出现了下一步会做什么不同的事？答不上就不跑。
- **只动自己起的进程。** 结束进程只按自己记下的 PID，不用 `pkill -f` / `pgrep -f` 盲杀。
- token、密码、机器 IP 不打印、不写日志、不进仓库。

## 机器池

槽位归机器，不归项目；一个槽位同时只跑一个作业。flow 在 `~/curryflow/machines` 里（109 或 aimax 上发起都行）：

```sh
# 在 aimax 的 p1 上跑，等结束：打印告警、日志最后 40 行、结论和完整日志的位置，退出码就是作业的退出码
flow run --machine aimax --slot p1 --project sdk --here -- ci/accept --stage func
flow run ... --no-wait -- <命令>      # 只打印 机器:作业号，作业不依赖这条命令和它的 ssh 连接
flow wait aimax:<作业号>              # 接着等（ssh 断了、Ctrl-C 了都不影响作业）
flow log aimax:<作业号> -n 200
flow cancel aimax:<作业号>
flow ls [--machine aimax]             # 各机器在跑的、最近结束的作业和告警
```

- `--commit` 是提交或 GitHub 上的分支、tag（默认 main）；`--here` 用当前仓库的 HEAD，经 ssh 直接推进目标机器的镜像
  `~/curryflow/mirror/<项目>.git`，GitHub 上不需要有这个提交。命令只有一个词时用 `bash -c` 跑。默认超时 4 小时，最长 24 小时。
  退出码：作业自己的；124 超时，143 取消，99 没起来（取不到提交、准备失败），255 丢了（启动进程和监管进程都不在了）。
- flow 经 `ssh <机器名>` 调目标机器上的 `executor`（`machines.json` 的 `ssh` 可以给某台发起机写别的别名，如 aimax 上用
  `ultra9-lan`）；发起机的 `~/.ssh/config` 里每台机器要有对应条目。在本机就直接调。
- executor 不常驻：`start` 在槽位目录拿 `.cf/busy`（占着就报错），建作业目录 `.cf/<id>/`，起一个脱离会话的启动进程后返回；
  启动进程在宿主机上从 GitHub 取提交（宿主机的 ssh key）、checkout，再进容器（`incus exec`，宿主机用户的 uid/gid）或在 Mac 上
  作为 system 域的 launchd daemon 起监管进程。监管进程管超时、取消和收尾：作业结束时把它留下的进程全部结束（Linux 按
  child subreaper 的进程树，Mac 按 launchd 给的联盟），写 `exit.json`，最后放掉 busy。作业目录留 7 天。
- 作业的环境：工作目录是槽位里该项目的常驻工作树（checkout 到指定提交，未跟踪文件清掉）；`CF_DEPS_CACHE`、
  `GH_TOKEN`（只给 release 下载用），作业在哪跑就在哪自己取依赖；git 一律走 ssh：宿主机直接用自己的 key，
  容器里用只读挂在 `/opt/cf-ssh` 的宿主机 `~/.ssh`（`GIT_SSH_COMMAND` 指向它）；`TMPDIR=<工作树>/.build/tmp`、
  `CF_JOB`、`CF_MACHINE`、`CF_SLOT`、`CF_CPUS`、`CF_THREADS`、`CF_MEM_GB`；容器里 `HOME` 是 `<槽位目录>/.home`。
- `.build/` 里的东西归项目自己清：`ci/accept` 每个阶段开跑前清掉自己的 `.build/accept/<阶段>`，其余产物由项目的
  脚本和 `make clean` 负责，产物只许写在 `.build/` 里。executor 只做与项目无关的：`deps.json` 或槽位环境变了（记在
  `.build/.cf-env.json`）或带 `--clean` 时整个清空；整个槽位目录不超过 50 GiB，作业前后各量一次，超了按 `.cf-env.json`
  的时间从旧到新删各工作树的 `.build/`（作业前不删本作业项目的）。
- **告警**：beat 每分钟看容器内存是否超过上限 90%、机器可用内存是否不到 5%、最近一分钟每秒换页是否超过 4 MiB。告警写进
  心跳（机器页标红）和本机 `~/curryflow/exec/<机器>/alerts.json`；`flow run` / `flow wait` 等作业时这台机器出告警会立刻打印。
  只上报，不结束作业。

machines 仓库（GitHub `curryflow-machines`，每台机器 clone 到 `~/curryflow/machines`）：

- `machines.json`：`page`（收心跳的 curryPlan 机，`user@host`）；每台机器的 `host`（主机名）、槽位（核、内存、容器名、`gpu`）、
  要报告的挂载点、依赖缓存 `deps_cache`、给作业加的环境变量 `env`、`ssh`；`projects` 是项目名到 `repo`（GitHub 地址）和
  `home`（每台机器上给人看的主目录）的表。
- `beat`、`status`、`executor`、`flow`、`mac-agents` 从 `templates/pool/` 拷来；`pool-container/` 是容器镜像的脚本。
- Linux 每台机器一行 cron：
  ```
  * * * * * cd ~/curryflow/machines && flock -n .git/beat.lock timeout 55 python3 beat --machine <名> >/dev/null 2>&1  # curryFlow 机器心跳
  ```
  Mac 上是 `./mac-agents install <名>`（beat 作为 system 域里以本用户运行的 LaunchDaemon，不需要有人登录；同时装
  `/etc/sudoers.d/curryflow`，免密只放行 executor 要用的 `launchctl bootstrap system`、`bootout system/cf.*` 和把作业
  plist 改成 root 属主的 `chown`）。FileVault 的 Mac 重启后要先在本机解锁一次。
- beat 每 10 分钟 `git pull` 这个 clone，并把本机登记了 `home` 的项目从 GitHub 取 main、快进本地 main（有本地改动挡着、main
  上有自己的提交就不动）；结果在心跳的 `pull`、`homes` 里，机器页上落后或失败标红。
- 心跳是 ssh 写到 curryPlan 机 `~/curryflow/hb/<机器>.json` 的 JSON；curryPlan 机 cron 每分钟在 machines 仓库的 clone
  里跑 `python3 status --history hist.json` 写出 `/var/www/plan/curryplan/machines/status.json`；页面是
  `templates/curryplan/machines/index.html`（告警表、各机器的槽位和作业）。重发页面只 scp `index.html` 和 `meta.json`。
- 改槽位、加项目：改 `machines.json` 推 GitHub，各机器十分钟内自己拉到。加机器：`beat --probe` → 在 `machines.json` 加一项 →
  在那台机器上 clone machines 仓库、装 cron 或 mac-agents、放 GitHub token → 在发起机的 `~/.ssh/config` 里加它。
- 每台机器上的数据只分四类，各放一处：

  | 类别 | 位置 | 对容器 | 规矩 |
  |---|---|---|---|
  | 代码 | `~/code/<项目>`（git 仓库） | 不挂（aimax 例外：rtlsim 开发树） | 只放源码和输入数据 |
  | 调度 | `~/curryflow`，只许 `machines`、`mirror`、`exec`、`slots`、`hb`（curryPlan 机） | 不挂载 | 不放大文件 |
  | 依赖 | `~/deps`：`cache`（`$CF_DEPS_CACHE`）和 `tools`（第三方工具） | `cache` 读写同路径挂入，`tools` 只读挂到 `/opt/tools` | 只留各项目 main 的 `deps.json` 引用的条目，下载包装完即删 |
  | 产物 | 槽位目录 | 读写 | 每个槽位目录不超过 50 GiB |

  依赖缓存只由槽位里的作业填（容器里装的就是容器的用户态），不在宿主机上编译条目；各机器登录 shell 的 `CF_DEPS_CACHE`
  只用于手工查看。
- curryFlow 本身也是登记的项目（`projects.curryFlow`），每台机器的 `home` 是 `~/code/curryFlow`，`~/.claude/skills/curryflow`
  链接到它。改 curryFlow：提交推 GitHub main，`templates/pool/` 里的脚本同时拷进 machines 仓库（提交说明写 curryFlow 的提交号）。

通用容器（incus 机器）：只负责隔离运行时环境，自己不存数据。同一台机器的容器用同一个镜像（基础系统加构建用的包，见 machines
仓库的 `pool-container/`），钉在槽位的核上，挂入依赖缓存、`/opt/tools` 和槽位目录（`~/<容器名>`，同路径，容器里的 uid 和
宿主机一一对应）；自研工具由作业从 GitHub 取源码在槽位里构建。Mac 不用容器：槽位就是 `~/curryflow/slots/<槽位>` 这个目录，
不钉核，作业进程按 launchd 联盟回收。

## curryPlan

项目的架构文档（`docs/*/index.html`）和待办（`docs/*/todo.html`）直接存在项目仓库里，由 `docs/pages.json` 和 `tools/publish-docs`
与 curryPlan 自动保持同源。页面排版规范（图表与表格优先、禁止大段文字、`<details>` 折叠历史排查明细）、发布与通知做法见 `curryplan.md`。
