"""渲染：map.json/stale.json → MAP.md / HIP.md / AGENTS.md 标记块。"""

from __future__ import annotations

BEGIN_MARKER = "<!-- projstate:begin 自动生成，勿手改 -->"
END_MARKER = "<!-- projstate:end -->"


def render_map(map_data: dict, findings: list, site: dict) -> str:
    """事实层完整版：入口引用计数 + 阅读锚点 + 现行版对照。"""
    lines: list[str] = []
    lines.append(f"# 项目地图（事实层 · 阅读锚点）")
    lines.append(f"")
    lines.append(f"项目：{site.get('name','?')} · 地图生成指纹 {site.get('fingerprint','?')} · {site.get('count',0)} 候选文件")
    lines.append(f"> 这是**阅读锚点**，不是依赖图。完整事实请用 grep。")
    lines.append(f"")

    reachable = [r for r, e in map_data.items() if e.get("reachable")]
    if reachable:
        lines.append(f"入口（确证）")
        for r in sorted(reachable):
            lines.append(f"  {r}  ·  被 {map_data[r].get('ref_count',0)} 个文件引用")
        lines.append(f"")

    by_ref = sorted(map_data.items(), key=lambda kv: kv[1].get("ref_count", 0), reverse=True)
    top = [r for r, e in by_ref if e.get("ref_count", 0) > 0][:20]
    if top:
        lines.append(f"关键文件（按被引次数，确证）")
        for r in top:
            lines.append(f"  {r}  ·  被 {map_data[r].get('ref_count',0)} 个文件引用")
        lines.append(f"")

    decided = [f for f in findings if f.canonical is not None]
    ambiguous = [f for f in findings if f.canonical is None]
    if decided:
        lines.append(f"现行版对照（推断）")
        for f in decided:
            lines.append(f"  {f.canonical}  ← 现行版；已作废：{', '.join(f.superseded) if f.superseded else '无'}")
        lines.append(f"")
    if ambiguous:
        lines.append(f"需人工确认（无决定性证据）")
        for f in ambiguous:
            lines.append(f"  {', '.join(f.ambiguous)}")
        lines.append(f"")

    lines.append(f"改任何文件前：`projstate explain <path>`")
    lines.append(f"不确定哪个是现行版：`projstate canonical <名>`")
    return "\n".join(lines) + "\n"


def render_hip(findings: list, generated: list[str]) -> str:
    """卫生层：勿读清单 + 现行版对照 + 生成物。"""
    lines: list[str] = []
    lines.append(f"# 项目卫生（勿读 / 现行版对照）")
    lines.append(f"")
    decided = [f for f in findings if f.canonical is not None]
    ambiguous = [f for f in findings if f.canonical is None]
    if decided:
        lines.append(f"现行版对照（推断）")
        for f in decided:
            names = "，".join(f.superseded) if f.superseded else "无"
            lines.append(f"  {f.canonical}  ← 当前    {names}")
        lines.append(f"")
    if ambiguous:
        lines.append(f"需人工确认（工具无决定性证据，不猜）")
        for f in ambiguous:
            lines.append(f"  候选：{', '.join(f.ambiguous)}")
        lines.append(f"  处理：`projstate resolve <候选>  # 确认一次，永久记住`")
        lines.append(f"")
    if generated:
        lines.append(f"生成物（勿读，可从源码重建）")
        for g in generated:
            lines.append(f"  {g}")
        lines.append(f"")
    if not findings:
        lines.append(f"无旧版判定。")
    return "\n".join(lines) + "\n"


def render_agents_block(summary: dict) -> str:
    """AGENTS.md 标记块（一页速览）。工具只写块内，块外一字不动。"""
    lines: list[str] = []
    lines.append(BEGIN_MARKER)
    lines.append(f"任务：{summary.get('task','未记录')} · 下一步：{summary.get('next','见 .ai/STATE.md')} · 候选 {summary.get('count',0)} 文件")
    lines.append(f"")
    lines.append(f"用法：改文件前 `projstate explain <path>`；查现行版 `projstate canonical <名>`；查卫生 `projstate check`")
    lines.append(f"完整地图：`.ai/MAP.md` · 完整卫生：`.ai/HIP.md`")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"
