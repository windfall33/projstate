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
