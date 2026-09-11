# projstate

零三方依赖、零模型的"项目状态与阅读锚点"。把项目记忆落盘为三层：
状态层（`.ai/STATE.md`，人写）、事实层（`.ai/map.json` + `MAP.md`）、卫生层（`.ai/HIP.md` + `stale.json`）。
任何窗口/任何 agent 开工先读，不再重读全仓，不再把旧文件当现行版。

## 痛点 → 产物

| 痛点 | 产物 | 用法 |
|---|---|---|
| 重开窗口/换 agent 重读全仓 | `.ai/STATE.md` | `projstate log "完成X，下一步Y"`；换窗口 `projstate status` |
| 精读漏重点、反复读漂移 | `.ai/MAP.md` | 开工读"入口/关键文件/改X读什么" |
| 旧文件当现行版 | `.ai/HIP.md` | 读"现行版对照+勿读清单" |

## 命令

| 命令 | 作用 |
|---|---|
| `projstate init` | 接入当前项目（写 AGENTS.md 标记块，需确认） |
| `projstate log <text>` | 追加一行到 `.ai/STATE.md`，并锚定该结论引用的文件哈希 |
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
- **map.json 幂等**：扫描排除根级 agent 规则文件（`AGENTS.md`/`CLAUDE.md`/`GEMINI.md`）——
  它们是工具改写的入口，不是项目源码事实，排除后每次 `refresh` 不产生自指的无意义 diff。
- **ambiguous 产物按阈值聚合**：超过 20 组待确认时，HIP.md / MAP.md 不逐行列全部候选，
  改为"统计 + 前 20 组摘要 + 指向 `.ai/stale.json`"，避免退化成超长"我不知道"清单。
- 同一会话内地图会旧，直到下次 refresh。
- **被引用的成员可否决"旧版"定论**：`ref_count > 0` 是「确证」级证据（它真的在服役），
  文件名只是「声明」。确证必须能否决声明——否则会建议归档 `agent/curator_backup.py`
  这类 711 行、被 6 个文件提及的活代码。实证：hermes-agent 上该否决把定论误报从 2/2 降到 0/2。
- **宁可漏报，不可误杀**：工具只在"同目录 + 同扩展名 + 同主干"的组内定论。
  没有同名兄弟的孤立旧文件（如一份过期的 `docs/drafts/xxx.md`）**永远不会被判定**——
  这是刻意的：漏报只是少一条提示，误杀会让 agent 主动跳过活代码。
- 状态层靠人/agent 自律维护；工具不替用户判断"做完了没"。**但工具会记录每条结论写下来时
  引用的文件内容**（`.ai/state_deps.json`）：这些文件一变更，该结论就在 `STATE.md` 里被就地
  标上 `⚠️ 依赖已变更：<path>`，并在 `refresh` / `check` / AGENTS.md 标记块中重复告警。
  标记是双向且幂等的——文件改回原样则标记自动消失，不会堆叠。

## 依赖与许可

零三方依赖（Python 标准库）。设计上借鉴 codegraph / Understand Anything 的局部可验证思路，
但不复制第三方代码；两者 LICENSE 已核实均为 MIT。若不复制代码，无版权义务。

## `.ai/` 的 git 策略

`.ai/`（含 `map.json`、`MAP.md`、`HIP.md`、`stale.json`、`canonical_overrides.json`、
`STATE.md`、`state_deps.json`、`unresolved.json`）**一律进 git**：项目认知与代码同版本，换机器 / 换人拿到同一份。
唯一排除的是 `.ai/.cache/`（增量缓存与路径历史，可重建），写入 `.gitignore`。
