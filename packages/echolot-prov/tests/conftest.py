"""Shared fixture-corpus helpers.

The corpus under ``packages/echolot-prov/fixtures/`` is the shared test corpus
for both the emitter and Core, so these helpers locate it by walking up from
this file rather than by assuming a working directory.
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def valid_cases() -> list[tuple[str, dict]]:
    return [(p.stem, _load(p)) for p in sorted((FIXTURES / "valid").glob("*.json"))]


def invalid_cases() -> list[tuple[str, dict]]:
    return [(p.stem, _load(p)) for p in sorted((FIXTURES / "invalid").glob("*.json"))]


def validate_against_committed_schema(envelope: object) -> None:
    """Assert a built envelope passes the committed JSON Schema, not just Pydantic.

    An envelope the emitter produces must satisfy the same contract file Core
    enforces. Checking only Pydantic here would test the library against itself
    and miss exactly the divergence the fixture corpus exists to catch.
    """
    from echolot_prov.schema import load_schema
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(
        load_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    payload = envelope.model_dump(mode="json", exclude_none=True)
    errors = sorted(validator.iter_errors(payload), key=str)
    assert not errors, [e.message for e in errors]
