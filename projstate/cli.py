"""CLI 入口。默认只读；init/archive/restore 才写且需确认。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from projstate.facts import build_map
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
    line = append_state(root, text)
    print(f"[projstate] 已记录：{line}")
    return 0


def cmd_status(root: Path) -> int:
    print(read_state(root))
    return 0


def cmd_refresh(root: Path) -> int:
    ai = root / ".ai"
    ignore = load_ignore(root)
    files = list(iter_files(root, ignore=ignore))
    map_data = build_map(root, files)
    track = _git_tracked(root)
    groups = group_candidates(list(map_data.keys()))
    findings = elect(groups, map_data, track)

    # 生成物统计（仅报告，不自动改）
    generated = sorted({
        rel_posix(root, d)
        for d in _top_dirs(root)
        if is_generated(rel_posix(root, d))
    })
    site = {"name": root.name, "fingerprint": _fingerprint(files), "count": len(files)}

    _write_json(ai / "map.json", {"files": map_data})
    (ai / "MAP.md").write_text(render_map(map_data, findings, site), encoding="utf-8")
    (ai / "HIP.md").write_text(render_hip(findings, generated), encoding="utf-8")
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
        })
        agents.write_text(_replace_block(agents.read_text(encoding="utf-8"), block), encoding="utf-8")
    print(f"[projstate] refresh 完成：{len(files)} 候选，{len(findings)} 组旧版判定")
    return 0


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
    return 1 if dirty else 0


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


def cmd_selftest() -> int:
    from projstate.stale import normalize_stem

    assert normalize_stem("export_v1.js") == "export"
    assert normalize_stem("finalize.js") == "finalize"
    print("[OK] selftest 通过")
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
    for line in read_state(root).splitlines():
        if line.startswith("# 当前任务"):
            return line.replace("# 当前任务", "").strip() or "未记录"
    return "未记录"


def _read_next(root: Path) -> str:
    lines = read_state(root).splitlines()
    for i, line in enumerate(lines):
        if line == "## 下一步" and i + 1 < len(lines):
            return lines[i + 1].strip() or "见 .ai/STATE.md"
    return "见 .ai/STATE.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="projstate", description="项目状态与阅读锚点")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    p_log = sub.add_parser("log"); p_log.add_argument("text")
    sub.add_parser("refresh")
    sub.add_parser("check")
    sub.add_parser("selftest")
    p_ex = sub.add_parser("explain"); p_ex.add_argument("path")
    p_ca = sub.add_parser("canonical"); p_ca.add_argument("name")
    p_wy = sub.add_parser("why"); p_wy.add_argument("path")
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
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "init":
        print("[projstate] init 将接入当前项目（写 AGENTS.md 标记块，需确认）——v1 请先手动确认后启用")
        return 0
    if cmd in ("archive", "restore"):
        print("[projstate] archive/restore 需二次确认，v1 预留", file=sys.stderr)
        return 1
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
