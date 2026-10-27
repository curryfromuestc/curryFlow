---
name: curryflow
description: >-
  给一个项目引导出多机开发流程：探测机器、和人定验收阶段与判据、定存储规矩，从模板生成项目自己的
  ci/（accept、machines、coord、executor）、AGENTS.md 和 .gitignore，建中枢 git 仓库和镜像，在每台机器装
  executor，开 curryPlan 项目；并写清协调器的通用规矩（任务生命周期、只看 diff 的独立审查、一次汇报、
  按任务挑模型、卡住就停）。触发于："用 curryFlow 引导 X"、"给项目搭多机流程"、"起协调器"、"加一台机器"。
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
   - agent 容器是单独的席位，每个 2–4 核，不占上面几类；核不够分的机器（aimax 16 核）席位写成个数、不钉核，
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
7. **开 curryPlan 项目。** 按 curryPlan skill 注册项目；状态页用 `templates/curryplan/status/`（把 `__PROJECT__`
   换成项目名），在 curryPlan 机上另 clone 一份，cron 每分钟 `ci/coord status --json` 写到页面目录的 `status.json`；
   方案页从 `templates/curryplan/plan/` 起，只写最终形态。

`ci/machines`（JSON）：

```json
{"project": "toy", "hubs": ["ci@<主中枢>:toy.git", "ci@<备用中枢>:toy.git"],
 "curryPlan": {"host": "yanggl@<curryPlan 机>", "page": "/var/www/plan/toy/status", "clone": "~/curryflow/toy-status"},
 "machines": {
  "aimax": {"ssh": "aimax", "dir": "~/curryflow/toy", "backend": "incus", "shared": false, "hub_proxy": "127.0.0.1:7890",
            "incus": {"image": "agentbox-base", "pool": "agentbox"},
            "slots": {"sim0": {"class": "sim", "cpus": "0-7,16-23", "mem": "24GiB", "disk": "20GiB"}},
            "agents": {"seats": ["4", "4"], "mem": "16GiB", "disk": "30GiB", "token": 1, "agentctl": "agentctl"},
            "cache": {"dir": "~/.cache/curryflow", "budget_gb": 100}, "keep_days": 7},
  "bics109": {"ssh": "bics109", "dir": "~/curryflow/toy", "backend": "bare", "shared": true,
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
