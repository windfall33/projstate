# projstate

零三方依赖、零模型的"项目状态与阅读锚点"。落盘为四样东西：

**交接单**（`AGENTS.md` 里的受保护块，人/agent 写）、状态层（`.ai/STATE.md`）、
事实层（`.ai/map.json` + `MAP.md`）、卫生层（`.ai/HIP.md` + `stale.json`）。

为什么交接单写在 `AGENTS.md` 而不是 `.ai/`：Codex 与 Claude Code 都会把 `AGENTS.md`
**自动注入上下文**，而 `.ai/` 需要 agent 额外读一次。第四轮实测里，没有 AGENTS.md 的仓库
（6 个真实开源仓库里的 5 个）中，agent 全程 0 次访问 `.ai/` —— 工具装上了但没有入口。
所以正文放进一定会被读到的文件，`.ai/` 只放机器产物。

## 痛点 → 产物

| 痛点 | 产物 | 用法 |
|---|---|---|
| 重开窗口/换 agent 重读全仓 | `AGENTS.md` 交接单块 | 开工先读块；收工更新块 + `projstate brief stamp` |
| 精读漏重点、反复读漂移 | `.ai/MAP.md` | 开工读"入口/关键文件/改X读什么" |
| 旧文件当现行版 | `.ai/HIP.md` | 读"现行版对照+勿读清单" |
| 交接单本身过期（改了代码没更新） | `projstate check` | 退出码 ≠ 0，提示"交接单已过期" |

## 命令

| 命令 | 作用 |
|---|---|
| `projstate init` | 接入当前项目：建 `.ai/`，**没有 `AGENTS.md` 就创建一个只含交接单的** |
| `projstate brief` | 打印交接单 + 是否过期 |
| `projstate brief stamp` | 只更新交接单的源码指纹戳（正文不动） |
| `projstate brief check` | **单一职责的退出码**：交接单过期=1，否则=0（不受其它体检项影响） |
| `projstate hook install` | 默认挂到 git `pre-commit`（交接单过期就挡住提交） |
| `projstate hook install --target claude` | 挂到 Claude Code 的 **Stop 钩子**（退出码 2 = 阻塞停止并把原因打回给模型） |
| `projstate hook uninstall [--target …]` | 卸掉对应钩子（只删本工具装的，别人的配置一字不动） |
| `projstate log <text>` | 追加一行到 `.ai/STATE.md` |
| `projstate status` | 打印任务状态 |
| `projstate refresh` | 增量刷新事实层+卫生层+渲染 AGENTS.md 块 |
| `projstate check` | 只读体检（干净=0，有发现=1） |
| `projstate explain <path>` | 该文件被谁引用、改它要连带读什么 |
| `projstate canonical <名>` | 该概念现行版是哪个 |
| `projstate why <path>` | 为什么判它陈旧 |
| `projstate resolve <候选>` | 人为确认某候选为现行版，写入 `.ai/canonical_overrides.json`，永久生效 |
| `projstate task <名>` | 设置 `STATE.md` 的当前任务（与 log 的下一步分开） |

## 已知限制

- `ref_count` 是下界，不是精确值——grep 是完整事实来源。
- **判定依赖"可验证证据"（引用计数 / 入口 / 明确旧版标记）。** 若某文件组缺乏任何可验证证据
  （如：两个候选都无引用、未达入口、都无明确 `_old`/`-旧` 等旧版标记），工具**标 ambiguous 而非乱判**。
  这是刻意的保守：宁可报告"需人工确认"，也不给一个可能反向的错误确定性。
- **`_v2`/`_new`/`_final` 等"疑似版本标记"不作决定性依据**——因为"现行版命名带 v2"与"旧版命名带 v2"
  无法仅凭文件名区分。只有 `_old`/`_backup`/`-旧`/`副本`/`(1)` 等**方向确定**的旧版标记才作决定性证据。
- **归组词表与判定词表统一**：`_new`/`_final`/`_latest`/`_next`/`_refactor`/`_rebuilt`/`_deprecated`/
  `_tmp`/`_original`/年份后缀/中文副本与旧 等，均已纳入归组（`VERSION_STEM_MARKER`）。含这些标记的
  文件现在能跟主干版归为一组，进入判定流程——修复前它们不进组，ref_count 再准也用不上。
- **对未提供引用链的仓库，地图可能只有标 ambiguous 的组，无法给出"哪个是现行版"的确定答案。**
  这时工具的价值在于"不误导"，而非"给出答案"；要拿到确定答案需要仓库有真实 import/引用关系。
- **`projstate resolve <候选>` 是突破口**：对无引用证据的 ambiguous 组，人/agent 确认一次后写入
  `canonical_overrides.json`，后续所有窗口/agent 直接读到确定判定，不再重复面对同一个不确定判断。
  这正对应"昂贵的判断要跨窗口持久化"，避免每个新窗口重新猜测（痛点二的漂移来源）。
- "职责描述"为机械推断，标 `（推断）`；引用计数/入口/git 为 `（确证）`。
- **交接单是"人写"，工具不推断语义。** `check` 只回答一个安全的问题：**源码变了，而交接单
  正文自上次盖章后一个字没动**吗（比对全仓内容指纹 + 正文指纹，与 mtime 无关，所以
  `git clone` 不会误报）。它**不判断**交接单写得对不对、有没有漏。
- **正文动过就不算过期**：人或 agent 更新了交接单、只是忘了跑 `brief stamp`，这时不能判过期
  —— 验收实验里，被误判的 agent 直接改用 `git commit --no-verify` 绕过了钩子。`brief stamp`
  只用于把戳对齐（此后正文再不动才会重新判过期）。
- **两个强制点，分工不同**：`pre-commit` 只在能写 `.git` 的环境里有效——Codex 的
  `workspace-write` 沙箱对 `.git` 是 DENY，agent 连 `git add` 都做不了，钩子根本到不了；
  Claude Code 的 Stop 钩子在沙箱里也能触发，且退出码 2 会把原因打回给模型让它继续。
  实测：装了 Stop 钩子 + pre-commit 后，agent 会在被挡住时**把交接单正文写好、盖章，
  再和代码一起提交**（提交信息里自己写明"因 pre-commit 钩子要求一并更新"）。
- `.claude/`、`.codex/` 属于 agent 工具状态，不进事实层，也不进源码指纹——否则"装钩子"
  这个动作本身就会把交接单判成过期（装完第一个提交就被自己挡住）。
- **`check` 会扫一遍全仓来算指纹**：小仓毫秒级，django（27 MB 源码）约 0.2 s，
  kubernetes（248 MB 源码）约数秒。挂在 `pre-commit` 上时要有这个预算。
- **没有 `AGENTS.md` 时 `init`/`refresh` 会创建它**（只含交接单块）。旧版本是"提示即可、
  不接入"，实测那等于没接入：真实仓库里 5/6 没有 AGENTS.md。块外内容一字不动。
- **map.json 幂等**：扫描排除根级 agent 规则文件（`AGENTS.md`/`CLAUDE.md`/`GEMINI.md`）——
  它们是工具改写的入口，不是项目源码事实，排除后每次 `refresh` 不产生自指的无意义 diff。
- **ambiguous 产物按阈值聚合**：超过 20 组待确认时，HIP.md / MAP.md 不逐行列全部候选，
  改为"统计 + 前 20 组摘要 + 指向 `.ai/stale.json`"，避免退化成超长"我不知道"清单。
- 同一会话内地图会旧，直到下次 refresh。
- 状态层靠人/agent 自律维护；工具不替用户判断"做完了没"。

## 依赖与许可

零三方依赖（Python 标准库）。设计上借鉴 codegraph / Understand Anything 的局部可验证思路，
但不复制第三方代码；两者 LICENSE 已核实均为 MIT。若不复制代码，无版权义务。

交接单这一层的**模式**来源（第四轮复用闸门：只借模式，不引代码、不引依赖）：
`adrrr/persistent-handoff`（"always the same file, re-read at every session start"）、
`hyamie/claude-code-starter-kit`（`/continue` + `/done`）、
`kskdotdot/to-the-next-session`（跨会话/跨机器的任务交接文件）、
`RRFRRF/project-memory-skill`（Markdown 形式的跨会话状态 + handoff）。
它们都是 Claude Code 专属的 agent 侧 skill 且体量很小；引入会与"零依赖 + 工具无关 +
产物进 git"冲突，因此只采纳模式，代码全部自写。

## `.ai/` 的 git 策略

`.ai/`（含 `map.json`、`MAP.md`、`HIP.md`、`stale.json`、`canonical_overrides.json`、
`STATE.md`、`unresolved.json`）**一律进 git**：项目认知与代码同版本，换机器 / 换人拿到同一份。
唯一排除的是 `.ai/.cache/`（增量缓存与路径历史，可重建），写入 `.gitignore`。
