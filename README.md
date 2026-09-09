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
| `projstate log <text>` | 追加一行到 `.ai/STATE.md` |
| `projstate status` | 打印任务状态 |
| `projstate refresh` | 增量刷新事实层+卫生层+渲染 AGENTS.md 块 |
| `projstate check` | 只读体检（干净=0，有发现=1） |
| `projstate explain <path>` | 该文件被谁引用、改它要连带读什么 |
| `projstate canonical <名>` | 该概念现行版是哪个 |
| `projstate why <path>` | 为什么判它陈旧 |

## 已知限制

- `ref_count` 是下界，不是精确值——grep 是完整事实来源。
- "职责描述"为机械推断，标 `（推断）`；引用计数/入口/git 为 `（确证）`。
- 同一会话内地图会旧，直到下次 refresh。
- 状态层靠人/agent 自律维护；工具不替用户判断"做完了没"。

## 依赖与许可

零三方依赖（Python 标准库）。设计上借鉴 codegraph / Understand Anything 的局部可验证思路，
但不复制第三方代码；两者 LICENSE 已核实均为 MIT。若不复制代码，无版权义务。
