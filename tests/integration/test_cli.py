import json
from types import SimpleNamespace

from blastradius import main
from blastradius.runner import TestRun as RunResult


def test_json_output_and_exit_code_report_selected_failure(monkeypatch, capsys, tmp_path):
    failed = RunResult(("tests/test_app.py::test_value",), ("pytest",), 1, 0.1, "", "failed")
    result = SimpleNamespace(
        analysis=SimpleNamespace(files=(), additions=0, deletions=0),
        graph=SimpleNamespace(nodes=(), edges=()),
        semantic=SimpleNamespace(risks=()),
        selection=SimpleNamespace(
            selected_test_ids=failed.test_ids,
            covered_risks=(),
            uncovered_risks=(),
            used_full_suite=False,
        ),
        selected_run=failed,
        evaluation=None,
    )
    monkeypatch.setattr("blastradius.run_pipeline", lambda *args, **kwargs: result)

    exit_code = main(
        [
            "run",
            "--repo",
            str(tmp_path),
            "--base",
            "base",
            "--target",
            "target",
            "--format",
            "json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_run"]["passed"] is False
    assert payload["environment"]["base_revision"] == "base"
