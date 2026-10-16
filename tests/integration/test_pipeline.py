import json
import subprocess

from blastradius.pipeline import run_pipeline


class FakeModel:
    def generate(self, prompt: str) -> str:
        return json.dumps(
            {
                "change_summary": "payment changed",
                "risks": [{"id": "R1", "description": "payment result"}],
                "test_recommendations": [
                    {
                        "test_id": "test_app.py::test_pay",
                        "covers": ["R1"],
                        "reason": "direct call",
                    }
                ],
            }
        )


def commit(repo, message):
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def revision(repo):
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def test_pipeline_wires_analysis_to_selection(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("def pay(amount):\n    return amount\n")
    (tmp_path / "test_app.py").write_text(
        "from app import pay\n\n"
        "def test_pay():\n    assert pay(1) == 1\n"
    )
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "app.py").write_text("def pay(amount):\n    return amount + 1\n")
    commit(tmp_path, "change")
    target = revision(tmp_path)

    result = run_pipeline(tmp_path, base, target, FakeModel(), execute=False)

    assert result.semantic.change_summary == "payment changed"
    assert result.selection.selected_test_ids == ("test_app.py::test_pay",)
    assert result.evaluation is None
    assert result.selected_run is None
