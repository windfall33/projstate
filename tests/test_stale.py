from projstate.stale import group_candidates, normalize_stem


def test_normalize_stem_strips_version_markers():
    assert normalize_stem("export_v1.js") == "export"
    assert normalize_stem("export_old.js") == "export"
    assert normalize_stem("export.js") == "export"
    assert normalize_stem("finalize.js") == "finalize"  # 词边界，finalize 不命中 final


def test_group_candidates_groups_same_stem():
    groups = group_candidates(["src/export.js", "src/export_v1.js", "src/export_old.js"])
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_group_candidates_keeps_distinct_separate():
    # 两个不同文件归为 2 组，但都只有单个成员 → 不返回（避免误判 api_v2 这类单成员）
    groups = group_candidates(["src/export.js", "src/import.js"])
    assert len(groups) == 0


def test_normalize_stem_keeps_api_v2_when_no_v1():
    assert normalize_stem("api_v2.js") == "api"


def test_elect_marks_ambiguous_when_no_decisive_signal():
    """场景 H：当前版 vs 旧版，全部信号平局时不可乱判，须标 ambiguous。"""
    from projstate.stale import elect

    facts = {
        "app/merge.py": {"ref_count": 0, "reachable": False, "mtime": 100, "language": "python"},
        "app/merge_alias.py": {"ref_count": 0, "reachable": False, "mtime": 100, "language": "python"},
    }
    findings = elect([["app/merge.py", "app/merge_alias.py"]], facts, set())
    assert len(findings) == 1
    assert findings[0].canonical is None
    assert findings[0].ambiguous == ("app/merge.py", "app/merge_alias.py")


def test_elect_prefers_referenced_over_unreferenced():
    """场景 B：被真实引用的文件应胜出，即使它名字带 v2 标记。"""
    from projstate.stale import elect

    facts = {
        "app/payment_v2.py": {"ref_count": 1, "reachable": False, "mtime": 6000, "language": "python"},
        "app/payment.py": {"ref_count": 0, "reachable": False, "mtime": 2000, "language": "python"},
    }
    findings = elect([["app/payment_v2.py", "app/payment.py"]], facts, set())
    assert len(findings) == 1
    assert findings[0].canonical == "app/payment_v2.py"
    assert findings[0].superseded == ("app/payment.py",)
