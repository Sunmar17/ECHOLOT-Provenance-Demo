"""The three-stage workflow: each stage emits its own provenance.

The fast tests call the stage functions directly, with a dict-built Context and
a fake Core. No Prefect, no server. The one slow test runs the real flow.
"""

import json

import httpx
import pytest
from echolot_prov.client import CoreClient
from echolot_prov.context import Context
from echolot_prov.schema import load_schema
from enrich_demo.flow import (
    CREATOR,
    QUALITY,
    load_table,
    missing_properties,
    reconcile,
    stage_enrich,
    stage_quality_check,
    stage_reconcile,
)
from jsonschema import Draft202012Validator

SUBJECT = "https://demo.echolot.eu/entity/object/1"


def context(service: str, task_id: str | None = None, run_id: str | None = None):
    return Context.from_dict(
        {
            "service": service,
            "model": "m",
            "config_hash": "sha256:" + "a" * 64,
            "run_id": run_id,
            "task_id": task_id,
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
                    "snapshot": "https://demo.echolot.eu/snapshot/"
                    + str(len(self.posted)),
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


def assert_valid(envelope: dict) -> None:
    validator = Draft202012Validator(
        load_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    assert not list(validator.iter_errors(envelope))


# ------------------------------------------------------------- pure helpers


def test_missing_properties_finds_the_gaps():
    required = ["a", "b", "c"]
    assert missing_properties({"a": ["x"]}, required) == ["b", "c"]
    assert missing_properties({"a": ["x"], "b": ["y"], "c": ["z"]}, required) == []


def test_a_candidate_carries_facts_for_the_enrichment_stage():
    """Stage 2 has nothing to do unless the table holds facts."""
    best = reconcile("Rubens")[0]
    assert best["facts"], "stage 2 needs facts to copy"
    assert "http://purl.org/dc/terms/created" in best["facts"]


# ------------------------------------------------------------ single stages


def test_stage_one_writes_the_match_and_its_source():
    core = FakeCore()
    result = stage_reconcile(
        SUBJECT, "Rubens", context=context("echolot-reconciler"), client=core.client()
    )

    envelope = core.posted[0]["envelope"]
    assert_valid(envelope)
    assert envelope["activity"]["type"] == "reconciliation"
    assert envelope["method"]["confidence"] == 0.71
    assert result["candidate"]["label"] == "Workshop of Rubens"
    assert core.posted[0]["statements"][CREATOR] == [
        {"type": "literal", "value": "Workshop of Rubens"}
    ]


def test_stage_two_uses_stage_one_s_result():
    """The stages are connected. Before this phase they were not."""
    core = FakeCore()
    candidate = reconcile("Rubens")[0]

    result = stage_enrich(
        SUBJECT, candidate, context=context("echolot-enricher"), client=core.client()
    )

    envelope = core.posted[0]["envelope"]
    assert_valid(envelope)
    assert envelope["activity"]["type"] == "enrichment"
    assert result["added"] == sorted(candidate["facts"])
    assert len(result["added"]) == 2


def test_stage_two_with_no_match_writes_nothing_but_still_records():
    core = FakeCore()
    stage_enrich(
        SUBJECT, None, context=context("echolot-enricher"), client=core.client()
    )

    assert core.posted[0]["statements"] == {}
    assert_valid(core.posted[0]["envelope"])


def test_stage_three_records_its_own_verdict():
    core = FakeCore()
    complete = {p: ["x"] for p in load_table()["_required_properties"]}

    result = stage_quality_check(
        SUBJECT, complete, context=context("echolot-quality"), client=core.client()
    )

    assert result["verdict"] == "complete"
    assert core.posted[0]["statements"][QUALITY] == [
        {"type": "literal", "value": "complete"}
    ]
    assert_valid(core.posted[0]["envelope"])


def test_stage_three_reports_what_is_missing():
    core = FakeCore()
    result = stage_quality_check(
        SUBJECT,
        {CREATOR: ["x"]},
        context=context("echolot-quality"),
        client=core.client(),
    )

    assert result["verdict"] == "incomplete"
    assert len(result["missing"]) == 2
    assert (
        core.posted[0]["envelope"]["method"]["parameters"]["missing"]
        == result["missing"]
    )


# ------------------------------------------------------------- the workflow


def test_three_stages_share_a_batch_and_differ_in_activity():
    """One workflow run is one batch. Each stage is a run inside it."""
    core = FakeCore()
    client = core.client()
    flow_run = "FLOW-1"

    stage_reconcile(
        SUBJECT,
        "Rubens",
        context=context("echolot-reconciler", "TASK-A", flow_run),
        client=client,
    )
    stage_enrich(
        SUBJECT,
        reconcile("Rubens")[0],
        context=context("echolot-enricher", "TASK-B", flow_run),
        client=client,
    )
    stage_quality_check(
        SUBJECT,
        {},
        context=context("echolot-quality", "TASK-C", flow_run),
        client=client,
    )

    activities = [p["envelope"]["activity"] for p in core.posted]
    assert len(core.posted) == 3, "one envelope per stage"
    assert {a["id"] for a in activities} == {"TASK-A", "TASK-B", "TASK-C"}
    assert {a["batch_of"] for a in activities} == {flow_run}
    assert [a["type"] for a in activities] == [
        "reconciliation",
        "enrichment",
        "validation",
    ]


@pytest.mark.slow
def test_the_real_flow_produces_a_traceable_batch():
    """Phase 4b's criterion, through real Prefect and a real Core."""
    import app.main as core_main
    import enrich_demo.flow as flow_module
    from app.store import Store
    from fastapi.testclient import TestClient

    core_main.store = Store()
    http = TestClient(core_main.app)
    flow_module.make_client = lambda _url: CoreClient("http://testserver", client=http)

    result = flow_module.curate_entity("Rubens", SUBJECT)

    assert len({s["activity"] for s in result["stages"]}) == 3
    assert len({s["batch"] for s in result["stages"]}) == 1

    batch = http.get("/batch", params={"id": result["batch"]}).json()
    assert len(batch["snapshots"]) == 3
    assert all(row["subject"] == SUBJECT for row in batch["snapshots"])

    history = http.get("/history", params={"subject": SUBJECT}).json()["snapshots"]
    assert len(history) == 3
    assert {row["batch"] for row in history} == {result["batch"]}
