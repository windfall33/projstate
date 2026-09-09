from pathlib import Path

from projstate.facts import build_map, entry_points, extract_imports


def test_entry_points_detects_index_and_package_main(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"main":"src/main/index.js"}', encoding="utf-8")
    assert entry_points(tmp_path, "src/main/index.js", "js") is True
    assert entry_points(tmp_path, "src/shared/config.js", "js") is False


def test_entry_points_detects_main_app_files():
    assert entry_points(Path("."), "main.js", "js") is True
    assert entry_points(Path("."), "app.py", "python") is False  # app.py 经包名判断；此处无包名


def test_extract_imports_js_listed():
    src = b'import a from "./a";\nconst b = require("./b");\n'
    assert set(extract_imports(src, "js")) == {"./a", "./b"}


def test_extract_imports_python():
    src = b"import os\nfrom . import sib\nfrom .pkg import thing\n"
    assert set(extract_imports(src, "python")) == {".", ".pkg"}


def test_build_map_counts_refs(tmp_path: Path):
    (tmp_path / "a.js").write_text('import a from "./a";', encoding="utf-8")
    (tmp_path / "b.js").write_text('import b from "./a";', encoding="utf-8")
    m = build_map(tmp_path, [tmp_path / "a.js", tmp_path / "b.js"])
    assert m["a.js"]["ref_count"] == 2
