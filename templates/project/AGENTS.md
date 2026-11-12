# <项目> 的规矩

- 验收只认 `ci/accept`：`ci/accept --stage <名>` 跑一个阶段，每阶段退出码 0 通过、77 跳过，最后一行是结论；阶段、判据和按改动分级看 `ci/accept --list`、`ci/accept --plan <文件>`。共享机上脚本取 CPU 和内存只用槽位环境变量（`$CF_CPUS`、`$CF_THREADS`、`$CF_CORES`、`$CF_MEM_GB`）或进程自身的亲和性（`sched_getaffinity`）与 cgroup `memory.max`，禁止读整机 `/proc/meminfo` 或全局 `cpuset`。
- 派生文件只放 `.build/`；写到别处，验收判失败。
- 用例来自真实 bug：修一个 bug，就在 <用例目录> 加一个能复现它的用例，写清 why。
- `docs/` 下的 HTML 就是 curryPlan 上的页面（`docs/pages.json` 列出每份对应哪一页；`docs/*/index.html` 是设计文档，`docs/*/todo.html` 是待办），改文档、改待办都改这份 HTML：合进 `main` 后 curryPlan 机上的 `tools/publish-docs` 自动发布，`style` 阶段用 `tools/publish-docs --check` 检查。
- 不能改的：<列出，例如"ORT 是用户侧，不改">；错误显式报出，不静默替代。
- 分支与发布：agent 在 `wip/<id>` 上干活、推中枢；`main` 只由协调器在验收全绿后推，推完中枢 `main` 接着推 GitHub `main`、出 GitHub release 发布件，并把宿主上的主工作树快进到最新 `main`。
