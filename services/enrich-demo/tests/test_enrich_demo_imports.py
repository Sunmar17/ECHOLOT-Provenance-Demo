"""Phase 0 placeholder: the enrich-demo module skeleton imports."""

import enrich_demo
from enrich_demo import flow


def test_package_imports():
    assert enrich_demo.__version__ == "0.1.0"


def test_flow_module_imports():
    assert flow is not None
