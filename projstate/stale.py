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
    """对每组选举现行版；硬否决：只有 ref_count==0 且不可达者才可列 superseded。"""
    findings: list[StaleFinding] = []
    for group in groups:
        ranked = _rank(group, facts, git_tracked)
        canonical, *_rest = ranked
        superseded: list[str] = []
        needs_review: list[str] = []
        ambiguous: list[str] = []
        for path in _rest:
            f = facts.get(path, {})
            if f.get("ref_count") == 0 and not f.get("reachable"):
                superseded.append(path)
            else:
                needs_review.append(path)
        if not superseded and not needs_review:
            ambiguous = list(_rest)
        suggestion = (
            "建议归档（0 引用不可达）" if superseded else
            "被引用或可达，需人工确认" if needs_review else
            "信号平局，需人工确认"
        )
        if superseded or needs_review or ambiguous:
            findings.append(
                StaleFinding(
                    group_key=tuple(group[0].replace("\\", "/").split("/")[:-1]),
                    canonical=canonical,
                    superseded=tuple(superseded),
                    needs_review=tuple(needs_review),
                    ambiguous=tuple(ambiguous),
                    signals={"group": tuple(group)},
                    suggestion=suggestion,
                )
            )
    return findings


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
