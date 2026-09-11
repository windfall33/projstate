"""`brief draft`：机械草稿的行为契约。

验收实验的结论要求它满足三件事：
1. 把"改了什么"免费写出来（文件 + 顶层符号 + map 口径的引用数）；
2. 只填空位、只追加，人写过的行一字不动；
3. 没改动就别动交接单。
"""

import os
import subprocess
from pathlib import Path

from projstate import brief
from projstate.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@e.st")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    assert main(["init"]) == 0
    return tmp_path


def test_draft_lists_changed_file_with_symbol(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    (repo / "src" / "alpha.py").write_text(
        "def alpha():\n    return 2\n\n\ndef beta():\n    return 3\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["brief", "draft"]) == 0
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "src/alpha.py" in text
    assert "定义 alpha、beta" in text
    assert "为什么这么改" in text, "空着的决策位要放待补提示"
    assert main(["brief", "check"]) == 0, "草稿写完就该是'最新'状态"


def test_draft_never_touches_handwritten_lines(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    agents = repo / "AGENTS.md"
    hand = "- 已定决策：截断放在 strip 之后（依据：不想把长度上限变成按分隔符对齐）"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace("- 已定决策（写出依据，别只写结论）：",
                                                   hand, 1),
        encoding="utf-8")
    (repo / "src" / "alpha.py").write_text("def alpha():\n    return 9\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["brief", "draft"]) == 0
    after = agents.read_text(encoding="utf-8")
    assert hand in after, "人写的决策行不得被覆盖"
    assert after.count("src/alpha.py") == 1, "同一个文件不重复列"


def test_draft_is_noop_without_changes(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    before = (repo / "AGENTS.md").read_bytes()
    capsys.readouterr()
    assert main(["brief", "draft"]) == 0
    assert (repo / "AGENTS.md").read_bytes() == before, "没改动就不该动交接单"
    assert "没检测到" in capsys.readouterr().out


def test_draft_falls_back_to_mtime_without_git(tmp_path, monkeypatch, capsys):
    """没有 git 的仓库（真实存在）也得能用：按"上次盖章之后被改过"列文件。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("X = 1\n", encoding="utf-8")
    assert main(["init"]) == 0
    capsys.readouterr()

    future = os.path.getmtime(tmp_path / "app" / "x.py") + 300
    os.utime(tmp_path / "app" / "x.py", (future, future))
    assert main(["brief", "draft"]) == 0
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "app/x.py" in text
    assert "mtime" in capsys.readouterr().out


def test_fill_draft_appends_section_when_missing():
    body = "## 交接单\n\n- 当前任务：x\n"
    out = brief.fill_draft(body, ["  - `a/b.py` — 定义 f"])
    assert "- 关键文件：" in out and "a/b.py" in out
