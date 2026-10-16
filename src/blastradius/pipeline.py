"""End-to-end BlastRadius pipeline."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .analyzer import ModelClient, SemanticAnalysis, analyze_semantics
from .diff import PatchAnalysis, analyze_patch
from .discovery import DependencyGraph, discover_dependencies
from .runner import EvaluationReport, TestRun, evaluate_selection, run_tests
from .selector import SelectionResult, select_tests


@dataclass(frozen=True)
class PipelineResult:
    analysis: PatchAnalysis
    graph: DependencyGraph
    semantic: SemanticAnalysis
    selection: SelectionResult
    selected_run: TestRun | None
    evaluation: EvaluationReport | None


def run_pipeline(
    repo: str | Path,
    base_revision: str,
    target_revision: str,
    client: ModelClient,
    *,
    execute: bool = True,
    compare: bool = False,
    python_executable: str | Path | None = None,
    timeout: float = 300,
) -> PipelineResult:
    """Run all BlastRadius stages in order."""
    root = Path(repo)
    analysis = analyze_patch(root, base_revision, target_revision)
    graph = discover_dependencies(str(root), analysis)
    semantic = analyze_semantics(analysis, graph, client)
    selection = select_tests(analysis, graph, semantic)
    selected_run = None
    evaluation = None
    if execute:
        _require_target_checked_out(root, target_revision)
        all_test_ids = tuple(test.id for test in graph.tests)
        if compare:
            evaluation = evaluate_selection(
                root,
                selection.selected_test_ids,
                all_test_ids,
                python_executable=python_executable,
                timeout=timeout,
            )
        else:
            selected_run = run_tests(
                root,
                selection.selected_test_ids,
                python_executable=python_executable,
                timeout=timeout,
            )
    return PipelineResult(
        analysis, graph, semantic, selection, selected_run, evaluation
    )


def _require_target_checked_out(repo: Path, target_revision: str) -> None:
    current = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    target = subprocess.run(
        ["git", "rev-parse", target_revision],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if current != target:
        raise ValueError(
            f"target revision {target_revision!r} is not checked out; "
            "checkout target before running tests"
        )
