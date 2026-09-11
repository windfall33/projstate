"""事实层：入口识别 + 文件级粗略被引计数。刻意不做符号/依赖图。"""

from __future__ import annotations

import json
from pathlib import Path

from projstate.languages import detect_language
from projstate.scan import rel_posix


def _normalize(rel: str) -> str:
    return rel.replace("\\", "/")


def _extract_js_imports(text: str) -> tuple[str, ...]:
    """JS/TS 导出：从 import/require 语句抽模块说明符。

    用折中方案：只处理结构简单的 import/require，避免依赖 tree-sitter。
    用逐字符扫描（而非一次性正则），容忍换行与引号差异。
    """
    imports: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        # from "./x" 或 import "./x" 或 require("./x")
        if text.startswith("from ", i) or text.startswith("import ", i) or text.startswith("require(", i):
            j = text.find('"', i)
            k = text.find("'", i)
            j = j if j != -1 else n
            k = k if k != -1 else n
            start = min(j, k)
            if start < n:
                quote = text[start]
                end = text.find(quote, start + 1)
                if end != -1:
                    imports.append(text[start + 1 : end])
                    i = end + 1
                    continue
            # 没找到引号，跳过这个词继续
            i += 1
        else:
            i += 1
    result: list[str] = []
    for imp in imports:
        if imp.startswith(".") or "/" in imp or imp.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
            result.append(imp)
    return tuple(dict.fromkeys(result))


def _py_specs(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Python 导入 → (基础说明符, 子模块候选说明符)。

    子模块候选专供 ref_count。`from pkg import submodule` 中被导入的名字可能是
    **子模块**，只记 node.module 会整个漏掉它：实证 hermes-agent 的
    `from agent import curator_backup` 因此让 curator_backup.py 的 ref_count=0，
    711 行、被 6 个文件提及的活代码被判「已作废，建议归档」。

    刻意不并入 extract_imports：被导入的名字也可能只是函数/常量，混进去会让
    find_unresolved 把不存在的子模块名报成「未解析导入」制造噪声
    （实测：`from .missing import x` 会多出一条 ".missing.x"）。
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return (), ()
    base: list[str] = []
    sub: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            level_prefix = "." * node.level
            base.append(level_prefix + (node.module or ""))
            head = level_prefix + (node.module + "." if node.module else "")
            for alias in node.names:
                if alias.name != "*":
                    sub.append(f"{head}{alias.name}")
    return tuple(dict.fromkeys(base)), tuple(dict.fromkeys(sub))


def _extract_py_imports(text: str) -> tuple[str, ...]:
    """Python 导入：用 ast 解析，能同时识别绝对导入与相对导入。"""
    return _py_specs(text)[0]


def _entry_basis(root: Path, rel: str, language: str) -> str | None:
    """入口判定**依据**：'manifest'（配置声明，确证）/ 'name'（命名推断）/ None。

    必须区分这两者：把命名推断标成「确证」，正是本项目要防的事。
    monorepo 里每个 barrel 文件都叫 index，按命名判会把十几个 ref_count=0 的
    文件列成「必看入口」——与 ProjectBrain 的 F3（critical = 字母序前 12 个
    配置文件）是同一个失败模式。
    """
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
                return "manifest"
    name = rel.replace("\\", "/").split("/")[-1]
    stem = name.split(".")[0]
    if stem in {"index", "main"} or (stem == "app" and language == "js"):
        return "name"
    return None


def entry_points(root: Path, rel: str, language: str) -> bool:
    """是否入口（任依据命中皆算）。依据本身见 _entry_basis。"""
    return _entry_basis(root, rel, language) is not None


def extract_imports(source: bytes, language: str) -> tuple[str, ...]:
    """抽 import 字符串。Python 用 ast，JS 用折中扫描。承认可能漏，但不做"完备"承诺。"""
    text = source.decode("utf-8", "replace")
    if language in ("js", "ts"):
        return _extract_js_imports(text)
    if language == "python":
        return _extract_py_imports(text)
    return ()


def build_map(root: Path, files: list[Path]) -> dict:
    """构建 `{rel: {language, imports, ref_count, reachable}}`。"""
    result: dict[str, dict] = {}
    module_index: dict[str, list[str]] = {}

    # 子模块候选（仅用于计数，不进 map.json——见 _py_specs）
    sub_by_rel: dict[str, tuple[str, ...]] = {}

    for path in files:
        rel = rel_posix(root, path)
        try:
            source = path.read_bytes()
        except OSError:
            source = b""
        lang = detect_language(rel)
        if lang == "python":
            imports, sub_specs = _py_specs(source.decode("utf-8", "replace"))
        else:
            imports, sub_specs = extract_imports(source, lang), ()
        sub_by_rel[rel] = sub_specs
        # 建立模块路径索引（去扩展名，POSIX）
        module_index.setdefault(_module_stem(rel), []).append(rel)
        basis = _entry_basis(root, rel, lang)
        result[rel] = {
            "language": lang,
            "imports": list(imports),
            "ref_count": 0,
            "reachable": basis is not None,
            "entry_basis": basis,
            "mtime": int(path.stat().st_mtime),
        }

    resolver = _build_resolver(root, module_index)
    for rel, entry in result.items():
        for imp in (*entry["imports"], *sub_by_rel.get(rel, ())):
            target = resolver.resolve(imp, rel)
            if target and target in result:
                result[target]["ref_count"] += 1

    return result


def find_unresolved(root: Path, files: list[Path]) -> dict:
    """收集"项目内引用但解析不到目标"的清单。

    - 相对导入（以 `.` 开头）resolve 不到 = 异常信号，一律记录（见 spec 9 节）。
    - 绝对模块路径（如 `core.config`）若其第一段与项目内某顶层目录/模块前缀匹配
      （说明它想引用项目内），而 resolve 不到 = 异常，记录；否则视为第三方依赖，跳过。
    - 裸名（`os` / `requests`）视为外部依赖，不记。
    """
    module_index: dict[str, list[str]] = {}
    for path in files:
        rel = rel_posix(root, path)
        module_index.setdefault(_module_stem(rel), []).append(rel)

    resolver = _build_resolver(root, module_index)
    # 项目内顶层"命名空间"：目录名 + 文件名主干，用于判断绝对导入是否指向项目内
    top_level = set()
    for rel in module_index:
        head = rel.split("/")[0]
        top_level.add(head)

    unresolved: dict[str, list[str]] = {}
    for path in files:
        rel = rel_posix(root, path)
        try:
            source = path.read_bytes()
        except OSError:
            continue
        lang = detect_language(rel)
        for imp in extract_imports(source, lang):
            if imp.startswith("."):
                target = resolver.resolve(imp, rel)
                if target is None:
                    unresolved.setdefault(rel, []).append(imp)
                continue
            # 绝对模块路径：第一段是项目内的目录/模块名，才视为"想引用项目内"
            head = imp.replace(".", "/").split("/")[0]
            if head not in top_level:
                continue  # 第三方 / 裸名
            target = resolver.resolve(imp, rel)
            if target is None:
                unresolved.setdefault(rel, []).append(imp)
    return unresolved


def _module_stem(rel: str) -> str:
    """去掉扩展名后的模块路径（a.js -> a；pkg/index.js -> pkg/index）。"""
    for ext in (".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".py", ".pyi"):
        if rel.endswith(ext):
            return rel[: -len(ext)]
    return rel


def _build_resolver(root: Path, module_index: dict[str, list[str]]) -> object:
    """构造解析器：把 import 说明符解析到候选文件的模块路径。"""
    class _Resolver:
        def __init__(self, module_index: dict[str, list[str]]):
            self.module_index = module_index

        def resolve(self, spec: str, importer: str) -> str | None:
            """把 import 说明符解析为候选文件 rel。绝对模块路径从项目根解析；相对路径基于 importer 目录。"""
            spec = spec.strip()
            if not spec:
                return None
            if spec.startswith("."):  # 相对导入
                base_dir = _module_stem(importer).rsplit("/", 1)[0] if "/" in _module_stem(importer) else ""
                dots = len(spec) - len(spec.lstrip("."))
                rel_part = spec[dots:]
                # 一级级向上
                parts = [p for p in base_dir.split("/") if p] if base_dir else []
                for _ in range(dots - 1):
                    if parts:
                        parts.pop()
                candidate_path = "/".join([*parts, rel_part] if parts else [rel_part])
                candidate = _module_stem_normalized(candidate_path)
            else:  # 绝对模块路径
                candidate = _module_stem_normalized(spec)
            # 直接命中
            if candidate in self.module_index:
                return self.module_index[candidate][0]
            # 以后缀命中（如 'app.payment_v2' 变成 'app/payment_v2'）
            dotted = candidate.replace(".", "/")
            if dotted in self.module_index:
                return self.module_index[dotted][0]
            # 兜底：尾段精确匹配
            tail = candidate.rsplit("/", 1)[-1]
            for mod in self.module_index:
                if mod == tail or mod.rsplit("/", 1)[-1] == tail:
                    return self.module_index[mod][0]
            return None

    return _Resolver(module_index)


def _module_stem_normalized(rel: str) -> str:
    """规范化模块路径：去掉扩展名、'.' / '..'，转 POSIX。"""
    parts: list[str] = []
    for seg in _module_stem(rel).replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)
