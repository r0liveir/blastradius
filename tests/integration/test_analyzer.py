import json
import subprocess
from io import BytesIO
from unittest.mock import patch

import pytest

from blastradius.analyzer import (
    AnalysisValidationError,
    OpenAICompatibleClient,
    analyze_semantics,
)
from blastradius.diff import analyze_patch
from blastradius.discovery import discover_dependencies


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.prompt = ""

    def generate(self, prompt):
        self.prompt = prompt
        return self.response


def commit(repo, message):
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


def revision(repo):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def context(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("def pay(amount):\n    return amount\n")
    (tmp_path / "test_app.py").write_text("from app import pay\n\ndef test_pay():\n    assert pay(1) == 1\n")
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "app.py").write_text("def pay(amount):\n    return amount + 1\n")
    commit(tmp_path, "change payment")
    analysis = analyze_patch(tmp_path, base, revision(tmp_path))
    return analysis, discover_dependencies(str(tmp_path), analysis)


def test_semantic_analysis_validates_catalog_references(tmp_path):
    analysis, graph = context(tmp_path)
    model = FakeModel(
        json.dumps(
            {
                "change_summary": "Payment amount changed",
                "risks": [{"id": "R1", "description": "Payment result changes", "evidence": ["app.py"]}],
                "test_recommendations": [
                    {
                        "test_id": "test_app.py::test_pay",
                        "covers": ["R1"],
                        "reason": "Directly calls pay",
                    }
                ],
            }
        )
    )

    result = analyze_semantics(analysis, graph, model)

    assert result.test_recommendations[0].test_id == "test_app.py::test_pay"
    assert "app.py" in model.prompt


def test_semantic_analysis_rejects_invented_tests(tmp_path):
    analysis, graph = context(tmp_path)
    model = FakeModel(
        '{"change_summary":"change","risks":[],"test_recommendations":['
        '{"test_id":"test_app.py::missing","covers":[],"reason":"x"}]}'
    )

    with pytest.raises(AnalysisValidationError, match="unknown test id"):
        analyze_semantics(analysis, graph, model)


def test_semantic_analysis_rejects_unmapped_actionable_risk(tmp_path):
    analysis, graph = context(tmp_path)
    model = FakeModel(
        '{"change_summary":"change","risks":[{"id":"R1","description":"risk"}],'
        '"test_recommendations":[]}'
    )

    with pytest.raises(AnalysisValidationError, match="mark unmapped=true"):
        analyze_semantics(analysis, graph, model)


def test_openai_compatible_client_uses_configured_provider():
    response = BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
    response.__enter__ = lambda: response
    response.__exit__ = lambda *args: None

    with patch("blastradius.analyzer.urlopen", return_value=response) as request:
        result = OpenAICompatibleClient(
            "http://localhost:11434/v1", "gemma-local", api_key=None
        ).generate("return JSON")

    assert result == "{}"
    assert request.call_args.args[0].full_url == "http://localhost:11434/v1/chat/completions"
