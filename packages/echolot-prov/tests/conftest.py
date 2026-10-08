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
