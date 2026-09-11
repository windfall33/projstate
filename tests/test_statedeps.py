"""依赖快照：状态层 + 文档层。

这一层解决的是"真值源静默过期"——ProjectBrain 实证里，工具以权威身份说谎、
agent 为此多花 2.5 倍时间推翻它。机制必须是"知道自己什么时候已经错了"。
"""
from pathlib import Path

from projstate import statedeps


def _mk(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ------------------------------------------------------------ extract_deps


def test_extract_deps_only_keeps_existing_files(tmp_path: Path):
    """只收真实存在的文件——提到过的名字不算依赖。"""
    _mk(tmp_path, "src/real.py", "x = 1\n")
    deps = statedeps.extract_deps("见 src/real.py 与 src/ghost.py", tmp_path)
    assert "src/real.py" in deps
    assert "src/ghost.py" not in deps


# ------------------------------------------------------------ 状态层


def test_snapshot_then_annotate_marks_and_is_bidirectional(tmp_path: Path):
    _mk(tmp_path, ".ai/STATE.md", "- 2026-09-11 上传重试已完成，见 src/upload.py\n")
    _mk(tmp_path, "src/upload.py", "def put():\n    return 1\n")
    assert statedeps.snapshot_line(tmp_path, "- 2026-09-11 上传重试已完成，见 src/upload.py") == 1

    assert statedeps.annotate(tmp_path) == []  # 未变更
    _mk(tmp_path, "src/upload.py", "def put():\n    return 2\n\n# TODO: 还没接完\n")
    assert len(statedeps.annotate(tmp_path)) == 1
    assert "依赖已变更" in (tmp_path / ".ai/STATE.md").read_text(encoding="utf-8")

    # 改回原样 → 标记自动消失
    _mk(tmp_path, "src/upload.py", "def put():\n    return 1\n")
    assert statedeps.annotate(tmp_path) == []
    assert "依赖已变更" not in (tmp_path / ".ai/STATE.md").read_text(encoding="utf-8")


def test_annotate_does_not_stack_marks(tmp_path: Path):
    _mk(tmp_path, ".ai/STATE.md", "- 2026-09-11 结论，见 src/a.py\n")
    _mk(tmp_path, "src/a.py", "v1\n")
    statedeps.snapshot_line(tmp_path, "- 2026-09-11 结论，见 src/a.py")
    _mk(tmp_path, "src/a.py", "v2\n")
    statedeps.annotate(tmp_path)
    statedeps.annotate(tmp_path)
    statedeps.annotate(tmp_path)
    text = (tmp_path / ".ai/STATE.md").read_text(encoding="utf-8")
    assert text.count("依赖已变更") == 1


# ------------------------------------------------------------ 文档层


def test_scan_docs_baselines_silently_then_flags_change(tmp_path: Path):
    _mk(tmp_path, "AGENTS.md", "# proj\n\n架构见 hermes_cli/main.py\n")
    _mk(tmp_path, "hermes_cli/main.py", "def main():\n    return 0\n")

    stale, snap = statedeps.scan_docs(tmp_path)
    assert stale == []                       # 首次只建基线
    assert "AGENTS.md" in snap               # 根级规则文件也要被覆盖

    _mk(tmp_path, "hermes_cli/main.py", "def main():\n    return 1\n")
    stale, _ = statedeps.scan_docs(tmp_path)
    assert [d["doc"] for d in stale] == ["AGENTS.md"]
    assert stale[0]["deps"] == ["hermes_cli/main.py"]


def test_scan_docs_covers_docs_subtree(tmp_path: Path):
    _mk(tmp_path, "docs/guide/setup.md", "跑 `src/run.py`\n")
    _mk(tmp_path, "README.md", "见 src/run.py\n")
    _mk(tmp_path, "src/run.py", "print(1)\n")
    _, snap = statedeps.scan_docs(tmp_path)
    assert "docs/guide/setup.md" in snap
    assert "README.md" in snap


def test_scan_docs_skips_docs_without_refs(tmp_path: Path):
    _mk(tmp_path, "docs/notes.md", "纯文字，没有引用任何文件\n")
    _, snap = statedeps.scan_docs(tmp_path)
    assert "docs/notes.md" not in snap


def test_scan_docs_does_not_self_heal_on_repeat(tmp_path: Path):
    """已失效的文档保留旧基线：再跑 refresh 不会悄悄"自愈"。"""
    _mk(tmp_path, "AGENTS.md", "见 src/a.py\n")
    _mk(tmp_path, "src/a.py", "v1\n")
    statedeps.scan_docs(tmp_path)
    _mk(tmp_path, "src/a.py", "v2\n")
    assert len(statedeps.scan_docs(tmp_path)[0]) == 1
    assert len(statedeps.scan_docs(tmp_path)[0]) == 1  # 第二次仍报
    assert len(statedeps.scan_docs(tmp_path)[0]) == 1  # 第三次仍报


def test_ack_clears_until_next_change(tmp_path: Path):
    """噪声抑制：ack 后静默，等下一次变更才重新报警。"""
    _mk(tmp_path, "AGENTS.md", "见 src/a.py\n")
    _mk(tmp_path, "src/a.py", "v1\n")
    statedeps.scan_docs(tmp_path)

    _mk(tmp_path, "src/a.py", "v2\n")
    assert len(statedeps.scan_docs(tmp_path)[0]) == 1

    assert statedeps.ack_docs(tmp_path, "AGENTS.md") == 1
    assert statedeps.scan_docs(tmp_path)[0] == []      # 看过了 → 静默

    _mk(tmp_path, "src/a.py", "v3\n")
    assert len(statedeps.scan_docs(tmp_path)[0]) == 1  # 又变了 → 再报

    # ack 记录留痕：报告里能看出"上次接受于何时"
    stale, _ = statedeps.scan_docs(tmp_path)
    assert stale[0]["acked_at"] is not None


def test_ack_without_path_resets_all(tmp_path: Path):
    _mk(tmp_path, "AGENTS.md", "见 src/a.py\n")
    _mk(tmp_path, "README.md", "见 src/b.py\n")
    _mk(tmp_path, "src/a.py", "v1\n")
    _mk(tmp_path, "src/b.py", "v1\n")
    statedeps.scan_docs(tmp_path)
    _mk(tmp_path, "src/a.py", "v2\n")
    _mk(tmp_path, "src/b.py", "v2\n")
    assert len(statedeps.scan_docs(tmp_path)[0]) == 2
    assert statedeps.ack_docs(tmp_path) == 2
    assert statedeps.scan_docs(tmp_path)[0] == []


def test_rule_file_block_does_not_self_trigger(tmp_path: Path):
    """根级规则文件的标记块由工具自己改写，不得自指触发报警。

    否则形成死循环：refresh 改 AGENTS.md → 引用它的文档全部失效 → ack →
    下次 refresh 又改 → 再次失效。实测 hermes-agent 上会牵动 54 份文档。
    """
    from projstate.render import BEGIN_MARKER, END_MARKER

    _mk(tmp_path, "AGENTS.md", "# proj\n\n见 src/a.py\n")
    _mk(tmp_path, "README.md", "见 AGENTS.md\n")
    _mk(tmp_path, "src/a.py", "v1\n")
    statedeps.scan_docs(tmp_path)

    # 工具写入标记块（含每次都会变的计数），块外内容一字未动
    base = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    for n in (1, 2, 3):
        (tmp_path / "AGENTS.md").write_text(
            base + f"\n{BEGIN_MARKER}\n任务：x · 候选 {n} 文件\n{END_MARKER}\n",
            encoding="utf-8",
        )
        assert statedeps.scan_docs(tmp_path)[0] == [], f"第 {n} 次工具改写触发了报警"

    # 但人工改动块外内容，仍必须报警
    (tmp_path / "AGENTS.md").write_text(
        base + "新增架构硬约束\n", encoding="utf-8"
    )
    assert [d["doc"] for d in statedeps.scan_docs(tmp_path)[0]] == ["README.md"]


def test_pending_docs_is_readonly(tmp_path: Path):
    """check 是只读体检：pending_docs 不得写盘。"""
    _mk(tmp_path, "AGENTS.md", "见 src/a.py\n")
    _mk(tmp_path, "src/a.py", "v1\n")
    statedeps.scan_docs(tmp_path)
    before = (tmp_path / statedeps.DEPS_FILE).read_text(encoding="utf-8")

    _mk(tmp_path, "src/a.py", "v2\n")
    pending = statedeps.pending_docs(tmp_path)
    assert [d["doc"] for d in pending] == ["AGENTS.md"]
    assert (tmp_path / statedeps.DEPS_FILE).read_text(encoding="utf-8") == before
