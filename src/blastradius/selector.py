"""Deterministic test selection from semantic risks and dependency evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .analyzer import SemanticAnalysis
from .diff import PatchAnalysis
from .discovery import DependencyGraph


@dataclass(frozen=True)
class SelectionResult:
    selected_test_ids: tuple[str, ...]
    covered_risks: tuple[str, ...]
    uncovered_risks: tuple[str, ...]
    mandatory_test_ids: tuple[str, ...]
    used_full_suite: bool


def select_tests(
    analysis: PatchAnalysis,
    graph: DependencyGraph,
    semantic: SemanticAnalysis,
    *,
    test_costs: dict[str, float] | None = None,
    mandatory_test_ids: set[str] | None = None,
) -> SelectionResult:
    """Select the cheapest deterministic test subset covering model risks."""
    all_tests = {test.id for test in graph.tests}
    costs = test_costs or {}
    risks = {risk.id for risk in semantic.risks}
    recommendations = {
        recommendation.test_id: set(recommendation.covers) & risks
        for recommendation in semantic.test_recommendations
    }
    mandatory = _directly_affected_tests(analysis, graph) | (mandatory_test_ids or set())
    mandatory &= all_tests
    selected = set(mandatory)
    covered = set().union(*(recommendations.get(test_id, set()) for test_id in selected))

    if _uncertain_affected_callers(analysis, graph):
        return SelectionResult(
            tuple(sorted(all_tests)),
            tuple(sorted(covered)),
            tuple(sorted(risks - covered)),
            tuple(sorted(mandatory)),
            True,
        )

    remaining = risks - covered
    while remaining:
        candidates = [
            test_id
            for test_id, test_risks in recommendations.items()
            if test_id not in selected and test_risks & remaining and test_id in all_tests
        ]
        if not candidates:
            return SelectionResult(
                tuple(sorted(all_tests)),
                tuple(sorted(covered)),
                tuple(sorted(remaining)),
                tuple(sorted(mandatory)),
                True,
            )
        chosen = min(
            candidates,
            key=lambda test_id: (
                -(len(recommendations[test_id] & remaining) / max(costs.get(test_id, 1.0), 0.000001)),
                costs.get(test_id, 1.0),
                test_id,
            ),
        )
        selected.add(chosen)
        covered.update(recommendations[chosen])
        remaining = risks - covered

    return SelectionResult(
        tuple(sorted(selected)),
        tuple(sorted(covered)),
        (),
        tuple(sorted(mandatory)),
        False,
    )


def _directly_affected_tests(analysis: PatchAnalysis, graph: DependencyGraph) -> set[str]:
    changed_nodes = set()
    for file in analysis.files:
        for function in file.functions:
            changed_nodes.add(graph.node_for_function(function.path, function.qualname))
        if not file.functions:
            changed_nodes.add(f"module:{_module_name(file.path)}")

    affected: set[str] = set()
    for test in graph.tests:
        reachable = _reachable(graph, test.node)
        if reachable & changed_nodes:
            affected.add(test.id)
    return affected


def _reachable(graph: DependencyGraph, source: str) -> set[str]:
    seen: set[str] = set()
    pending = [source]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(edge.target for edge in graph.edges_from(node))
    return seen


def _uncertain_affected_callers(analysis: PatchAnalysis, graph: DependencyGraph) -> bool:
    """Fallback when an unknown call sits on a path to a changed symbol."""
    changed_nodes = {
        graph.node_for_function(function.path, function.qualname)
        for file in analysis.files
        for function in file.functions
    }
    if not changed_nodes:
        return False

    callers: set[str] = set(changed_nodes)
    pending = list(changed_nodes)
    while pending:
        node = pending.pop()
        for edge in graph.edges_to(node):
            if edge.source not in callers:
                callers.add(edge.source)
                pending.append(edge.source)
    return any(
        any(unknown.startswith(f"{caller}:") for unknown in graph.unknown)
        for caller in callers
    )


def _module_name(path: str) -> str:
    parts = path.removesuffix(".py").replace("/", ".").split(".")
    if parts[-1] == "__init__":
        parts.pop()
    for index, part in enumerate(parts):
        if part in {"src", "benchmark"}:
            continue
        return ".".join(parts[index:])
    return ".".join(parts)
