"""Phase 0 placeholder: the emitter package and its module skeleton import."""

import echolot_prov


def test_package_imports():
    assert echolot_prov.__version__ == "0.1.0"


def test_module_skeleton_imports():
    from echolot_prov import client, context, emitter, models, schema

    assert all(m is not None for m in (models, schema, context, emitter, client))
