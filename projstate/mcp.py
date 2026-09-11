"""MCP 服务：把 projstate 的**只读**能力暴露给 agent。

设计铁律：**只报告，永不筛选。**

ProjectBrain 的实测教训（见其真实压力实验）：一旦工具替 agent 决定"哪些文件不该看"，
它就会以权威身份误导——

  - `listRelevantFiles` 开头 `if status === "legacy" || "intermediate" continue`，
    一个错误分类把正确答案整个删出候选池（实证：改回 status 后查询从 0 条变 4 条）；
  - `load_context` 的 critical 清单 = 字母序前 12 个配置文件，源码模块一个都进不去；
  - 规则写"禁止把 intermediate 当作现状"，恰好指引 agent 忽略唯一能戳穿谎话的文件。

所以这里**不提供**检索/过滤、**不提供**重要性推断、**不提供**清理建议。
只回答两类问题：已确证的事实（引用计数、入口、人工登记），以及
"工具知道自己在什么时候已经错了"（依赖快照失效）。

零三方依赖：MCP 的 stdio 传输就是按行分隔的 JSON-RPC 2.0，这里手写实现，
不引入 mcp SDK——与本项目"零三方依赖、零模型"的设定一致。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

SERVER_NAME = "projstate"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

_NO_FILTER = "只报告已确证的事实，不做筛选、不做重要性推断；完整事实请用 grep。"

TOOLS: list[dict] = [
    {
        "name": "projstate_status",
        "description": (
            "换窗口/换 agent 开工第一件事：当前任务与进展，以及哪些结论的依赖已变更、"
            "已不可再当现状。" + _NO_FILTER
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "projstate_context",
        "description": (
            "一页就位：任务/下一步 + 入口与关键文件（按被引次数，确证）+ 现行版对照 + 陈旧告警。"
            "注意这是**阅读锚点，不是文件清单**，更不是依赖图。" + _NO_FILTER
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "projstate_explain",
        "description": (
            "改某个文件之前读它：被几个文件引用、引用了谁、是否入口。确证级事实，不含推断。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "项目根下的相对路径"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "projstate_canonical",
        "description": (
            "某个概念/模块的现行版是哪个。**无决定性证据时明确回答'不确定'并列出候选**，"
            "绝不给一个可能反向的确定答案。人工确认用 `projstate resolve <候选>`（一次写入永久生效）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "模块名或路径片段"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "projstate_check",
        "description": (
            "只读体检：地图是否已生成、多少旧版判定待处理、多少结论与文档的依赖已变更。"
            "返回文本里会写明体检结论，不要把它当作错误。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "projstate_why",
        "description": "某个文件为什么被判为陈旧，附触发它的信号。未被判定时如实说明。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "项目根下的相对路径"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """工具级错误：作为 isError 结果返回，不中断服务。"""


# ------------------------------------------------------------------ 数据组装


def _capture(fn, *args) -> str:
    """调用 CLI 实现并捕获其 print 输出。

    刻意复用 cli 的实现而不是另写一份：两份实现必然漂移，
    而"工具说的和事实不一样"正是本项目要防的事。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().strip()


def _json_file(root: Path, rel: str) -> object | None:
    p = root / rel
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _status(root: Path) -> str:
    from projstate import statedeps
    from projstate.cli import read_state

    parts = [read_state(root).rstrip()]

    lines = statedeps.load(root)["lines"]
    flagged: list[str] = []
    for key, deps in lines.items():
        bad = statedeps._changed(root, deps)
        if bad:
            flagged.append(f"  {key}  ← 依赖已变更：{', '.join(bad)}")
    docs = statedeps.pending_docs(root)

    if flagged or docs:
        parts.append("")
        parts.append("⚠️ 以下内容已不可再当现状（依赖已变更，须复核）：")
        parts.extend(flagged)
        for d in docs[:10]:
            parts.append(f"  {d['doc']}  ← 引用已失效：{'、'.join(d['deps'])}")
        if len(docs) > 10:
            parts.append(f"  …另有 {len(docs) - 10} 份文档，见 .ai/HIP.md")
        parts.append("  看过并接受：`projstate ack <文档>`")
    return "\n".join(parts)


def _context(root: Path) -> str:
    from projstate import statedeps
    from projstate.cli import _read_next, _read_task

    out = [
        f"当前任务：{_read_task(root)}",
        f"下一步：{_read_next(root)}",
        "",
    ]

    m = _json_file(root, ".ai/map.json")
    files = (m or {}).get("files", {}) if isinstance(m, dict) else {}
    if files:
        # 与 MAP.md 共用同一份实现：两份必然漂移，而"工具说的和事实不一样"正是要防的事
        from projstate.render import anchor_lines

        out.extend(anchor_lines(files, top_limit=12))

    stale = _json_file(root, ".ai/stale.json")
    if isinstance(stale, list) and stale:
        decided = [s for s in stale if s.get("canonical")]
        ambiguous = [s for s in stale if not s.get("canonical")]
        if decided:
            out.append("现行版对照（定论依据：明确旧版标记 / 人工登记）")
            for s in decided:
                sup = "，".join(s.get("superseded") or []) or "无"
                out.append(f"  {s['canonical']}  ← 当前    {sup}")
            out.append("")
        if ambiguous:
            out.append(f"需人工确认（工具无决定性证据，不猜）共 {len(ambiguous)} 组")
            for s in ambiguous[:10]:
                out.append(f"  候选：{', '.join(s.get('ambiguous') or [])}")
            out.append("  处理：`projstate resolve <候选>` 确认一次，永久记住")
            out.append("")

    docs = statedeps.pending_docs(root)
    if docs:
        out.append(f"⚠️ {len(docs)} 份文档引用的文件已变更，其结论须复核")
        for d in docs[:5]:
            out.append(f"  {d['doc']}  ← {'、'.join(d['deps'])}")
        out.append("")

    out.append("> 这是**阅读锚点**，不是依赖图，也不是文件清单。完整事实请用 grep。")
    out.append("> 追问：`projstate_explain <path>`（改它前先读）/ `projstate_canonical <名>` / `projstate_check`")
    return "\n".join(out)


def _explain(root: Path, path: str) -> str:
    from projstate.cli import cmd_explain

    return _capture(cmd_explain, root, path) or f"（{path} 不在事实层，可能被忽略或不存在）"


def _canonical(root: Path, name: str) -> str:
    from projstate.cli import cmd_canonical

    text = _capture(cmd_canonical, root, name)
    return text or f"未找到与 {name} 匹配的现行版判定"


def _check(root: Path) -> str:
    from projstate.cli import cmd_check

    text = _capture(cmd_check, root)
    body = text or "无待处理项。"
    return body


def _why(root: Path, path: str) -> str:
    from projstate.cli import cmd_why

    text = _capture(cmd_why, root, path)
    return text or f"{path} 无明显旧版判定"


_TOOL_IMPL = {
    "projstate_status": lambda root, a: _status(root),
    "projstate_context": lambda root, a: _context(root),
    "projstate_explain": lambda root, a: _explain(root, _req(a, "path")),
    "projstate_canonical": lambda root, a: _canonical(root, _req(a, "name")),
    "projstate_check": lambda root, a: _check(root),
    "projstate_why": lambda root, a: _why(root, _req(a, "path")),
}


def _req(args: dict, key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ToolError(f"缺少必填参数：{key}")
    return val


def call_tool(name: str, args: dict, root: Path) -> str:
    impl = _TOOL_IMPL.get(name)
    if impl is None:
        raise ToolError(f"未知工具：{name}（本服务刻意不提供检索/筛选类工具）")
    # CLI 以 cwd 定位项目根，保持一致
    cwd = os.getcwd()
    os.chdir(root)
    try:
        return impl(root, args)
    finally:
        os.chdir(cwd)


# ------------------------------------------------------------------ 协议


def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_message(msg: dict, root: Path) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args, root)
        except ToolError as e:
            return _ok(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
    if mid is None:
        return None  # 其他通知，忽略
    return _err(mid, -32601, f"Method not found: {method}")


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(root: Path | None = None) -> int:
    """stdio 主循环：按行读 JSON-RPC，按行回。EOF 即退出。"""
    root = (root or Path.cwd()).resolve()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        resp = handle_message(msg, root)
        if resp is not None:
            _send(resp)
    return 0
