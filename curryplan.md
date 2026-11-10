# curryPlan

人看的那一面：一个手机上也好用的页面目录（PWA），按项目分组，最新的在上。每个页面上能选中实体评论（文字或
手绘）和提问（AI 讲解）。讨论和拍板在会话里，页面只放结果。

- 主机 `user@plan.example`（key 登录），站点根 `/var/www/plan`，nginx 在 8443 端口，口令 cookie 挡在前面。
- 域名还没备案，现在直接用 IP 访问（Let's Encrypt IP 证书，服务器上 lego-renew.timer 自动续期）；备案完成后改成正式域名。
- `/api/*` 由 `~/curry-site/server.py`（127.0.0.1:8765）处理：评论、提问、可编辑正文。**不要改它**，也不要动
  站点根下的公共文件（`kit.css`、`ui.css`、`ui.js`、`kit.js`、`app.*`、`index.html`、`sw.js`）——它们的源在
  box-a 的 `/home/user/code/curryPlan/shell/`。
- 动手前先看两三个现有页面学样子：`/var/www/plan/curryplan/flow/`（方案页的好样板）、`currygpu/sm/`、`rtlsim/paper/`。

## 页面约定

- 位置：`/var/www/plan/<项目>/<页面>/index.html`，同目录放 `meta.json` 和图片、视频、PDF。
- `meta.json`：`{"title", "say", "kind", "updated": "YYYY-MM-DD HH:MM", "machine": 写页面的机器名}`。
  `say` 是一句话结论，首页搜索读它；`kind` 用 方案 / 笔记 / 学习 / 状态（首页有 方案、笔记 两个筛选按钮）。
- 骨架从 `templates/curryplan/page/index.html` 起：头里引 `/manifest.webmanifest`、`/icon-180.png`、`/kit.css`、`/ui.css`，
  尾部引 `/kit.js`、`/ui.js`。顶上 `.back`（← 全部）和 `.top`（h1、`.sub`），一句话结论放 `.oneline`。
- kit.css 的件：`.sec > h2 small`（小节）、`.wall`（桌面两栏、手机单栏的卡片墙）、`.card`（`.wide` 占满）、
  `.say`、`.pair`、`.kpi`、`table.t`（数字列 `.n`）、`.scroll`、`svg.chart` 一族（`.bar .grid .tk .lb .val .ref`）。
  颜色只用它的变量（`--ink --ink-2 --ink-3 --accent --c2 --c3 --warn --line --sunk --surface`），深色模式自动跟上。
- 能评论的实体：带 `data-c="稳定的名字"` 的元素，或图、卡片、表格行、段落、标题、列表项。给要讨论的卡片和
  表加 `data-c`，改版后评论还能挂回原处。
- 内容：中文；**只写最终的产品形态**——结论、数字、图、表；不写讨论过程、待定选项、"我们考虑过"。
  拍板发生在会话里，定了再改页面。高密度、图表优先，图能说清的不写段落。
- 不带外部依赖（CDN、字体）；图片放同目录，大视频压一下再放。

## 发布

```sh
ssh user@plan.example 'mkdir -p /var/www/plan/<项目>/<页面>'
scp -r <目录>/* user@plan.example:/var/www/plan/<项目>/<页面>/
ssh user@plan.example 'python3 /var/www/plan/gen_catalog.py'     # 重写首页目录 catalog.json
```

每次发布都更新 `meta.json` 的 `updated`。`bin/planpub 项目 页面名 目录 "标题" "一句话结论" [种类]` 一条命令做完
上面三步（自己写 meta.json，任何机器上都能用）。地址是 `https://plan.example:8443/<项目>/<页面>/`。

## 注册新项目

`/var/www/plan/projects.json` 是 `[{"id", "name"}, …]`，顺序就是首页顺序；`id` 也是目录名。

```sh
ssh user@plan.example 'cd /var/www/plan && python3 - <<EOF
import json; p = json.load(open("projects.json"))
p.append({"id": "<项目>", "name": "<显示名>"}) if all(x["id"] != "<项目>" for x in p) else None
json.dump(p, open("projects.json", "w"), ensure_ascii=False)
EOF
mkdir -p <项目> && python3 gen_catalog.py'
```

## 状态页（curryFlow 项目）

模板在 `templates/curryplan/status/index.html`（把 `__PROJECT__` 换成项目名），页面本身不带
数据，每 30 秒读同目录的 `status.json`：租约、任务、候选 × 阶段的结论、executor 心跳和槽位、每台机器每天的磁盘
占用。`status.json` 由 curryPlan 机上的一份项目 clone 每分钟从中枢读出来（`ci/coord status --json`）：

```sh
# 一次：clone（curryPlan 机的公钥要在中枢 ci 用户的 authorized_keys 里）
ssh user@plan.example 'mkdir -p ~/curryflow && git clone -q ci@<主中枢>:<项目>.git ~/curryflow/<项目>-status'
# crontab 加一行（保留已有的行；行尾注释用来认出它）
* * * * * cd ~/curryflow/<项目>-status && flock -n .git/cf-status.lock sh -c "git pull -q --ff-only; python3 ci/coord status --json > /var/www/plan/<项目>/status/status.json.tmp && mv /var/www/plan/<项目>/status/status.json.tmp /var/www/plan/<项目>/status/status.json" >/dev/null 2>&1  # curryFlow <项目> 状态页
```

状态页的 `meta.json` 用 `"kind": "状态"`。读不到中枢时页面会显示"读不到 status.json"，数据时间写在右上角。
