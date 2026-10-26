"""Every fixture is checked against its own contract version.

Two different rules, for a reason. A committed schema file must keep accepting
the envelopes it always accepted, for as long as Core carries it. The Pydantic
models express only the current version, so an older fixture failing there is
the design working, not a defect.
"""

import pytest
from conftest import (
    ids_for,
    invalid_cases,
    valid_cases,
    validate_against_committed_schema,
    versions,
)
from echolot_prov.models import SCHEMA_VERSION, Envelope
from echolot_prov.schema import available_versions, load_schema
from jsonschema import Draft202012Validator
from pydantic import ValidationError

ALL_VALID = valid_cases()
ALL_INVALID = invalid_cases()
CURRENT_VALID = valid_cases(SCHEMA_VERSION)
CURRENT_INVALID = invalid_cases(SCHEMA_VERSION)


def _validator(version: str) -> Draft202012Validator:
    # The format checker is required for parity: without it, `format: date-time`
    # is advisory and a malformed timestamp would pass jsonschema while failing
    # Pydantic. See docs/revisionfile.md entry 9.
    return Draft202012Validator(
        load_schema(version), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


# ------------------------------------------------------------------ coverage


def test_every_committed_version_has_fixtures():
    """A schema nobody tests is a schema nobody can trust."""
    assert set(versions()) == set(available_versions())


def test_the_corpus_is_populated():
    assert len(CURRENT_VALID) >= 6
    assert len(CURRENT_INVALID) >= 6


def test_every_activity_type_is_covered():
    covered = {case["activity"]["type"] for _, _, case in CURRENT_VALID}
    assert covered == {
        "reconciliation",
        "enrichment",
        "validation",
        "import",
        "deletion",
    }


# --------------------------------------------- every version, its own schema


@pytest.mark.parametrize("version,name,payload", ALL_VALID, ids=ids_for(ALL_VALID))
def test_a_valid_fixture_passes_its_own_schema(version, name, payload):
    errors = sorted(_validator(version).iter_errors(payload), key=str)
    assert not errors, f"{name} rejected: {[e.message for e in errors]}"


@pytest.mark.parametrize("version,name,payload", ALL_INVALID, ids=ids_for(ALL_INVALID))
def test_an_invalid_fixture_fails_its_own_schema(version, name, payload):
    assert list(_validator(version).iter_errors(payload)), f"{name} wrongly accepted"


def test_an_older_envelope_is_still_accepted_by_its_own_schema():
    """The point of keeping old schema files. A producer need not upgrade at once."""
    older = [v for v in versions() if v != SCHEMA_VERSION]
    assert older, "there should be at least one superseded version by now"
    for version in older:
        for _, name, payload in valid_cases(version):
            assert not list(_validator(version).iter_errors(payload)), name


# ------------------------------------------------- current version, Pydantic


@pytest.mark.parametrize(
    "version,name,payload", CURRENT_VALID, ids=ids_for(CURRENT_VALID)
)
def test_a_current_valid_fixture_passes_pydantic(version, name, payload):
    Envelope.model_validate(payload)


@pytest.mark.parametrize(
    "version,name,payload", CURRENT_INVALID, ids=ids_for(CURRENT_INVALID)
)
def test_a_current_invalid_fixture_fails_pydantic(version, name, payload):
    with pytest.raises(ValidationError):
        Envelope.model_validate(payload)


def test_an_older_fixture_does_not_pass_the_current_models():
    """By design. The models express one version; the schema files express all."""
    older = [v for v in versions() if v != SCHEMA_VERSION]
    for version in older:
        for _, _name, payload in valid_cases(version):
            with pytest.raises(ValidationError):
                Envelope.model_validate(payload)


# --------------------------------------------------- what 1.1 exists to allow


def test_two_claims_of_one_property_can_be_told_apart():
    """The reason for schema 1.1.

    In 1.0 a run that changed two creators left a record nobody could read
    back. Now each claim is named.
    """
    case = next(c for _, name, c in CURRENT_VALID if name == "two-claims-one-property")
    envelope = Envelope.model_validate(case)

    statements = envelope.target.statements
    assert len(statements) == 2
    assert {s.property for s in statements} == {"http://purl.org/dc/terms/creator"}
    assert len({s.statement for s in statements}) == 2, "two distinct identities"
    validate_against_committed_schema(case)


def test_a_new_claim_needs_no_identity():
    """A producer asserting something new has no identifier for it yet."""
    case = next(c for _, name, c in CURRENT_VALID if name == "claim-without-identity")
    envelope = Envelope.model_validate(case)
    assert envelope.target.statements[0].statement is None
