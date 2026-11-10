#!/bin/bash
# 在 bwrap 里起 bridge：代码树只读，只有 dsh/ 可写；家目录是空的 tmpfs，碰不到 ~/.ssh 等。
H=$HOME; D=$(cd "$(dirname "$0")" && pwd)
exec nice -n 10 bwrap --die-with-parent --unshare-pid \
  --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin --symlink usr/sbin /sbin \
  --ro-bind /etc /etc --ro-bind /run/systemd/resolve /run/systemd/resolve --proc /proc --dev /dev --tmpfs /tmp --tmpfs "$H" \
  --ro-bind "$H/miniconda3" "$H/miniconda3" \
  --ro-bind "$H/code/curryGPU" "$H/code/curryGPU" \
  --ro-bind "$H/code/curryGPUpaper/sim" "$H/code/curryGPUpaper/sim" \
  --bind "$D" "$D" --chdir "$D" \
  "$D/.venv/bin/python3" "$D/bridge.py"
