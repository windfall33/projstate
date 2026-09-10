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


def test_normalize_stem_covers_new_final_latest_and_chinese():
    """归组层词表必须覆盖 P0 清单中的常见版本标记，否则永远进不了判定。"""
    for name in (
        "a_new.py", "a_final.py", "a_latest.py", "a_next.py", "a_refactor.py",
        "a_rebuilt.py", "a_2026.py", "a_newest.py", "a_deprecated.py", "a_tmp.py",
        "a_original.py", "a_副本.py", "a-旧.py", "a_v2.py", "a_old.py",
    ):
        assert normalize_stem(name) == "a", f"{name} 未归到主干 a"
    # 词边界：normalize 不误命中
    assert normalize_stem("finalize.py") == "finalize"
    assert normalize_stem("newly.py") == "newly"


def test_group_candidates_groups_new_and_final_suffix():
    """shipping.py + shipping_new.py 必须归组，否则 ref_count 再准也用不上。"""
    groups = group_candidates(["src/report.py", "src/report_final.py"])
    assert len(groups) == 1
    assert len(groups[0]) == 2


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


def test_elect_does_not_decide_by_ref_count():
    """验收 A：组内无明确旧版标记时，不得仅凭 ref_count（或 reachable/mtime）定论。

    本用例是 P0 改造前 `test_elect_prefers_referenced_over_unreferenced` 的反向断言。
    改造前：payment_v2 引用 1 次、payment 引用 0 次 → 工具判 v2 为现行版。
    改造后：两者都没有明确旧版标记 → 一律「待登记」，由人用 resolve 确认一次。
    """
    from projstate.stale import elect

    facts = {
        "app/payment_v2.py": {"ref_count": 1, "reachable": False, "mtime": 6000, "language": "python"},
        "app/payment.py": {"ref_count": 0, "reachable": False, "mtime": 2000, "language": "python"},
    }
    findings = elect([["app/payment.py", "app/payment_v2.py"]], facts, set())
    assert len(findings) == 1
    assert findings[0].canonical is None
    assert findings[0].superseded == ()
    assert findings[0].ambiguous == ("app/payment.py", "app/payment_v2.py")
    # 排序证据必须仍在（降级为「建议」而不是被丢掉）
    assert [h["path"] for h in findings[0].signals["hint"]] == \
        ["app/payment_v2.py", "app/payment.py"]


def test_elect_explicit_old_marker_beats_higher_ref_count():
    """自声明旧版的文件即使被引用更多，也不得成为现行版。

    这是 P0 改造前的一个真实反向误判：`a_old.py` 引用 5 次、`a.py` 引用 0 次时，
    旧实现让 `a_old.py` 当上现行版，并建议归档 `a.py`。
    """
    from projstate.stale import elect

    facts = {
        "app/a.py": {"ref_count": 0, "reachable": False, "mtime": 1000, "language": "python"},
        "app/a_old.py": {"ref_count": 5, "reachable": False, "mtime": 2000, "language": "python"},
    }
    findings = elect([["app/a.py", "app/a_old.py"]], facts, set())
    assert findings[0].canonical == "app/a.py"
    assert findings[0].superseded == ("app/a_old.py",)


def test_elect_still_decides_when_old_marker_present():
    """保留能力：明确旧版标记仍可定论，不需要人工登记。"""
    from projstate.stale import elect

    facts = {
        "svc/auth.py": {"ref_count": 0, "reachable": False, "mtime": 1000, "language": "python"},
        "svc/auth_old.py": {"ref_count": 3, "reachable": False, "mtime": 2000, "language": "python"},
    }
    findings = elect([["svc/auth.py", "svc/auth_old.py"]], facts, set())
    assert findings[0].canonical == "svc/auth.py"
    assert findings[0].superseded == ("svc/auth_old.py",)


def test_elect_all_marked_is_ambiguous():
    """全是旧版声明、没有现行候选时不定论。"""
    from projstate.stale import elect

    facts = {p: {"ref_count": 0, "reachable": False, "mtime": 1, "language": "python"}
             for p in ("app/a_old.py", "app/a_backup.py")}
    findings = elect([["app/a_old.py", "app/a_backup.py"]], facts, set())
    assert findings[0].canonical is None


def test_group_candidates_never_groups_test_files():
    """验收 B：测试文件永不参与版本归组（它是行为契约，不是旧版本）。"""
    assert group_candidates(["src/catalog.ts", "src/catalog.test.ts"]) == []
    assert group_candidates(["src/handler.py", "src/handler_test.py"]) == []
    assert group_candidates(["src/app.js", "src/app.spec.js"]) == []
    assert group_candidates(["src/test_utils.py", "src/utils.py"]) == []
    # 对照：真实的版本关系仍然归组
    assert group_candidates(["src/report.py", "src/report_old.py"]) != []


def test_normalize_stem_keeps_compound_extension_separator():
    """双扩展名：剥版本词不得吞掉分隔符，否则与不带版本号的主文件永不归组。

    注意 normalize_stem 返回的是「不含末级扩展名」的主干（扩展名由调用方单独取），
    所以 `index.d.ts` 的主干是 `index.d`。
    """
    assert normalize_stem("registry-entry-v1.schema.json") == "registry-entry.schema"
    assert normalize_stem("registry-entry-v2.schema.json") == "registry-entry.schema"
    assert normalize_stem("registry-entry.schema.json") == "registry-entry.schema"
    assert normalize_stem("index-v2.d.ts") == "index.d"
    # registry-entry 三件套必须真的归到同一组（改造前 v1/v2 与主文件分属两组）
    group = group_candidates([
        "schemas/registry-entry.schema.json",
        "schemas/registry-entry-v1.schema.json",
        "schemas/registry-entry-v2.schema.json",
    ])
    assert len(group) == 1 and len(group[0]) == 3
    # .d.ts 同样归组
    assert len(group_candidates(["src/index.d.ts", "src/index-v2.d.ts"])) == 1


def test_normalize_stem_handles_chained_markers():
    """链式标记：_v2 不得吃掉 _final 的前导分隔符。"""
    assert normalize_stem("a_v2_final.py") == "a"
    assert normalize_stem("payment_v2_final.py") == "payment"
    assert normalize_stem("payment_new_final.py") == "payment"
