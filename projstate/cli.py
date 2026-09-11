"""CLI 入口。默认只读；init/archive/restore 才写且需确认。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from projstate import brief
from projstate.facts import build_map, find_unresolved
from projstate.languages import is_generated
from projstate.render import render_hip, render_map
from projstate.scan import iter_files, load_ignore, rel_posix
from projstate.stale import group_candidates, elect
from projstate.state.store import append_state, read_state


def _root() -> Path:
    return Path.cwd()


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")




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
    site = {"name": root.name, "fingerprint": brief.fingerprint_files(files), "count": len(files)}

    _write_json(ai / "map.json", {"files": map_data})
    _write_json(ai / "unresolved.json", unresolved)
    (ai / "MAP.md").write_text(render_map(map_data, findings, site), encoding="utf-8")
    (ai / "HIP.md").write_text(render_hip(findings, generated), encoding="utf-8")
    _write_json(ai / "stale.json", [_finding_dict(f) for f in findings])

    # 确保交接单块存在（已有正文不动 —— 那是人/agent 写的东西）
    what = _ensure_brief(root, files)
    if what == "created":
        print(f"[projstate] 未发现 AGENTS.md，已创建只含交接单的 {brief.AGENTS_FILE}", file=sys.stderr)
    print(f"[projstate] refresh 完成：{len(files)} 候选，{len(findings)} 组旧版判定")
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

    # 交接单是否过期：源码改了但交接单没跟上 —— 这正是"换窗口后读到过期认知"的入口。
    text = brief.read_agents(root)
    if brief.read_block(text) is None:
        print(f"[projstate] 未接入交接单：{brief.AGENTS_FILE} 里没有 projstate 块（init/refresh 可补）")
    else:
        files = list(iter_files(root, ignore=load_ignore(root)))
        if brief.is_stale(text, brief.fingerprint_files(files)):
            st = brief.read_stamp(text)
            print("[projstate] 交接单已过期：源码在 "
                  f"{st.at if st else '?'} 之后被改过（交接单正文没动）。"
                  f"请更新 {brief.AGENTS_FILE} 的交接单，"
                  "然后 `projstate brief stamp`")
            dirty = True
    return 1 if dirty else 0


def cmd_brief(root: Path, action: str) -> int:
    """交接单：`show` 打印当前块，`stamp` 只更新指纹戳（正文不动）。

    写正文请直接改 AGENTS.md 的块——agent 改 markdown 比学一套新参数可靠。
    """
    text = brief.read_agents(root)
    if action == "show":
        blk = brief.read_block(text)
        if blk is None:
            print(f"[projstate] {brief.AGENTS_FILE} 里没有交接单块，先跑 init 或 refresh",
                  file=sys.stderr)
            return 1
        st = brief.read_stamp(text)
        print(blk)
        if st:
            files = list(iter_files(root, ignore=load_ignore(root)))
            flag = "已过期" if brief.is_stale(text, brief.fingerprint_files(files)) else "最新"
            print(f"[projstate] 交接单状态：{flag}（盖章于 {st.at}）")
        return 0
    if action == "stamp":
        files = list(iter_files(root, ignore=load_ignore(root)))
        src = brief.fingerprint_files(files)
        had = brief.read_block(text) is not None
        brief.write_agents(root, brief.set_stamp(text, src))
        print(f"[projstate] 交接单已盖章：src={src} at={brief.now_stamp()}"
              + ("" if had else f"（原无块，已新建于 {brief.AGENTS_FILE}）"))
        return 0
    if action == "check":
        # 单一职责的退出码：只回答"交接单过期没有"，不管旧版判定等其它体检项。
        # pre-commit 必须用这个，否则有未确认版本组的仓库会连提交都被挡住。
        if brief.read_block(text) is None:
            print(f"[projstate] {brief.AGENTS_FILE} 里没有交接单块，先跑 init 或 refresh",
                  file=sys.stderr)
            return 1
        files = list(iter_files(root, ignore=load_ignore(root)))
        if brief.is_stale(text, brief.fingerprint_files(files)):
            st = brief.read_stamp(text)
            print("[projstate] 交接单已过期：源码在 "
                  f"{st.at if st else '?'} 之后被改过（交接单正文没动）。"
                  f"更新 {brief.AGENTS_FILE} 的交接单后跑 "
                  "`projstate brief stamp`", file=sys.stderr)
            return 1
        return 0
    print("[projstate] 用法：projstate brief [show|stamp|check]", file=sys.stderr)
    return 2


HOOK_BEGIN = "# projstate:begin"
HOOK_BODY = (
    "#!/bin/sh\n"
    "# projstate:begin（由 projstate hook install 写入，勿手改）\n"
    "if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi\n"
    '"$PY" -m projstate.cli brief check || exit 1\n'
    "# projstate:end\n"
)


def cmd_hook(root: Path, action: str) -> int:
    """把 `check` 挂到 git pre-commit 上：交接单过期就挡住提交。"""
    import os

    if not (root / ".git").is_dir():
        print("[projstate] 不是 git 仓库，装不了钩子", file=sys.stderr)
        return 1
    target = root / ".git" / "hooks" / "pre-commit"
    old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""

    if action == "status":
        print(f"[projstate] pre-commit：{'已安装' if HOOK_BEGIN in old else ('存在但不是本工具装的' if old else '未安装')}")
        return 0
    if action == "install":
        if old and HOOK_BEGIN not in old:
            print(f"[projstate] {target} 已存在且不是本工具装的，未改动", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(HOOK_BODY, encoding="utf-8")
        try:
            os.chmod(target, 0o755)
        except OSError:
            pass
        print(f"[projstate] 已安装 pre-commit：{target}")
        return 0
    if action == "uninstall":
        if not old:
            print("[projstate] 没有 pre-commit 钩子，无需卸载")
            return 0
        if HOOK_BEGIN not in old:
            print(f"[projstate] {target} 不是本工具装的，未改动", file=sys.stderr)
            return 1
        target.unlink()
        print("[projstate] 已卸载 pre-commit")
        return 0
    print("[projstate] 用法：projstate hook [status|install|uninstall]", file=sys.stderr)
    return 2


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
    """接入当前项目：建 .ai/、初始化 STATE.md、确保 AGENTS.md 里有交接单块。

    与 v0.1 的差别：不再因为"没有 AGENTS.md"就放弃接入。交接单的载体就是 AGENTS.md，
    没有就创建一个只含交接单的文件并说明（第四轮实测：6 个真实仓库里 5 个没有它，
    旧行为等于工具装上了但没有任何入口）。已有正文一字不动。
    """
    from projstate.state.store import STATE_FILE, template

    ai = root / ".ai"
    ai.mkdir(parents=True, exist_ok=True)
    state = ai / STATE_FILE.replace(".ai/", "")
    if not state.is_file():
        state.write_text(template(), encoding="utf-8")
        print(f"[projstate] 已创建 {state}")

    what = _ensure_brief(root)
    if what == "created":
        print(f"[projstate] 未发现 AGENTS.md，已创建只含交接单的 {brief.AGENTS_FILE}")
    elif what == "inserted":
        print(f"[projstate] 已在 {brief.AGENTS_FILE} 里插入交接单块")
    else:
        print(f"[projstate] {brief.AGENTS_FILE} 里已有交接单块，正文未改动")
    print(f"[projstate] 下一步：填 {brief.AGENTS_FILE} 的交接单，然后 `projstate brief stamp`")
    return 0


def _ensure_brief(root: Path, files: list[Path] | None = None) -> str:
    """确保 AGENTS.md 里有交接单块。返回 created / inserted / kept。

    只在"没有文件"或"没有块"时写；已有块的正文一字不动（改的是人与 agent，不是我）。
    """
    agents = root / brief.AGENTS_FILE
    existed = agents.is_file()
    text = brief.read_agents(root)
    if files is None:
        files = list(iter_files(root, ignore=load_ignore(root)))
    src = brief.fingerprint_files(files)
    if brief.read_block(text) is None:
        brief.write_agents(root, brief.upsert_block(text, brief.default_block(src)))
        return "inserted" if existed else "created"
    if brief.read_stamp(text) is None:
        # 历史版本的块没有指纹戳：补戳，正文不动
        brief.write_agents(root, brief.set_stamp(text, src))
    return "kept"


def _top_dirs(root: Path) -> list[Path]:
    """返回根下的顶层目录（用于生成物报告）。"""
    try:
        return [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []


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
    sub.add_parser("init")
    p_br = sub.add_parser("brief")
    p_br.add_argument("action", nargs="?", default="show",
                      choices=["show", "stamp", "check"])
    p_hk = sub.add_parser("hook")
    p_hk.add_argument("action", nargs="?", default="status",
                      choices=["status", "install", "uninstall"])
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
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "init":
        return cmd_init(root)
    if cmd == "brief":
        return cmd_brief(root, args.action)
    if cmd == "hook":
        return cmd_hook(root, args.action)
    if cmd in ("archive", "restore"):
        print("[projstate] archive/restore 需二次确认，v1 预留", file=sys.stderr)
        return 1
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
