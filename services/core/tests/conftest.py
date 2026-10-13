"""Test helpers for Core, driven by the shared fixture corpus.

The corpus under ``packages/echolot-prov/fixtures/`` is the same one the emitter
tests use. A fixture Core treats differently from the library is a contract bug,
which is exactly what sharing it is meant to surface.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "packages" / "echolot-prov" / "fixtures"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def valid_envelopes() -> list[tuple[str, dict]]:
    return [(p.stem, _load(p)) for p in sorted((FIXTURES / "valid").glob("*.json"))]


def invalid_envelopes() -> list[tuple[str, dict]]:
    return [(p.stem, _load(p)) for p in sorted((FIXTURES / "invalid").glob("*.json"))]


def payload_for(envelope: dict, value: str = "example value") -> dict[str, Any]:
    """Synthesise a data payload that matches an envelope's declared statements.

    Fixtures carry only the envelope, so a write needs values invented for the
    properties it names. Strict integrity checking means these must line up with
    ``Target.statements`` exactly.
    """
    return {
        predicate: [{"type": "literal", "value": f"{value} for {predicate}"}]
        for predicate in envelope.get("target", {}).get("statements", [])
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """A Core with its own empty in-memory store per test."""
    from app import main
    from app.store import Store

    monkeypatch.setattr(main, "store", Store())
    return TestClient(main.app)


@pytest.fixture
def store(client: TestClient):
    """The store behind the client fixture."""
    from app import main

    return main.store
