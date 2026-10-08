"""The committed schema is the contract: it must not drift, and it must be consistent.

Two separate guarantees:

* Drift, for the current version only. The models only ever generate the current
  version, so a historical file cannot be regenerated for comparison.
* Consistency, across every committed version. This is what covers the
  historical files the drift test cannot reach.
"""

import json
import re

import pytest
from echolot_prov.models import SCHEMA_VERSION
from echolot_prov.schema import (
    ID_BASE,
    FrozenSchemaError,
    available_versions,
    generate,
    load_schema,
    schema_filename,
    schema_id,
    schemas_dir,
    serialise,
    write,
)

VERSIONS = available_versions()


def test_current_version_is_committed():
    assert SCHEMA_VERSION in VERSIONS


def test_committed_schema_matches_freshly_generated():
    """Schema drift check, current version only."""
    committed = (schemas_dir() / schema_filename()).read_text(encoding="utf-8")
    assert committed == serialise(generate()), (
        "committed schema differs from the models; "
        "run `uv run python -m echolot_prov.schema`"
    )


@pytest.mark.parametrize("version", VERSIONS)
def test_version_is_consistent_across_filename_id_and_const(version: str):
    """Filename version, `$id` version segment and `schema_version` const agree."""
    schema = load_schema(version)

    assert schema["$id"] == schema_id(version)
    assert schema["$id"].startswith(f"{ID_BASE}/")
    assert schema["$id"].rsplit("/", 1)[-1] == version

    const = schema["properties"]["schema_version"]["const"]
    assert const == version, f"{schema_filename(version)} declares const {const!r}"


@pytest.mark.parametrize("version", VERSIONS)
def test_version_filename_is_well_formed(version: str):
    assert re.fullmatch(r"\d+\.\d+", version), f"unexpected version {version!r}"


def test_schema_forbids_unknown_fields():
    """`extra="forbid"` must reach the generated contract (invariant 3)."""
    assert load_schema()["additionalProperties"] is False


def test_software_agent_conditional_is_in_the_schema():
    """The conditional must be structural, not only a Pydantic validator."""
    schema = load_schema()
    branches = schema["properties"]["agents"]["items"]["oneOf"]
    assert len(branches) == 2

    required = {
        name: set(schema["$defs"][name]["required"])
        for name in ("SoftwareAgent", "PersonAgent")
    }
    assert {"model", "config_hash"} <= required["SoftwareAgent"]
    assert not {"model", "config_hash"} & required["PersonAgent"]


def test_regeneration_is_a_no_op():
    path, changed = write()
    assert path.name == schema_filename()
    assert not changed, "regenerating an unchanged schema must not rewrite it"


def test_guard_refuses_to_overwrite_a_differing_committed_file(tmp_path, monkeypatch):
    """A released version file is frozen (invariant 1)."""
    import echolot_prov.schema as schema_module

    staged = tmp_path / "schemas"
    staged.mkdir()
    target = staged / schema_filename()

    tampered = generate()
    tampered["x-hand-edit"] = "a well-meaning hand edit"
    target.write_text(serialise(tampered), encoding="utf-8")

    monkeypatch.setattr(schema_module, "schemas_dir", lambda: staged)

    with pytest.raises(FrozenSchemaError):
        schema_module.write()

    # The guard must not have touched the file.
    assert json.loads(target.read_text(encoding="utf-8"))["x-hand-edit"]

    # force is the documented pre-release escape hatch: it restores the file to
    # exactly what the models generate.
    _, changed = schema_module.write(force=True)
    assert changed
    assert target.read_text(encoding="utf-8") == serialise(generate())
