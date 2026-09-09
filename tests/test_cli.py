from pathlib import Path
import json

from projstate.cli import main


def test_status_prints_template(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0


def test_log_appends_and_status_shows(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["log", "完成导出重构"]) == 0
    assert "完成导出重构" in (tmp_path / ".ai" / "STATE.md").read_text(encoding="utf-8")


def test_refresh_creates_map_and_agents_block(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("export const a = 1", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("", encoding="utf-8")
    assert main(["refresh"]) == 0
    assert (tmp_path / ".ai" / "MAP.md").is_file()
    block = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "projstate:begin" in block


def test_resolve_persists_and_apply_overrides(tmp_path: Path, monkeypatch):
    """方向3：resolve 一次确认 → canonical_overrides.json → 再 refresh 变确定判定。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "feature.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app" / "feature_v2.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("", encoding="utf-8")

    # 首次 refresh：两文件都无引用、无明确旧版标记 → ambiguous
    assert main(["refresh"]) == 0
    sf = json.loads((tmp_path / ".ai" / "stale.json").read_text(encoding="utf-8"))
    assert any(e.get("ambiguous") for e in sf)

    # resolve 确认 feature_v2 为现行版
    assert main(["resolve", "app/feature_v2.py"]) == 0
    assert (tmp_path / ".ai" / "canonical_overrides.json").is_file()

    # 再 refresh：应合并 override，判定确定
    assert main(["refresh"]) == 0
    sf2 = json.loads((tmp_path / ".ai" / "stale.json").read_text(encoding="utf-8"))
    decided = [e for e in sf2 if e["canonical"] and "feature_v2" in e["canonical"]]
    assert decided, "resolve 后应给出确定的现行版判定"
    assert decided[0]["superseded"] == ["app/feature.py"]
