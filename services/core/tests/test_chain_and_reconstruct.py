"""The snapshot chain and reconstruction.

Covers "Done when" (c) and (d): a second write to the same subject yields a
two-link chain, and reconstruction at a time between the two writes returns the
pre-second-write state.
"""

import pyoxigraph as ox
import pytest

CREATOR = "http://purl.org/dc/terms/creator"
SUBJECT = "https://demo.echolot.eu/entity/object/42"

FIRST_RUN = {
    "schema_version": "1.0",
    "activity": {
        "id": "run-1",
        "type": "reconciliation",
        "started_at": "2026-03-01T10:00:00+00:00",
        "ended_at": "2026-03-01T10:00:05+00:00",
    },
    "agents": [
        {
            "type": "software",
            "role": "executed",
            "id": "echolot-reconciler",
            "model": "name-match-v2",
            "config_hash": "sha256:aaa",
        }
    ],
    "target": {"subject": SUBJECT, "statements": [CREATOR]},
    "method": {"name": "wikidata-lookup", "confidence": 0.7},
    "validation": {"state": "auto_accepted"},
}

SECOND_RUN = {
    **FIRST_RUN,
    "activity": {
        "id": "run-2",
        "type": "validation",
        "started_at": "2026-03-05T09:00:00+00:00",
        "ended_at": "2026-03-05T09:00:30+00:00",
    },
    "agents": [
        {
            "type": "person",
            "role": "validated",
            "id": "https://orcid.org/0000-0002-1825-0097",
        }
    ],
    "method": {"name": "curator-review"},
    "validation": {
        "state": "human_corrected",
        "by": "https://orcid.org/0000-0002-1825-0097",
        "at": "2026-03-05T09:00:28+00:00",
    },
}

BETWEEN = "2026-03-03T00:00:00+00:00"


def _write(client, envelope, value):
    return client.post(
        "/write",
        json={
            "envelope": envelope,
            "statements": {CREATOR: [{"type": "literal", "value": value}]},
        },
    )


@pytest.fixture
def two_writes(client):
    first = _write(client, FIRST_RUN, "Workshop of Rubens")
    second = _write(client, SECOND_RUN, "Peter Paul Rubens")
    assert first.status_code == 200 and second.status_code == 200
    return first.json(), second.json()


def test_second_write_yields_a_two_link_chain(two_writes, store):
    first, second = two_writes
    chain = store.chain(ox.NamedNode(SUBJECT))

    assert [s.value for s in chain] == [second["snapshot"], first["snapshot"]]
    assert store.parent_of(chain[0]).value == first["snapshot"]
    assert store.parent_of(chain[1]) is None


def test_history_reports_the_chain_newest_first(two_writes, client):
    snapshots = client.get("/history", params={"subject": SUBJECT}).json()["snapshots"]

    assert len(snapshots) == 2
    assert snapshots[0]["generated_at"] > snapshots[1]["generated_at"]
    assert snapshots[0]["derived_from"] == snapshots[1]["snapshot"]
    assert snapshots[0]["agents"] == ["https://orcid.org/0000-0002-1825-0097"]
    assert "curator-review" in snapshots[0]["description"]
    assert snapshots[0]["validation_state"] == "human_corrected"


def test_current_state_is_the_corrected_value(two_writes, client):
    now = client.get(
        "/reconstruct", params={"subject": SUBJECT, "at": "2026-12-31T00:00:00+00:00"}
    ).json()
    assert [s["object"]["value"] for s in now["statements"]] == ["Peter Paul Rubens"]


def test_reconstruction_between_writes_returns_the_earlier_state(two_writes, client):
    past = client.get("/reconstruct", params={"subject": SUBJECT, "at": BETWEEN}).json()

    assert [s["object"]["value"] for s in past["statements"]] == ["Workshop of Rubens"]
    assert past["statements"][0]["predicate"] == CREATOR


def test_reconstruction_before_the_first_write_is_empty(two_writes, client):
    past = client.get(
        "/reconstruct", params={"subject": SUBJECT, "at": "2026-01-01T00:00:00+00:00"}
    ).json()
    assert past["statements"] == []


def test_reconstruction_does_not_mutate_the_store(two_writes, client, store):
    before = store.counts()
    for at in (BETWEEN, "2026-01-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00"):
        client.get("/reconstruct", params={"subject": SUBJECT, "at": at})
    assert store.counts() == before


def test_snapshot_iris_are_deterministic(client, store):
    """Invariant 10: a rebuild must reproduce the same identifiers."""
    from app.namespaces import mint_snapshot

    first = _write(client, FIRST_RUN, "Workshop of Rubens").json()["snapshot"]
    assert first == mint_snapshot(SUBJECT, "run-1", "2026-03-01T10:00:05+00:00").value


def test_unknown_subject_has_no_history(client):
    body = client.get(
        "/history", params={"subject": "https://demo.echolot.eu/entity/nope"}
    ).json()
    assert body["snapshots"] == []


def test_bad_timestamp_is_rejected(client):
    response = client.get(
        "/reconstruct", params={"subject": SUBJECT, "at": "last Tuesday"}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/at"
