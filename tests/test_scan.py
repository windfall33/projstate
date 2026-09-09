from pathlib import Path

from projstate.languages import detect_language, is_archive, is_generated
from projstate.languages import has_name_version_marker
from projstate.scan import iter_files, rel_posix


def test_detect_language_maps_known_extensions():
    assert detect_language("src/a.js") == "js"
    assert detect_language("src/a.tsx") == "ts"
    assert detect_language("src/a.py") == "python"
    assert detect_language("README.md") == "other"


def test_is_generated_flags_build_output_and_dep_dirs():
    assert is_generated("dist/bundle.js") is True
    assert is_generated("dist2/bundle.js") is True
    assert is_generated("resources/x.asar") is True
    assert is_generated("node_modules/x/index.js") is True
    assert is_generated("src/export.js") is False


def test_is_archive_flags_archive_dirs():
    assert is_archive("_archive/x/export.js") is True
    assert is_archive("deprecated/old.py") is True
    assert is_archive("src/export.js") is False


def test_iter_files_skips_generated_and_ignore(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("export const a = 1", encoding="utf-8")
    (tmp_path / "src" / "skip.js").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.js").write_text("x", encoding="utf-8")

    got = sorted(rel_posix(tmp_path, p) for p in iter_files(tmp_path, ignore=("src/skip.js",)))
    assert got == ["src/a.js"]


def test_rel_posix_uses_forward_slashes(tmp_path: Path):
    p = tmp_path / "a" / "b.js"
    assert rel_posix(tmp_path, p) == "a/b.js"


def test_name_version_marker_covers_common_patterns():
    assert has_name_version_marker("src/a_v2.js") is True
    assert has_name_version_marker("src/a_new.py") is True
    assert has_name_version_marker("src/a_final.js") is True
    assert has_name_version_marker("src/a_latest.js") is True
    assert has_name_version_marker("src/a.bak") is True
    # 词边界：不应误命中
    assert has_name_version_marker("src/finalize.js") is False
    assert has_name_version_marker("src/newly.js") is False
    assert has_name_version_marker("src/a.py") is False
    # 裸数字编号（feature_0 / a_2）不是版本标记——命名编号误报会污染判定
    assert has_name_version_marker("src/feature_0.py") is False
    assert has_name_version_marker("src/a_2.py") is False
