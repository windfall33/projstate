from projstate.render import render_agents_block, render_hip, render_map


def test_render_map_lists_entries_and_refs():
    data = {
        "src/main.js": {"language": "js", "imports": [], "ref_count": 3, "reachable": True, "mtime": 1},
        "src/config.js": {"language": "js", "imports": ["src/main.js"], "ref_count": 1, "reachable": False, "mtime": 2},
    }
    out = render_map(data, [], {"name": "demo", "fingerprint": "a1b2", "count": 2})
    assert "src/main.js" in out
    assert "（确证）" in out
    assert "阅读锚点" in out


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
