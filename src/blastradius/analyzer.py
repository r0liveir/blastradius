"""Structured semantic analysis of a patch."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from .diff import PatchAnalysis
from .discovery import DependencyGraph


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    unmapped: bool = False


class TestRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str = Field(min_length=1)
    covers: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class SemanticAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_summary: str = Field(min_length=1)
    risks: list[Risk] = Field(default_factory=list)
    test_recommendations: list[TestRecommendation] = Field(default_factory=list)


class ModelClient(Protocol):
    def generate(self, prompt: str) -> str: ...


class AnalysisValidationError(ValueError):
    """Model output is valid JSON but violates repository references."""


@dataclass(frozen=True)
class OpenAICompatibleClient:
    """Client for OpenAI-compatible endpoints, including local Ollama."""

    base_url: str
    model: str
    api_key: str | None = None
    timeout: int = 60

    @classmethod
    def from_env(cls) -> OpenAICompatibleClient:
        return cls(
            base_url=os.getenv("BLASTRADIUS_BASE_URL", "http://localhost:11434/v1"),
            model=os.getenv("BLASTRADIUS_MODEL", "gemma-local"),
            api_key=os.getenv("BLASTRADIUS_API_KEY"),
        )

    def generate(self, prompt: str) -> str:
        url = urljoin(f"{self.base_url.rstrip('/')}/", "chat/completions")
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            body = json.load(response)
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("model response has no text candidate") from error


def analyze_semantics(
    analysis: PatchAnalysis, graph: DependencyGraph, client: ModelClient
) -> SemanticAnalysis:
    """Ask a model for risks and validate every recommended test reference."""
    response = client.generate(build_prompt(analysis, graph))
    result = SemanticAnalysis.model_validate_json(_json_text(response))
    _validate_references(result, graph)
    return result


def build_prompt(analysis: PatchAnalysis, graph: DependencyGraph) -> str:
    changed = [
        {
            "path": file.path,
            "status": file.status,
            "additions": file.additions,
            "deletions": file.deletions,
            "functions": [
                {"name": function.qualname, "change": function.change}
                for function in file.functions
            ],
            "source": file.source,
        }
        for file in analysis.files
    ]
    edges = [edge.__dict__ for edge in graph.edges]
    tests = [{"id": test.id, "source": test.source} for test in graph.tests]
    return """You are a regression-risk analyst. Return JSON only.

Identify behavioral risks caused by this patch. Recommend only tests from the
provided catalog. Do not invent test IDs, commands, fixes, or code. Evidence
must refer to the supplied patch or source.

When a patch changes a comparison, boundary, validation rule, or arithmetic
condition, explicitly look for catalog tests exercising equality and limit
values. Prefer those tests over ordinary non-boundary examples.

Required JSON shape:
{"change_summary": "...", "risks": [{"id": "R1", "description": "...", "evidence": ["..."], "unmapped": false}], "test_recommendations": [{"test_id": "...", "covers": ["R1"], "reason": "..."}]}

Every risk must be covered by at least one catalog test. If no existing test
can cover a risk, set unmapped=true; never invent a test ID.

PATCH:
""" + json.dumps({"files": changed, "edges": edges, "tests": tests}, indent=2)


def _json_text(response: str) -> str:
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return text


def _validate_references(result: SemanticAnalysis, graph: DependencyGraph) -> None:
    risk_ids = {risk.id for risk in result.risks}
    if len(risk_ids) != len(result.risks):
        raise AnalysisValidationError("duplicate risk id")
    test_ids = {test.id for test in graph.tests}
    for recommendation in result.test_recommendations:
        if recommendation.test_id not in test_ids:
            raise AnalysisValidationError(f"unknown test id: {recommendation.test_id}")
        unknown_risks = set(recommendation.covers) - risk_ids
        if unknown_risks:
            raise AnalysisValidationError(f"unknown risk ids: {sorted(unknown_risks)}")
    covered = {risk_id for recommendation in result.test_recommendations for risk_id in recommendation.covers}
    for risk in result.risks:
        if not risk.unmapped and risk.id not in covered:
            raise AnalysisValidationError(
                f"risk {risk.id} has no test recommendation; mark unmapped=true"
            )
