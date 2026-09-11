"""CLI 入口。默认只读；init/archive/restore 才写且需确认。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from projstate.facts import build_map, find_unresolved
from projstate.languages import is_generated
from projstate.render import (
    BEGIN_MARKER,
    END_MARKER,
    render_agents_block,
    render_hip,
    render_map,
)
from projstate.scan import iter_files, load_ignore, rel_posix
from projstate.stale import group_candidates, elect
from projstate.state.store import append_state, read_state


def _root() -> Path:
    return Path.cwd()


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _replace_block(text: str, block: str) -> str:
    """用新的块替换 AGENTS.md 里旧的 projstate 块；无块则追加。"""
    if BEGIN_MARKER not in text or END_MARKER not in text:
        return text.rstrip() + "\n\n" + block
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    return text[:start] + block.strip() + text[end:]


def _git_tracked(root: Path) -> set[str]:
    """获取 git 跟踪的相对 POSIX 路径；非 git 仓库返回空集。不假装知道。"""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return set()
        return {p.replace("\\", "/") for p in out.stdout.splitlines()}
    except Exception:
        return set()


def cmd_log(root: Path, text: str) -> int:
    from projstate import statedeps

    line = append_state(root, text)
    n = statedeps.snapshot_line(root, line)
    print(f"[projstate] 已记录：{line}")
    if n:
        print(f"[projstate] 已锚定该结论引用的 {n} 个文件；它们日后变更会在此行标出")
    return 0


def cmd_task(root: Path, name: str) -> int:
    """设置 STATE.md 的 `# 当前任务：`（每次开窗口都能看到在做什么）。"""
    from projstate.state.store import STATE_FILE, template

    state = root / ".ai" / "STATE.md"
    if not state.is_file():
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(template(), encoding="utf-8")
    content = state.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    for idx, ln in enumerate(lines):
        if ln.startswith("# 当前任务"):
            lines[idx] = f"# 当前任务：{name.strip()}\n"
            break
    state.write_text("".join(lines), encoding="utf-8")
    print(f"[projstate] 当前任务已设为：{name.strip()}")
    return 0


def cmd_status(root: Path) -> int:
    print(read_state(root))
    return 0


def cmd_refresh(root: Path) -> int:
    ai = root / ".ai"
    ignore = load_ignore(root)
    files = list(iter_files(root, ignore=ignore))
    map_data = build_map(root, files)
    unresolved = find_unresolved(root, files)
    track = _git_tracked(root)
    groups = group_candidates(list(map_data.keys()))
    findings = elect(groups, map_data, track)
    findings = _apply_overrides(root, findings)

    # 生成物统计（仅报告，不自动改）
    generated = sorted({
        rel_posix(root, d)
        for d in _top_dirs(root)
        if is_generated(rel_posix(root, d))
    })
    site = {"name": root.name, "fingerprint": _fingerprint(files), "count": len(files)}

    # 状态层复核：STATE.md 里结论的依赖若已变更，就地标出
    from projstate import statedeps

    stale_lines = statedeps.annotate(root)
    # 文档层复核：AGENTS.md / README.md / docs 里断言的依赖
    stale_docs, _ = statedeps.scan_docs(root)

    _write_json(ai / "map.json", {"files": map_data})
    _write_json(ai / "unresolved.json", unresolved)
    (ai / "MAP.md").write_text(render_map(map_data, findings, site), encoding="utf-8")
    (ai / "HIP.md").write_text(render_hip(findings, generated, stale_docs), encoding="utf-8")
    _write_json(ai / "stale.json", [_finding_dict(f) for f in findings])

    # 写 AGENTS.md 标记块
    agents = root / "AGENTS.md"
    if not agents.is_file():
        print("[projstate] 未发现 AGENTS.md，可运行 init 接入", file=sys.stderr)
    else:
        block = render_agents_block({
            "task": _read_task(root),
            "next": _read_next(root),
            "count": len(files),
            "stale": stale_lines,
            "stale_docs": stale_docs,
        })
        agents.write_text(_replace_block(agents.read_text(encoding="utf-8"), block), encoding="utf-8")
    print(f"[projstate] refresh 完成：{len(files)} 候选，{len(findings)} 组旧版判定")
    if stale_lines:
        print(f"[projstate] ⚠️ {len(stale_lines)} 条结论的依赖已变更，已在 STATE.md 标出，需复核：")
        for ln in stale_lines:
            print(f"  {ln}")
    if stale_docs:
        print(f"[projstate] ⚠️ {len(stale_docs)} 份文档的引用已失效，需复核：")
        for d in stale_docs[:10]:
            print(f"  {d['doc']}  ← {'、'.join(d['deps'])}")
        if len(stale_docs) > 10:
            print(f"  …另有 {len(stale_docs) - 10} 份，完整清单见 .ai/HIP.md")
        print("[projstate]   看过并接受：`projstate ack <文档路径>`（此后下一次变更才会再报）")
    return 0


def _apply_overrides(root: Path, findings: list) -> list:
    """把 canonical_overrides.json 里人为确认的现行版合并进判定结果。"""
    overrides_file = root / ".ai" / "canonical_overrides.json"
    if not overrides_file.is_file():
        return findings
    try:
        overrides = json.loads(overrides_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return findings

    from projstate.stale import StaleFinding
    import re

    result: list[StaleFinding] = []
    for f in findings:
        for stem, confirmed in overrides.items():
            group = f.signals.get("group", [])
            group_stems = [re.sub(r"\.(py|js|ts|tsx|jsx|mjs|cjs)$", "", g).lstrip("./") for g in group]
            if stem in group_stems:
                # 用人为确认的覆盖 canonical，其余为 superseded
                others = [g for g in group if g != confirmed]
                result.append(StaleFinding(
                    group_key=f.group_key,
                    canonical=confirmed,
                    superseded=tuple(others),
                    needs_review=(),
                    ambiguous=(),
                    signals={**f.signals, "overridden": True},
                    suggestion="人工确认（resolve）",
                ))
                break
        else:
            result.append(f)
    return result


def cmd_check(root: Path) -> int:
    ai = root / ".ai"
    dirty = False
    if not (ai / "map.json").is_file():
        print("[projstate] 地图未生成，请先 refresh")
        dirty = True
    stale = ai / "stale.json"
    if stale.is_file():
        entries = json.loads(stale.read_text(encoding="utf-8"))
        if entries:
            print(f"[projstate] {len(entries)} 组旧版判定待处理")
            dirty = True
    # 状态层：结论的依赖是否已变更（只报告，不写盘——check 是只读体检）
    from projstate import statedeps

    data = statedeps.load(root)
    flagged: list[str] = []
    for key, deps in data["lines"].items():
        bad = statedeps._changed(root, deps)
        if bad:
            flagged.append(f"{key}  ← 依赖已变更：{', '.join(bad)}")
    if flagged:
        print(f"[projstate] ⚠️ {len(flagged)} 条结论的依赖已变更，需复核：")
        for f in flagged:
            print(f"  {f}")
        dirty = True
    # 文档层：AGENTS.md / README.md / docs 里断言的依赖
    docs = statedeps.pending_docs(root)
    if docs:
        print(f"[projstate] ⚠️ {len(docs)} 份文档的引用已失效，需复核：")
        for d in docs[:10]:
            print(f"  {d['doc']}  ← {'、'.join(d['deps'])}")
        if len(docs) > 10:
            print(f"  …另有 {len(docs) - 10} 份，见 .ai/HIP.md")
        print("[projstate]   看过并接受：`projstate ack <文档路径>`")
        dirty = True
    return 1 if dirty else 0


def cmd_ack(root: Path, path: str | None) -> int:
    """把文档依赖基线重置为当前状态——"这次变更我看过了"。

    噪声抑制：活跃开发中，被引用的文件每次改动都会把文档标红；没有这条出路，
    清单会迅速退化成狼来了。ack 后要等**下一次**变更才会重新报警。
    """
    from projstate import statedeps

    rel = path.replace("\\", "/") if path else None
    if rel is not None:
        known = set(statedeps.load(root).get("docs") or {})
        if rel not in known:
            print(f"[projstate] {rel} 不在已锚定的文档清单中，无需 ack", file=sys.stderr)
            return 1
    n = statedeps.ack_docs(root, rel)
    print(f"[projstate] 已重置 {n} 份文档的依赖基线；下次它们引用的文件再变更时会重新报警")
    return 0


def cmd_explain(root: Path, path: str) -> int:
    mf = root / ".ai" / "map.json"
    if not mf.is_file():
        print("[projstate] 请先 refresh", file=sys.stderr)
        return 1
    data = json.loads(mf.read_text(encoding="utf-8"))["files"]
    rel = path.replace("\\", "/")
    if rel not in data:
        print(f"[projstate] {rel} 不在事实层（可能被忽略）", file=sys.stderr)
        return 1
    e = data[rel]
    print(f"{rel}  ·  被 {e.get('ref_count',0)} 个文件引用  ·  入口={e.get('reachable',False)}")
    if e.get("imports"):
        print(f"  引用：{', '.join(e['imports'])}")
    return 0


def cmd_canonical(root: Path, name: str) -> int:
    sf = root / ".ai" / "stale.json"
    if not sf.is_file():
        print("[projstate] 请先 refresh", file=sys.stderr)
        return 1
    entries = json.loads(sf.read_text(encoding="utf-8"))
    for entry in entries:
        for p in [entry.get("canonical"), *entry.get("superseded", [])]:
            if p and name in p:
                print(f"现行版：{entry.get('canonical')}  已作废：{', '.join(entry.get('superseded', []))}")
                return 0
    # 命中 ambiguous 组：这是"有冲突但不确定"，不是"不存在"
    for entry in entries:
        amb = entry.get("ambiguous", [])
        if amb and any(name in p for p in amb):
            print(f"[projstate] 模块 {name} 存在版本冲突但当前无决定性证据，候选：")
            for p in amb:
                print(f"  {p}")
            print(f"请用 `projstate resolve <候选>` 确认一次，将永久记住。")
            return 2
    print(f"[projstate] 未找到与 {name} 匹配的现行版判定", file=sys.stderr)
    return 1


def cmd_why(root: Path, path: str) -> int:
    sf = root / ".ai" / "stale.json"
    if not sf.is_file():
        print("[projstate] 请先 refresh", file=sys.stderr)
        return 1
    entries = json.loads(sf.read_text(encoding="utf-8"))
    rel = path.replace("\\", "/")
    for entry in entries:
        if rel in entry.get("superseded", []) or rel == entry.get("canonical"):
            print(f"{rel}  {entry.get('suggestion','')}")
            print(f"  组内：{', '.join(entry.get('signals',{}).get('group',[]))}")
            return 0
    print(f"[projstate] {rel} 无明显旧版判定", file=sys.stderr)
    return 1


def cmd_resolve(root: Path, path: str) -> int:
    """人为确认某候选为现行版，写入 canonical_overrides.json，永久记住。"""
    overrides_file = root / ".ai" / "canonical_overrides.json"
    overrides_file.parent.mkdir(parents=True, exist_ok=True)
    if overrides_file.is_file():
        try:
            overrides = json.loads(overrides_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            overrides = {}
    else:
        overrides = {}

    sf = root / ".ai" / "stale.json"
    if sf.is_file():
        entries = json.loads(sf.read_text(encoding="utf-8"))
        matched_group = None
        for entry in entries:
            amb = entry.get("ambiguous", [])
            if path in amb:
                matched_group = entry.get("signals", {}).get("group", [])
                break
        if matched_group:
            # 用组内主干名作为 key
            import re
            stem = re.sub(r"\.(py|js|ts|tsx|jsx|mjs|cjs)$", "", path).lstrip("./")
            overrides[stem] = path
            _write_json(overrides_file, overrides)
            print(f"[projstate] 已确认 {path} 为该组现行版，写入 {overrides_file}")
            return 0
    print(f"[projstate] {path} 不在任何待确认组中，无需 resolve", file=sys.stderr)
    return 1


def cmd_selftest() -> int:
    from projstate.stale import normalize_stem

    assert normalize_stem("export_v1.js") == "export"
    assert normalize_stem("finalize.js") == "finalize"
    print("[OK] selftest 通过")
    return 0


def cmd_init(root: Path) -> int:
    """接入当前项目：建 .ai/、初始化 STATE.md、把标记块写入 AGENTS.md。"""
    from projstate.state.store import STATE_FILE, template

    ai = root / ".ai"
    ai.mkdir(parents=True, exist_ok=True)
    state = ai / STATE_FILE.replace(".ai/", "")
    if not state.is_file():
        state.write_text(template(), encoding="utf-8")
        print(f"[projstate] 已创建 {state}")
    agents = root / "AGENTS.md"
    if not agents.is_file():
        # 不擅自创建 AGENTS.md（尊重用户入口文件体系），提示即可
        print("[projstate] 未发现 AGENTS.md，未接入。请创建 AGENTS.md 后重跑 init")
        return 0
    block = render_agents_block({
        "task": _read_task(root),
        "next": _read_next(root),
        "count": 0,
    })
    agents.write_text(_replace_block(agents.read_text(encoding="utf-8"), block), encoding="utf-8")
    print(f"[projstate] 已将标记块写入 {agents}")
    return 0


def _top_dirs(root: Path) -> list[Path]:
    """返回根下的顶层目录（用于生成物报告）。"""
    try:
        return [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []


def _fingerprint(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        try:
            h.update(f.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:8]


def _finding_dict(f) -> dict:
    """StaleFinding → 可 JSON 序列化 dict。"""
    return {
        "canonical": f.canonical,
        "superseded": list(f.superseded),
        "needs_review": list(f.needs_review),
        "ambiguous": list(f.ambiguous),
        "signals": f.signals,
        "suggestion": f.suggestion,
    }


def _read_task(root: Path) -> str:
    lines = read_state(root).splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# 当前任务"):
            tail = line.replace("# 当前任务", "").strip(" #:：-")
            if tail:
                return tail
            # 标题行本身为空，取标题后第一个非空、非小标题、非占位的内容
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s and not s.startswith("##"):
                    return s
            break
    # 不读"下一步"分组：任务名与下一步是两回事，避免错位
    return "未记录"


def _read_next(root: Path) -> str:
    lines = read_state(root).splitlines()
    for entry in _section_lines(lines, "下一步"):
        s = entry.strip()
        if s.startswith("- "):
            return _strip_date_prefix(s[2:]) or "见 .ai/STATE.md"
        if s and not s.startswith("##") and not s.startswith("*"):
            return s
    return "见 .ai/STATE.md"


def _section_lines(lines: list[str], section: str) -> list[str]:
    """返回某个 `## <section>` 分组下的内容行（不含其它小标题）。"""
    result: list[str] = []
    capture = False
    for ln in lines:
        stripped = ln.strip()
        if stripped == f"## {section}":
            capture = True
            continue
        if capture and stripped.startswith("##"):
            break
        if capture:
            result.append(ln)
    return result


def _strip_date_prefix(text: str) -> str:
    """去掉 log 行开头的 `YYYY-MM-DD ` 前缀。"""
    import re

    return re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", text).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="projstate", description="项目状态与阅读锚点")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    p_log = sub.add_parser("log"); p_log.add_argument("text")
    p_task = sub.add_parser("task"); p_task.add_argument("name")
    sub.add_parser("refresh")
    sub.add_parser("check")
    sub.add_parser("selftest")
    p_ex = sub.add_parser("explain"); p_ex.add_argument("path")
    p_ca = sub.add_parser("canonical"); p_ca.add_argument("name")
    p_wy = sub.add_parser("why"); p_wy.add_argument("path")
    p_re = sub.add_parser("resolve"); p_re.add_argument("path")
    p_ack = sub.add_parser("ack"); p_ack.add_argument("path", nargs="?")
    p_mcp = sub.add_parser("mcp"); p_mcp.add_argument("--root", default=None)
    sub.add_parser("init")
    sub.add_parser("archive")
    sub.add_parser("restore")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(sys.stderr)
        return 1
    root = _root()
    cmd = args.command
    if cmd == "status":
        return cmd_status(root)
    if cmd == "log":
        return cmd_log(root, args.text)
    if cmd == "task":
        return cmd_task(root, args.name)
    if cmd == "refresh":
        return cmd_refresh(root)
    if cmd == "check":
        return cmd_check(root)
    if cmd == "explain":
        return cmd_explain(root, args.path)
    if cmd == "canonical":
        return cmd_canonical(root, args.name)
    if cmd == "why":
        return cmd_why(root, args.path)
    if cmd == "resolve":
        return cmd_resolve(root, args.path)
    if cmd == "ack":
        return cmd_ack(root, args.path)
    if cmd == "mcp":
        from projstate.mcp import serve

        return serve(Path(args.root) if args.root else root)
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "init":
        return cmd_init(root)
    if cmd in ("archive", "restore"):
        print("[projstate] archive/restore 需二次确认，v1 预留", file=sys.stderr)
        return 1
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
