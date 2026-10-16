import sys
from pathlib import Path

from blastradius.runner import evaluate_selection, run_tests


def write_test_repo(root: Path, *, failing: bool) -> None:
    root.joinpath("app.py").write_text(f"def value():\n    return {2 if failing else 1}\n")
    root.joinpath("test_app.py").write_text(
        "from app import value\n\n"
        "def test_value():\n"
        "    assert value() == 1\n\n"
        "def test_constant():\n"
        "    assert 1 + 1 == 2\n"
    )


def test_run_tests_captures_passing_result(tmp_path):
    write_test_repo(tmp_path, failing=False)

    result = run_tests(
        tmp_path,
        ("test_app.py::test_value",),
        python_executable=sys.executable,
    )

    assert result.passed
    assert result.returncode == 0
    assert result.duration >= 0


def test_evaluation_detects_regression_and_reports_ratio(tmp_path):
    write_test_repo(tmp_path, failing=True)
    all_tests = ("test_app.py::test_value", "test_app.py::test_constant")

    report = evaluate_selection(tmp_path, (all_tests[0],), all_tests)

    assert not report.selected.passed
    assert not report.full_suite.passed
    assert report.regression_detected
    assert report.selection_ratio == 0.5
    assert report.selected.stdout
