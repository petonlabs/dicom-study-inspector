"""Executable size, import and dependency rules for this PR's implementation/tests."""

import ast
from pathlib import Path

import numpy as np
import pytest

from comparison_test_support import MemoryTransport
from dicom_experiment.acquisition import Source
from dicom_experiment.pixels import DisplayTransform

ROOT = Path(__file__).parent
PACKAGE = ROOT / "dicom_experiment"
ALLOWED = {
    "__init__": set(),
    "config": set(),
    "artifacts": {"config"},
    "models": {"config"},
    "metadata": {"config"},
    "temporal": set(),
    "spatial": set(),
    "structure": {"spatial", "temporal"},
    "decoding": {"config"},
    "pixels": {"decoding"},
    "transport": {"config"},
    "catalog": {"artifacts", "config"},
    "eligibility": {"config"},
    "acquisition_state": {"artifacts", "catalog", "config"},
    "downloads": {"acquisition_state", "artifacts", "catalog", "eligibility"},
    "selection": {"config", "models"},
    "acquisition": {
        "acquisition_state",
        "artifacts",
        "catalog",
        "config",
        "downloads",
        "models",
        "selection",
        "transport",
    },
    "exporting": {"artifacts", "config", "decoding", "pixels"},
    "measurements": {"config", "metadata", "pixels"},
    "reporting": {"artifacts", "config", "structure"},
    "comparison": {
        "artifacts",
        "config",
        "decoding",
        "exporting",
        "measurements",
        "metadata",
        "models",
        "reporting",
        "structure",
    },
    "verification": {"artifacts", "config", "decoding", "pixels"},
}
OFFLINE_FORBIDDEN = {
    "acquisition",
    "acquisition_state",
    "catalog",
    "downloads",
    "selection",
    "transport",
}
NETWORK_MODULES = {"urllib", "http", "requests", "httpx", "aiohttp", "socket"}
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
IMPORTS = (ast.Import, ast.ImportFrom)


LEGACY_MODULES = {
    "inspector.py",
    "test_inspector.py",
    "synthetic.py",
    "echoview_inspect_v2.py",
    "make_preaccess_fixture.py",
    "verify_preaccess_outputs.py",
}


def scoped_files():
    additions = [path for path in ROOT.glob("*.py") if path.name not in LEGACY_MODULES]
    return sorted(PACKAGE.rglob("*.py")) + sorted(additions)


def oversized_functions(path, tree):
    failures = []
    for node in ast.walk(tree):
        if isinstance(node, FUNCTIONS):
            length = node.end_lineno - node.lineno + 1
            if length > 15:
                failures.append(f"{path.name}:{node.lineno} {node.name}: {length} lines")
    return failures


def import_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        assert node.level == 1, "Parent-relative imports escape the package boundary"
        return ["dicom_experiment." + (node.module or alias.name) for alias in node.names]
    if node.module == "dicom_experiment":
        return [node.module + "." + alias.name for alias in node.names]
    return [node.module or ""]


def dependencies(tree):
    names = {
        name for node in ast.walk(tree) if isinstance(node, IMPORTS) for name in import_names(node)
    }
    internal = {name.split(".")[1] for name in names if name.startswith("dicom_experiment.")}
    external = {name.split(".")[0] for name in names if not name.startswith("dicom_experiment.")}
    assert not {"study_compare", "osteosarc_source"}.intersection(external)
    return internal, external


def reachable(graph, current, trail=()):
    assert current not in trail, "Dependency cycle: " + " -> ".join((*trail, current))
    found = {current}
    for dependency in graph[current]:
        found.update(reachable(graph, dependency, (*trail, current)))
    return found


def test_all_new_functions_have_at_most_fifteen_physical_lines():
    failures = []
    for path in scoped_files():
        failures.extend(oversized_functions(path, ast.parse(path.read_text())))
    assert not failures, "\n".join(failures)


def test_imports_are_module_level_and_statements_are_not_packed():
    for path in scoped_files():
        tree = ast.parse(path.read_text())
        top_level = {id(node) for node in tree.body if isinstance(node, IMPORTS)}
        lines = []
        for node in ast.walk(tree):
            if isinstance(node, IMPORTS):
                assert id(node) in top_level, f"Nested import: {path.name}:{node.lineno}"
            if isinstance(node, ast.stmt) and not is_protocol_stub(node):
                lines.append(node.lineno)
        assert len(lines) == len(set(lines)), f"Packed statements: {path.name}"


def test_dependency_boundaries_and_offline_transitive_closure():
    modules = {
        path.stem: dependencies(ast.parse(path.read_text())) for path in PACKAGE.glob("*.py")
    }
    assert modules.keys() == ALLOWED.keys()
    graph = {name: internal for name, (internal, _) in modules.items()}
    for name, internal in graph.items():
        assert internal <= ALLOWED[name], (
            f"Forbidden dependencies: {name}: {internal - ALLOWED[name]}"
        )
        reachable(graph, name)
    for root in ("comparison", "verification"):
        closure = reachable(graph, root)
        assert not closure.intersection(OFFLINE_FORBIDDEN)
        assert not any(modules[name][1].intersection(NETWORK_MODULES) for name in closure)


@pytest.mark.parametrize(
    "code",
    [
        "from .transport import ArchiveTransport",
        "import dicom_experiment.transport",
        "from dicom_experiment import transport",
        "from dicom_experiment.transport import ArchiveTransport",
    ],
)
def test_dependency_detection_covers_all_import_forms(code):
    assert dependencies(ast.parse(code))[0] == {"transport"}


def test_dependency_cycles_are_rejected():
    with pytest.raises(AssertionError, match="Dependency cycle"):
        reachable({"a": {"b"}, "b": {"a"}}, "a")


def test_size_check_covers_nested_async_functions_and_signatures():
    code = "def outer():\n    async def inner(\n        value,\n    ):\n" + "        pass\n" * 13
    violations = oversized_functions(Path("example.py"), ast.parse(code))
    assert len(violations) == 2
    assert "inner: 16 lines" in violations[1]


def test_display_contract_has_known_quantization_and_inverse():
    pixels = np.array([-100, 0, 100], dtype=np.int16)
    transform = DisplayTransform.from_pixels(pixels, "MONOCHROME2")
    shown = transform.render(pixels)
    np.testing.assert_array_equal(shown, [0, 127, 255])
    np.testing.assert_allclose(transform.reconstruct(shown), [-100, -0.3921568627451, 100])
    inverted = DisplayTransform.from_pixels(pixels, "MONOCHROME1")
    np.testing.assert_array_equal(inverted.render(pixels), [255, 128, 0])


def test_acquisition_accepts_a_network_free_transport(tmp_path):
    listing = b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated></ListBucketResult>'
    transport = MemoryTransport(listing=listing)
    (tmp_path / "catalog").mkdir()
    source = Source(tmp_path, {"listings": []}, transport=transport)
    assert source.listing("dicom/") == ([], [], False)
    assert source.listing("dicom/") == ([], [], False)
    assert transport.calls == ["metadata"]


def is_protocol_stub(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    )
