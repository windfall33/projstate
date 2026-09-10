"""卫生层：陈旧判定与现行版定论。

可定论的只有一种情况：**明确旧版标记**（方向确定）。
其余一律 ambiguous（报告「需人工登记」），把判断交给 `projstate resolve` —— 一次写入永久确定。
刻意不用 ref_count / reachable / mtime 定论：它们方向不确定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from projstate.languages import VERSION_STEM_MARKER, has_old_marker, is_test_file


@dataclass(frozen=True)
class StaleFinding:
    group_key: tuple[str, ...]
    canonical: str | None
    superseded: tuple[str, ...] = ()
    needs_review: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    signals: dict[str, object] = field(default_factory=dict)
    suggestion: str = ""


def normalize_stem(name: str) -> str:
    """去掉文件名中的版本/旧版标记。用词边界，finalize 不命中 final。"""
    stem = name.rsplit(".", 1)[0]
    return VERSION_STEM_MARKER.sub("", stem).strip("_- ")


def group_candidates(paths: list[str]) -> list[list[str]]:
    """按「同目录 + 同扩展名 + 规范化主干相同」归组。

    硬闸门：测试文件（`x.test.ts` / `x_test.py` / `test_x.py`）直接跳过，永不参与归组。
    测试与源文件是并存关系，不是版本关系；让它们同组会导致测试文件因 ref_count=0
    而被判「建议归档」——那是把「行为契约」误报成「废弃实现」。
    """
    groups: dict[tuple[str, str, str], list[str]] = {}
    for p in paths:
        if is_test_file(p):
            continue
        parts = p.replace("\\", "/").split("/")
        directory = "/".join(parts[:-1])
        name = parts[-1]
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        stem = normalize_stem(name)
        key = (directory, ext, stem)
        groups.setdefault(key, []).append(p)
    return [v for v in groups.values() if len(v) > 1]


def elect(groups: list[list[str]], facts: dict, git_tracked: set[str]) -> list[StaleFinding]:
    """对每组选举现行版。

    策略（P0 收窄后）：**只有「明确旧版标记」可定论**。
    组内同时存在「带明确旧版标记」与「不带标记」两类、且不带标记的只有一个时，
    该文件即现行版，带标记的成员全部作废；其余情况一律 ambiguous。

    为什么不再用 reachable / ref_count / mtime 定论：
      - `ref_count` 方向不确定 —— 旧版在迁移未完成时仍可能被大量引用，
        此时「引用更多」恰恰指向旧版（实证：`a_old.py` 引用 5 次会击败引用 0 次的 `a.py`，
        于是自声明旧版的文件被选成现行版，并建议归档真正的现行版）；
      - `reachable` 只判「文件名像不像入口」，在 monorepo 里 99.7% 是噪声；
      - `mtime` 会被复制/检出/批量改写刷新，本身不携带方向。
    它们仍由 `_rank` 排序，但排序结果只作为**登记时的建议**写进 `signals.hint`，不参与定论。

    不猜的代价是「待登记」变多 —— 这是刻意的：判断交给 `projstate resolve`，一次写入永久确定。
    """
    findings: list[StaleFinding] = []
    for group in groups:
        group_key = tuple(group[0].replace("\\", "/").split("/")[:-1])
        decided = _decide_by_old_marker(group)
        if decided is not None:
            canonical, superseded = decided
            findings.append(
                StaleFinding(
                    group_key=group_key,
                    canonical=canonical,
                    superseded=superseded,
                    needs_review=(),
                    ambiguous=(),
                    signals={"group": tuple(group)},
                    suggestion="建议归档（文件名明确声明旧版）",
                )
            )
            continue

        # 无决定性证据 → ambiguous，只报告不选，不猜；附排序证据作为登记时的建议
        ranked = _rank(group, facts, git_tracked)
        findings.append(
            StaleFinding(
                group_key=group_key,
                canonical=None,
                superseded=(),
                needs_review=(),
                ambiguous=tuple(group),
                signals={
                    "group": tuple(group),
                    "decisive": False,
                    "hint": [
                        {
                            "path": p,
                            "ref_count": int(facts.get(p, {}).get("ref_count", 0)),
                            "reachable": bool(facts.get(p, {}).get("reachable")),
                            "old_marker": has_old_marker(p),
                        }
                        for p in ranked
                    ],
                },
                suggestion="无决定性证据，需人工登记（projstate resolve <候选>）",
            )
        )
    return findings


def _decide_by_old_marker(group: list[str]) -> tuple[str, tuple[str, ...]] | None:
    """只有「明确旧版标记」方向确定，可作为定论依据。

    组内恰好存在一个「不带明确旧版标记」的成员、且至少有一个「带标记」的成员时，
    前者是现行版，后者全部作废。其余情况返回 None（上层标 ambiguous）。

    典型：`auth.py` + `auth_old.py` → 定论 `auth.py`
         `payment.py` + `payment_v2.py` → None（v2 方向不确定，等人工登记）
         `a_old.py` + `a_backup.py` → None（全是旧版声明，缺现行候选）
    """
    marked = [p for p in group if has_old_marker(p)]
    clean = [p for p in group if not has_old_marker(p)]
    if marked and len(clean) == 1:
        return clean[0], tuple(marked)
    return None


def _rank(group: list[str], facts: dict, git_tracked: set[str]) -> list[str]:
    """按 (reachable, ref_count, 无明确旧版标记, mtime) 降序排。

    注意：这个排序**不再参与定论**（见 elect）。它只用于在 ambiguous 组里给出
    「如果要人工登记，按这个顺序看更像现行版」的提示，写进 signals.hint。
    `git_tracked` 仍在签名里但未参与排序。
    """
    def key(path: str):
        f = facts.get(path, {})
        return (
            not bool(f.get("reachable")),          # reachable 优先（升序则 False 更先）
            -int(f.get("ref_count", 0)),           # ref_count 更大优先
            has_old_marker(path),                  # 无明确旧版标记优先
            -int(f.get("mtime", 0)),               # mtime 更新优先
        )
    return sorted(group, key=key)
