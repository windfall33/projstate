"""交接单（AGENTS.md 里的块）：行为契约。

第四轮实验的结论落成四条不变量：
1. 接入不再依赖"仓库里已经存在 AGENTS.md"——没有就创建（旧行为等于没接入）。
2. 正文归人/agent，工具不覆盖；工具只维护指纹戳。
3. 源码改了而交接单没盖章 → check 必须报过期（这是"换窗口读到过期认知"的入口）。
4. 旧版本的块要被迁移，不能因为标记文案变了就插出第二个块。
"""

from pathlib import Path
import json

from projstate import brief
from projstate.cli import main


def _mk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")


def test_init_creates_agents_and_block_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    assert main(["init"]) == 0

    agents = tmp_path / "AGENTS.md"
    assert agents.is_file(), "没有 AGENTS.md 时必须创建载体，而不是放弃接入"
    text = agents.read_text(encoding="utf-8")
    assert "projstate:begin" in text and "projstate:end" in text
    assert "交接单" in text
    assert brief.read_stamp(text) is not None
    assert (tmp_path / ".ai" / "STATE.md").is_file()


def test_check_flags_stale_brief_then_stamp_clears_it(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    assert main(["init"]) == 0
    assert main(["refresh"]) == 0
    capsys.readouterr()
    assert main(["check"]) == 0, "刚接入、没改代码，不应报过期"

    # 改代码但不更新交接单 → 必须报过期
    (tmp_path / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["check"]) == 1
    assert "交接单已过期" in capsys.readouterr().out

    # 盖章后恢复干净
    assert main(["brief", "stamp"]) == 0
    capsys.readouterr()
    assert main(["check"]) == 0


def test_refresh_and_stamp_never_clobber_brief_body(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    assert main(["init"]) == 0

    agents = tmp_path / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    hand_written = "- 当前任务：把定价改成满 300 减 80\n- 已定决策：先不动 legacy/（依据：对账脚本还在用）"
    agents.write_text(
        text.replace("- 当前任务：", hand_written + "\n- 当前任务：", 1), encoding="utf-8"
    )
    edited = agents.read_text(encoding="utf-8")

    assert main(["refresh"]) == 0
    assert main(["brief", "stamp"]) == 0

    after = agents.read_text(encoding="utf-8")
    assert hand_written in after, "工具不得覆盖人/agent 写的交接正文"
    assert after.count("projstate:begin") == 1, "不得插出第二个块"
    stamp = brief.read_stamp(after)
    assert stamp is not None
    src_now = brief.fingerprint_files(
        [tmp_path / "src" / "a.py"]
    )
    assert stamp.src == src_now, "盖章后指纹应等于当前源码指纹"
    assert brief.is_stale(after, src_now) is False
    # 改动源码后必须重新变过期（戳是源码内容的函数，不是时间戳游戏）
    (tmp_path / "src" / "a.py").write_text("A = 3\n", encoding="utf-8")
    assert brief.is_stale(after, brief.fingerprint_files([tmp_path / "src" / "a.py"])) is True


def test_body_edit_alone_clears_staleness(tmp_path, monkeypatch):
    """验收实验发现的行为：agent 改了代码、也把交接单正文写好了，只是没跑 stamp
    —— 这时工具不该报过期（否则只会逼出 `git commit --no-verify`）。"""
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    assert main(["init"]) == 0
    agents = tmp_path / "AGENTS.md"
    before = agents.read_text(encoding="utf-8")

    (tmp_path / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    src2 = brief.fingerprint_files([tmp_path / "src" / "a.py"])
    assert brief.is_stale(before, src2) is True, "源码变了、正文没动 → 过期"

    agents.write_text(
        before.replace("- 当前任务：", "- 当前任务：把 A 从 1 改成 2", 1), encoding="utf-8"
    )
    assert brief.is_stale(agents.read_text(encoding="utf-8"), src2) is False, \
        "正文动过 = 有人管过，不算过期"

    # 改了正文后 stamp 仍可用：把戳对齐（此后正文再不动才会重新判过期）
    assert main(["brief", "stamp"]) == 0
    assert brief.is_stale(agents.read_text(encoding="utf-8"), src2) is False


def test_legacy_block_is_migrated_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    legacy = (
        "# 项目约定\n\n"
        "<!-- projstate:begin 自动生成，勿手改 -->\n"
        "任务：- [ ] · 下一步：- · 候选 0 文件\n"
        "<!-- projstate:end -->\n"
    )
    (tmp_path / "AGENTS.md").write_text(legacy, encoding="utf-8")

    assert main(["refresh"]) == 0
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("projstate:begin") == 1
    assert "# 项目约定" in text, "块外内容一字不动"
    assert brief.read_stamp(text) is not None, "历史块要补上指纹戳"


def test_hook_install_uninstall_and_refuses_foreign(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git" / "hooks").mkdir(parents=True)

    assert main(["hook", "install"]) == 0
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert "projstate:begin" in hook.read_text(encoding="utf-8")
    assert "projstate.cli brief check" in hook.read_text(encoding="utf-8")

    assert main(["hook", "uninstall"]) == 0
    assert not hook.exists()

    hook.write_text("#!/bin/sh\necho 别人的钩子\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["hook", "install"]) == 1, "不得覆盖别的工具装的钩子"
    assert "别人的钩子" in hook.read_text(encoding="utf-8")


def test_brief_show_reports_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["brief", "show"]) == 0
    out = capsys.readouterr().out
    assert "交接单" in out and "交接单状态" in out


def test_brief_check_is_single_purpose(tmp_path, monkeypatch, capsys):
    """`brief check` 只回答"交接单过期没有"，不受旧版判定等其它体检项影响
    —— pre-commit 用这个退出码，否则有未确认版本组的仓库永远提交不了。"""
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    (tmp_path / "app").mkdir()
    # 造一组 ambiguous 版本冲突（无引用、无明确旧版标记），让普通 check 变脏
    (tmp_path / "app" / "x.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "app" / "x_v2.py").write_text("X = 2\n", encoding="utf-8")
    assert main(["init"]) == 0
    assert main(["refresh"]) == 0
    capsys.readouterr()

    assert main(["check"]) == 1, "普通 check 因未确认版本组而脏（预期）"
    assert main(["brief", "check"]) == 0, "交接单本身没过期，就不该挡提交"

    (tmp_path / "src" / "a.py").write_text("A = 9\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["brief", "check"]) == 1
    assert "交接单已过期" in capsys.readouterr().err


def _stop_entries() -> list:
    p = Path(".claude") / "settings.json"
    return ((json.loads(p.read_text(encoding="utf-8")).get("hooks") or {}).get("Stop") or [])


def test_claude_stop_hook_install_merge_uninstall(tmp_path, monkeypatch, capsys):
    """Claude Stop 钩子：幂等安装、不破坏别人的配置、只删自己那条。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"defaultMode": "auto"},
                    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo 别人的"}]}]}},
                   ensure_ascii=False),
        encoding="utf-8")

    assert main(["hook", "install", "--target", "claude"]) == 0
    entries = _stop_entries()
    assert len(entries) == 2, "应追加自己的条目，不动别人的"
    assert "exit 2" in entries[-1]["hooks"][0]["command"], "Stop 钩子必须用退出码 2 阻塞"

    capsys.readouterr()
    assert main(["hook", "install", "--target", "claude"]) == 0
    assert "已存在" in capsys.readouterr().out
    assert len(_stop_entries()) == 2, "重复安装必须幂等"

    assert main(["hook", "uninstall", "--target", "claude"]) == 0
    entries = _stop_entries()
    assert len(entries) == 1 and entries[0]["hooks"][0]["command"] == "echo 别人的"
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"] == {"defaultMode": "auto"}, "别人的配置字段要留着"


def test_claude_stop_hook_created_and_removed_with_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["hook", "install", "--target", "claude"]) == 0
    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert len(_stop_entries()) == 1
    assert main(["hook", "uninstall", "--target", "claude"]) == 0
    assert not (tmp_path / ".claude" / "settings.json").exists(), "只剩空壳时删掉文件"


def test_writing_agent_config_does_not_stale_the_brief(tmp_path, monkeypatch, capsys):
    """装钩子会写 .claude/settings.json；它不能被算进源码指纹，
    否则刚接入就把自己的交接单判成过期（装完第一个提交就被自己挡住）。"""
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path)
    assert main(["init"]) == 0
    assert main(["refresh"]) == 0
    capsys.readouterr()
    assert main(["brief", "check"]) == 0

    assert main(["hook", "install", "--target", "claude"]) == 0
    capsys.readouterr()
    assert main(["brief", "check"]) == 0, "写 agent 配置不该让交接单过期"
