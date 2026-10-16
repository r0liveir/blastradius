"""Git patch analysis for Python repositories"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

_HUNK = re.compile(
    r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: int
    removed_lines: int
    lines: tuple[str, ...]
    changed_old_lines: tuple[int, ...]
    changed_new_lines: tuple[int, ...]


@dataclass(frozen=True)
class ChangedFunction:
    name: str
    qualname: str
    path: str
    start_line: int
    end_line: int
    change: str


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    additions: int
    deletions: int
    hunks: tuple[Hunk, ...]
    functions: tuple[ChangedFunction, ...]
    source: str


@dataclass(frozen=True)
class PatchAnalysis:
    base_revision: str
    target_revision: str
    files: tuple[ChangedFile, ...]
    raw_diff: str

    @property
    def additions(self) -> int:
        return sum(file.additions for file in self.files)

    @property
    def deletions(self) -> int:
        return sum(file.deletions for file in self.files)


def analyze_patch(
    repo: str | Path, base_revision: str, target_revision: str
) -> PatchAnalysis:
    """Analyze Python changes between two revisions in ``repo``."""
    root = Path(repo)
    raw_diff = _git(
        root,
        "diff",
        "--no-color",
        "--find-renames",
        "--unified=3",
        base_revision,
        target_revision,
    )
    statuses = _statuses(root, base_revision, target_revision)
    blocks = _diff_blocks(raw_diff)
    files = tuple(
        _file_analysis(
            root, base_revision, target_revision, statuses.get(path, "M"), block
        )
        for path, block in blocks.items()
    )
    return PatchAnalysis(base_revision, target_revision, files, raw_diff)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout


def _statuses(root: Path, base: str, target: str) -> dict[str, str]:
    output = _git(root, "diff", "--name-status", "--find-renames", base, target)
    statuses: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if not fields:
            continue
        status = fields[0][0]
        statuses[fields[-1]] = status
    return statuses


def _diff_blocks(raw_diff: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    current: list[str] = []
    path: str | None = None
    for line in raw_diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if path is not None:
                blocks[path] = "".join(current)
            current = [line]
            path = None
        else:
            current.append(line)
            if line.startswith(("+++ b/", "--- a/")) and (
                path is None or line.startswith("+++ b/")
            ):
                path = line[6:].rstrip("\n")
    if path is not None:
        blocks[path] = "".join(current)
    return blocks


def _file_analysis(
    root: Path, base: str, target: str, status: str, block: str
) -> ChangedFile:
    hunks = _hunks(block)
    path = _path_from_block(block)
    source = _show(root, target if status != "D" else base, path)
    functions = _changed_functions(root, base, target, path, hunks)
    return ChangedFile(
        path=path,
        status=status,
        additions=sum(hunk.added_lines for hunk in hunks),
        deletions=sum(hunk.removed_lines for hunk in hunks),
        hunks=tuple(hunks),
        functions=tuple(functions),
        source=source,
    )


def _path_from_block(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("+++ b/"):
            return line[6:]
    for line in block.splitlines():
        if line.startswith("--- a/"):
            return line[6:]
    raise ValueError("diff block has no path")


def _hunks(block: str) -> list[Hunk]:
    result: list[Hunk] = []
    lines = block.splitlines()
    for index, line in enumerate(lines):
        match = _HUNK.match(line)
        if not match:
            continue
        body: list[str] = []
        added = removed = 0
        old_line = int(match["old"])
        new_line = int(match["new"])
        changed_old: list[int] = []
        changed_new: list[int] = []
        for body_line in lines[index + 1 :]:
            if body_line.startswith(("@@ ", "diff --git ")):
                break
            body.append(body_line)
            if body_line.startswith("+") and not body_line.startswith("+++"):
                added += 1
                changed_new.append(new_line)
                new_line += 1
            elif body_line.startswith("-") and not body_line.startswith("---"):
                removed += 1
                changed_old.append(old_line)
                old_line += 1
            else:
                old_line += 1
                new_line += 1
        result.append(
            Hunk(
                old_start=int(match["old"]),
                old_count=1 if match["old_count"] is None else int(match["old_count"]),
                new_start=int(match["new"]),
                new_count=1 if match["new_count"] is None else int(match["new_count"]),
                added_lines=added,
                removed_lines=removed,
                lines=tuple(body),
                changed_old_lines=tuple(changed_old),
                changed_new_lines=tuple(changed_new),
            )
        )
    return result


def _show(root: Path, revision: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        check=False,
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _changed_functions(
    root: Path, base: str, target: str, path: str, hunks: list[Hunk]
) -> list[ChangedFunction]:
    old = _functions(_show(root, base, path), path)
    new = _functions(_show(root, target, path), path)
    old_by_name = {function.qualname: function for function in old}
    new_by_name = {function.qualname: function for function in new}
    result: list[ChangedFunction] = []
    for qualname in sorted(set(old_by_name) | set(new_by_name)):
        before, after = old_by_name.get(qualname), new_by_name.get(qualname)
        if after and _touches(hunks, after.start_line, after.end_line, new=True):
            change = "added" if before is None else "modified"
            result.append(replace(after, change=change))
        elif before and _touches(hunks, before.start_line, before.end_line, new=False):
            result.append(replace(before, change="removed"))
    return result


def _functions(source: str, path: str) -> list[ChangedFunction]:
    if not source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    result: list[ChangedFunction] = []

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{node.name}" if prefix else node.name
                result.append(
                    ChangedFunction(
                        node.name,
                        qualname,
                        path,
                        node.lineno,
                        node.end_lineno or node.lineno,
                        "",
                    )
                )
                visit(node.body, qualname)
            elif isinstance(node, ast.ClassDef):
                qualname = f"{prefix}.{node.name}" if prefix else node.name
                visit(node.body, qualname)

    visit(tree.body)
    return result


def _touches(hunks: list[Hunk], start: int, end: int, *, new: bool) -> bool:
    for hunk in hunks:
        changed_lines = hunk.changed_new_lines if new else hunk.changed_old_lines
        if any(start <= line <= end for line in changed_lines):
            return True
    return False
