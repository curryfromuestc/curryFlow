---
name: curryflows
description: >-
  herdr 注入器:起长跑 worker(默认 codex,可点名换 kimi k3,另备只读位 argv),注入契约、
  后台 wait 事件唤醒、稀疏心跳巡检、驭偏、合并门禁、回收;本 session 的角色(派工/评审/裁决/全包)
  由用户开场注入,skill 不预设流程。触发于:"起 curryflows"、"派个 codex 长跑任务"、"监督 codex 别跑飞"。
user-invocable: true
argument-hint: "[本 session 的角色 + 要推进的活]"
type: skill
tags: [herdr, codex, kimi, injector, loop]
requires: ["herdr (>=0.8)", codex CLI, kimi CLI, "git (>=2.5, worktree)"]
---

# curryflows

herdr 注入器:只提供机制、不预设流程,本 session 的角色由用户开场说明;没有看板文件、没有
辅助脚本,**herdr 就是看板**。对用户叙述一律中文;`/curryflows status` = herdr agent list + 各线程一句话近况。

## 第 0 步:拿授权

worker argv 若含 `--dangerously-bypass-approvals-and-sandbox`,**必须先向用户要授权**再起第一个线程:
skill 自带的危险标志一律被 Claude Code 分类器判为 agent 自选而拒绝(实测)。说明理由(沙箱会挡掉
worker 要用的 docker/外部工具),拿到答复再起;不授权就用 `--full-auto`。

## 铁律

- 本 session 不亲手改产物代码,一切改动经 worker;每 worker 独立分支 + worktree,全程不碰 main。
- **worker 在途期间不往本地 main 提交**——文档 commit 排队等合并窗口;真撞上了用 rebase 不用 merge。
- 合并只进**本地** main,**推送远端永远归人类**。
- 契约必带 BUDGET(硬上限)与 BLOCKED_STOP(无路可走即停下报告)。
- 只读位永不带写权限进被审对象;要跑代码的复验位在 detached-HEAD 副本里跑。
- 用户自己的 workspace 永不触碰,只操作自己创建的 `cfx` 前缀 workspace。
- **状态一律外置**:契约落文件、事实落仓库文档、进度落 git。本 session 上下文会被压缩(实测 7 小时
  6 次),脑子里记的线程清单与踩过的坑必丢;herdr 的 label 只够当状态灯,不够当记忆。

## 开并行线程前:共享可写状态 pre-flight

worktree 只隔离文件树。先列出 worker 之间共享的可写状态:浮动 license(并发数)、工具的固定输出
路径(下一次 run 静默覆盖上一次的报告)、共享构建缓存、需重建的跨 worktree 生成物。**列不出来就
先串行跑一段,边跑边测。**

## herdr 操作手册(0.8;`herdr <子命令> --help` 是唯一权威,输出默认 JSON)

**起线程**(一条命令 = 分支 + worktree + 带名 workspace;返回的 `.result.root_pane.pane_id` 就是 agent 要用的窗格):
```bash
herdr worktree create --workspace "$HERDR_WORKSPACE_ID" --branch cfx/<任务名> --base main \
  --label "cfx <任务名>" --no-focus
# --base main 必带,否则从当前 HEAD 切;想让线程挂到项目 workspace 而非本 session 名下,才用 --cwd <项目>
herdr agent start cfx-<小写任务名> --kind codex --pane <root_pane> --timeout 120000 \
  -- --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=max
# agent 名:小写开头,只含 a-z0-9-_,≤32 字符,大写直接报 invalid_agent_name;workspace label 不受此限
# --kind 已决定可执行文件,`--` 后只跟追加参数,别再写一遍 codex;kimi worker:--kind kimi -- --yolo -m kimi-code/k3
```

**起只读位 / 多位编组**(评审、审计类角色;一位一 tab):
```bash
git -C <项目> worktree add --detach <副本路径> <目标commit>      # 复验位跑代码用的隔离副本
herdr worktree open --workspace "$HERDR_WORKSPACE_ID" --path <副本路径> --label "cfx-rv <任务名>" --no-focus
herdr tab create --workspace <wid> --label "rv-<视角>" --no-focus   # 同样返回 root_pane
herdr agent start rv-<小写名> --kind codex --pane <root_pane> -- -s read-only -c model_reasoning_effort=medium
# 只读 argv:codex -s read-only / kimi --plan / claude --permission-mode plan
# 防篡改核验:起位前后各比对 { git status --porcelain; git diff HEAD; git rev-parse HEAD; } | sha256sum
```

**注入任务**:长提示词(契约、任务书、评审维度书)一律先落 md 文件,composer 只进一行"读文件"短
指令;直贴长文已实测翻车(截断丢护栏段、CJK 残留草稿 ctrl+u 清不掉)。契约文件**写在 worktree 外**
(如 `/tmp/cfx-<名>.md`),worker 就无从把它 commit 进产物——`.git/info/exclude` 在 linked worktree 里
解析到主仓公共目录,写进 `.git/worktrees/<名>/info/` 是空转(实测)。
```bash
herdr agent prompt cfx-<名> "完整契约见 /tmp/cfx-<名>.md:先通读它,再严格按其执行" --wait --timeout 60000
# 一条顶旧版 send + send-keys enter + wait 三步;正文是位置参数,不要写 --text
# 返回 agent_prompt_stalled 就原样重发一次:agent start 之后的首个 prompt 必 stall(实测)
```

契约四段 + 两格(codex 用 `/goal` 前缀持久化,kimi 去前缀直发):
```
OUTCOME      要什么,必须写出可核对的物证。「验证 X 可用」只会收到"能打开,可用";写「用 X 定位到
             一处真实失配,且只用文本查询」才逼得出证据。连续量(覆盖率/面积/耗时/精度)必须钉死
             三元组:测哪个对象 / 什么口径 / 跟谁比——只写"达标"必然收到一个满足契约但量错对象的结果。
VERIFICATION 自查命令
生成物依赖   本段要用到的 gitignore 生成物,及产生它的那条命令。新 worktree 里它们不存在,漏写
             worker 就卡住或绕路(实测同一成因犯三次);复核者的复跑脚本照这格写。
BUDGET       硬上限
BLOCKED_STOP 无路可走即停下报告
```
/goal 跨回合自驱推进,适合大任务;小任务普通消息即可。给在途 codex 追加指令发**普通消息**、绝不带
`/goal` 前缀(弹 "Replace goal?" 顶掉原契约,esc 可退)。

**完成唤醒**:挂后台 Bash 任务(run_in_background),退出即 task-notification 唤醒:
```bash
herdr agent wait cfx-<名> --timeout 3600000   # 不带 --until 即匹配 idle|done|blocked 三个终态
# 认 agent 名也认 pane_id;done 是"完工但 tab 没在 UI 里被看过"的 idle,与 idle 同一状态,一条全覆盖
```
唤醒是信号不是证据:醒来先 `herdr agent read` 确认(不认识的菜单会让状态回落 idle);超时同样唤醒,
当卡死信号处理;pane 已回收时 wait 立即报错退出。`pane wait-output --match` 慎用:worker 复述契约即
把字面量带上屏误触发,只当确认证据、不当唤醒源。

**巡检 / 驭 / 标状态 / 回收**:
```bash
herdr agent list                                      # 全线程状态
herdr agent read cfx-<名> --source recent-unwrapped --lines 50   # kimi 此源返回空,改 --source visible
herdr agent send-keys cfx-<名> esc                    # 软停:进程存活、上下文完整;纠偏 = 软停后照「注入任务」再发
herdr workspace rename <wid> "cfx <任务名> · ❓等你"    # label 就是看板,状态变了就改名
herdr notification show "需要你裁决" --body "<一句话>" --sound request
herdr worktree remove --workspace <wid> --force       # 回收线程;分支自删 git branch -D cfx/<任务名>
herdr workspace close <wid>                           # 回收编组;副本另收 git worktree remove <副本路径>
```

## 合并门禁:唯一真正拦住问题的地方

实测 43 次巡检 tick 抓到 0 个质量问题,质量问题 100% 在这一节被抓。**别加密巡检,要加厚门禁。**

1. **协调器独立复跑,不读 worker 的自称。** worker 自查拦得住"自称完成",拦不住量错对象。
2. **机器验布尔量,人验连续量。** 覆盖率/面积/时序余量/精度这类,机器只看到"有个数字"。实测一次
   人工抽查捅破了"覆盖率 dashboard 把测试平台自身代码算进分母,两套平台数学上不可比"——worker
   100% 满足契约,同时量错对象。裁不动的连续量交人。
3. 有界读 diff → 跑验证 → 绿了才 rebase + 合本地 main → 回填基线文档。

## tick:事件驱动为主,稀疏心跳兜底

主等待路径是 `herdr agent wait` 后台任务;`/loop` 心跳放到 20-30 分钟,**只兜"worker 卡死不动"的底**
——实测 43 次 tick 的全部战果是 2 次卡死唤醒,却吃掉 79% 的成本。每个 tick 必须 `herdr agent list`
全量扫一遍,不能只看唤醒源,working 线程逐个 read 近况;**逐 tick 的日志观察不进主上下文**,读完
就丢、只留一行结论。取向:跑偏 → 驭;干完 → 按开场注入的角色处置(移交下游、上报、或过门禁合并
后回收);裁不动 / 对外不可逆 → rename + notification,其余线程照推。**收尾硬规则:先摘要,后约钟**
——先用中文输出一行如实摘要(no-op 也要说,不绿洗),ScheduleWakeup 必须是本 turn 最后一个动作,
顺序反了 tick 会静默收工(已观测多次)。
