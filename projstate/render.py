"""渲染：map.json/stale.json → MAP.md / HIP.md / AGENTS.md 标记块。"""

from __future__ import annotations

BEGIN_MARKER = "<!-- projstate:begin 自动生成，勿手改 -->"
END_MARKER = "<!-- projstate:end -->"

# ambiguous 组超过此阈值时，产物不逐行列全部候选（避免退化成"我不知道"清单），改为聚合。
AMBIGUOUS_DETAIL_LIMIT = 20

# 命名推断的入口候选最多列几个（按被引次数取前 N）。monorepo 里叫 index 的文件可能上百。
ENTRY_NAME_LIMIT = 10

# 已定论条目的依据说明。P0 收窄后只有两种定论来源，都不是「推断」，故不能再标「（推断）」。
DECIDE_BASIS_HEADER = "现行版对照（定论依据：明确旧版标记 / 人工登记）"


def _basis(f) -> str:
    """该条定论的依据：人工登记（resolve）还是文件名明确声明旧版。"""
    if getattr(f, "signals", {}).get("overridden"):
        return "人工登记"
    return "明确旧版标记"


def _entry_basis(entry: dict) -> str | None:
    """入口判定依据；老 map.json 没有该字段时按 reachable 退化为「命名推断」。"""
    return entry.get("entry_basis") or ("name" if entry.get("reachable") else None)


def anchor_lines(map_data: dict, top_limit: int = 20) -> list[str]:
    """阅读锚点：入口（确证与推断**分开列**）+ 关键文件（按被引次数，确证）。

    入口必须按依据分开：manifest 声明是确证，命名推断不是。混在一起标「确证」，
    正是本项目要防的事——monorepo 里每个 barrel 都叫 index，按命名判会把十几个
    ref_count=0 的文件列成「必看入口」，与 ProjectBrain 的 F3
    （critical = 字母序前 12 个配置文件）是同一个失败模式。

    由 render_map 与 MCP 的 projstate_context 共用：两份实现必然漂移，
    而"工具说的和事实不一样"正是本项目要防的事。
    """
    lines: list[str] = []
    manifest = sorted(r for r, e in map_data.items() if _entry_basis(e) == "manifest")
    by_name = sorted(
        (r for r, e in map_data.items() if _entry_basis(e) == "name"),
        key=lambda r: -map_data[r].get("ref_count", 0),
    )
    if manifest:
        lines.append("入口（manifest 声明，确证）")
        for r in manifest:
            lines.append(f"  {r}  ·  被 {map_data[r].get('ref_count', 0)} 个文件引用")
        lines.append("")
    if by_name:
        lines.append("入口候选（按命名推断，**非确证**；按被引次数排序）")
        for r in by_name[:ENTRY_NAME_LIMIT]:
            lines.append(f"  {r}  ·  被 {map_data[r].get('ref_count', 0)} 个文件引用")
        if len(by_name) > ENTRY_NAME_LIMIT:
            lines.append(f"  …另有 {len(by_name) - ENTRY_NAME_LIMIT} 个同名文件，完整清单见 .ai/map.json")
        lines.append("")

    by_ref = sorted(map_data.items(), key=lambda kv: kv[1].get("ref_count", 0), reverse=True)
    top = [r for r, e in by_ref if e.get("ref_count", 0) > 0][:top_limit]
    if top:
        lines.append("关键文件（按被引次数，确证）")
        for r in top:
            lines.append(f"  {r}  ·  被 {map_data[r].get('ref_count', 0)} 个文件引用")
        lines.append("")
    return lines


def render_map(map_data: dict, findings: list, site: dict) -> str:
    """事实层完整版：入口引用计数 + 阅读锚点 + 现行版对照。"""
    lines: list[str] = []
    lines.append(f"# 项目地图（事实层 · 阅读锚点）")
    lines.append(f"")
    lines.append(f"项目：{site.get('name','?')} · 地图生成指纹 {site.get('fingerprint','?')} · {site.get('count',0)} 候选文件")
    lines.append(f"> 这是**阅读锚点**，不是依赖图。完整事实请用 grep。")
    lines.append(f"")

    lines.extend(anchor_lines(map_data))

    decided = [f for f in findings if f.canonical is not None]
    ambiguous = [f for f in findings if f.canonical is None]
    if decided:
        lines.append(DECIDE_BASIS_HEADER)
        for f in decided:
            superseded = ', '.join(f.superseded) if f.superseded else '无'
            lines.append(f"  {f.canonical}  ← 现行版；已作废：{superseded}"
                         f"  （{_basis(f)}）")
        lines.append(f"")
    if ambiguous:
        lines.append(f"需人工确认（无决定性证据）")
        if len(ambiguous) <= AMBIGUOUS_DETAIL_LIMIT:
            for f in ambiguous:
                lines.append(f"  {', '.join(f.ambiguous)}")
        else:
            lines.append(f"  {len(ambiguous)} 组需人工确认，仅列出前 {AMBIGUOUS_DETAIL_LIMIT} 组：")
            for f in ambiguous[:AMBIGUOUS_DETAIL_LIMIT]:
                lines.append(f"    {', '.join(f.ambiguous)}")
            lines.append(f"  完整清单见 `.ai/stale.json`")
        lines.append(f"")

    lines.append(f"改任何文件前：`projstate explain <path>`")
    lines.append(f"不确定哪个是现行版：`projstate canonical <名>`")
    return "\n".join(lines) + "\n"


def render_hip(findings: list, generated: list[str], stale_docs: list | None = None) -> str:
    """卫生层：勿读清单 + 现行版对照 + 文档引用失效 + 生成物。"""
    lines: list[str] = []
    lines.append(f"# 项目卫生（勿读 / 现行版对照）")
    lines.append(f"")
    decided = [f for f in findings if f.canonical is not None]
    ambiguous = [f for f in findings if f.canonical is None]
    if decided:
        lines.append(DECIDE_BASIS_HEADER)
        for f in decided:
            names = "，".join(f.superseded) if f.superseded else "无"
            lines.append(f"  {f.canonical}  ← 当前    {names}  （{_basis(f)}）")
        lines.append(f"")
    if ambiguous:
        lines.append(f"需人工确认（工具无决定性证据，不猜）")
        if len(ambiguous) <= AMBIGUOUS_DETAIL_LIMIT:
            for f in ambiguous:
                lines.append(f"  候选：{', '.join(f.ambiguous)}")
            lines.append(f"  处理：`projstate resolve <候选>  # 确认一次，永久记住`")
        else:
            lines.append(f"  共 {len(ambiguous)} 组需人工确认，仅列出前 {AMBIGUOUS_DETAIL_LIMIT} 组：")
            for f in ambiguous[:AMBIGUOUS_DETAIL_LIMIT]:
                lines.append(f"    候选：{', '.join(f.ambiguous)}")
            lines.append(f"  完整清单见 `.ai/stale.json`；每组建议用 `projstate resolve <候选>` 确认一次。")
        lines.append(f"")
    if stale_docs:
        lines.append(f"文档引用已失效（这些文档引用的文件已变更，其结论不可再当现状）")
        for d in stale_docs:
            acked = f"　上次接受于 {d['acked_at']}" if d.get("acked_at") else ""
            lines.append(f"  {d['doc']}  ←  {'、'.join(d['deps'])}{acked}")
        lines.append(f"  看过并接受：`projstate ack <文档路径>`（此后下一次变更才会再报）")
        lines.append(f"")
    if generated:
        lines.append(f"生成物（勿读，可从源码重建）")
        for g in generated:
            lines.append(f"  {g}")
        lines.append(f"")
    if not findings and not stale_docs:
        lines.append(f"无旧版判定，也无文档引用失效。")
    return "\n".join(lines) + "\n"


def render_agents_block(summary: dict) -> str:
    """AGENTS.md 标记块（一页速览）。工具只写块内，块外一字不动。"""
    lines: list[str] = []
    lines.append(BEGIN_MARKER)
    lines.append(f"任务：{summary.get('task','未记录')} · 下一步：{summary.get('next','见 .ai/STATE.md')} · 候选 {summary.get('count',0)} 文件")
    stale = summary.get("stale") or []
    if stale:
        lines.append(
            f"⚠️ {len(stale)} 条结论的依赖文件已变更，已不可当现状，须复核（见 `.ai/STATE.md` 中标了「依赖已变更」的行）"
        )
    stale_docs = summary.get("stale_docs") or []
    if stale_docs:
        sample = "、".join(d["doc"] for d in stale_docs[:3])
        more = f" 等 {len(stale_docs)} 份" if len(stale_docs) > 3 else ""
        lines.append(
            f"⚠️ {len(stale_docs)} 份文档引用的文件已变更，其结论须复核：{sample}{more}（见 `.ai/HIP.md`，接受用 `projstate ack`）"
        )
    lines.append(f"")
    lines.append(f"用法：改文件前 `projstate explain <path>`；查现行版 `projstate canonical <名>`；查卫生 `projstate check`")
    lines.append(f"完整地图：`.ai/MAP.md` · 完整卫生：`.ai/HIP.md`")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"
