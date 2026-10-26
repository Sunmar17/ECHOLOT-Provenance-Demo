"""The enrich flow: its logic, and the provenance that falls out of running it.

Everything here except the marked integration test runs without Prefect and
without a Core, which is what the logic/orchestration split buys.
"""

import json

import httpx
import pytest
from echolot_prov.client import CoreClient
from echolot_prov.context import Context
from echolot_prov.schema import load_schema
from enrich_demo.flow import (
    CREATOR,
    RECONCILE_CONFIG,
    load_table,
    reconcile,
    run_correction,
    run_reconciliation,
)
from jsonschema import Draft202012Validator

SUBJECT = "https://demo.echolot.eu/entity/object/1138"
CURATOR = "https://orcid.org/0000-0002-1825-0097"

CONTEXT = Context.from_dict(
    {
        "service": "echolot-reconciler",
        "model": "name-match-v2",
        "config_hash": "sha256:" + "a" * 64,
        "run_id": "prefect-run-under-test",
    }
)


class FakeCore:
    """A Core that records what it was sent and never existed."""

    def __init__(self) -> None:
        self.posted: list[dict] = []

    def client(self) -> CoreClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.posted.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "snapshot": "https://demo.echolot.eu/snapshot/test",
                    "changed": True,
                    "added": 1,
                    "removed": 0,
                },
            )

        return CoreClient(
            "http://core",
            client=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="http://core"
            ),
        )


def assert_valid_envelope(envelope: dict) -> None:
    """The envelope a flow emitted must satisfy the committed contract."""
    validator = Draft202012Validator(
        load_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    errors = sorted(validator.iter_errors(envelope), key=str)
    assert not errors, [e.message for e in errors]


# ---------------------------------------------------------------- the lookup


def test_candidates_are_ranked_best_first():
    assert [c["label"] for c in reconcile("Rubens")] == [
        "Workshop of Rubens",
        "Peter Paul Rubens",
    ]


def test_an_unknown_name_has_no_candidates():
    assert reconcile("Nobody At All") == []


def test_reconciliation_is_deterministic():
    assert reconcile("Jan Brueghel") == reconcile("Jan Brueghel")


def test_the_table_declares_its_own_source_and_snapshot():
    """Whatever a source is pinned to must come from the source, not a constant."""
    table = load_table()
    assert table["_source"].startswith("https://")
    assert table["_snapshot"] == "2026-02-01"


def test_the_flow_module_makes_no_network_imports():
    """Invariant 9, checked rather than asserted in prose."""
    import enrich_demo.flow as module

    source = module.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("requests.get", "urlopen", "socket."):
        assert forbidden not in text


# --------------------------------------------------------- provenance emitted


def test_reconciliation_writes_data_and_envelope_together():
    core = FakeCore()
    result = run_reconciliation(
        SUBJECT, "Rubens", context=CONTEXT, client=core.client()
    )

    assert result["snapshot"].endswith("/test")
    assert len(core.posted) == 1, "one write per run"
    body = core.posted[0]
    assert set(body) == {"envelope", "statements"}
    assert_valid_envelope(body["envelope"])


def test_the_envelope_records_what_the_work_actually_did():
    core = FakeCore()
    run_reconciliation(SUBJECT, "Rubens", context=CONTEXT, client=core.client())
    envelope = core.posted[0]["envelope"]

    assert envelope["activity"]["type"] == "reconciliation"
    assert envelope["method"]["confidence"] == 0.71, "the candidate's own score"
    assert envelope["method"]["candidate_rank"] == 1
    threshold = RECONCILE_CONFIG["threshold"]
    assert envelope["method"]["parameters"]["threshold"] == threshold
    assert envelope["sources"][0]["snapshot"] == "2026-02-01"
    assert envelope["target"]["statements"] == [CREATOR], "derived by the comparator"


def test_the_written_value_is_the_best_candidate():
    core = FakeCore()
    run_reconciliation(SUBJECT, "Rubens", context=CONTEXT, client=core.client())
    statements = core.posted[0]["statements"]
    assert statements[CREATOR] == [{"type": "literal", "value": "Workshop of Rubens"}]


def test_finding_nothing_is_still_recorded():
    """ "We looked and found no candidate" is a real curation event."""
    core = FakeCore()
    run_reconciliation(SUBJECT, "Nobody At All", context=CONTEXT, client=core.client())

    body = core.posted[0]
    assert_valid_envelope(body["envelope"])
    assert body["statements"] == {}
    assert body["envelope"]["target"]["statements"] == []
    assert body["envelope"]["method"].get("confidence") is None


def test_correction_is_attributed_to_a_person():
    core = FakeCore()
    run_correction(
        SUBJECT, "Peter Paul Rubens", CURATOR, context=CONTEXT, client=core.client()
    )
    envelope = core.posted[0]["envelope"]

    assert_valid_envelope(envelope)
    assert envelope["activity"]["type"] == "validation"
    assert envelope["validation"]["state"] == "human_corrected"
    assert envelope["validation"]["by"] == CURATOR

    people = [a for a in envelope["agents"] if a["type"] == "person"]
    assert [a["id"] for a in people] == [CURATOR]
    assert [a["role"] for a in people] == ["validated"]


def test_correction_writes_the_corrected_value():
    core = FakeCore()
    run_correction(
        SUBJECT, "Peter Paul Rubens", CURATOR, context=CONTEXT, client=core.client()
    )
    assert core.posted[0]["statements"][CREATOR] == [
        {"type": "literal", "value": "Peter Paul Rubens"}
    ]


# ----------------------------------------------------------------- the phase


@pytest.mark.slow
def test_both_real_flows_yield_a_two_link_chain_with_different_agents():
    """Phase 4's "Done when", run through actual Prefect flows.

    Slow: Prefect starts and stops a temporary server per flow run. Marked so
    `pytest -m "not slow"` stays fast, but it runs by default because this is
    the criterion the phase is judged on.
    """
    import app.main as core_main
    import enrich_demo.flow as flow_module
    from app.store import Store
    from fastapi.testclient import TestClient

    core_main.store = Store()
    http = TestClient(core_main.app)
    flow_module.make_client = lambda _url: CoreClient("http://testserver", client=http)

    first = flow_module.reconcile_person("Rubens", SUBJECT)
    second = flow_module.correct_attribution(SUBJECT, "Peter Paul Rubens", CURATOR)

    snapshots = http.get("/history", params={"subject": SUBJECT}).json()["snapshots"]
    assert len(snapshots) == 2
    assert snapshots[0]["snapshot"] == second["snapshot"]
    assert snapshots[0]["derived_from"] == first["snapshot"]

    newest, oldest = snapshots
    # Each snapshot carries the executing service plus anyone else involved.
    # The correction names a person; the reconciliation names only software.
    assert CURATOR in newest["agents"], "the curator corrected it"
    assert "https://demo.echolot.eu/agent/echolot-curator-ui" in newest["agents"]
    assert oldest["agents"] == ["https://demo.echolot.eu/agent/echolot-reconciler"], (
        "the service reconciled it, with no human involved"
    )
    assert CURATOR not in oldest["agents"]
    assert newest["validation_state"] == "human_corrected"

    past = http.get(
        "/reconstruct", params={"subject": SUBJECT, "at": snapshots[1]["generated_at"]}
    ).json()
    assert [s["object"]["value"] for s in past["statements"]] == ["Workshop of Rubens"]


@pytest.mark.slow
def test_the_activity_id_is_the_real_prefect_run_id():
    """Ambient capture, proven: the provenance names the orchestrated run."""
    import uuid

    import enrich_demo.flow as flow_module

    core = FakeCore()
    flow_module.make_client = lambda _url: core.client()
    flow_module.reconcile_person("Rubens", SUBJECT)

    activity_id = core.posted[0]["envelope"]["activity"]["id"]
    uuid.UUID(activity_id)  # raises unless Prefect supplied a real run id
