#!/usr/bin/env python3
"""聊天页端到端：真浏览器（本机 Chrome，Playwright）点一遍，模型真跑。

用法：CP_URL=https://主机:端口 CP_COOKIE=<cpk 的值> uvx --with playwright python site/test/chat/e2e.py 图片.png 截图目录
步骤：启动模型 → 发图+文字 → 刷新后会话和图片还在 → 重新生成 → 中途停止（存成"已停止"）→ 改名 → 手机宽度与深色截图
→ 删除会话 → 停止模型。每步打印 PASS/FAIL，结束打印汇总，退出码为失败数。
"""
import os
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

URL = os.environ["CP_URL"].rstrip("/") + "/curryplan/chat/"
COOKIE = os.environ["CP_COOKIE"]
IMG, SHOTS = sys.argv[1], sys.argv[2]
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail else ""), flush=True)


def wait_reply(page, timeout=240, started=False):
    """等这一轮生成结束。先等"停止生成"出现（生成开始了；否则会把上一轮留下的完成状态当成这一轮的），
    再等它藏起来、最后一条回答有了用时。started=True 表示调用方已经确认开始了。"""
    if not started:
        page.wait_for_function("() => !document.getElementById('bAbort').hidden", timeout=15000)
    page.wait_for_function("() => document.getElementById('bAbort').hidden", timeout=timeout * 1000)
    page.wait_for_function("() => { const a=[...document.querySelectorAll('.m.a .meta')].pop(); return a && /秒/.test(a.textContent); }",
                           timeout=30000)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1280, "height": 860}, locale="zh-CN")
    ctx.add_cookies([{"name": "cpk", "value": COOKIE, "domain": urlsplit(URL).hostname, "path": "/", "secure": True, "httpOnly": True}])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # 站点本来就没有 favicon.ico（全站 404），不算这个页面的错
    page.on("response", lambda r: r.status >= 400 and not r.url.endswith("/favicon.ico") and errors.append("%d %s" % (r.status, r.url)))
    page.on("dialog", lambda d: d.accept("测试会话-改名") if d.type == "prompt" else d.accept())

    page.goto(URL)
    page.wait_for_function("() => document.getElementById('stx').textContent !== '…'", timeout=15000)
    check("页面加载、读到模型状态", True, page.inner_text("#stx"))

    if page.is_enabled("#bStart"):
        page.click("#bStart")
    page.wait_for_function("() => document.getElementById('stx').textContent.startsWith('运行中')", timeout=120000)
    check("启动模型", True, page.inner_text("#stx"))

    page.set_input_files("#file", IMG)
    page.wait_for_selector("#thumbs img")
    page.fill("#txt", "图里写了什么字？只回答那几个字")
    page.click("#bSend")
    wait_reply(page)
    ans = page.locator(".m.a .md").last.inner_text()
    check("发图对话", "HELLO" in ans.upper() and "42" in ans, repr(ans[:60]))
    check("思考过程折叠显示", page.locator(".m.a details").count() >= 1)

    cid = page.evaluate("new URLSearchParams(location.search).get('c')")
    page.reload()
    page.wait_for_selector(".conv.on")
    page.wait_for_selector(".m.u img")
    page.wait_for_function("() => [...document.querySelectorAll('.m.u img')].every(i => i.complete && i.naturalWidth > 0)", timeout=15000)
    check("刷新后会话和图片还在", page.locator(".m.u").count() == 1 and page.locator(".m.a").count() == 1, "c=" + str(cid))
    check("会话出现在列表里", page.locator(".conv.on").count() == 1, page.locator(".conv.on").first.inner_text().replace("\n", " "))

    # 刚刷新完立刻点：模型状态可能还没查回来，页面应当先查一次再生成，而不是静默不动
    page.click("[data-regen]")
    wait_reply(page)
    check("重新生成（仍只有一条回答）", page.locator(".m.a").count() == 1)

    page.fill("#txt", "从 1 数到 300，每个数字单独一行，不要省略")
    page.click("#bSend")
    page.wait_for_function("() => document.querySelectorAll('.m.a').length === 2", timeout=10000)
    time.sleep(4)
    page.click("#bAbort")
    wait_reply(page, 60, started=True)
    meta = page.locator(".m.a .meta").last.inner_text()
    check("中途停止并存成「已停止」", "已停止" in meta, meta.replace("\n", " "))

    page.click("#bRen")
    page.wait_for_function("() => document.getElementById('ttl').textContent === '测试会话-改名'", timeout=10000)
    check("改名", True)

    page.fill("#q", "测试会话")
    page.wait_for_timeout(800)
    check("搜索", page.locator(".conv").count() >= 1)
    page.fill("#q", "")

    page.screenshot(path=os.path.join(SHOTS, "desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=os.path.join(SHOTS, "mobile.png"))
    page.click("#bMenu")
    page.wait_for_timeout(400)
    page.screenshot(path=os.path.join(SHOTS, "mobile-menu.png"))
    page.click(".conv.on")
    page.emulate_media(color_scheme="dark")
    page.set_viewport_size({"width": 1280, "height": 860})
    page.screenshot(path=os.path.join(SHOTS, "dark.png"))
    page.emulate_media(color_scheme="light")

    page.click("#bDel")
    page.wait_for_function("() => document.getElementById('ttl').textContent === '新对话'", timeout=10000)
    page.wait_for_timeout(600)
    check("删除会话", page.locator(f".conv[data-id='{cid}']").count() == 0)

    page.click("#bStop")
    page.wait_for_function("() => document.getElementById('stx').textContent.startsWith('已停止')", timeout=30000)
    check("停止模型", True)

    check("没有脚本错误、没有失败的请求", not errors, "; ".join(errors[:3]))
    b.close()

fails = sum(1 for _, ok in results if not ok)
print("\n%d/%d PASS" % (len(results) - fails, len(results)))
sys.exit(fails)
