"""依赖快照：让结论在依赖变更后自动暴露，而不是静默过期。

覆盖两层：
  - **状态层**（`.ai/STATE.md`）：人/agent 经 `projstate log` 写下的结论，就地标注。
  - **文档层**（`AGENTS.md` / `README.md` / `docs/**/*.md`）：仓库里已有的断言。
    这一层才是 agent 自动读的东西，且存量巨大（实测 hermes-agent：
    267 份文档、596 处对真实文件的引用，此前零保护）。

工具不替人判断真假（那是判断，不是事实）。它只记录：写下这条断言的时候，
它引用的文件是什么内容。之后这些文件一变更，这条断言就不再是"现状"，
而是"需要复核的旧话"。

纯元数据操作：不猜语义、不判真假，只比对哈希。所以它失败时是漏报，不是误导。

**噪声抑制**：文档层天然会反复报警（引用的文件在活跃开发中就会一直变），
不管就会退化成狼来了。`projstate ack <文档>` 把基线重置为当前状态——
"这次变更我看过了"——此后要等**下一次**变更才会重新报警。

对应 ProjectBrain 实证里的 F4/F10：`sync` 只刷新文件哈希，不检测语义过时，
于是"大脑"以权威事实源的身份静默说谎，agent 为此多花 2.5 倍时间去推翻它。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

DEPS_FILE = ".ai/state_deps.json"
STALE_MARK = "⚠️ 依赖已变更"

# 结论里可能引用的文件：带源码/文档扩展名的路径样 token。
_DEP_TOKEN = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*\."
    r"(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|mts|cts|md|json|toml|yaml|yml"
    r"|rs|go|java|kt|css|html|vue|svelte|sh|sql|ini|cfg)",
    re.IGNORECASE,
)

# 文档层覆盖范围：markdown 一律算文档；根级规则文件由工具自己改写（不进 iter_files），
# 单独补进来——它们恰恰是 agent 最先读的。
_DOC_SUFFIXES = (".md", ".markdown")
_ROOT_DOCS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "README.md")


def _today() -> str:
    return datetime.date.today().isoformat()


# 根级规则文件：本工具会往里写自己的标记块，因此不能整文件哈希。
_RULE_NAMES = {"agents.md", "claude.md", "gemini.md"}


def _strip_tool_block(raw: bytes) -> bytes:
    """去掉本工具写进 AGENTS.md 的标记块，只留人工内容。"""
    from projstate.render import BEGIN_MARKER, END_MARKER  # 延迟导入，避免环

    text = raw.decode("utf-8", "replace")
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start != -1 and end != -1:
        text = text[:start] + text[end + len(END_MARKER):]
    # 首尾空白归一化：插入/移除标记块会在块外留下空行，那不算人工内容变更
    return text.strip().encode("utf-8")


def _hash_file(p: Path) -> str:
    try:
        raw = p.read_bytes()
    except OSError:
        return ""
    # 规则文件只哈希**标记块之外**的内容。整文件哈希会自指：工具每次 refresh 都
    # 改写标记块 → 引用 AGENTS.md 的文档全部被判失效 → ack → 下次 refresh 又改
    # → 再次失效，形成永远无法收敛的报警循环。实测 hermes-agent：改一次
    # AGENTS.md 会牵动 54 份文档。
    if p.name.lower() in _RULE_NAMES:
        raw = _strip_tool_block(raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def extract_deps(text: str, root: Path) -> dict[str, str]:
    """从一段文字里抽出它引用的、当前真实存在的文件 → 内容哈希。

    只收真实存在的文件：抽不到就不记，避免把"提到过的名字"当成依赖。
    """
    deps: dict[str, str] = {}
    for raw in _DEP_TOKEN.findall(text.replace("\\", "/")):
        rel = raw.lstrip("./")
        if not rel:
            continue
        candidate = root / rel
        if candidate.is_file():
            deps[rel] = _hash_file(candidate)
    return deps


def load(root: Path) -> dict:
    """读快照表；损坏或缺失时返回空表（不抛，坏文件不该阻断 refresh）。"""
    empty = {"version": 1, "lines": {}, "docs": {}}
    p = root / DEPS_FILE
    if not p.is_file():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    if not isinstance(data.get("lines"), dict):
        data["lines"] = {}
    if not isinstance(data.get("docs"), dict):
        data["docs"] = {}
    return data


def save(root: Path, data: dict) -> None:
    p = root / DEPS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _changed(root: Path, deps: dict[str, str]) -> list[str]:
    """哪些依赖的内容已经和记录时不一样了。文件消失也算变更。"""
    return [rel for rel, old in deps.items() if _hash_file(root / rel) != old]


# ---------------------------------------------------------------- 状态层


def _key(line: str) -> str:
    """结论行的身份：去掉陈旧标记与首尾空白后的原文。"""
    return _strip_stale_mark(line).strip()


def _strip_stale_mark(line: str) -> str:
    idx = line.find(STALE_MARK)
    return line[:idx].rstrip() if idx != -1 else line


def snapshot_line(root: Path, line: str) -> int:
    """写一条结论时记下它引用的文件哈希。返回记录的依赖数（0 表示无引用，不记）。"""
    deps = extract_deps(line, root)
    if not deps:
        return 0
    data = load(root)
    data["lines"][_key(line)] = deps
    save(root, data)
    return len(deps)


def annotate(root: Path) -> list[str]:
    """在 STATE.md 上就地标注依赖已变更的结论行；返回被标注的行原文。

    幂等且双向：先剥掉旧标记再重新判定，所以文件被改回原样时标记会自动消失。
    没有快照的行首次见到即补基线——基线之后才可能被检出变更。
    """
    state = root / ".ai" / "STATE.md"
    if not state.is_file():
        return []
    original = state.read_text(encoding="utf-8")
    data = load(root)
    lines: dict = data["lines"]
    stale_lines: list[str] = []
    out: list[str] = []

    for raw in original.splitlines():
        base = _strip_stale_mark(raw)
        key = base.strip()
        # 只处理结论行（列表项），跳过标题与空行
        if key.startswith(("- ", "* ")) and len(key) > 3:
            deps = lines.get(key)
            if deps is None:
                fresh = extract_deps(key, root)
                if fresh:
                    lines[key] = fresh
                out.append(base)
                continue
            bad = _changed(root, deps)
            if bad:
                out.append(f"{base.rstrip()}  {STALE_MARK}：{', '.join(bad)}")
                stale_lines.append(key)
                continue
        out.append(base)

    new_text = "\n".join(out) + "\n"
    if new_text != original:
        state.write_text(new_text, encoding="utf-8")
    save(root, data)
    return stale_lines


# ---------------------------------------------------------------- 文档层


def doc_files(root: Path) -> list[str]:
    """项目里的 markdown 文档（含根级规则文件），POSIX 相对路径。

    复用 scan.iter_files 的排除规则（生成物目录、归档目录、.gitignore），
    所以 node_modules / dist / archive 下的 markdown 不会被拉进来。
    """
    from projstate.scan import iter_files, load_ignore, rel_posix

    out: set[str] = set()
    for p in iter_files(root, ignore=load_ignore(root)):
        if p.suffix.lower() in _DOC_SUFFIXES:
            out.add(rel_posix(root, p))
    for name in _ROOT_DOCS:
        if (root / name).is_file():
            out.add(name)
    return sorted(out)


def scan_docs(root: Path) -> tuple[list[dict], dict]:
    """重算所有文档的依赖快照。

    返回 (失效清单, 新快照)。失效清单每项含 doc / deps（已变的） / acked_at。
    已失效的文档**保留旧基线**，因此不 ack 就会一直报——不会因为再跑一次
    refresh 就悄悄"自愈"。
    """
    data = load(root)
    old: dict = data.get("docs") or {}
    stale: list[dict] = []
    new: dict = {}

    for rel in doc_files(root):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        deps = extract_deps(text, root)
        if not deps:
            continue
        prev = old.get(rel)
        if isinstance(prev, dict) and isinstance(prev.get("deps"), dict):
            bad = _changed(root, prev["deps"])
            if bad:
                stale.append({
                    "doc": rel,
                    "deps": bad,
                    "acked_at": prev.get("acked_at"),
                    "since": prev.get("at"),
                })
                new[rel] = prev  # 保留旧基线：不 ack 就一直标着
                continue
            if prev["deps"] == deps:
                new[rel] = prev  # 完全没变：原样保留，包括 ack 留痕（也免去文件抖动）
                continue
            # 引用的文件集合变了但内容都没变：更新基线，保留 ack 留痕——
            # 否则每次 refresh 都会把"上次接受于何时"抹掉。
            new[rel] = {"deps": deps, "at": _today(), "acked_at": prev.get("acked_at")}
            continue
        new[rel] = {"deps": deps, "at": _today(), "acked_at": None}

    dirty = new != old
    if dirty:
        data["docs"] = new
        save(root, data)
    return stale, new


def pending_docs(root: Path) -> list[dict]:
    """只读：当前有哪些文档的依赖已变更。供 `check` 用，不写盘。"""
    data = load(root)
    out: list[dict] = []
    for rel, entry in (data.get("docs") or {}).items():
        if not isinstance(entry, dict) or not isinstance(entry.get("deps"), dict):
            continue
        bad = _changed(root, entry["deps"])
        if bad:
            out.append({
                "doc": rel,
                "deps": bad,
                "acked_at": entry.get("acked_at"),
                "since": entry.get("at"),
            })
    return sorted(out, key=lambda d: d["doc"])


def ack_docs(root: Path, rel: str | None = None) -> int:
    """把文档依赖基线重置为当前状态（"这次变更我看过了"）。

    重置后标记清除；等**下一次**变更才会重新报警。这就是噪声抑制：
    没有它，活跃开发中每个被引用的文件都会把文档反复标红，最后没人再看。
    """
    data = load(root)
    docs: dict = data.get("docs") or {}
    targets = [rel] if rel else list(docs.keys())
    n = 0
    for t in targets:
        p = root / t
        if not p.is_file():
            continue
        try:
            deps = extract_deps(p.read_text(encoding="utf-8", errors="replace"), root)
        except OSError:
            continue
        if not deps:
            continue
        docs[t] = {"deps": deps, "at": _today(), "acked_at": _today()}
        n += 1
    data["docs"] = docs
    save(root, data)
    return n
