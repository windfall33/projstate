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
        # agent 工具自己的状态目录：不是项目事实，而且工具会往里写文件——
        # 算进源码指纹会导致"刚装完钩子就把自己的交接单判成过期"。
        ".claude", ".codex",
    }
)

# 生成物后缀：单文件也判生成物。
GENERATED_EXTS: frozenset[str] = frozenset(
    {".asar", ".pak", ".pyc", ".dll", ".exe", ".so", ".bin", ".min.js"}
)

# 明确的旧版语义词：方向确定，可作现行版选举的决定性信号。
_OLD_WORDS = "old|backup|bak|legacy|deprecated|archive|original"
# 疑似版本语义词：方向不确定，不作决定性信号。
# 不包含 test/spec —— 见 TEST_FILE_MARKER：测试文件不是「版本」，把它当版本词会让
# `catalog.ts` 与 `catalog.test.ts` 被削成同一主干而同组，测试文件 ref_count=0 就被判「建议归档」。
_AMBIG_WORDS = "v\\d+|new|next|final|latest|newest|refactor|rebuilt|copy|temp|tmp"

# 测试文件标记：测试定义行为契约，是「该读的文件」，不是「旧版本」。
# 命中者永不参与版本归组（见 stale.group_candidates 的硬闸门）。
TEST_FILE_MARKER = re.compile(
    r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|py)$"
    r"|_test\.py$|^test_.*\.py$",
    re.IGNORECASE,
)

# 明确旧版标记：方向确定。例：export_old.py / export_backup.py / settle-旧.py / a_original.py
OLD_NAME_MARKER = re.compile(
    rf"(?:^|[_-])(?:{_OLD_WORDS})(?:[._-]|$)"
    r"|(?:-旧|-副本|\(1\)| 2|\.old|\.bak)$",
    re.IGNORECASE,
)

# 疑似版本标记：方向不确定。例：export_v2.py / export_new.py / export_final.py / a_next.py
AMBIG_VERSION_MARKER = re.compile(
    rf"(?:^|[_-])(?:{_AMBIG_WORDS})(?:[._-]|$)"
    r"|(?:\.new|\.copy\.|\.tmp)$",
    re.IGNORECASE,
)

# 归组用：去掉一切版本/旧版/副本/年份后缀标记，得到主干名。
# 覆盖明确旧版 + 疑似版本 + 中文副本 + 年份后缀（2026 等）。不含裸数字（feature_0 非版本）。
# 第一段用**前瞻** `(?=[._-]|$)` 而非吃掉尾随分隔符：否则 `registry-entry-v1.schema.json`
# 里的 `-v1.` 会把 "schema" 前的点一并吃掉，主干变成 `registry-entryschema`，
# 与不带版本号的 `registry-entry.schema.json` 永不归组；`a_v2_final.py` 也会被吃成 `afinal`。
VERSION_STEM_MARKER = re.compile(
    rf"(?:[._-](?:{_OLD_WORDS}|{_AMBIG_WORDS})(?=[._-]|$))"
    r"|[._-](?:旧|副本)|-\s*(?:旧|副本)"
    r"|(?:[._-](?:19|20)\d{2}$)"
    r"|\(\d+\)$",
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
    """文件名是否含任何版本/旧版标记（弱信号，不作为决定性依据）。"""
    name = _parts(rel)[-1]
    if name.lower().endswith((".bak", ".old", ".new", ".copy", ".tmp")):
        return True
    stem = name.rsplit(".", 1)[0]  # 其他情况剥普通扩展名，避免 .py 挡住数字后缀
    return bool(OLD_NAME_MARKER.search(stem) or AMBIG_VERSION_MARKER.search(stem))


def has_old_marker(rel: str) -> bool:
    """文件名是否含**明确旧版**标记（old/backup/legacy/-旧/副本/(1) 等）。

    方向确定：这类命名声明自己是旧版，可作现行版选举的决定性证据。
    """
    name = _parts(rel)[-1]
    if name.lower().endswith((".bak", ".old")):
        return True
    stem = name.rsplit(".", 1)[0]
    return bool(OLD_NAME_MARKER.search(stem))


def is_test_file(rel: str) -> bool:
    """是否测试文件。测试与源文件是并存关系，不是版本关系，故永不参与版本归组。"""
    return bool(TEST_FILE_MARKER.search(rel.replace("\\", "/")))
