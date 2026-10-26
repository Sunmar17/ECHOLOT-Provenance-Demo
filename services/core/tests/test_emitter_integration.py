"""The emitter writing through to the real Core.

Lives on Core's side of the workspace deliberately. ``echolot-core`` depends on
``echolot-prov``, so Core's tests may use the emitter; the reverse would invert
that dependency and stop the library's suite running on its own.
"""

from datetime import UTC, datetime

import pytest
from echolot_prov.client import CoreClient, CoreValidationError
from echolot_prov.context import Context
from echolot_prov.emitter import record_activity

CREATOR = "http://purl.org/dc/terms/creator"
SUBJECT = "https://demo.echolot.eu/entity/person/1"

CONTEXT = Context.from_dict(
    {
        "service": "echolot-reconciler",
        "model": "name-match-v2",
        "config_hash": "sha256:" + "a" * 64,
        "run_id": "prefect-run-1",
    }
)


def an_envelope(subject: str = SUBJECT):
    moments = iter(
        [
            datetime(2026, 3, 4, 9, 12, 0, tzinfo=UTC),
            datetime(2026, 3, 4, 9, 12, 2, tzinfo=UTC),
        ]
    )
    with record_activity(
        "reconciliation", context=CONTEXT, subject=subject, clock=lambda: next(moments)
    ) as act:
        act.method("wikidata-lookup", confidence=0.93)
        act.changed({}, {CREATOR: ["Peter Paul Rubens"]})
    return act.envelope


def test_writes_through_to_the_real_core(client):
    """A real envelope down the real write path into the Phase 2 Core.

    TestClient is an httpx.Client, so the app is handed straight to CoreClient:
    no server, no port, and the same code path a deployed producer would use.
    """
    core = CoreClient("http://testserver", client=client)

    result = core.write(SUBJECT, {CREATOR: ["Peter Paul Rubens"]}, an_envelope())

    assert result["changed"] is True
    assert result["added"] == 1

    history = client.get("/history", params={"subject": SUBJECT}).json()["snapshots"]
    assert len(history) == 1
    assert history[0]["snapshot"] == result["snapshot"]
    assert "echolot-reconciler" in history[0]["description"]
    assert history[0]["statements"] == [CREATOR]


def test_the_real_core_rejects_an_envelope_the_emitter_did_not_build(client):
    """Core's 422 reaches the producer as a typed error with the reason intact."""
    core = CoreClient("http://testserver", client=client)

    from echolot_prov.models import StatementRef

    # A well-formed envelope that names a property the payload does not carry.
    # It passes schema validation, so what rejects it is Core's integrity check.
    envelope = an_envelope()
    tampered = envelope.model_copy(deep=True)
    tampered.target.statements = [
        StatementRef(property="http://purl.org/dc/terms/undeclared")
    ]

    with pytest.raises(CoreValidationError) as caught:
        core.write(SUBJECT, {CREATOR: ["Rubens"]}, tampered)

    assert caught.value.errors[0]["path"] == "/statements"
