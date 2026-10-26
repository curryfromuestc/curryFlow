"""curryFlow 运行时的公用部分：读 ci/machines，和中枢之间读写 ref。只用标准库。

中枢上的 ref（全部是普通 git ref，机器之间只走 git）：
  refs/heads/main               主线；只有通过验收的候选才推上来
  refs/heads/wip/<id>           agent 的工作分支
  refs/tasks/<id>               任务：一串提交，树里是 task.json、state.json、msgs/NNN.md、reports/NNN.md
  refs/claims/task-<id>         executor 认领任务；推新 ref，已存在就被拒，等于锁
  refs/claims/ci-<job>          executor 认领验收作业
  refs/ci/cand/<cid>            候选提交（main 合上 wip/<id>）
  refs/ci/queue/<cid>.<stage>   验收作业：树里是 job.json，父提交是候选
  refs/ci/result/<cid>.<stage>  结论：树里是 verdict、result.json、reports/*，父提交是候选
  refs/ci/exec/<machine>        executor 心跳和占用中的槽位
  refs/ci/usage/<machine>       executor 每天一份磁盘占用
  refs/coord/lease              协调器租约；心跳就是每隔一会儿推一个新提交
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

CI = Path(__file__).resolve().parent
ROOT = CI.parent
BUILD = ROOT / ".build"
LOCAL = "refs/cf/"          # 中枢 ref 在本地的镜像：refs/tasks/x -> refs/cf/tasks/x
SPACES = ["heads", "tasks", "claims", "ci", "coord"]
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_machines():
    return json.loads((CI / "machines").read_text())


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


class GitError(Exception):
    pass


def ssh_cmd():
    s = ("ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15"
         " -o ControlMaster=auto -o ControlPersist=10m -o ControlPath=~/.ssh/cf-%C")
    key, proxy = os.environ.get("CURRYFLOW_HUB_KEY"), os.environ.get("CURRYFLOW_HUB_PROXY")
    s += f" -i {key} -o IdentitiesOnly=yes" if key else ""
    # 直连中枢的 22 端口不稳的机器（国内）经本机的 HTTP 代理连：ci/machines 里该机器的 hub_proxy
    return s + (f" -o 'ProxyCommand=nc -X connect -x {proxy} %h %p'" if proxy else "")


def git(*args, input=None, check=True, env=None, cwd=None, raw=False):
    e = {**os.environ, "GIT_SSH_COMMAND": ssh_cmd(), "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, input=input, capture_output=True, env=e,
                       text=not raw)
    if check and r.returncode:
        err = r.stderr if not raw else r.stderr.decode(errors="replace")
        raise GitError(f"git {' '.join(args[:3])}: {err.strip()[-600:]}")
    return r if raw else (r.stdout.strip() if check else r)


IDENT = {"GIT_AUTHOR_NAME": "curryflow", "GIT_AUTHOR_EMAIL": f"curryflow@{socket.gethostname()}",
         "GIT_COMMITTER_NAME": "curryflow", "GIT_COMMITTER_EMAIL": f"curryflow@{socket.gethostname()}"}


def commit(files, parent=None, msg="curryflow", base=None):
    """Writes a commit with parent PARENT whose tree is BASE's tree (or empty) with FILES {path: str|None} applied."""
    fd, idx = tempfile.mkstemp(prefix="cf-index-")
    os.close(fd)
    os.unlink(idx)
    env = {"GIT_INDEX_FILE": idx, **IDENT}
    try:
        if base:
            git("read-tree", base + "^{tree}", env=env)
        for path, data in files.items():
            if data is None:
                git("update-index", "--force-remove", path, env=env)
                continue
            blob = git("hash-object", "-w", "--stdin", input=data if isinstance(data, str) else data.decode())
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git("write-tree", env=env)
    finally:
        if os.path.exists(idx):
            os.unlink(idx)
    return git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", msg, env=IDENT)


def show(rev, path, default=None):
    r = git("show", f"{rev}:{path}", check=False)
    return r.stdout if r.returncode == 0 else default


def show_json(rev, path, default=None):
    s = show(rev, path)
    return json.loads(s) if s else default


def ls_tree(rev, path):
    r = git("ls-tree", "--name-only", f"{rev}:{path}", check=False)
    return r.stdout.split() if r.returncode == 0 else []


class Hub:
    """The hubs from ci/machines, in order; the first one that answers is used (the next ones are mirrors)."""

    def __init__(self, cfg):
        self.urls = cfg["hubs"]
        self.url = None

    def _try(self, fn):
        last = None
        for u in ([self.url] if self.url else []) + [u for u in self.urls if u != self.url]:
            try:
                out = fn(u)
                self.url = u
                return out
            except GitError as e:
                last = e
        raise last

    def ls(self, *patterns):
        """{ref: sha} straight from the hub, without touching local refs (safe next to other processes)."""
        out = self._try(lambda u: git("ls-remote", u, *patterns))
        return {l.split("\t")[1]: l.split("\t")[0] for l in out.splitlines() if "\t" in l}

    def sync(self):
        """Mirrors the hub's refs into refs/cf/* and returns {hub ref: sha}."""
        specs = [f"+refs/{s}/*:{LOCAL}{s}/*" for s in SPACES]
        self._try(lambda u: git("fetch", "-q", "--prune", "--no-tags", "--no-write-fetch-head", u, *specs))
        return refs()

    def fetch(self, *refspecs):
        self._try(lambda u: git("fetch", "-q", "--no-tags", "--no-write-fetch-head", u, *refspecs))

    def push(self, ref, new, expect):
        """Compare-and-swap: sets REF to NEW (None deletes it) only if it is EXPECT now ('' = must not exist)."""
        src = new or ""
        r = git("push", "-q", "--porcelain", f"--force-with-lease={ref}:{expect or ''}", self.url or self.urls[0],
                f"{src}:{ref}", check=False)
        if r.returncode == 0:
            loc = LOCAL + ref[len("refs/"):]
            if new:
                git("update-ref", loc, new)
            else:
                git("update-ref", "-d", loc, check=False)
            return True
        if "Could not read from remote" in r.stderr or "Connection" in r.stderr:
            raise GitError(r.stderr.strip()[-300:])
        return False

    def create(self, ref, files, msg, parent=None):
        """Pushes a new ref; False when it already exists (the lock)."""
        return self.push(ref, commit(files, parent=parent, msg=msg), "")

    def update(self, ref, fn, msg, tries=8):
        """Read-modify-write of a state ref. FN(sha) returns {path: content} to change, or None to leave it."""
        for _ in range(tries):
            try:
                self.fetch(f"+{ref}:{LOCAL}{ref[5:]}")
            except GitError:
                if self.ls(ref):
                    raise
                git("update-ref", "-d", LOCAL + ref[5:], check=False)
            old = git("rev-parse", "-q", "--verify", LOCAL + ref[5:], check=False).stdout.strip()
            files = fn(old or None)
            if files is None:
                return old
            new = commit(files, parent=old or None, msg=msg, base=old or None)
            if self.push(ref, new, old):
                return new
            time.sleep(0.5)
        raise GitError(f"{ref}: lost the race {tries} times")


def refs(prefix=LOCAL):
    """{hub ref: sha} of the local mirror."""
    out = git("for-each-ref", "--format=%(refname) %(objectname)", prefix)
    res = {}
    for line in out.splitlines():
        name, sha = line.split()
        res["refs/" + name[len(LOCAL):]] = sha
    return res


def under(rs, prefix):
    """{name after PREFIX: sha} for refs starting with PREFIX."""
    return {k[len(prefix):]: v for k, v in rs.items() if k.startswith(prefix)}


def task_view(sha):
    t = show_json(sha, "task.json", {})
    st = show_json(sha, "state.json", {})
    return {**t, **st, "sha": sha}


def stage_order(job_name):
    return job_name.rsplit(".", 1)


def candidates(rs):
    """{cid: {stage: {"queued": sha, "claim": .., "result": ..}}} from the mirror."""
    c = {}
    for kind, prefix in (("queued", "refs/ci/queue/"), ("result", "refs/ci/result/")):
        for name, sha in under(rs, prefix).items():
            cid, stage = stage_order(name)
            c.setdefault(cid, {}).setdefault(stage, {})[kind] = sha
    for name, sha in under(rs, "refs/claims/ci-").items():
        cid, stage = stage_order(name)
        if cid in c and stage in c[cid]:
            c[cid][stage]["claim"] = sha
    return c


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def cpus(spec):
    """'0-7,16-23' -> {0..7, 16..23}"""
    out = set()
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out
