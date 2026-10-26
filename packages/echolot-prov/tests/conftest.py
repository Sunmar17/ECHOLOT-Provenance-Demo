"""Shared fixture-corpus helpers.

The corpus is organised by contract version:

    fixtures/1.0/valid/  fixtures/1.0/invalid/
    fixtures/1.1/valid/  fixtures/1.1/invalid/

Two rules follow from that, and they are different rules:

* **Every** fixture, of any version, is checked against **its own** committed
  schema. This is what proves Core can still accept envelopes a producer sent
  before upgrading.
* **Only** current-version fixtures are checked through Pydantic. The models
  express one version, the current one, so an older fixture failing there is
  correct rather than a defect.
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def versions() -> list[str]:
    """Every contract version the corpus covers, ascending."""
    return sorted(
        (p.name for p in FIXTURES.iterdir() if p.is_dir()),
        key=lambda v: [int(part) for part in v.split(".")],
    )


def _cases(kind: str, version: str | None = None) -> list[tuple[str, str, dict]]:
    wanted = [version] if version else versions()
    return [
        (v, p.stem, _load(p))
        for v in wanted
        for p in sorted((FIXTURES / v / kind).glob("*.json"))
    ]


def valid_cases(version: str | None = None) -> list[tuple[str, str, dict]]:
    """(version, name, envelope) for every valid fixture."""
    return _cases("valid", version)


def invalid_cases(version: str | None = None) -> list[tuple[str, str, dict]]:
    """(version, name, envelope) for every invalid fixture."""
    return _cases("invalid", version)


def ids_for(cases: list[tuple[str, str, dict]]) -> list[str]:
    return [f"{version}-{name}" for version, name, _ in cases]


def validate_against_committed_schema(
    envelope: object, version: str | None = None
) -> None:
    """Assert an envelope passes a committed JSON Schema, not just Pydantic.

    An envelope the emitter produces must satisfy the same contract file Core
    enforces. Checking only Pydantic here would test the library against itself
    and miss exactly the divergence the fixture corpus exists to catch.
    """
    from echolot_prov.schema import load_schema
    from jsonschema import Draft202012Validator

    payload = (
        envelope
        if isinstance(envelope, dict)
        else envelope.model_dump(mode="json", exclude_none=True)
    )
    validator = Draft202012Validator(
        load_schema(version or payload.get("schema_version")),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    errors = sorted(validator.iter_errors(payload), key=str)
    assert not errors, [e.message for e in errors]
