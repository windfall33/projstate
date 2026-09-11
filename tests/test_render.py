from projstate.render import render_agents_block, render_hip, render_map
from projstate.stale import StaleFinding


def test_render_map_lists_entries_and_refs():
    data = {
        "src/main.js": {"language": "js", "imports": [], "ref_count": 3, "reachable": True, "mtime": 1},
        "src/config.js": {"language": "js", "imports": ["src/main.js"], "ref_count": 1, "reachable": False, "mtime": 2},
    }
    out = render_map(data, [], {"name": "demo", "fingerprint": "a1b2", "count": 2})
    assert "src/main.js" in out
    assert "阅读锚点" in out
    # 只有命名依据 → 必须标成「非确证」候选。旧实现在这里写「入口（确证）」，
    # 把按文件名猜出来的东西标成确证——正是本项目要防的事。
    assert "入口候选" in out
    assert "非确证" in out
    assert "关键文件（按被引次数，确证）" in out


def test_render_map_separates_manifest_entries_from_name_guesses():
    data = {
        "src/index.js": {"language": "js", "ref_count": 0, "reachable": True,
                         "entry_basis": "name", "mtime": 1},
        "bin/cli.js": {"language": "js", "ref_count": 0, "reachable": True,
                       "entry_basis": "manifest", "mtime": 2},
    }
    out = render_map(data, [], {"name": "demo", "fingerprint": "x", "count": 2})
    assert "入口（manifest 声明，确证）" in out
    assert "入口候选（按命名推断，**非确证**" in out
    assert out.index("bin/cli.js") < out.index("src/index.js")


def test_anchor_lines_caps_name_guesses():
    """命名推断的候选要封顶，否则 monorepo 里上百个 index 会把锚点淹掉。"""
    from projstate.render import ENTRY_NAME_LIMIT, anchor_lines

    data = {
        f"pkg{i}/index.ts": {"language": "ts", "ref_count": 0, "reachable": True,
                             "entry_basis": "name", "mtime": i}
        for i in range(ENTRY_NAME_LIMIT + 7)
    }
    out = "\n".join(anchor_lines(data))
    assert "另有 7 个同名文件" in out


def test_render_hip_flags_superseded():
    class F:
        canonical = "src/export.js"
        superseded = ("src/export_v1.js",)
        needs_review = ()
        ambiguous = ()
        suggestion = "建议归档"
    out = render_hip([F()], ["dist2/"])
    assert "src/export_v1.js" in out
    assert "勿读" in out
    assert "dist2/" in out


def test_render_agents_block_small():
    out = render_agents_block({"task": "导出重构", "next": "改验证逻辑", "count": 10})
    assert "<!-- projstate:begin" in out
    assert "导出重构" in out
    assert "<!-- projstate:end" in out


def test_render_hip_no_none_and_lists_ambiguous():
    """缺陷1/2：ambiguous 组不得打印 None，且须列出候选文件。"""
    findings = [
        StaleFinding(
            group_key=("svc",),
            canonical=None,
            ambiguous=("svc/feature_0.py", "svc/feature_0_old.py", "svc/feature_0_v2.py"),
            signals={"group": ("svc/feature_0.py", "svc/feature_0_old.py", "svc/feature_0_v2.py")},
            suggestion="需人工确认",
        ),
        StaleFinding(
            group_key=("app",),
            canonical="app/auth.py",
            superseded=("app/auth_old.py",),
            signals={"group": ("app/auth.py", "app/auth_old.py")},
            suggestion="建议归档",
        ),
    ]
    out = render_hip(findings, [])
    assert "None" not in out
    assert "svc/feature_0_v2.py" in out
    assert "svc/feature_0.py" in out
    assert "需人工确认" in out
    assert "app/auth.py" in out


def test_render_map_marks_ambiguous_group():
    findings = [
        StaleFinding(
            group_key=("svc",),
            canonical=None,
            ambiguous=("svc/feature_0.py", "svc/feature_0_v2.py"),
            signals={},
            suggestion="需人工确认",
        )
    ]
    out = render_map({}, findings, {"name": "x", "fingerprint": "f", "count": 2})
    assert "需人工确认" in out
    assert "svc/feature_0_v2.py" in out


def test_render_marks_decision_basis_not_inference():
    """P0：定论来源只有「明确旧版标记」或「人工登记」，两种都不是推断，故不得再标「（推断）」。"""
    findings = [
        StaleFinding(
            group_key=("app",),
            canonical="app/auth.py",
            superseded=("app/auth_old.py",),
            signals={"group": ("app/auth.py", "app/auth_old.py")},
            suggestion="建议归档（文件名明确声明旧版）",
        ),
        StaleFinding(
            group_key=("svc",),
            canonical="svc/feature_v2.py",
            superseded=("svc/feature.py",),
            signals={"group": ("svc/feature.py", "svc/feature_v2.py"), "overridden": True},
            suggestion="人工确认（resolve）",
        ),
    ]
    hip = render_hip(findings, [])
    assert "（推断）" not in hip
    assert "明确旧版标记" in hip
    assert "人工登记" in hip
    mp = render_map({}, findings, {"name": "x", "fingerprint": "f", "count": 0})
    assert "（推断）" not in mp


def test_render_hip_aggregates_many_ambiguous():
    """产物膨胀：超过阈值时聚合为统计+摘要，而非逐行列全部候选。"""
    findings = [
        StaleFinding(
            group_key=(f"g{i}",),
            canonical=None,
            ambiguous=(f"src/g{i}.py", f"src/g{i}_v2.py"),
            signals={},
            suggestion="需人工确认",
        )
        for i in range(25)
    ]
    out = render_hip(findings, [])
    assert "共 25 组需人工确认" in out
    assert "完整清单见" in out and "stale.json" in out
    # 不应列全部 25 组
    assert out.count("候选：") < 25
