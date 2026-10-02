"""Phase 0 placeholder: the Core module skeleton imports."""

import app


def test_package_imports():
    assert app.__version__ == "0.1.0"


def test_module_skeleton_imports():
    from app import main, namespaces, provenance, reconstruct, store, validation

    modules = (main, validation, provenance, store, reconstruct, namespaces)
    assert all(m is not None for m in modules)
