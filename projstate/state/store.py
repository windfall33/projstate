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
    """把一行 `- <日期> <text>` 写入 STATE.md 的"下一步"分组，返回该行。

    写入"下一步"而非文件末尾：log 的含义是"有了新进展/下一步计划"，
    这样换窗口 `status` 时新的进展直接浮现在顶部。
    """
    now = datetime.date.today().isoformat()
    line = f"- {now} {text.strip()}"
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.is_file():
        p.write_text(template() + "\n", encoding="utf-8")
    content = p.read_text(encoding="utf-8")
    marker = "## 下一步"
    if marker in content:
        lines = content.splitlines(keepends=True)
        # 找 marker 行索引
        marker_idx = next(i for i, ln in enumerate(lines) if ln.strip() == marker)
        # 标题后的首行若非空且有实际内容则直接插入在其前；
        # 若为占位符（纯 - / - [ ]）则替换掉它。
        tail = lines[marker_idx + 1:] if marker_idx + 1 < len(lines) else []
        placeholder = bool(tail and tail[0].strip() in ("-", "- ", "", "- [ ] ", "- [ ]", "-  "))
        if placeholder:
            lines[marker_idx + 1] = line + "\n"
        else:
            lines.insert(marker_idx + 1, line + "\n")
        content = "".join(lines)
    else:
        content = content.rstrip() + "\n\n" + line + "\n"
    p.write_text(content, encoding="utf-8")
    return line


def read_state(root: Path) -> str:
    """读取 STATE.md；不存在则返回模板（首次暴露模板让用户填）。"""
    p = _path(root)
    if not p.is_file():
        return template()
    return p.read_text(encoding="utf-8", errors="replace")
