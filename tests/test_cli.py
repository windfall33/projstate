from pathlib import Path

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
