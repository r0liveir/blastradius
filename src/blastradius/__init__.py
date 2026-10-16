"""BlastRadius command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .analyzer import OpenAICompatibleClient
from .pipeline import PipelineResult, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        _load_dotenv(args.repo / ".env")
        result = run_pipeline(
            args.repo,
            args.base,
            args.target,
            OpenAICompatibleClient.from_env(),
            execute=not args.no_run,
            compare=args.compare,
            python_executable=args.python_executable or os.getenv("BLASTRADIUS_PYTHON"),
            timeout=args.timeout,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        if args.output_format == "json":
            print(json.dumps({"error": str(error)}))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 2
    payload = _result_payload(result, args)
    if args.record:
        try:
            Path(args.record).write_text(json.dumps(payload, indent=2) + "\n")
        except OSError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
    if args.output_format == "json":
        print(json.dumps(payload))
    else:
        _print_result(result)
    return 1 if _tests_failed(result) else 0


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE settings without adding a dependency."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blastradius")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="analyze patch and select regression tests")
    run.add_argument("--repo", type=Path, default=Path("."))
    run.add_argument("--base", required=True, help="base git revision")
    run.add_argument("--target", required=True, help="target git revision")
    run.add_argument("--timeout", type=float, default=300)
    run.add_argument(
        "--python",
        dest="python_executable",
        help="target Python executable (or set BLASTRADIUS_PYTHON)",
    )
    run.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    run.add_argument(
        "--record",
        type=Path,
        help="write machine-readable result JSON to this path",
    )
    run.add_argument(
        "--compare",
        action="store_true",
        help="also run the full test suite for comparison",
    )
    run.add_argument(
        "--no-run",
        action="store_true",
        help="stop after selection; do not execute pytest",
    )
    return parser


def _print_result(result: PipelineResult) -> None:
    analysis = result.analysis
    selection = result.selection
    print(f"Changed: {len(analysis.files)} files (+{analysis.additions}/-{analysis.deletions})")
    print(f"Graph: {len(result.graph.nodes)} nodes, {len(result.graph.edges)} edges")
    print(f"Risks: {len(result.semantic.risks)}")
    print(f"Selected: {len(selection.selected_test_ids)}/{len(result.graph.tests)} tests")
    for test_id in selection.selected_test_ids:
        print(f"  {test_id}")
    print(f"Full-suite fallback: {selection.used_full_suite}")
    if result.selected_run is not None:
        print(f"Selected run: {'PASS' if result.selected_run.passed else 'FAIL'}")
    if result.evaluation is not None:
        evaluation = result.evaluation
        print(f"Full run: {'PASS' if evaluation.full_suite.passed else 'FAIL'}")
        print(f"Time reduction: {evaluation.execution_time_reduction:.1%}")
        if evaluation.regression_detected:
            print("Regression detected: yes")


def _tests_failed(result: PipelineResult) -> bool:
    if result.selected_run is not None:
        return not result.selected_run.passed
    if result.evaluation is not None:
        return not result.evaluation.selected.passed or not result.evaluation.full_suite.passed
    return False


def _run_payload(run) -> dict[str, object]:
    return {
        "test_ids": list(run.test_ids),
        "returncode": run.returncode,
        "passed": run.passed,
        "duration": run.duration,
        "stdout": run.stdout,
        "stderr": run.stderr,
    }


def _result_payload(result: PipelineResult, args) -> dict[str, object]:
    evaluation = result.evaluation
    selected_run = result.selected_run or (evaluation.selected if evaluation else None)
    return {
        "environment": {
            "base_revision": args.base,
            "target_revision": args.target,
            "repo": str(Path(args.repo).resolve()),
            "model": os.getenv("BLASTRADIUS_MODEL", "gemma-local"),
            "base_url": _safe_url(os.getenv("BLASTRADIUS_BASE_URL", "")),
            "python": sys.version,
            "platform": platform.platform(),
            "test_python": selected_run.command[0] if selected_run else None,
        },
        "changed_files": [file.path for file in result.analysis.files],
        "additions": result.analysis.additions,
        "deletions": result.analysis.deletions,
        "graph": {"nodes": len(result.graph.nodes), "edges": len(result.graph.edges)},
        "change_summary": getattr(result.semantic, "change_summary", ""),
        "risks": [risk.model_dump() for risk in result.semantic.risks],
        "test_recommendations": [
            recommendation.model_dump()
            for recommendation in getattr(result.semantic, "test_recommendations", ())
        ],
        "selected_test_ids": list(result.selection.selected_test_ids),
        "covered_risks": list(result.selection.covered_risks),
        "uncovered_risks": list(result.selection.uncovered_risks),
        "full_suite_fallback": result.selection.used_full_suite,
        "selected_run": _run_payload(result.selected_run) if result.selected_run else None,
        "comparison": (
            {
                "selected": _run_payload(evaluation.selected),
                "full_suite": _run_payload(evaluation.full_suite),
                "selection_ratio": evaluation.selection_ratio,
                "execution_time_reduction": evaluation.execution_time_reduction,
                "regression_detected": evaluation.regression_detected,
            }
            if evaluation
            else None
        ),
    }


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
