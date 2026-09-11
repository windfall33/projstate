"""状态层依赖快照：让 STATE.md 里的结论在依赖变更后自动暴露，而不是静默过期。

工具不替人判断"做完了没"（那是判断，不是事实）。但工具**可以**记录：
写下这条结论的时候，它引用的文件是什么内容。之后这些文件一变更，
这条结论就不再是"现状"，而是"需要复核的旧话"——并在原地标出来。

这是纯元数据操作：不猜语义、不判真假，只比对哈希。
所以它失败时是漏报，不是误导。

对应 ProjectBrain 实证里的 F4/F10：`sync` 只刷新文件哈希，不检测语义过时，
于是"大脑"以权威事实源的身份静默说谎，agent 为此多花 2.5 倍时间去推翻它。
"""

from __future__ import annotations

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


def _hash_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


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
    p = root / DEPS_FILE
    if not p.is_file():
        return {"version": 1, "lines": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "lines": {}}
    if not isinstance(data, dict) or not isinstance(data.get("lines"), dict):
        return {"version": 1, "lines": {}}
    return data


def save(root: Path, data: dict) -> None:
    p = root / DEPS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_line(root: Path, line: str) -> int:
    """写一条结论时记下它引用的文件哈希。返回记录的依赖数（0 表示无引用，不记）。"""
    deps = extract_deps(line, root)
    if not deps:
        return 0
    data = load(root)
    data["lines"][_key(line)] = deps
    save(root, data)
    return len(deps)


def _key(line: str) -> str:
    """结论行的身份：去掉陈旧标记与首尾空白后的原文。"""
    return _strip_stale_mark(line).strip()


def _strip_stale_mark(line: str) -> str:
    idx = line.find(STALE_MARK)
    return line[:idx].rstrip() if idx != -1 else line


def _changed(root: Path, deps: dict[str, str]) -> list[str]:
    """哪些依赖的内容已经和写结论时不一样了。文件消失也算变更。"""
    out: list[str] = []
    for rel, old in deps.items():
        if _hash_file(root / rel) != old:
            out.append(rel)
    return out


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
