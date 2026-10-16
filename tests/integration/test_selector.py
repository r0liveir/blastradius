from blastradius.analyzer import Risk, SemanticAnalysis
from blastradius.analyzer import TestRecommendation as Recommendation
from blastradius.diff import ChangedFile, ChangedFunction, PatchAnalysis
from blastradius.discovery import DependencyGraph, Edge
from blastradius.discovery import TestCase as GraphTestCase
from blastradius.selector import select_tests


def context():
    changed = ChangedFile(
        "app.py", "M", 1, 1, (), (ChangedFunction("pay", "pay", "app.py", 1, 2, "modified"),), ""
    )
    analysis = PatchAnalysis("base", "target", (changed,), "")
    tests = (
        GraphTestCase("tests/test_pay.py::test_boundary", "test:boundary", ""),
        GraphTestCase("tests/test_pay.py::test_fee", "test:fee", ""),
        GraphTestCase("tests/test_pay.py::test_both", "test:both", ""),
    )
    graph = DependencyGraph(
        nodes=("symbol:app.pay", "test:boundary", "test:fee", "test:both"),
        edges=(Edge("test:boundary", "symbol:app.pay", "call"),),
        tests=tests,
        unknown=(),
    )
    semantic = SemanticAnalysis(
        change_summary="payment changed",
        risks=(Risk(id="R1", description="boundary"), Risk(id="R2", description="fee")),
        test_recommendations=(
            Recommendation(
                test_id="tests/test_pay.py::test_boundary", covers=["R1"], reason="boundary"
            ),
            Recommendation(
                test_id="tests/test_pay.py::test_fee", covers=["R2"], reason="fee"
            ),
            Recommendation(
                test_id="tests/test_pay.py::test_both", covers=["R1", "R2"], reason="both"
            ),
        ),
    )
    return analysis, graph, semantic


def test_selector_covers_risks_with_cheapest_set():
    analysis, graph, semantic = context()

    result = select_tests(
        analysis,
        graph,
        semantic,
        test_costs={
            "tests/test_pay.py::test_boundary": 1,
            "tests/test_pay.py::test_fee": 1,
            "tests/test_pay.py::test_both": 1,
        },
    )

    assert result.selected_test_ids == (
        "tests/test_pay.py::test_both",
        "tests/test_pay.py::test_boundary",
    )
    assert result.covered_risks == ("R1", "R2")
    assert not result.used_full_suite


def test_selector_falls_back_to_full_suite_when_risk_uncovered():
    analysis, graph, semantic = context()
    semantic = semantic.model_copy(
        update={
            "test_recommendations": [
                Recommendation(
                    test_id="tests/test_pay.py::test_boundary", covers=["R1"], reason="boundary"
                )
            ]
        }
    )

    result = select_tests(analysis, graph, semantic)

    assert result.selected_test_ids == tuple(sorted(test.id for test in graph.tests))
    assert result.uncovered_risks == ("R2",)
    assert result.used_full_suite


def test_selector_keeps_directly_affected_test_mandatory():
    analysis, graph, semantic = context()

    result = select_tests(analysis, graph, semantic)

    assert "tests/test_pay.py::test_boundary" in result.mandatory_test_ids


def test_selector_falls_back_when_affected_call_chain_is_unknown():
    analysis, _, semantic = context()
    graph = DependencyGraph(
        nodes=("symbol:app.pay", "symbol:app.wrapper", "test:wrapper", "test:fee"),
        edges=(
            Edge("symbol:app.wrapper", "symbol:app.pay", "call"),
            Edge("test:wrapper", "symbol:app.wrapper", "call"),
        ),
        tests=(
            GraphTestCase("tests/test_wrapper.py::test_wrapper", "test:wrapper", ""),
            GraphTestCase("tests/test_fee.py::test_fee", "test:fee", ""),
        ),
        unknown=("symbol:app.wrapper:app.py:3",),
    )

    result = select_tests(analysis, graph, semantic)

    assert result.used_full_suite
    assert result.selected_test_ids == tuple(sorted(test.id for test in graph.tests))
