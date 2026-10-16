import subprocess

from blastradius.diff import analyze_patch
from blastradius.discovery import discover_dependencies


def commit(repo, message):
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


def revision(repo):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def test_blasteradius_analyzes_python_change_end_to_end(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "payment.py").write_text("def pay(amount):\n    return amount\n")
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "payment.py").write_text("def pay(amount):\n    return amount + 1\n")
    commit(tmp_path, "change payment behavior")

    analysis = analyze_patch(tmp_path, base, revision(tmp_path))

    changed = analysis.files[0]
    assert (changed.path, changed.status, changed.additions, changed.deletions) == ("payment.py", "M", 1, 1)
    assert changed.functions[0].qualname == "pay"
    assert changed.functions[0].change == "modified"
    assert "return amount + 1" in changed.source


def test_blasteradius_reports_cross_file_patch(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "account.py").write_text("def balance():\n    return 10\n")
    (tmp_path / "payment.py").write_text("from account import balance\n\ndef pay():\n    return balance()\n")
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "account.py").write_text("def balance():\n    return 0\n")
    (tmp_path / "payment.py").write_text("from account import balance\n\ndef pay():\n    return balance() - 1\n")
    commit(tmp_path, "break payment")

    analysis = analyze_patch(tmp_path, base, revision(tmp_path))

    assert {changed.path for changed in analysis.files} == {"account.py", "payment.py"}
    assert analysis.additions == analysis.deletions == 2


def test_blasteradius_reports_added_function(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".keep").write_text("")
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "feature.py").write_text("def new_feature():\n    return True\n")
    commit(tmp_path, "add feature")

    analysis = analyze_patch(tmp_path, base, revision(tmp_path))

    assert analysis.files[0].functions[0].change == "added"


def test_blasteradius_ignores_unchanged_hunk_context(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "feature.py").write_text("def existing():\n    return 1\n")
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "feature.py").write_text(
        "def existing():\n    return 1\n\ndef added():\n    return 2\n"
    )
    commit(tmp_path, "add function")

    analysis = analyze_patch(tmp_path, base, revision(tmp_path))

    assert [function.qualname for function in analysis.files[0].functions] == ["added"]


def test_dependency_discovery_connects_test_to_imported_function(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("def pay(amount):\n    return amount\n")
    (tmp_path / "test_app.py").write_text("from app import pay\n\ndef test_pay():\n    assert pay(1) == 1\n")
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "app.py").write_text("def pay(amount):\n    return amount + 1\n")
    commit(tmp_path, "change payment")

    graph = discover_dependencies(str(tmp_path), analyze_patch(tmp_path, base, revision(tmp_path)))

    assert any(edge.target == "symbol:app.pay" for edge in graph.edges_from("test:test_app.py::test_pay"))
    assert graph.tests[0].id == "test_app.py::test_pay"


def test_dependency_discovery_handles_src_package_init(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src" / "benchmark").mkdir(parents=True)
    (tmp_path / "src" / "benchmark" / "__init__.py").write_text("")
    (tmp_path / "src" / "benchmark" / "account.py").write_text(
        "def balance():\n    return 10\n"
    )
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "src" / "benchmark" / "account.py").write_text(
        "def balance():\n    return 0\n"
    )
    commit(tmp_path, "change account")

    graph = discover_dependencies(str(tmp_path), analyze_patch(tmp_path, base, revision(tmp_path)))

    assert "module:benchmark" in graph.nodes


def test_dependency_discovery_connects_call_chain_to_changed_method(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "account.py").write_text(
        "class Account:\n"
        "    def withdraw(self, amount):\n"
        "        return amount\n"
    )
    (tmp_path / "payment.py").write_text(
        "from account import Account\n\n"
        "def pay(sender: Account, amount):\n"
        "    sender.withdraw(amount)\n"
    )
    (tmp_path / "test_payment.py").write_text(
        "from account import Account\n"
        "from payment import pay\n\n"
        "def test_pay():\n"
        "    pay(Account(), 1)\n"
    )
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "account.py").write_text(
        "class Account:\n"
        "    def withdraw(self, amount):\n"
        "        return amount + 1\n"
    )
    commit(tmp_path, "change withdrawal")

    graph = discover_dependencies(str(tmp_path), analyze_patch(tmp_path, base, revision(tmp_path)))

    test_node = "test:test_payment.py::test_pay"
    assert any(
        edge.target == "symbol:account.Account.withdraw"
        for edge in graph.edges_from(test_node)
    ) or any(
        edge.target == "symbol:account.Account.withdraw"
        for edge in graph.edges_from("symbol:payment.pay")
    )


def test_dependency_discovery_ignores_known_builtin_calls(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "fees.py").write_text(
        "def fee(amount):\n"
        "    return max(1, amount // 100)\n"
    )
    commit(tmp_path, "baseline")
    base = revision(tmp_path)
    (tmp_path / "fees.py").write_text(
        "def fee(amount):\n"
        "    return max(2, amount // 100)\n"
    )
    commit(tmp_path, "change fee")

    graph = discover_dependencies(str(tmp_path), analyze_patch(tmp_path, base, revision(tmp_path)))

    assert not any("fees.fee" in unknown for unknown in graph.unknown)
