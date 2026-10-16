"""Execute selected tests and compare them with the full suite."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestRun:
    test_ids: tuple[str, ...]
    command: tuple[str, ...]
    returncode: int
    duration: float
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class EvaluationReport:
    selected: TestRun
    full_suite: TestRun
    selection_ratio: float
    execution_time_reduction: float
    regression_detected: bool


def run_tests(
    repo: str | Path,
    test_ids: Iterable[str],
    *,
    python_executable: str | Path | None = None,
    timeout: float = 300,
) -> TestRun:
    """Run pytest IDs in ``repo`` with the target repository's Python."""
    ids = tuple(test_ids)
    executable = _executable(repo, python_executable)
    command = (executable, "-m", "pytest", "-q", *ids)
    if not ids:
        return TestRun(ids, command, 0, 0.0, "", "")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        returncode = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as error:
        returncode = 124
        stdout = _text(error.stdout)
        stderr = f"pytest timed out after {timeout:g}s\n{_text(error.stderr)}"
    return TestRun(ids, command, returncode, time.perf_counter() - started, stdout, stderr)


def evaluate_selection(
    repo: str | Path,
    selected_test_ids: Iterable[str],
    all_test_ids: Iterable[str],
    *,
    python_executable: str | Path | None = None,
    timeout: float = 300,
) -> EvaluationReport:
    """Run selected and complete test sets against the same worktree."""
    selected_ids = tuple(selected_test_ids)
    all_ids = tuple(all_test_ids)
    selected = run_tests(
        repo, selected_ids, python_executable=python_executable, timeout=timeout
    )
    full_suite = run_tests(
        repo, all_ids, python_executable=python_executable, timeout=timeout
    )
    ratio = len(selected_ids) / len(all_ids) if all_ids else 0.0
    reduction = (
        1.0 - selected.duration / full_suite.duration
        if full_suite.duration > 0
        else 0.0
    )
    regression_detected = not selected.passed and not full_suite.passed
    return EvaluationReport(selected, full_suite, ratio, reduction, regression_detected)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else value


def _executable(repo: str | Path, configured: str | Path | None) -> str:
    if configured is None:
        return sys.executable
    path = Path(configured)
    if not path.is_absolute():
        target_path = Path(repo) / path
        if target_path.exists():
            return str(target_path)
    return str(path)
