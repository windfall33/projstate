"""交接单：写在 AGENTS.md 里的跨窗口/跨 agent 状态块。

为什么是这个形态（依据见第四轮真实开发者压力测试报告）：

1. 第四轮实验里，唯一被验证有效的机制是"上一个窗口自己写的交接文档"——五轮、
   跨 Codex/Claude 交替，终局准确率不变、零漂移。
2. 载体必须是 agent 一定会读到的文件：AGENTS.md 会被 Codex / Claude Code
   自动注入上下文；而 .ai/ 需要额外一跳。6 个真实仓库里 5 个没有 AGENTS.md，
   于是工具等于没装。
3. 所以：正文写在 AGENTS.md 的块里，.ai/ 继续放机器产物。

工具在这里不做推断，只做两件机械的事：

- 维护块的存在（首次写入 / 迁移旧块），且不覆盖已有正文；
- 维护块尾的源码指纹戳，让 check 能回答一个安全的问题：
  "源码在交接单最后盖章之后，被改过吗？"
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from projstate.render import AGENTS_FILE

# 块标记：以 projstate:begin 开头即可（兼容历史版本的旧标记文案）。
# 解析用正则而不是精确字符串，这样升级不会把旧块当成"没有块"而插出第二个块。
BEGIN_RE = re.compile(r"<!--\s*projstate:begin[^>]*-->")
END_MARKER = "<!-- projstate:end -->"
BLOCK_RE = re.compile(
    r"<!--\s*projstate:begin[^>]*-->.*?<!--\s*projstate:end\s*-->", re.S
)
STAMP_RE = re.compile(
    r"<!--\s*projstate:stamp\s+src=([0-9a-fA-F]{4,})"
    r"(?:\s+body=([0-9a-fA-F]{4,}))?"
    r"(?:\s+head=([0-9a-fA-F]{7,40}|none))?"
    r"\s+at=([0-9T:\-+]{10,25})\s*-->"
)

BEGIN_MARKER = "<!-- projstate:begin 本块由人/agent 维护；会话结束前更新，标记行勿改 -->"

TEMPLATE_BODY = """## 交接单（换窗口 / 换 agent 先读这里）

> 改代码前先读本块。**结束本次会话前**：先跑 `projstate brief draft`
> （它会把"你改了哪些文件、定义了什么"机械写进下面并盖章），然后你只需要补两行
> ——"为什么这么改"和"踩了什么坑"。不更新的话 `brief check` 会报"交接单已过期"。

- 当前任务：
- 下一步：
- 关键文件：
- 已定决策（写出依据，别只写结论）：
- 坑 / 注意：
"""


def fingerprint_files(files: list[Path]) -> str:
    """源码指纹：内容哈希，与 mtime 无关。只覆盖被扫描的文件集合。"""
    h = hashlib.sha256()
    for f in sorted(files):
        try:
            h.update(f.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:12]


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def render_block(body: str, src: str, at: str, head: str | None = None) -> str:
    """块 = 标记头 + 正文 + 指纹戳 + 标记尾。

    戳里记 `head=<commit>`：草稿靠它精确回答"自上次盖章起改了什么"。
    只用时间戳做不到——git 的 `--since` 是分钟/秒粒度，会把同一分钟的基线提交也算进来。
    """
    body = body.strip("\n")
    bh = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    stamp = f"src={src} body={bh}"
    if head:
        stamp += f" head={head}"
    stamp += f" at={at}"
    return (
        f"{BEGIN_MARKER}\n"
        f"{body}\n\n"
        f"<!-- projstate:stamp {stamp} -->\n"
        f"{END_MARKER}\n"
    )


def default_block(src: str, at: str | None = None, head: str | None = None) -> str:
    return render_block(TEMPLATE_BODY, src, at or now_stamp(), head)


def read_block(text: str) -> str | None:
    m = BLOCK_RE.search(text)
    return m.group(0) if m else None


class Stamp(NamedTuple):
    """戳：src=盖章时的源码指纹；body=盖章时交接单正文的指纹；at=时间。

    body 用来区分两种"源码变了"：正文没动 = 忘了更新交接单；正文动过 = 更新过了，
    只是没跑 `brief stamp`（这种情况不能判过期，否则逼着 agent 用 --no-verify 绕过）。
    历史版本的戳没有 body 字段（None），按老规则处理：源码变了就算过期。
    """

    src: str
    body: str | None
    head: str | None
    at: str


def read_stamp(text: str) -> Stamp | None:
    blk = read_block(text)
    if not blk:
        return None
    m = STAMP_RE.search(blk)
    return Stamp(m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


def body_of(block: str) -> str:
    """块里由人/agent 维护的那部分（去掉标记行与戳）。"""
    b = BEGIN_RE.sub("", block, count=1).replace(END_MARKER, "")
    return STAMP_RE.sub("", b).strip()


def body_of_text(text: str) -> str:
    blk = read_block(text)
    return body_of(blk) if blk else ""


def upsert_block(text: str, block: str) -> str:
    """有块就整体替换，没有就追加。"""
    if BLOCK_RE.search(text):
        return BLOCK_RE.sub(lambda _: block.strip(), text, count=1)
    if not text.strip():
        return block.strip() + "\n"
    return text.rstrip() + "\n\n" + block.strip() + "\n"


def set_stamp(text: str, src: str, at: str | None = None, head: str | None = None) -> str:
    """只改指纹戳，正文一字不动。块不存在时插入默认空块。"""
    at = at or now_stamp()
    blk = read_block(text)
    if not blk:
        return upsert_block(text, default_block(src, at, head))
    body = body_of(blk)
    bh = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    fields = f"src={src} body={bh}" + (f" head={head}" if head else "") + f" at={at}"
    new_line = f"<!-- projstate:stamp {fields} -->"
    if STAMP_RE.search(blk):
        new_blk = STAMP_RE.sub(new_line, blk, count=1)
    else:
        # 历史块没有戳：补一个在 END 之前
        new_blk = blk.replace(END_MARKER, f"{new_line}\n{END_MARKER}", 1)
    return text.replace(blk, new_blk, 1)


def is_stale(text: str, current_src: str) -> bool:
    """过期 = 源码变了，**且**交接单正文自上次盖章后一个字没动。

    正文动过（人/agent 更新了交接单）就不算过期——工具不判内容对错，只判"有没有人管过"。
    没有块/没有戳不算过期（交给"未接入"分支）。
    """
    st = read_stamp(text)
    if not st:
        return False
    if st.src == current_src:
        return False
    if st.body is None:
        return True  # 历史戳没有正文指纹，按老规则
    cur = hashlib.sha256(body_of_text(text).encode("utf-8")).hexdigest()[:12]
    return cur == st.body


def read_agents(root: Path) -> str:
    p = root / AGENTS_FILE
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def write_agents(root: Path, text: str) -> None:
    (root / AGENTS_FILE).write_text(text, encoding="utf-8")


KEY_FILES_HEAD = "- 关键文件："
DECISIONS_HEAD = "- 已定决策（写出依据，别只写结论）："
PITFALLS_HEAD = "- 坑 / 注意："
DECISION_HINT = "（待补：为什么这么改？放弃了什么备选？）"
PITFALL_HINT = "（待补：环境 / 依赖 / 第一次踩的坑）"


def _is_empty(line: str, head: str) -> bool:
    return line.strip() == head or line.strip() == head.rstrip("：" ) + "："


def fill_draft(body: str, file_lines: list[str]) -> str:
    """把草稿行填进"关键文件"，给空着的"已定决策/坑"放待补占位。

    只填空位与追加：人/agent 已经写过的行一字不动。
    """
    out: list[str] = []
    filled_key = False
    for ln in body.splitlines():
        out.append(ln)
        stripped = ln.strip()
        if not filled_key and stripped.startswith("- 关键文件"):
            filled_key = True
            for cand in file_lines:
                rel = cand.split("`")[1] if "`" in cand else cand
                if rel in body:          # 已经写过这个文件，不重复
                    continue
                out.append(cand)
        elif _is_empty(ln, DECISIONS_HEAD):
            out.append(f"  - {DECISION_HINT}")
        elif _is_empty(ln, PITFALLS_HEAD):
            out.append(f"  - {PITFALL_HINT}")
    if not filled_key and file_lines:
        out.append("")
        out.append(KEY_FILES_HEAD)
        out.extend(file_lines)
    return "\n".join(out)
