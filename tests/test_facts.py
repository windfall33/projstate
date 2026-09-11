from pathlib import Path

from projstate.facts import build_map, entry_points, extract_imports, find_unresolved


def test_entry_points_detects_index_and_package_main(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"main":"src/main/index.js"}', encoding="utf-8")
    assert entry_points(tmp_path, "src/main/index.js", "js") is True
    assert entry_points(tmp_path, "src/shared/config.js", "js") is False


def test_entry_points_detects_main_app_files():
    assert entry_points(Path("."), "main.js", "js") is True
    assert entry_points(Path("."), "app.py", "python") is False  # app.py 经包名判断；此处无包名


def test_entry_basis_distinguishes_manifest_from_name(tmp_path: Path):
    """入口依据必须可区分：manifest 声明是确证，命名推断不是。

    混为一谈会把 monorepo 里十几个叫 index 的 barrel 文件标成「确证入口」，
    与 ProjectBrain 的 F3（critical = 字母序前 12 个配置文件）是同一失败模式。
    """
    from projstate.facts import _entry_basis

    (tmp_path / "package.json").write_text('{"main":"src/main/index.js"}', encoding="utf-8")
    assert _entry_basis(tmp_path, "src/main/index.js", "js") == "manifest"
    assert _entry_basis(tmp_path, "src/shared/index.ts", "ts") == "name"
    assert _entry_basis(tmp_path, "src/util.py", "python") is None


def test_build_map_records_entry_basis(tmp_path: Path):
    (tmp_path / "index.js").write_text("export {};\n", encoding="utf-8")
    m = build_map(tmp_path, [tmp_path / "index.js"])
    assert m["index.js"]["entry_basis"] == "name"
    assert m["index.js"]["reachable"] is True


def test_extract_imports_js_listed():
    src = b'import a from "./a";\nconst b = require("./b");\n'
    assert set(extract_imports(src, "js")) == {"./a", "./b"}


def test_extract_imports_python():
    src = b"import os\nfrom . import sib\nfrom .pkg import thing\n"
    assert set(extract_imports(src, "python")) == {".", ".pkg", "os"}


def test_extract_imports_python_absolute():
    """Python 绝对导入（主流写法）必须被解析到，否则 ref_count 恒 0。"""
    src = b"from app.payment_v2 import charge\nimport app.config\n"
    assert set(extract_imports(src, "python")) == {"app.payment_v2", "app.config"}


def test_build_map_counts_refs(tmp_path: Path):
    (tmp_path / "a.js").write_text('import a from "./a";', encoding="utf-8")
    (tmp_path / "b.js").write_text('import b from "./a";', encoding="utf-8")
    m = build_map(tmp_path, [tmp_path / "a.js", tmp_path / "b.js"])
    assert m["a.js"]["ref_count"] == 2


def test_build_map_resolves_python_absolute_import(tmp_path: Path):
    """绝对导入必须计入被引数——这是痛点三不反向误判的前提。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "payment.py").write_text("def charge(a): return a", encoding="utf-8")
    (tmp_path / "app" / "payment_v2.py").write_text("def charge(a): return a", encoding="utf-8")
    (tmp_path / "app" / "orders.py").write_text(
        "from app.payment_v2 import charge\n", encoding="utf-8"
    )
    m = build_map(tmp_path, [
        tmp_path / "app" / "payment.py",
        tmp_path / "app" / "payment_v2.py",
        tmp_path / "app" / "orders.py",
    ])
    assert m["app/payment_v2.py"]["ref_count"] == 1
    assert m["app/payment.py"]["ref_count"] == 0


def test_build_map_counts_submodule_import(tmp_path: Path):
    """`from agent import curator_backup` 这类**子模块导入**必须计入被引数。

    实证：只记 node.module 时该导入退化成 "agent"，curator_backup.py 的
    ref_count=0，于是 711 行、被 6 个文件提及的活代码被旧版规则判
    「已作废，建议归档」。子模块候选是专为计数开的通道。
    """
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "agent" / "curator.py").write_text(
        "from agent import curator_backup\n", encoding="utf-8"
    )
    (tmp_path / "agent" / "curator_backup.py").write_text(
        "def snapshot():\n    return 1\n", encoding="utf-8"
    )
    m = build_map(tmp_path, [
        tmp_path / "agent" / "__init__.py",
        tmp_path / "agent" / "curator.py",
        tmp_path / "agent" / "curator_backup.py",
    ])
    assert m["agent/curator_backup.py"]["ref_count"] == 1


def test_submodule_specs_do_not_leak_into_unresolved(tmp_path: Path):
    """子模块候选不得泄漏进 unresolved。

    否则每条 `from .x import y` 都会多报一条 ".x.y"，把 unresolved.json
    变成噪声清单——这正是它没被并入 extract_imports 的原因。
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("from .missing import x\n", encoding="utf-8")
    unresolved = find_unresolved(tmp_path, [tmp_path / "src" / "a.py"])
    assert unresolved["src/a.py"] == [".missing"]


def test_find_unresolved_records_broken_relative_import(tmp_path: Path):
    """相对导入指向不存在的文件，须记入 unresolved 而非静默丢弃。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("from .missing import x\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("from .a import y\n", encoding="utf-8")
    unresolved = find_unresolved(tmp_path, [tmp_path / "src" / "a.py", tmp_path / "src" / "b.py"])
    assert "src/a.py" in unresolved
    assert unresolved["src/a.py"] == [".missing"]


def test_find_unresolved_records_absolute_project_import_missing(tmp_path: Path):
    """缺陷5：`from core.config import CFG` 而 core/config.py 不存在时，应记入 unresolved。"""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "mod.py").write_text("from core.config import CFG\n", encoding="utf-8")
    unresolved = find_unresolved(tmp_path, [tmp_path / "core" / "mod.py"])
    assert "core/mod.py" in unresolved
    assert "core.config" in unresolved["core/mod.py"]


def test_find_unresolved_ignores_third_party_absolute(tmp_path: Path):
    """裸包名 / 第三方（如 requests / os）不记为未解析。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "mod.py").write_text("import os\nimport requests\n", encoding="utf-8")
    unresolved = find_unresolved(tmp_path, [tmp_path / "app" / "mod.py"])
    assert unresolved == {}
