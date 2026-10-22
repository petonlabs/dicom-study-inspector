"""Guard module boundaries and observable contracts, not private implementation steps."""

import ast
from pathlib import Path

import numpy as np

from dicom_experiment.acquisition import Source
from dicom_experiment.pixels import DisplayTransform

ROOT = Path(__file__).parent
ALLOWED_DEPENDENCIES = {
    "__init__": set(),
    "config": set(),
    "artifacts": {"config"},
    "metadata": {"config"},
    "structure": set(),
    "pixels": {"config"},
    "transport": {"config"},
    "acquisition": {"artifacts", "config", "transport"},
    "comparison": {"artifacts", "config", "metadata", "pixels", "structure"},
    "verification": {"artifacts", "config", "pixels"},
}


def test_package_has_one_way_dependencies_and_module_level_imports():
    for file in (ROOT / "dicom_experiment").glob("*.py"):
        module = ast.parse(file.read_text())
        top_level_imports = {
            id(n) for n in module.body if isinstance(n, (ast.Import, ast.ImportFrom))
        }
        for node in ast.walk(module):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            assert id(node) in top_level_imports, (
                f"Nested import: {file.name}:{node.lineno}"
            )
            if isinstance(node, ast.ImportFrom):
                assert node.module not in ("study_compare", "osteosarc_source")
                if node.level:
                    assert node.module in ALLOWED_DEPENDENCIES[file.stem]
    cli = ast.parse((ROOT / "study_compare.py").read_text())
    assert all(
        not isinstance(n, (ast.Import, ast.ImportFrom))
        for func in cli.body
        if isinstance(func, ast.FunctionDef)
        for n in ast.walk(func)
    )


def test_display_contract_has_known_quantization_and_inverse():
    pixels = np.array([-100, 0, 100], dtype=np.int16)
    transform = DisplayTransform.from_pixels(pixels, "MONOCHROME2")
    shown = transform.render(pixels)
    np.testing.assert_array_equal(shown, [0, 127, 255])
    np.testing.assert_allclose(
        transform.reconstruct(shown), [-100, -0.3921568627451, 100]
    )
    inverted = DisplayTransform.from_pixels(pixels, "MONOCHROME1")
    np.testing.assert_array_equal(inverted.render(pixels), [255, 128, 0])


def test_acquisition_accepts_a_network_free_transport(tmp_path):
    class FakeTransport:
        def metadata(self, url):
            return b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated></ListBucketResult>'

        def content_length(self, url):
            raise AssertionError("No source download authorized in this test")

        def download(self, url, destination, expected_size):
            raise AssertionError("No source download authorized in this test")

    (tmp_path / "catalog").mkdir()
    source = Source(tmp_path, {"listings": []}, transport=FakeTransport())
    assert source.listing("dicom/") == ([], [], False)
