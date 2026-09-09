"""扩展名 → 语言映射，以及"归档 / 生成物"分类判定。"""

from __future__ import annotations

import re

# 归档目录：命中即标 archived，agent 默认不当作现行版。
ARCHIVE_DIRS: frozenset[str] = frozenset(
    {
        "_archive", "archive", "deprecated", "old", "backup", "bak",
        "legacy", "obsolete",
    }
)

# 生成物目录：能从源码重建，属于垃圾，不进事实层。
GENERATED_DIRS: frozenset[str] = frozenset(
    {
        "node_modules", "dist", "dist2", "build", "out", "target",
        ".venv", "venv", "__pycache__", ".git", ".ai", ".cache",
        ".mypy_cache", ".pytest_cache", "coverage", "resources",
        ".build-src",
    }
)

# 生成物后缀：单文件也判生成物。
GENERATED_EXTS: frozenset[str] = frozenset(
    {".asar", ".pak", ".pyc", ".dll", ".exe", ".so", ".bin", ".min.js"}
)

# 文件名版本标记（谨慎使用，作现行版选举的第 4 级信号）。
NAME_VERSION_PATTERN = re.compile(
    r"(?:^|[_-])(?:v\d+|old|new|final|latest|backup|copy|bak|deprecated|temp|tmp)(?:[._-]|$)"
    r"|(?:-旧|-副本| 2|\(1\)|\.final|\.old|\.new|\.bak)$"
    r"|(?<![a-zA-Z])\d{1,3}$",
    re.IGNORECASE,
)

_LANG_BY_EXT: dict[str, str] = {
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".py": "python", ".pyi": "python",
}


def detect_language(rel: str) -> str:
    """按扩展名判断语言；不支持返回 'other'（降级，只有文件名事实）。"""
    lower = rel.lower()
    for ext, lang in _LANG_BY_EXT.items():
        if lower.endswith(ext):
            return lang
    return "other"


def _parts(rel: str) -> list[str]:
    return rel.replace("\\", "/").split("/")


def is_generated(rel: str) -> bool:
    """是否生成物：路径含生成目录，或为生成物后缀。"""
    parts = _parts(rel)
    if any(part in GENERATED_DIRS for part in parts[:-1]):
        return True
    lower = rel.lower()
    return any(lower.endswith(ext) for ext in GENERATED_EXTS)


def is_archive(rel: str) -> bool:
    """是否归档区文件：路径含归档目录。目录名本身即为声明，零误报。"""
    parts = _parts(rel)
    return any(part in ARCHIVE_DIRS for part in parts[:-1])


def has_name_version_marker(rel: str) -> bool:
    """文件名是否含版本/旧版标记（用于现行版选举的弱信号）。"""
    name = _parts(rel)[-1]
    # 备份类后缀（.bak/.old/.new/.copy）本身就是标记，不剥掉
    if name.lower().endswith((".bak", ".old", ".new", ".copy", ".tmp")):
        return True
    stem = name.rsplit(".", 1)[0]  # 其他情况剥普通扩展名，避免 .py 挡住数字后缀
    return bool(NAME_VERSION_PATTERN.search(stem))
