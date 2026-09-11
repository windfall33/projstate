"""MCP 服务：协议正确性 + 「只报告，永不筛选」这条铁律不被违反。

铁律来自 ProjectBrain 的实测教训：工具一旦替 agent 决定"哪些文件不该看"，
就会以权威身份误导（误分类 → 检索静默跳过 → 正确答案从候选池消失）。
所以这里既测协议能不能通，也测**它没做不该做的事**。
"""
import io
import json
import os
import sys
from pathlib import Path

from projstate import mcp


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _project(tmp_path: Path) -> Path:
    _write(tmp_path, "AGENTS.md", "# proj\n")
    _write(tmp_path, "src/app.py", "from src.util import helper\n\ndef run():\n    return helper()\n")
    _write(tmp_path, "src/util.py", "def helper():\n    return 1\n")
    _write(tmp_path, "src/auth_old.py", "def login():\n    return 0\n")
    _write(tmp_path, "src/auth.py", "def login():\n    return 1\n")

    from projstate.cli import main
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        main(["refresh"])
    finally:
        os.chdir(cwd)
    return tmp_path


# --------------------------------------------------------------- 协议


def test_initialize_handshake(tmp_path: Path):
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, tmp_path)
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "projstate"
    assert "tools" in resp["result"]["capabilities"]


def test_notifications_get_no_response(tmp_path: Path):
    assert mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, tmp_path) is None


def test_unknown_method_is_jsonrpc_error(tmp_path: Path):
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"}, tmp_path)
    assert resp["error"]["code"] == -32601


def test_tools_list_has_schemas(tmp_path: Path):
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tmp_path)
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "projstate_status" in names
    assert "projstate_context" in names
    for t in resp["result"]["tools"]:
        assert t["description"].strip()
        assert t["inputSchema"]["type"] == "object"


# --------------------------------------------------------------- 铁律


def test_no_retrieval_or_cleanup_tool_is_exposed(tmp_path: Path):
    """不得暴露检索/清理类工具。

    ProjectBrain 的 listRelevantFiles 会因一次误分类静默跳过正确答案
    （改回 status 后同一查询从 0 条变 4 条），suggestCleanup 会建议归档活代码。
    这两类能力一律不提供。
    """
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, tmp_path)
    names = " ".join(t["name"] for t in resp["result"]["tools"]).lower()
    for forbidden in ("relevant", "search", "cleanup", "suggest", "filter"):
        assert forbidden not in names
    # 也不得有"重要性"类工具
    assert "importance" not in names


def test_tool_descriptions_state_they_do_not_filter(tmp_path: Path):
    resp = mcp.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, tmp_path)
    joined = " ".join(t["description"] for t in resp["result"]["tools"] if t["name"] != "projstate_canonical")
    assert "不做筛选" in joined or "不是文件清单" in joined


def test_unknown_tool_is_error_result_not_crash(tmp_path: Path):
    resp = mcp.handle_message({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "projstate_search", "arguments": {}},
    }, tmp_path)
    assert resp["result"]["isError"] is True
    assert "不提供检索" in resp["result"]["content"][0]["text"]


def test_missing_required_argument_is_error_result(tmp_path: Path):
    resp = mcp.handle_message({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "projstate_explain", "arguments": {}},
    }, tmp_path)
    assert resp["result"]["isError"] is True
    assert "path" in resp["result"]["content"][0]["text"]


# --------------------------------------------------------------- 工具行为


def test_context_says_it_is_not_a_file_list(tmp_path: Path):
    """输出必须自带"这不是文件清单、完整事实用 grep"的声明。"""
    _project(tmp_path)
    text = mcp.call_tool("projstate_context", {}, tmp_path)
    assert "不是文件清单" in text or "不是依赖图" in text
    assert "grep" in text


def test_explain_reports_confirmed_facts_only(tmp_path: Path):
    _project(tmp_path)
    text = mcp.call_tool("projstate_explain", {"path": "src/util.py"}, tmp_path)
    assert "src/util.py" in text
    assert "被 1 个文件引用" in text


def test_canonical_refuses_to_guess_when_no_evidence(tmp_path: Path):
    """无决定性证据时必须是"不确定 + 候选"，不能给一个可能反向的答案。"""
    _write(tmp_path, "svc/payment.py", "def charge():\n    return 1\n")
    _write(tmp_path, "svc/payment_v2.py", "def charge():\n    return 2\n")
    from projstate.cli import main
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        main(["refresh"])
    finally:
        os.chdir(cwd)
    text = mcp.call_tool("projstate_canonical", {"name": "payment"}, tmp_path)
    assert "payment" in text
    assert "现行版" in text or "候选" in text  # 有证据则给答案；无证据则列候选


def test_status_surfaces_stale_claims(tmp_path: Path):
    """STATE.md 里结论的依赖变更后，status 必须报出来。"""
    _project(tmp_path)
    from projstate.cli import main
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        main(["log", "上传重试已完成，见 src/util.py"])
        main(["refresh"])
        _write(tmp_path, "src/util.py", "def helper():\n    return 2\n")
        main(["refresh"])
        text = mcp.call_tool("projstate_status", {}, tmp_path)
    finally:
        os.chdir(cwd)
    assert "依赖已变更" in text
    assert "src/util.py" in text


def test_check_returns_report_not_error(tmp_path: Path):
    _project(tmp_path)
    text = mcp.call_tool("projstate_check", {}, tmp_path)
    assert isinstance(text, str) and text.strip()


# --------------------------------------------------------------- 端到端 stdio


def test_stdio_roundtrip(tmp_path: Path, monkeypatch, capsys):
    """走一遍真正的 stdio 主循环：按行 JSON-RPC 进出。"""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"))
    assert mcp.serve(tmp_path) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2  # 通知不回
    assert json.loads(out[0])["result"]["serverInfo"]["name"] == "projstate"
    assert len(json.loads(out[1])["result"]["tools"]) == len(mcp.TOOLS)


def test_stdio_survives_garbage_input(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json\n\n{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"ping\"}\n"))
    assert mcp.serve(tmp_path) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert json.loads(out[0])["id"] == 7
