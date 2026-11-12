# curryPlan

人看的那一面：一个手机上也好用的页面目录（PWA），按项目分组，最新的在上。每个页面上能选中实体评论（文字或
手绘）和提问（AI 讲解）。讨论和拍板在会话里，页面只放结果。

- 主机和地址只在本地文件 `~/.config/curryflow/local.env` 里（不入库）：`CURRYPLAN_SSH=用户@主机`、
  `CURRYPLAN_URL=https://主机:端口`；下面的命令先 `. ~/.config/curryflow/local.env`。
- 主机 `$CURRYPLAN_SSH`（key 登录），站点根 `/var/www/plan`，nginx 在 8443 端口，口令 cookie 挡在前面。
- 域名还没备案，现在直接用 IP 访问（Let's Encrypt IP 证书，服务器上 lego-renew.timer 自动续期）；备案完成后改成正式域名。
- `/api/*` 由 `~/curry-site/server.py`（127.0.0.1:8765）处理：评论、提问、可编辑正文。**不要改它**，也不要直接手改
  站点根下的公共文件（`kit.css`、`ui.css`、`ui.js`、`kit.js`、`app.*`、`index.html`、`sw.js`）——它们的源在
  本仓库（curryFlow）的 `site/shell/`，服务端在 `site/server/`，端到端测试在 `site/test/push/`。

## 排版铁律（禁止大段文字）

页面是给人快速扫读、加深对项目理解的，**严禁写成大段散文或满屏文字卡片**：

1. **段落不超 2 行，数字对比一律进表或图**
   - 任何 `<p>` 或 `<li>` 在桌面端不得超过 2 行；禁止用分号、括号把十几个模块名、拍数、commit 号塞进同一段话。
   - 只要出现 3 组以上数字或前后对比（如改前/改后/目标、各层延迟、模块面积/WNS、配置参数），**禁止写进文字段落**，必须用 `table.t`（数字列加 `.n`）、`.metrics > .metric` 指标带或 `svg.chart` 条形图。
2. **设计文档页（`docs/*/index.html`）：框图 + 规格表优先**
   - 每个核心章节以一张 `svg.dg`（模块框图、数据通路、流水线时序图）配一张 `table.t`（位宽、深度、吞吐、延迟、参数）为主体。
   - 禁止连续堆砌几十张结构雷同的"是什么 / 为什么 / 不这样"纯文字卡片；只有反直觉的设计取舍才用精简卡片或表格列说明。
3. **待办页（`docs/*/todo.html`）：结论外露，排查明细折叠**
   - 每张待办卡片外层只放四样东西：
     - 标题行 + 状态标签（`.badge.warn` 阻塞/缺口、`.badge.acc` 待合并、`.badge.muted` 待验证）
     - 一句话现状与差距（不超过 2 行）
     - 关键指标表（`table.t` 或 `.metrics`）
     - 下一步动作（`<div class="next">下一步：…</div>`）
   - 历史排查过程、试过但没成的方案、逐层/逐内核长列表，一律放进 `<details class="fold"><summary>排查记录与明细</summary>…</details>` 默认折叠，点开才看。

```
待办卡片标准结构:
  .card[id][data-c]
    ├─ h3: <span class="badge warn">性能缺口</span> 标题
    ├─ p.say: 一句话结论（≤2行）
    ├─ table.t 或 .metrics: 基线 / 当前 / 目标 / 瓶颈对比表
    ├─ .next: 下一步要做的具体动作
    └─ details.fold: <summary>排查记录与明细</summary> 历史实验、长列表
```

## 页面结构与组件

- 位置：`/var/www/plan/<项目>/<页面>/index.html`，同目录放 `meta.json` 和图片、视频、PDF。
- 仓库同源页面（推荐）：项目仓库里的 `docs/<模块>/index.html` 和 `docs/<模块>/todo.html` 在 `<head>` 写：
  ```html
  <meta name="cp-title" content="标题（与 <title> 完全一致）">
  <meta name="cp-say" content="一句话结论（与正文 .oneline 完全一致）">
  <meta name="cp-kind" content="方案"><!-- 方案 / 笔记 / 学习 / 状态 -->
  ```
  并在 `docs/pages.json` 登记 `{"pages": [{"source": "docs/rtl/index.html", "project": "currygpu", "page": "rtl"}, ...]}`。
  `tools/publish-docs --check` 挂进项目的 `style` 验收阶段（检查标签闭合、死链、`id`/`data-c` 唯一、仓库内 `docs/...#id` 引用有效）。
- `kit.css` 组件：
  - 布局：`.sec > h2 small`、`.wall`（两栏卡片墙）、`.card`（`.wide` 占满两栏）、`.pair`、`.scroll > table.t`（数字列 `.n`）
  - 视觉提炼：`.kpi`、`.metrics > .metric`（`<b>数值</b><span>说明</span>`）、`.badge.{acc,warn,ok,muted}`、`.ev.{nv,pub,rtl,inf,usr}`、`.next`（下一步动作条）、`details.fold > summary`（折叠明细）
  - 图表：`svg.chart`（`.bar .grid .tk .lb .val .ref`）、`svg.dg`（`.box .box.hl .box.ref .box.off .t .s .lb .w`）
  - 颜色只用 CSS 变量（`--ink --ink-2 --ink-3 --accent --c2 --c3 --warn --line --sunk --surface`），深色模式自动适配。
- 能评论的实体：带 `data-c="稳定的名字"` 的元素。给卡片和表加 `data-c`，改版后评论仍挂回原处。
- 不带外部依赖（CDN、外链字体）。

## 发布与自动同步

1. **项目 `docs/` 自动同步（主方式）**
   在 curryPlan 机上建项目的稀疏 clone，cron 每分钟拉中枢 `main` 并执行 `tools/publish-docs`：
   ```sh
   ssh "$CURRYPLAN_SSH" 'git clone -q --filter=blob:none --sparse ci@<主中枢>:<项目>.git ~/curryflow/<项目>-docs &&
     git -C ~/curryflow/<项目>-docs sparse-checkout set docs tools/publish-docs'
   # crontab 加一行
   * * * * * cd ~/curryflow/<项目>-docs && flock -n .git/pub.lock sh -c "git pull -q --ff-only && python3 tools/publish-docs" >/dev/null 2>&1  # curryPlan <项目> docs
   ```
2. **独立页面手工发布**
   `bin/planpub 项目 页面名 目录 "标题" "一句话结论" [种类]`。

## 注册新项目

`/var/www/plan/projects.json` 是 `[{"id", "name"}, …]`，顺序就是首页顺序；`id` 也是目录名。

```sh
ssh "$CURRYPLAN_SSH" 'cd /var/www/plan && python3 - <<EOF
import json; p = json.load(open("projects.json"))
p.append({"id": "<项目>", "name": "<显示名>"}) if all(x["id"] != "<项目>" for x in p) else None
json.dump(p, open("projects.json", "w"), ensure_ascii=False)
EOF
mkdir -p <项目> && python3 gen_catalog.py'
```

## 通知

curryPlan 能往手机推通知（标准 Web Push）：机器告警、作业完成这类消息。它只是通道，什么时候发由调用方决定。

- 开：首页右上角的"通知"→"开启通知"→ 允许 →"发一条测试"。每台设备各开一次，同一个地方可以关闭。
- iPhone：网页通知只在主屏幕应用里可用（iOS 16.4 起）。先在 Safari 里打开站点，分享 → "添加到主屏幕"，再从主屏幕图标打开开启。
- Android：Chrome 标签页里直接开（手机要能连上谷歌 FCM；curryPlan 机经 `~/curry-site/push.env` 的 `FCM_PROXY_CMD` 转发）。
- 发：
  ```sh
  ssh "$CURRYPLAN_SSH" '~/curry-site/notify "标题" "正文" [url] [--tag 名字]'
  ```
