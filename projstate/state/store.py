"""状态层：人/agent 手写，跨窗口共享。唯一允许手改的文件。"""

from __future__ import annotations

import datetime
from pathlib import Path

STATE_FILE = ".ai/STATE.md"


def template() -> str:
    """新仓库的 STATE.md 模板。"""
    return (
        "# 当前任务：\n"
        "## 已完成\n"
        "- [ ] \n"
        "## 进行中\n"
        "- [ ] \n"
        "## 下一步\n"
        "- \n"
        "## 决策记录\n"
        "- \n"
        "## 卡点\n"
        "- 无\n"
    )


def _path(root: Path) -> Path:
    return root / STATE_FILE


def append_state(root: Path, text: str) -> str:
    """追加一行 `- <日期> <text>` 到 STATE.md，返回该行。"""
    now = datetime.date.today().isoformat()
    line = f"- {now} {text.strip()}"
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.is_file():
        p.write_text(template() + "\n", encoding="utf-8")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line


def read_state(root: Path) -> str:
    """读取 STATE.md；不存在则返回模板（首次暴露模板让用户填）。"""
    p = _path(root)
    if not p.is_file():
        return template()
    return p.read_text(encoding="utf-8", errors="replace")
