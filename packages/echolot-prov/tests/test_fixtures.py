"""Every valid fixture passes and every invalid fixture fails, on both paths.

Both paths means Pydantic and ``jsonschema`` against the committed schema file.
A fixture that failed on only one path would mean the library and the contract
disagree, which is the thing invariant 1 exists to prevent.
"""

import pytest
from conftest import invalid_cases, valid_cases
from echolot_prov.models import Envelope
from echolot_prov.schema import load_schema
from jsonschema import Draft202012Validator
from pydantic import ValidationError

VALID = valid_cases()
INVALID = invalid_cases()


def _validator() -> Draft202012Validator:
    # The format checker is required for parity: without it, `format: date-time`
    # is advisory and a malformed timestamp would pass jsonschema while failing
    # Pydantic. See docs/revisionfile.md.
    return Draft202012Validator(
        load_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def test_corpus_is_populated():
    assert len(VALID) >= 6, "plan requires 6 to 8 valid fixtures"
    assert len(INVALID) >= 6


def test_every_activity_type_is_covered():
    covered = {case["activity"]["type"] for _, case in VALID}
    assert covered == {
        "reconciliation",
        "enrichment",
        "validation",
        "import",
        "deletion",
    }


@pytest.mark.parametrize("name,payload", VALID, ids=[n for n, _ in VALID])
def test_valid_fixture_passes_pydantic(name: str, payload: dict):
    Envelope.model_validate(payload)


@pytest.mark.parametrize("name,payload", VALID, ids=[n for n, _ in VALID])
def test_valid_fixture_passes_jsonschema(name: str, payload: dict):
    errors = sorted(_validator().iter_errors(payload), key=str)
    assert not errors, f"{name} rejected: {[e.message for e in errors]}"


@pytest.mark.parametrize("name,payload", INVALID, ids=[n for n, _ in INVALID])
def test_invalid_fixture_fails_pydantic(name: str, payload: dict):
    with pytest.raises(ValidationError):
        Envelope.model_validate(payload)


@pytest.mark.parametrize("name,payload", INVALID, ids=[n for n, _ in INVALID])
def test_invalid_fixture_fails_jsonschema(name: str, payload: dict):
    assert list(_validator().iter_errors(payload)), f"{name} wrongly accepted"
