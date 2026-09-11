"""交接单草稿：把"这一轮到底改了什么"用机械办法写出来，不调模型、不通读仓库。

为什么要有它（第五轮验收的结论）：交接单唯一不可替代的价值是"为什么/踩了什么坑"，
但让 agent 通读一遍再手写总结太贵——django 上生产一份要 $0.57，消费窗口只省 $0.016，
回本要 36 个窗口。这里把"改了什么"变成免费动作（git + AST + 已有事实层），
只把两行留给 agent：为什么、坑。

边界（写清楚，不装作更强）：
- 只报告**机械可证**的东西：改了哪些文件、定义了什么符号、事实层记了多少引用。
- 不判断现行版、不猜意图、不补调用点清单（引用数是 map 口径的下界，标注出来）。
- git 不可用时退化为"mtime 晚于上次盖章"的文件列表。
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from projstate.languages import is_archive, is_generated
from projstate.scan import is_ignored, is_rule_file, iter_files, load_ignore, rel_posix


def keep(rel: str, ignore: tuple[str, ...]) -> bool:
    """与扫描器同一套口径：工具产物、生成物、归档、规则文件、.gitignore 命中的都不算改动。"""
    rel = rel.replace("\\", "/")
    return not (is_rule_file(rel) or is_generated(rel) or is_archive(rel)
                or is_ignored(rel, ignore))


def git_head(root: Path) -> str:
    """当前 HEAD；不是 git 仓库或还没有提交时返回 'none'（显式记进戳里）。"""
    out = _git(root, "rev-parse", "HEAD")
    return out[0].strip() if out and out[0].strip() else "none"


def _git(root: Path, *args: str) -> list[str]:
    try:
        p = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return p.stdout.splitlines() if p.returncode == 0 else []


def changed_files(root: Path, stamp=None) -> tuple[list[str], str]:
    """返回 (改动文件, 数据来源说明)。

    首选：`git diff <戳里的 head>` —— 精确等于"自上次盖章以来的全部改动（含工作区）"。
    退路：老戳没有 head 时按时间（可能把同一分钟的基线提交也带上）；没有 git 时按 mtime。
    """
    seen: dict[str, None] = {}
    ignore = load_ignore(root)

    def add(rel: str) -> None:
        rel = rel.strip().strip('"').replace("\\", "/")
        if rel and keep(rel, ignore):
            seen[rel] = None

    has_git = (root / ".git").exists()
    head = getattr(stamp, "head", None)
    since = getattr(stamp, "at", None)
    if has_git:
        if head and head != "none":
            for ln in _git(root, "diff", "--name-only", head):
                add(ln)
            for ln in _git(root, "status", "--porcelain"):
                if len(ln) > 3:
                    add(ln[3:])
            return sorted(seen), f"git（自盖章提交 {head[:7]} 起）"
        if since:
            for ln in _git(root, "log", f"--since={since}", "--name-only",
                           "--pretty=format:", "HEAD"):
                add(ln)
        for ln in _git(root, "diff", "--name-only", "HEAD"):
            add(ln)
        for ln in _git(root, "status", "--porcelain"):
            if len(ln) > 3:
                add(ln[3:])
        if seen or not since:
            return sorted(seen), "git（提交 + 工作区 + 未跟踪）"

    # 兜底：没有 git（或 git 什么也没给）时，用 mtime 跟盖章时间比
    if not since:
        return [], "无 git 且没有上次盖章时间"
    try:
        cutoff = __import__("datetime").datetime.strptime(since, "%Y-%m-%dT%H:%M").timestamp()
    except ValueError:
        return [], "盖章时间格式不可解析"
    out = []
    for p in iter_files(root, ignore=load_ignore(root)):
        try:
            if p.stat().st_mtime > cutoff:
                out.append(rel_posix(root, p))
        except OSError:
            continue
    return sorted(out), "mtime（无 git，按上次盖章时间之后被改过）"


_JS_SYM = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+(\w+)"
    r"|^\s*export\s+(?:const|let|var)\s+(\w+)",
    re.M,
)


def symbols(root: Path, rel: str, limit: int = 4) -> list[str]:
    """顶层符号名（Python 用 ast，JS/TS 用正则）。拿不到就返回空。"""
    p = root / rel
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if rel.endswith(".py"):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return []
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        return names[:limit]
    if rel.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        names = [a or b for a, b in _JS_SYM.findall(src)]
        return names[:limit]
    return []


def facts(root: Path) -> dict:
    """读事实层（可能不存在或已过期——草稿只用它记引用数，不拿它做判断）。"""
    import json

    p = root / ".ai" / "map.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("files", {})
    except Exception:  # noqa: BLE001
        return {}


def render_lines(root: Path, files: list[str], map_data: dict) -> list[str]:
    """把改动文件渲染成交接单里"关键文件"那一段的行。"""
    out: list[str] = []
    for rel in files:
        bits: list[str] = []
        if not (root / rel).exists():
            bits.append("已删除")
        else:
            sym = symbols(root, rel)
            if sym:
                bits.append("定义 " + "、".join(sym))
        entry = map_data.get(rel)
        if entry is not None:
            bits.append(f"被 {entry.get('ref_count', 0)} 个文件引用（map 口径，下界）")
        suffix = "；".join(bits)
        out.append(f"  - `{rel}`" + (f" — {suffix}" if suffix else ""))
    return out
