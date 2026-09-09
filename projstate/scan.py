"""文件遍历：忽略规则 + 归档/生成物过滤。"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path

from projstate.languages import GENERATED_DIRS, is_archive, is_generated


def rel_posix(root: Path, p: Path) -> str:
    """相对项目根的 POSIX 风格路径（正斜杠），保证跨平台产物一致。"""
    return p.relative_to(root).as_posix()


def load_ignore(root: Path) -> tuple[str, ...]:
    """读取 .gitignore 的普通模式。不支持 `!` 取反与 `**` 递归通配，遇到就跳过该行。"""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return ()
    patterns: list[str] = []
    for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.rstrip("/"))
    return tuple(patterns)


def _ignored(rel: str, ignore: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*") for pat in ignore)


def iter_files(root: Path, ignore: tuple[str, ...] = ()) -> Iterator[Path]:
    """深度优先遍历 root，跳过生成目录/归档目录/规则文件/忽略模式，产出文件 Path。

    排除根级 agent 规则文件（AGENTS.md / CLAUDE.md / GEMINI.md）——它们是工具会改写的入口，
    不是项目源码事实，不应进入 map.json，否则改写其 mtime 会造成无意义的自指 diff。
    """
    rule_files = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            rel = rel_posix(root, entry)
            if entry.is_dir():
                if entry.name in GENERATED_DIRS or is_archive(rel) or _ignored(rel, ignore):
                    continue
                stack.append(entry)
            else:
                if entry.name in rule_files or is_generated(rel) or is_archive(rel) or _ignored(rel, ignore):
                    continue
                yield entry
