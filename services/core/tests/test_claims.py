"""Claims: values with an identity of their own (Phase 1.1b).

A subject links to its claims with ``echolot:statement``; each claim holds its
own values. These tests build claims up through ``POST /write``, the way a
producer does, and check what Core stores, records and reconstructs.
"""

import copy

import pyoxigraph as ox
import pytest
from conftest import FIXTURES, _load

CREATOR = "http://purl.org/dc/terms/creator"
MATCHED = "http://purl.org/dc/terms/references"
TITLE = "http://purl.org/dc/terms/title"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
STATEMENT = "https://demo.echolot.eu/ns#statement"
AFFECTED = "https://demo.echolot.eu/ns#affectedStatement"
SUBJECT = "https://demo.echolot.eu/sample/item/91005"
FORSYTHE = "http://www.wikidata.org/entity/Q706362"

BASE = {
    "schema_version": "1.1",
    "activity": {
        "id": "run-import",
        "type": "import",
        "started_at": "2026-10-29T09:00:00+00:00",
        "ended_at": "2026-10-29T09:00:01+00:00",
    },
    "agents": [
        {
            "type": "software",
            "role": "executed",
            "id": "echolot-importer",
            "model": "record-import-v1",
            "config_hash": "sha256:aaa",
        }
    ],
    "target": {"subject": SUBJECT, "statements": []},
    "method": {"name": "csv-import"},
    "validation": {"state": "auto_accepted"},
}


def literal(value):
    return {"type": "literal", "value": value}


def uri(value):
    return {"type": "uri", "value": value}


def envelope(run, kind, ended, refs):
    out = copy.deepcopy(BASE)
    out["activity"].update(
        {"id": run, "type": kind, "started_at": ended, "ended_at": ended}
    )
    out["target"]["statements"] = refs
    return out


def imported(client):
    """An artifact with a title, a label and two creators, each its own claim."""
    env = envelope(
        "run-import",
        "import",
        "2026-10-29T09:00:01+00:00",
        [{"property": TITLE}, {"property": LABEL}, {"property": CREATOR}],
    )
    response = client.post(
        "/write",
        json={
            "envelope": env,
            "statements": {
                TITLE: [literal("Probenvideo")],
                LABEL: [literal("Probenvideo")],
            },
            "claims": [
                {"values": {CREATOR: [literal("Forsythe, William")]}},
                {"values": {CREATOR: [literal("Okafor, D.")]}},
            ],
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


def matched(client, claim):
    """Reconciliation puts the match on the one claim it belongs to."""
    env = envelope(
        "run-reconcile",
        "reconciliation",
        "2026-10-29T09:00:05+00:00",
        [{"property": MATCHED, "statement": claim}],
    )
    response = client.post(
        "/write",
        json={
            "envelope": env,
            "statements": {},
            "claims": [{"statement": claim, "values": {MATCHED: [uri(FORSYTHE)]}}],
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


def data(store):
    return {
        (q.subject.value, q.predicate.value, q.object.value)
        for q in store._store.quads_for_pattern(
            None, None, None, ox.NamedNode("https://demo.echolot.eu/graph/data")
        )
    }


# -------------------------------------------------------------------- write


def test_new_claims_are_minted_linked_and_returned_in_order(client, store):
    written = imported(client)
    forsythe, okafor = written["claims"]

    triples = data(store)
    assert (SUBJECT, STATEMENT, forsythe) in triples
    assert (SUBJECT, STATEMENT, okafor) in triples
    assert (forsythe, CREATOR, "Forsythe, William") in triples
    assert (okafor, CREATOR, "Okafor, D.") in triples
    assert not any(s == SUBJECT and p == CREATOR for s, p, _ in triples), (
        "a creator lives on its claim, not on the record"
    )


def test_the_match_sits_on_the_claim_it_belongs_to(client, store):
    forsythe, okafor = imported(client)["claims"]
    matched(client, forsythe)

    triples = data(store)
    assert (forsythe, MATCHED, FORSYTHE) in triples
    assert not any(s == okafor and p == MATCHED for s, p, _ in triples)
    assert not any(s == SUBJECT and p == MATCHED for s, p, _ in triples)


def test_a_snapshot_points_at_the_claims_it_changed(client, store):
    forsythe, _ = imported(client)["claims"]
    written = matched(client, forsythe)

    affected = store.properties_of(ox.NamedNode(written["snapshot"]))[AFFECTED]
    assert [n.value for n in affected] == [forsythe]


def test_new_claim_iris_are_deterministic(client, store, monkeypatch):
    """A projection rebuild replays the same envelopes and must mint the same IRIs."""
    first = imported(client)["claims"]

    from app import main
    from app.store import Store

    monkeypatch.setattr(main, "store", Store())
    assert imported(client)["claims"] == first


# ----------------------------------------------------------------- refusals


ELSEWHERE = "https://demo.echolot.eu/claim/elsewhere"
MATCH = {MATCHED: [uri(FORSYTHE)]}


@pytest.mark.parametrize(
    ("claims", "words"),
    [
        ([{"statement": ELSEWHERE, "values": MATCH}], "not a claim of this subject"),
        ([{"values": {}}], "at least one value"),
        ([{"statement": "not an iri", "values": MATCH}], "absolute IRI"),
        ("not a list", "must be a list"),
    ],
)
def test_a_bad_claim_is_refused_and_nothing_is_written(client, store, claims, words):
    imported(client)
    before = store.counts()
    env = envelope(
        "run-x",
        "reconciliation",
        "2026-10-29T09:00:09+00:00",
        [{"property": MATCHED, "statement": ELSEWHERE}],
    )
    response = client.post(
        "/write", json={"envelope": env, "statements": {}, "claims": claims}
    )
    assert response.status_code == 422
    assert words in response.json()["errors"][0]["message"]
    assert store.counts() == before, "a refused write leaves all three graphs alone"


def test_a_claim_the_envelope_does_not_name_is_refused(client, store):
    forsythe, okafor = imported(client)["claims"]
    before = store.counts()
    env = envelope(
        "run-x",
        "reconciliation",
        "2026-10-29T09:00:09+00:00",
        [{"property": MATCHED, "statement": forsythe}],
    )
    response = client.post(
        "/write",
        json={
            "envelope": env,
            "statements": {},
            "claims": [{"statement": okafor, "values": {MATCHED: [uri(FORSYTHE)]}}],
        },
    )
    assert response.status_code == 422
    assert "do not match" in response.json()["errors"][0]["message"]
    assert store.counts() == before


def test_a_named_claim_needs_to_exist(client):
    """The 1.1 corpus names claims that were never created, so a cold write fails."""
    env = _load(FIXTURES / "1.1" / "valid" / "two-claims-one-property.json")
    claims = [
        {"statement": ref["statement"], "values": {ref["property"]: [literal("x")]}}
        for ref in env["target"]["statements"]
    ]
    response = client.post(
        "/write", json={"envelope": env, "statements": {}, "claims": claims}
    )
    assert response.status_code == 422
    assert "not a claim of this subject" in response.json()["errors"][0]["message"]


def test_a_1_1_envelope_without_claims_writes_as_before(client):
    env = _load(FIXTURES / "1.1" / "valid" / "claim-without-identity.json")
    statements = {
        ref["property"]: [literal("value")] for ref in env["target"]["statements"]
    }
    response = client.post("/write", json={"envelope": env, "statements": statements})
    assert response.status_code == 200, response.json()
    assert response.json()["claims"] == []


# --------------------------------------------------------------------- read


def test_history_groups_each_change_by_its_claim(client):
    forsythe, okafor = imported(client)["claims"]
    matched(client, forsythe)

    newest, oldest = client.get("/history", params={"subject": SUBJECT}).json()[
        "snapshots"
    ]
    assert newest["changes"] == [
        {
            "statement": forsythe,
            "property": MATCHED,
            "added": [uri(FORSYTHE)],
            "removed": [],
            "new_claim": False,
        }
    ]
    created = {c["statement"] for c in oldest["changes"] if c["new_claim"]}
    assert created == {forsythe, okafor}
    plain = {c["property"] for c in oldest["changes"] if c["statement"] is None}
    assert plain == {TITLE, LABEL}


def test_reconstruction_includes_claims_and_undoes_a_later_match(client):
    forsythe, okafor = imported(client)["claims"]
    matched(client, forsythe)

    def state(at):
        params = {"subject": SUBJECT, "at": at}
        rows = client.get("/reconstruct", params=params).json()["statements"]
        return {(r["statement"], r["predicate"], r["object"]["value"]) for r in rows}

    after_import = state("2026-10-29T09:00:02+00:00")
    assert (forsythe, CREATOR, "Forsythe, William") in after_import
    assert (okafor, CREATOR, "Okafor, D.") in after_import
    assert (None, TITLE, "Probenvideo") in after_import
    assert not any(p == MATCHED for _, p, _ in after_import)

    now = state("2026-10-29T10:00:00+00:00")
    assert (forsythe, MATCHED, FORSYTHE) in now

    before_anything = state("2026-10-29T08:00:00+00:00")
    assert before_anything == set()


def test_subjects_carry_their_labels(client):
    imported(client)
    body = client.get("/subjects").json()
    assert body["labels"] == {SUBJECT: "Probenvideo"}
