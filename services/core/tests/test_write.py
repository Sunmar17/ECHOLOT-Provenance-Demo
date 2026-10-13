"""POST /write: the single write path, and what it refuses.

Covers "Done when" (a) and (b): every valid fixture produces exactly one
snapshot, and every invalid one is rejected with no change to any graph.
"""

import pytest
from conftest import invalid_envelopes, payload_for, valid_envelopes

VALID = valid_envelopes()
INVALID = invalid_envelopes()


@pytest.mark.parametrize("name,envelope", VALID, ids=[n for n, _ in VALID])
def test_valid_fixture_produces_exactly_one_snapshot(name, envelope, client, store):
    subject = __import__("pyoxigraph").NamedNode(envelope["target"]["subject"])

    response = client.post(
        "/write", json={"envelope": envelope, "statements": payload_for(envelope)}
    )

    assert response.status_code == 200, response.json()
    assert len(store.snapshots_of(subject)) == 1
    assert response.json()["snapshot"] in {s.value for s in store.snapshots_of(subject)}


@pytest.mark.parametrize("name,envelope", INVALID, ids=[n for n, _ in INVALID])
def test_invalid_fixture_is_rejected_and_writes_nothing(name, envelope, client, store):
    before = store.counts()

    response = client.post(
        "/write", json={"envelope": envelope, "statements": payload_for(envelope)}
    )

    assert response.status_code == 422, response.json()
    assert response.json()["errors"], "a rejection must say why"
    assert store.counts() == before, "a rejected write must leave every graph untouched"


def test_empty_store_starts_clean(store):
    assert all(count == 0 for count in store.counts().values())


def test_a_write_with_no_statements_still_records_provenance(client, store):
    """An empty delta is a real curation event: "we looked and found nothing".

    `reconciliation-minimal.json` is a valid fixture with no statements, so
    invariant 4's "no provenance without data" is about atomicity rather than
    forbidding an empty delta.
    """
    import pyoxigraph as ox

    subject = "urn:uuid:empty-delta-case"
    envelope = {**VALID[0][1], "target": {"subject": subject}}

    response = client.post("/write", json={"envelope": envelope, "statements": {}})

    assert response.status_code == 200, response.json()
    assert response.json()["changed"] is False
    assert len(store.snapshots_of(ox.NamedNode(subject))) == 1
    assert store.counts()["https://demo.echolot.eu/graph/data"] == 0


def test_payload_must_match_envelope_target_statements(client, store):
    """A payload Core cannot vouch for is refused, not silently recorded."""
    envelope = next(e for _, e in VALID if e["target"].get("statements"))
    before = store.counts()

    response = client.post(
        "/write",
        json={
            "envelope": envelope,
            "statements": {
                **payload_for(envelope),
                "http://purl.org/dc/terms/undeclared": [
                    {"type": "literal", "value": "not in the envelope"}
                ],
            },
        },
    )

    assert response.status_code == 422
    assert "do not match" in response.json()["errors"][0]["message"]
    assert store.counts() == before


def test_unknown_schema_version_is_a_clean_422(client):
    response = client.post(
        "/write", json={"envelope": {"schema_version": "9.9"}, "statements": {}}
    )
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["path"] == "/schema_version"
    assert "unknown schema_version" in error["message"]


def test_missing_envelope_is_rejected(client):
    """No code path writes data without an envelope (invariant 2)."""
    response = client.post("/write", json={"statements": {}})
    assert response.status_code == 422


def test_rewriting_the_same_value_is_an_empty_delta(client, store):
    """Intent and actual change are different things, and both get recorded."""
    envelope = next(e for _, e in VALID if len(e["target"].get("statements", [])) == 1)
    payload = payload_for(envelope)

    first = client.post("/write", json={"envelope": envelope, "statements": payload})
    assert first.json()["changed"] is True

    envelope_again = {
        **envelope,
        "activity": {**envelope["activity"], "id": "second-run-same-value"},
    }
    second = client.post(
        "/write", json={"envelope": envelope_again, "statements": payload}
    )

    assert second.status_code == 200
    assert second.json()["changed"] is False, "nothing actually changed in the store"
    assert second.json()["snapshot"] != first.json()["snapshot"], "still a new snapshot"
