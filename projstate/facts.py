"""事实层：入口识别 + 文件级粗略被引计数。刻意不做符号/依赖图。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from projstate.languages import detect_language
from projstate.scan import rel_posix

_IMPORT_JS = re.compile(r'(?:from\s+|require\()["\']([^"\']+)["\']', re.MULTILINE)
_IMPORT_PY = re.compile(
    r'^\s*(?:from\s+(\.{1,2}[^\s]*)|import\s+(\.{1,2}[^\s]*))',
    re.MULTILINE,
)


def _normalize(rel: str) -> str:
    return rel.replace("\\", "/").lstrip("./")


def entry_points(root: Path, rel: str, language: str) -> bool:
    """是否入口：package.json main/bin 指向，或 index/main/app 命名（app 仅 js 弱判定）。"""
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        targets: list[str] = []
        for key in ("main", "module", "bin", "exports"):
            val = data.get(key)
            if isinstance(val, str):
                targets.append(val)
            elif isinstance(val, dict):
                targets.extend(v for v in val.values() if isinstance(v, str))
        for t in targets:
            if _normalize(t) == _normalize(rel):
                return True
    name = rel.replace("\\", "/").split("/")[-1]
    stem = name.split(".")[0]
    return stem in {"index", "main"} or (stem == "app" and language == "js")


def extract_imports(source: bytes, language: str) -> tuple[str, ...]:
    """粗略抽 import 字符串。正则而非 AST——承认可能漏，但确定性、零误报为"完备"承诺。"""
    text = source.decode("utf-8", "replace")
    if language in ("js", "ts"):
        return tuple(dict.fromkeys(_IMPORT_JS.findall(text)))
    if language == "python":
        # findall 返回 (group1, group2) 元组；只取非空组。
        raw = _IMPORT_PY.findall(text)
        return tuple(dict.fromkeys(g for groups in raw for g in groups if g))
    return ()


def build_map(root: Path, files: list[Path]) -> dict:
    """构建 `{rel: {language, imports, ref_count, reachable}}`。"""
    result: dict[str, dict] = {}
    resolved_to_target: dict[str, list[str]] = {}

    for path in files:
        rel = rel_posix(root, path)
        try:
            source = path.read_bytes()
        except OSError:
            source = b""
        lang = detect_language(rel)
        imports = extract_imports(source, lang)
        result[rel] = {
            "language": lang,
            "imports": list(imports),
            "ref_count": 0,
            "reachable": entry_points(root, rel, lang),
        }
        for imp in imports:
            if imp.startswith("."):  # 项目内相对引用
                resolved_to_target.setdefault(_normalize(imp), []).append(rel)

    for target, importers in resolved_to_target.items():
        t_stem = _module_stem(target)
        for candidate in result:
            c_stem = _module_stem(_normalize(candidate))
            if c_stem == t_stem or c_stem.endswith("/" + t_stem):
                result[candidate]["ref_count"] += len(importers)

    return result


def _module_stem(rel: str) -> str:
    """去掉扩展名后的模块路径（a.js -> a；pkg/index.js -> pkg/index）。"""
    for ext in (".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".py", ".pyi"):
        if rel.endswith(ext):
            return rel[: -len(ext)]
    return rel
