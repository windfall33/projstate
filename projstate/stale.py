"""卫生层：陈旧判定 + 现行版选举（精度优先，宁可漏报不可误报）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from projstate.languages import has_name_version_marker

_MARKERS = re.compile(
    r"(?:[._-](?:v\d+|old|backup|copy|bak))|(?:-旧|-副本| 2|\(1\)|\.final)",
    re.IGNORECASE,
)


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
    return _MARKERS.sub("", stem).strip("_- ")


def group_candidates(paths: list[str]) -> list[list[str]]:
    """按「同目录 + 同扩展名 + 规范化主干相同」归组。"""
    groups: dict[tuple[str, str, str], list[str]] = {}
    for p in paths:
        parts = p.replace("\\", "/").split("/")
        directory = "/".join(parts[:-1])
        name = parts[-1]
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        stem = normalize_stem(name)
        key = (directory, ext, stem)
        groups.setdefault(key, []).append(p)
    return [v for v in groups.values() if len(v) > 1]


def elect(groups: list[list[str]], facts: dict, git_tracked: set[str]) -> list[StaleFinding]:
    """对每组选举现行版；保守原则：只有当第一名在某高优先级信号上**严格优于**第二名时
    才输出 superseded/current；否则一律 ambiguous（不猜，宁可漏报不可误报）。"""
    findings: list[StaleFinding] = []
    for group in groups:
        ranked = _rank(group, facts, git_tracked)
        canonical, *_rest = ranked
        if _is_decisive(canonical, _rest[0], facts, git_tracked):
            superseded: list[str] = []
            needs_review: list[str] = []
            for path in _rest:
                f = facts.get(path, {})
                if f.get("ref_count") == 0 and not f.get("reachable"):
                    superseded.append(path)
                else:
                    needs_review.append(path)
            suggestion = (
                "建议归档（0 引用不可达）" if superseded else
                "被引用或可达，需人工确认" if needs_review else
                "待人工确认"
            )
            if superseded or needs_review:
                findings.append(
                    StaleFinding(
                        group_key=tuple(group[0].replace("\\", "/").split("/")[:-1]),
                        canonical=canonical,
                        superseded=tuple(superseded),
                        needs_review=tuple(needs_review),
                        ambiguous=(),
                        signals={"group": tuple(group)},
                        suggestion=suggestion,
                    )
                )
                continue

        # 无决定性区分 → ambiguous，只报告不选，不猜
        findings.append(
            StaleFinding(
                group_key=tuple(group[0].replace("\\", "/").split("/")[:-1]),
                canonical=None,
                superseded=(),
                needs_review=(),
                ambiguous=tuple(group),
                signals={"group": tuple(group), "decisive": False},
                suggestion="信号平局或缺乏决定性证据，需人工确认",
            )
        )
    return findings


def _signal_tuple(path: str, facts: dict, git_tracked: set[str]) -> tuple:
    """提取一个文件的排序信号（与 _rank 优先级一一对应）。"""
    f = facts.get(path, {})
    return (
        bool(f.get("reachable")),
        int(f.get("ref_count", 0)),
        path in git_tracked,
        not has_name_version_marker(path),
        int(f.get("mtime", 0)),
    )


def _is_decisive(a: str, b: str, facts: dict, git_tracked: set[str]) -> bool:
    """a 是否在某个高优先级信号上严格优于 b。

    _rank 用 (reachable, ref_count, git_tracked, 无标记, mtime) 降序。布尔/计数信号
    越靠前权重越大。我们在第一个出现差异的信号上要求严格占优（x > y），
    才算"决定性区分"。全部信号相等 → False → 上层标 ambiguous。

    附加保护：若唯一差异落在 `name_marker`（下标 3）或更靠后，而前三项
    （reachable / ref_count / git_tracked）全部打平且都是"低位"（不可达、0 引用、
    非 git tracked），则**不算决定性**——文件名版本标记方向不可靠，现行版带 v2
    与旧版带 v2 无法仅凭文件名区分，宁可标 ambiguous。
    """
    sa = _signal_tuple(a, facts, git_tracked)
    sb = _signal_tuple(b, facts, git_tracked)
    for i, (x, y) in enumerate(zip(sa, sb)):
        if x != y:
            if i >= 3:
                low_flat = all(sa[j] == sb[j] and not sa[j] for j in range(3))
                if low_flat:
                    return False
            return x > y
    return False


def _rank(group: list[str], facts: dict, git_tracked: set[str]) -> list[str]:
    """按 5 级优先级降序排：reachable > ref_count > git_tracked > 无name_pattern > mtime。"""
    def key(path: str):
        f = facts.get(path, {})
        return (
            not bool(f.get("reachable")),          # reachable 优先（升序则 False 更先）
            -int(f.get("ref_count", 0)),           # ref_count 更大优先
            not (path in git_tracked),             # git_tracked 优先
            has_name_version_marker(path),         # 无标记优先
            -int(f.get("mtime", 0)),               # mtime 更新优先
        )
    return sorted(group, key=key)
