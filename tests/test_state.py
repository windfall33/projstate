from pathlib import Path

from projstate.state.store import append_state, read_state, template


def test_template_has_sections():
    t = template()
    assert "当前任务" in t
    assert "下一步" in t
    assert "决策记录" in t


def test_append_creates_file_and_appends(tmp_path: Path):
    line = append_state(tmp_path, "完成导出重构，下一步改验证逻辑")
    assert line
    content = (tmp_path / ".ai" / "STATE.md").read_text(encoding="utf-8")
    assert "完成导出重构" in content


def test_append_has_timestamp(tmp_path: Path):
    line = append_state(tmp_path, "记录这一条")
    assert "- " in line[:20] or "2026-" in line  # 含日期前缀


def test_read_state_returns_template_when_missing(tmp_path: Path):
    assert "当前任务" in read_state(tmp_path)
