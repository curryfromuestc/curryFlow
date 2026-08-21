---
name: curryflows
description: >-
  用 herdr 编排用户指定的 worker CLI：为长跑任务创建独立分支、worktree 和 workspace，
  启动并识别 agent，发送任务说明，等待与巡检执行状态，组织只读复核，验证后合并到本地 main，
  并回收运行资源。适用于“起 curryflows”“派 worker 长跑任务”“监督 worker”“组织并行实现或评审”等请求。
---
# curryflows
用 herdr 管理 worker；不预设本 session 的职责。按用户指定承担派工、评审、裁决或完整流程。
对用户使用中文。`/curryflows status` 输出 `herdr agent list`、`herdr pane list` 和各线程一句话近况。
## 运行边界
- 先执行 `test "${HERDR_ENV:-}" = 1`；失败时说明当前不在 Herdr pane 内并停止。
- 用 `herdr <命令> --help` 确认当前 CLI 语法，并从 JSON 响应读取 ID。
- worker 由用户指定；未指定时先询问，启动前执行 `command -v <worker>`。
- 启动本 session 的第一个可写 worker 前，说明可写参数会自动批准或绕过 worker 的权限检查；
  Codex 的对应参数还会关闭其沙箱。取得用户明确授权后再启动；未获授权时不启动可写 worker。
- 本 session 不直接修改任务产物。每个可写 worker 使用独立 `cfx/*` 分支和 worktree。
- worker 运行期间不修改本地 `main`。只将验证通过的结果合并到本地 `main`；不推送远端。
- 只操作本 session 创建的 `cfx` 前缀 workspace、tab、pane、worktree 和分支。
- 只读评审不得写入被审对象；需要运行代码时使用 detached-HEAD worktree，并在评审前后核对仓库状态。
- 将任务说明写入 worktree 外的临时文件，将事实写入仓库文档，将进度写入 git 和 Herdr label。
## 并行前检查
列出 worker 之间共享的可写状态，包括：
- 浮动 license 和并发额度；
- 固定输出路径；
- 共享构建缓存；
- worktree 中缺失、需要重建的生成物。
无法确认并行安全时先串行执行。
## Worker 能力
| worker | Herdr 控制面 | 完成检测 |
|---|---|---|
| Herdr 可识别的 agent | `agent prompt/read/wait/send-keys` | `agent wait`，心跳兜底 |
| Herdr 未识别的交互式 CLI | `pane send-text/read/send-keys` | 心跳读取 pane |
可写 worker 必须带对应的全权限参数：
| worker | 可写参数 |
|---|---|
| `codex` / `tcodex` | `--dangerously-bypass-approvals-and-sandbox` |
| `claude` / `tclaude` | `--dangerously-skip-permissions` |
| `kimi` | `--yolo` |
| `codebuddy` | `--dangerously-skip-permissions` |
包装器只要呈现 Herdr 可识别的上游 UI，也可使用 agent 控制面。以 `herdr agent explain <pane>` 的结果
为准，不按可执行文件名猜测。
取得可写授权后、启动第一个正式线程前，用临时 pane 携带同一可写参数启动 worker，并通过 `pane read`
确认登录状态；检查完关闭临时 pane。
## 创建可写线程
```bash
herdr worktree create \
  --workspace "$HERDR_WORKSPACE_ID" \
  --branch cfx/<任务名> \
  --base main \
  --label "cfx <任务名>" \
  --no-focus
```
从响应的 `.result.root_pane.pane_id` 读取 `<pane>`，然后启动 worker：
```bash
herdr pane run <pane> codex \
  --dangerously-bypass-approvals-and-sandbox <附加参数...>
herdr pane run <pane> tcodex \
  --dangerously-bypass-approvals-and-sandbox <附加参数...>
herdr pane run <pane> claude \
  --dangerously-skip-permissions <附加参数...>
herdr pane run <pane> tclaude \
  --dangerously-skip-permissions <附加参数...>
herdr pane run <pane> kimi \
  --yolo <附加参数...>
herdr pane run <pane> codebuddy \
  --dangerously-skip-permissions <附加参数...>
```
只执行与用户指定 worker 对应的一条命令。`tcodex` 和 `tclaude` 直接接收并转发这些参数。
在 60 秒内轮询识别状态：
```bash
deadline=$((SECONDS + 60))
while ! herdr agent explain <pane> >/dev/null 2>&1; do
  (( SECONDS >= deadline )) && break
  sleep 2
done
```
识别成功时：
```bash
herdr agent read <pane> --source visible
herdr agent rename <pane> cfx-<小写任务名>
```
agent 名必须匹配 `[a-z][a-z0-9_-]{0,31}`。未识别时读取 pane：若显示登录界面，先完成登录；
若 worker 已正常运行，保留 pane ID，后续使用 pane 控制面。
每个新 worktree 都可能出现目录信任或登录提示。发送任务前必须读取可见区域：
1. `agent read <pane> --source visible`，未识别时用 `pane read`；
2. 仅在明确看到需要确认的安全提示时发送对应按键；
3. 再次读取，确认已进入 worker 主界面；
4. 发送任务后再次读取，确认任务已被接收。
不要仅依据 `idle`、`done` 或命令成功返回判断界面是否就绪。
## 创建只读评审位
需要执行验证时先创建隔离副本：
```bash
git -C <项目> worktree add --detach <副本路径> <目标commit>
herdr worktree open \
  --workspace "$HERDR_WORKSPACE_ID" \
  --path <副本路径> \
  --label "cfx-rv <任务名>" \
  --no-focus
```
在评审 workspace 中按视角创建 tab：
```bash
herdr tab create --workspace <wid> --label "rv-<视角>" --no-focus
herdr pane run <root_pane> <worker> <只读参数...>
```
只读评审位仅使用对应的只读参数，不叠加可写参数：
- Codex：`-s read-only`
- Claude Code：`--permission-mode plan`
- Kimi：`--plan`
- CodeBuddy：`--permission-mode plan`
启动前后分别执行并比对：
```bash
{ git status --porcelain; git diff HEAD; git rev-parse HEAD; } | sha256sum
```
## 编写并发送任务说明
长任务说明写入 worktree 外的 Markdown 文件，例如 `/tmp/cfx-<名>.md`。内容包括：
```text
OUTCOME
  明确产物和可核对证据。连续指标写清对象、口径和对照基线。
VERIFICATION
  worker 必须执行的自查命令。
GENERATED_INPUTS
  所需的 gitignored 生成物及其生成命令。
BUDGET
  时间、轮次或资源硬上限。
BLOCKED_STOP
  无法继续时停止，并报告阻塞点、已尝试方法和所需输入。
```
Herdr 已识别 worker 时：
```bash
herdr agent prompt cfx-<名> \
  "完整任务说明见 /tmp/cfx-<名>.md；先通读，再严格执行。" \
  --wait \
  --timeout 60000
herdr agent read cfx-<名> --source visible
```
未识别 worker 时：
```bash
herdr pane send-text <pane> \
  "完整任务说明见 /tmp/cfx-<名>.md；先通读，再严格执行。"
herdr pane send-keys <pane> enter
herdr pane read <pane> --source visible
```
Codex 的大型、多回合任务可在初始说明前使用 `/goal`。追加指令使用普通消息，避免替换当前 goal。
其他 worker 使用普通消息。
## 等待与巡检
Herdr 已识别 worker：
```bash
herdr agent wait cfx-<名> --timeout 3600000
```
将长时间等待作为后台 shell 任务运行。等待结束后读取 agent 输出；超时视为巡检信号，不视为失败结论。
Herdr 未识别 worker 时，在心跳中读取：
```bash
herdr pane read <pane> --source visible --lines 50
```
每次心跳执行：
```bash
herdr agent list
herdr pane list
```
对运行中的线程逐个读取近况。只保留一句状态结论：
- 继续执行：不干预；
- 偏离任务：发送 `esc` 软停，再发送纠偏说明；
- 已完成：进入独立验证；
- 需要用户裁决：更新 workspace label，并发送通知；
- 长时间无进展：读取上下文，判断阻塞、纠偏或停止。
```bash
herdr agent send-keys cfx-<名> esc
herdr pane send-keys <pane> esc
herdr workspace rename <wid> "cfx <任务名> · ❓等你"
herdr notification show "需要你裁决" --body "<一句话>" --sound request
```
有未识别 worker 时，应按其预期运行时长设置足够频繁的心跳。使用 `/loop` 时，每轮先输出中文状态摘要，
再安排下一次唤醒。
## 验证与合并
1. 独立读取有界 diff，不以 worker 的完成声明代替验证。
2. 独立执行任务说明中的验证命令。
3. 检查连续指标的对象、口径和基线；无法客观裁定时交由用户。
4. 验证通过后，将 worker 分支 rebase 到最新本地 `main`。
5. 合并到本地 `main`，必要时更新基线文档。
6. 不推送远端。
## 回收
```bash
herdr worktree remove --workspace <wid> --force
git branch -D cfx/<任务名>
herdr workspace close <wid>
git worktree remove <副本路径>
```
回收前确认产物、验证记录和需要保留的结论均已落盘。
