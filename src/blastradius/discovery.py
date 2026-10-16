"""Lightweight dependency discovery for Python repositories."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath

from .diff import PatchAnalysis


_IGNORED_UNKNOWN_CALLS = frozenset({
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "len",
    "max",
    "min",
    "sum",
    "all",
    "any",
    "sorted",
    "isinstance",
    "super",
})


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class TestCase:
    id: str
    node: str
    source: str


@dataclass(frozen=True)
class DependencyGraph:
    nodes: tuple[str, ...]
    edges: tuple[Edge, ...]
    tests: tuple[TestCase, ...]
    unknown: tuple[str, ...]

    def edges_from(self, node: str) -> tuple[Edge, ...]:
        return tuple(edge for edge in self.edges if edge.source == node)

    def edges_to(self, node: str) -> tuple[Edge, ...]:
        return tuple(edge for edge in self.edges if edge.target == node)

    def node_for_function(self, path: str, qualname: str) -> str:
        for module in _module_candidates(path):
            candidate = f"symbol:{module}.{qualname}"
            if candidate in self.nodes:
                return candidate
        return f"symbol:{_module_candidates(path)[0]}.{qualname}"


def discover_dependencies(repo: str, analysis: PatchAnalysis) -> DependencyGraph:
    """Build a conservative Python dependency graph at analysis target revision."""
    files = _python_files(repo, analysis.target_revision)
    module_names = _module_names(files)
    nodes: set[str] = set()
    edges: set[Edge] = set()
    tests: list[TestCase] = []
    unknown: set[str] = set()

    for path, source in files.items():
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            unknown.add(f"syntax:{path}")
            continue
        module = _module_name(path, module_names)
        module_node = f"module:{module}"
        nodes.add(module_node)
        definitions = _definitions(tree, module)
        nodes.update(definitions.values())
        imports = _imports(tree, module, module_names)
        for target in imports.values():
            edges.add(Edge(module_node, target, "import"))

        test_functions = _test_functions(path, tree)
        tests.extend(TestCase(f"{path}::{name}", f"test:{path}::{name}", source) for name, _ in test_functions)
        for name, function in test_functions:
            test_node = f"test:{path}::{name}"
            nodes.add(test_node)
            _walk_body(
                function,
                test_node,
                module,
                definitions,
                imports,
                edges,
                unknown,
                path,
            )

        if not test_functions:
            for qualname, function in _definition_bodies(tree):
                _walk_body(
                    function,
                    definitions[qualname],
                    module,
                    definitions,
                    imports,
                    edges,
                    unknown,
                    path,
                )

    return DependencyGraph(tuple(sorted(nodes)), tuple(sorted(edges, key=lambda e: (e.source, e.target, e.kind))), tuple(tests), tuple(sorted(unknown)))


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _python_files(repo: str, revision: str) -> dict[str, str]:
    paths = _git(repo, "ls-tree", "-r", "--name-only", revision).splitlines()
    return {
        path: subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for path in paths
        if path.endswith(".py")
    }


def _module_names(files: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for path in files:
        parts = PurePosixPath(path).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for index in range(len(parts)):
            names.add(".".join(parts[index:]))
    return names


def _module_name(path: str, names: set[str]) -> str:
    candidates = _module_candidates(path)
    return next((candidate for candidate in candidates if candidate in names), candidates[0])


def _module_candidates(path: str) -> list[str]:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return [
        ".".join(parts[index:])
        for index in range(len(parts))
        if parts[index] != "src"
        and not (parts[index] == "benchmark" and index == 0)
    ]


def _definitions(tree: ast.Module, module: str) -> dict[str, str]:
    definitions: dict[str, str] = {}

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}.{node.name}" if prefix else node.name
                definitions[qualname] = f"symbol:{module}.{qualname}"
                visit(node.body, qualname)

    visit(tree.body)
    return definitions


def _definition_bodies(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    bodies: list[tuple[str, ast.AST]] = []

    def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{node.name}" if prefix else node.name
                bodies.append((qualname, node))
                visit(node.body, qualname)
            elif isinstance(node, ast.ClassDef):
                visit(node.body, node.name if not prefix else f"{prefix}.{node.name}")

    visit(tree.body)
    return bodies


def _imports(tree: ast.Module, module: str, names: set[str]) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                alias = item.asname or item.name.split(".")[0]
                imports[alias] = f"module:{item.name}"
        elif isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import(node, module, names)
            if not imported_module:
                continue
            for item in node.names:
                if item.name == "*":
                    imports["*"] = f"module:{imported_module}"
                else:
                    imports[item.asname or item.name] = f"symbol:{imported_module}.{item.name}"
    return imports


def _resolve_import(node: ast.ImportFrom, module: str, names: set[str]) -> str | None:
    if node.level:
        prefix = module.split(".")[:-1]
        prefix = prefix[: max(0, len(prefix) - node.level + 1)]
        candidate = ".".join((*prefix, *(node.module or "").split("."))).strip(".")
    else:
        candidate = (node.module or "").strip(".")
    return candidate if candidate in names else None


def _test_functions(path: str, tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    result = []
    if "/test" not in f"/{path}" and not path.startswith("test"):
        return result
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            result.append((node.name, node))
    return result


def _walk_body(
    tree: ast.AST,
    source: str,
    module: str,
    definitions: dict[str, str],
    imports: dict[str, str],
    edges: set[Edge],
    unknown: set[str],
    path: str,
) -> None:
    variables = _argument_types(tree, imports)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            target = _call_target(node.value.func, module, definitions, imports)
            if target and target.startswith("symbol:"):
                for assignment in node.targets:
                    for name in _assigned_names(assignment):
                        variables[name] = target
        if not isinstance(node, ast.Call):
            continue
        target = _call_target(node.func, module, definitions, imports, variables)
        if target:
            edges.add(Edge(source, target, "call"))
        elif not isinstance(node.func, ast.Name) or node.func.id not in _IGNORED_UNKNOWN_CALLS:
            unknown.add(f"{source}:{path}:{node.lineno}")


def _assigned_names(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(name for item in node.elts for name in _assigned_names(item))
    return ()


def _argument_types(tree: ast.AST, imports: dict[str, str]) -> dict[str, str]:
    if not isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {}
    variables: dict[str, str] = {}
    for argument in (*tree.args.posonlyargs, *tree.args.args, *tree.args.kwonlyargs):
        if isinstance(argument.annotation, ast.Name):
            target = imports.get(argument.annotation.id)
            if target and target.startswith("symbol:"):
                variables[argument.arg] = target
    return variables


def _call_target(
    node: ast.expr,
    module: str,
    definitions: dict[str, str],
    imports: dict[str, str],
    variables: dict[str, str] | None = None,
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in imports:
            return imports[node.id]
        return definitions.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if variables and node.value.id in variables:
            return f"{variables[node.value.id]}.{node.attr}"
        imported = imports.get(node.value.id)
        if imported and imported.startswith("module:"):
            return f"symbol:{imported.removeprefix('module:')}.{node.attr}"
    return None
