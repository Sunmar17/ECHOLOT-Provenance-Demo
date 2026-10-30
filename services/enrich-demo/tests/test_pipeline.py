"""The upload workflow: each stage, for each record, emits its own provenance.

The fast tests call the stage functions directly, with a dict-built Context and
a fake Core. No Prefect, no server. The slow tests run the real flow over both
packaged samples, against a real Core served in-process.
"""

import json
from importlib import resources

import httpx
import pytest
from echolot_prov.client import CoreClient
from echolot_prov.context import Context
from echolot_prov.schema import load_schema
from enrich_demo.flow import (
    MATCHED,
    QUALITY,
    match_gnd,
    missing_properties,
    normalise_name,
    reconcile,
    stage_enrich,
    stage_import,
    stage_quality_check,
    stage_reconcile,
)
from enrich_demo.upload import parse_upload, to_statements, upload_source
from enrich_demo.vocab import CREATOR, VIAF_ID, WIKIDATA_ID, field
from jsonschema import Draft202012Validator

ORCID = "https://orcid.org/0000-0002-1825-0097"
BERIO = "http://www.wikidata.org/entity/Q221450"


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
            body = json.loads(request.content)
            self.posted.append(body)
            n = len(self.posted)
            # Like Core: a named claim keeps its IRI, a new one is minted.
            claims = [
                c.get("statement") or f"https://demo.echolot.eu/claim/{n}-{i}"
                for i, c in enumerate(body.get("claims", []))
            ]
            return httpx.Response(
                200,
                json={
                    "snapshot": f"https://demo.echolot.eu/snapshot/{n}",
                    "changed": True,
                    "added": 1,
                    "removed": 0,
                    "claims": claims,
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


def sample(name: str) -> bytes:
    return resources.files("enrich_demo").joinpath("samples", name).read_bytes()


def person():
    data = sample("person.json")
    return parse_upload("person.json", data)[0], upload_source("person.json", data)


def artifact(row: str):
    data = ("kind,id,title,year,personList\n" + row + "\n").encode()
    return parse_upload("a.csv", data)[0], upload_source("a.csv", data)


def imported(record, upload, core, uploader=None):
    return stage_import(
        record,
        upload,
        uploader=uploader,
        context=context("echolot-importer"),
        client=core.client(),
    )


# ------------------------------------------------------------- pure helpers


def test_names_are_read_family_first():
    assert normalise_name("Berio, Luciano") == "Luciano Berio"
    assert normalise_name("Studio Kreisel") == "Studio Kreisel"
    assert normalise_name("L. Berio") == "L. Berio"


def test_reconcile_reads_the_ways_a_name_is_written():
    for written in ("Berio, Luciano", "L. Berio", "luciano  berio"):
        assert reconcile(written)[0]["iri"] == BERIO
    assert reconcile("Hartmann, Lena") == []


def test_an_ambiguous_name_ranks_the_wrong_person_first():
    """Kept on purpose: this is the case a curator has to catch."""
    ranked = reconcile("Molnar, Vera")
    assert [c["label"] for c in ranked] == ["Vera Molnar", "Vera Molnár"]
    assert ranked[0]["score"] > ranked[1]["score"]


def test_gnd_is_an_exact_match():
    assert match_gnd("118509594")["iri"] == BERIO
    assert match_gnd("118509594")["score"] == 1.0
    assert match_gnd("0") is None


def test_missing_properties_finds_the_gaps():
    required = ["a", "b", "c"]
    assert missing_properties({"a": ["x"]}, required) == ["b", "c"]
    assert missing_properties({"a": ["x"], "b": ["y"], "c": ["z"]}, required) == []


# ------------------------------------------------------------ single stages


def test_import_writes_the_record_and_names_the_file_and_uploader():
    record, upload = person()
    core = FakeCore()
    imported(record, upload, core, uploader=ORCID)

    sent = core.posted[0]
    envelope = sent["envelope"]
    assert_valid(envelope)
    assert envelope["activity"]["type"] == "import"
    assert envelope["sources"] == [{"id": upload.iri, "snapshot": upload.digest}]
    assert {(a["role"], a["id"]) for a in envelope["agents"]} == {
        ("executed", "echolot-importer"),
        ("commissioned", ORCID),
    }
    assert set(sent["statements"]) == set(to_statements(record))


def test_import_without_an_uploader_names_only_the_importer():
    record, upload = person()
    core = FakeCore()
    imported(record, upload, core)
    assert [a["role"] for a in core.posted[0]["envelope"]["agents"]] == ["executed"]


def test_a_person_is_reconciled_on_its_gnd_id():
    record, upload = person()
    core = FakeCore()
    progress = stage_reconcile(
        imported(record, upload, core),
        context=context("echolot-reconciler"),
        client=core.client(),
    )

    sent = core.posted[1]
    assert_valid(sent["envelope"])
    assert sent["envelope"]["method"]["name"] == "gnd-id-match"
    assert sent["envelope"]["method"]["confidence"] == 1.0
    assert sent["statements"][WIKIDATA_ID] == [{"type": "uri", "value": BERIO}]
    assert progress["match"]["iri"] == BERIO


def test_an_artifact_records_its_matches_and_what_it_could_not_match():
    record, upload = artifact('item,1,T,2004,"Forsythe, William / Okafor, D."')
    core = FakeCore()
    stage_reconcile(
        imported(record, upload, core),
        context=context("echolot-reconciler"),
        client=core.client(),
    )

    forsythe, okafor = core.posted[0]["claims"]
    assert forsythe["values"][CREATOR][0]["value"] == "Forsythe, William"
    assert okafor["values"][CREATOR][0]["value"] == "Okafor, D."

    sent = core.posted[1]
    method = sent["envelope"]["method"]
    assert_valid(sent["envelope"])
    assert method["name"] == "name-match"
    assert method["parameters"]["unmatched"] == ["Okafor, D."]
    assert [m["label"] for m in method["parameters"]["matches"]] == ["William Forsythe"]

    # The match sits on the Forsythe claim, and only there.
    forsythe_iri = "https://demo.echolot.eu/claim/1-0"
    assert sent["statements"] == {}
    assert sent["claims"] == [
        {
            "statement": forsythe_iri,
            "values": {
                MATCHED: [
                    {"type": "uri", "value": "http://www.wikidata.org/entity/Q706362"}
                ]
            },
        }
    ]
    assert sent["envelope"]["target"]["statements"] == [
        {"property": MATCHED, "statement": forsythe_iri}
    ]


def test_the_weakest_match_sets_the_confidence():
    record, upload = artifact('item,1,T,1987,"Berio, Luciano / Stockhausen, K."')
    core = FakeCore()
    stage_reconcile(
        imported(record, upload, core),
        context=context("echolot-reconciler"),
        client=core.client(),
    )
    assert core.posted[1]["envelope"]["method"]["confidence"] == 0.79


def test_no_match_writes_nothing_but_still_records():
    record, upload = artifact("item,1,T,1987,")
    core = FakeCore()
    stage_reconcile(
        imported(record, upload, core),
        context=context("echolot-reconciler"),
        client=core.client(),
    )
    assert core.posted[1]["statements"] == {}
    assert_valid(core.posted[1]["envelope"])


def test_enrichment_copies_only_the_ids_a_person_lacks():
    record, upload = person()
    core = FakeCore()
    progress = stage_reconcile(
        imported(record, upload, core),
        context=context("echolot-reconciler"),
        client=core.client(),
    )
    stage_enrich(progress, context=context("echolot-enricher"), client=core.client())

    sent = core.posted[2]
    assert_valid(sent["envelope"])
    assert sent["envelope"]["activity"]["type"] == "enrichment"
    assert sent["statements"] == {
        VIAF_ID: [{"type": "literal", "value": "110843905"}]
    }, "gndId is already in the record, so only viafId is copied"


def test_an_artifact_is_not_enriched_and_no_envelope_says_it_was():
    record, upload = artifact('work,1,T,1974,"Paik, Nam June"')
    core = FakeCore()
    progress = stage_enrich(
        imported(record, upload, core),
        context=context("echolot-enricher"),
        client=core.client(),
    )
    assert len(core.posted) == 1, "only the import was written"
    assert progress["skipped"] == ["enrichment"]


def test_the_quality_check_reports_what_is_missing():
    record, upload = artifact("work,1,Ohne Titel,,")
    core = FakeCore()
    progress = stage_quality_check(
        imported(record, upload, core),
        context=context("echolot-quality"),
        client=core.client(),
    )
    sent = core.posted[1]
    assert_valid(sent["envelope"])
    assert progress["verdict"] == "incomplete"
    assert progress["missing"] == [
        "http://purl.org/dc/terms/creator",
        "http://purl.org/dc/terms/date",
    ]
    assert sent["statements"][QUALITY] == [{"type": "literal", "value": "incomplete"}]


def test_a_reconciled_and_enriched_person_is_complete():
    record, upload = person()
    core = FakeCore()
    progress = imported(record, upload, core)
    for stage, service in (
        (stage_reconcile, "echolot-reconciler"),
        (stage_enrich, "echolot-enricher"),
        (stage_quality_check, "echolot-quality"),
    ):
        progress = stage(progress, context=context(service), client=core.client())
    assert progress["verdict"] == "complete"
    assert progress["state"][field("viafId")] == ["110843905"]


# --------------------------------------------------------------- correction


def correct(core, **kwargs):
    from enrich_demo.flow import run_correction

    run_correction(
        "https://demo.echolot.eu/sample/item/1",
        ORCID,
        context=context("echolot-curator-ui"),
        client=core.client(),
        **kwargs,
    )
    return core.posted[-1]


def test_a_correction_on_a_claim_names_the_claim_and_what_it_corrects():
    claim = "https://demo.echolot.eu/claim/abc"
    sent = correct(
        FakeCore(),
        property=MATCHED,
        value="http://www.wikidata.org/entity/Q1658002",
        value_is_iri=True,
        statement=claim,
        corrects="https://demo.echolot.eu/snapshot/s1",
    )
    envelope = sent["envelope"]
    assert_valid(envelope)
    assert sent["statements"] == {}
    assert sent["claims"] == [
        {
            "statement": claim,
            "values": {
                MATCHED: [
                    {"type": "uri", "value": "http://www.wikidata.org/entity/Q1658002"}
                ]
            },
        }
    ]
    assert envelope["target"]["statements"] == [
        {"property": MATCHED, "statement": claim}
    ]
    assert envelope["method"]["parameters"]["corrects"].endswith("/s1")
    assert envelope["validation"] == {"state": "human_corrected", "by": ORCID}


def test_a_correction_of_a_plain_value_writes_it_on_the_subject():
    sent = correct(FakeCore(), property=field("signature"), value="SA-I-0099")
    assert_valid(sent["envelope"])
    assert sent["statements"] == {
        field("signature"): [{"type": "literal", "value": "SA-I-0099"}]
    }
    assert "claims" not in sent
    assert "corrects" not in sent["envelope"]["method"]["parameters"]


# ------------------------------------------------------------- the workflow


def test_one_record_s_stages_share_a_batch_and_differ_in_activity():
    """One upload is one batch. Each stage of each record is a run inside it."""
    record, upload = person()
    core = FakeCore()
    flow_run = "FLOW-1"
    progress = stage_import(
        record,
        upload,
        context=context("echolot-importer", "TASK-0", flow_run),
        client=core.client(),
    )
    for stage, task_id in (
        (stage_reconcile, "TASK-A"),
        (stage_enrich, "TASK-B"),
        (stage_quality_check, "TASK-C"),
    ):
        progress = stage(
            progress, context=context("s", task_id, flow_run), client=core.client()
        )

    activities = [p["envelope"]["activity"] for p in core.posted]
    assert [a["id"] for a in activities] == ["TASK-0", "TASK-A", "TASK-B", "TASK-C"]
    assert {a["batch_of"] for a in activities} == {flow_run}
    assert [a["type"] for a in activities] == [
        "import",
        "reconciliation",
        "enrichment",
        "validation",
    ]
    assert [s["stage"] for s in progress["stages"]] == [
        "import",
        "reconciliation",
        "enrichment",
        "quality-check",
    ]


# ------------------------------------------------------- the real workflow


@pytest.fixture
def real_core():
    """A real Core, in-process, that every stage's client writes to."""
    import app.main as core_main
    import enrich_demo.flow as flow_module
    from app.store import Store
    from fastapi.testclient import TestClient

    core_main.store = Store()
    http = TestClient(core_main.app)
    original = flow_module.make_client
    flow_module.make_client = lambda _url: CoreClient("http://testserver", client=http)
    yield http
    flow_module.make_client = original


def run_sample(name: str) -> dict:
    from enrich_demo.flow import curate_upload

    data = sample(name)
    return curate_upload(parse_upload(name, data), upload_source(name, data), ORCID)


@pytest.mark.slow
def test_the_artifact_sample_is_one_traceable_batch(real_core):
    """Phase 4c's criterion, through real Prefect and a real Core."""
    result = run_sample("artifacts.csv")

    assert result["records"] == 20
    assert result["snapshots"] == 60, "import, reconcile, quality; no enrichment"
    assert all(r["skipped"] == ["enrichment"] for r in result["results"])

    batch = real_core.get("/batch", params={"id": result["batch"]}).json()
    assert len(batch["snapshots"]) == 60
    assert len({row["subject"] for row in batch["snapshots"]}) == 20
    assert len({row["activity"] for row in batch["snapshots"]}) == 60

    verdicts = {r["id"]: r["verdict"] for r in result["results"]}
    assert verdicts["92003"] == "complete"
    assert verdicts["92010"] == "incomplete", "no year, no creator"


@pytest.mark.slow
def test_an_artifact_s_first_snapshot_is_the_record_as_uploaded(real_core):
    result = run_sample("artifacts.csv")
    work = next(r for r in result["results"] if r["id"] == "92005")

    history = real_core.get("/history", params={"subject": work["subject"]}).json()
    oldest = history["snapshots"][-1]
    state = real_core.get(
        "/reconstruct",
        params={"subject": work["subject"], "at": oldest["generated_at"]},
    ).json()

    record = next(
        r
        for r in parse_upload("artifacts.csv", sample("artifacts.csv"))
        if r.id == "92005"
    )
    plain = {s["predicate"] for s in state["statements"] if s["statement"] is None}
    on_claims = [s for s in state["statements"] if s["statement"] is not None]
    assert plain == set(to_statements(record))
    assert [(s["predicate"], s["object"]["value"]) for s in on_claims] == [
        (CREATOR, "Paik, Nam June")
    ], "the creator claim as uploaded, before any match"


def creator_claims(http, subject: str) -> dict[str, dict]:
    """The current claims of a subject: creator name to its claim's values."""
    rows = http.get(
        "/reconstruct", params={"subject": subject, "at": "2999-01-01T00:00:00Z"}
    ).json()["statements"]
    by_claim: dict[str, dict] = {}
    for row in rows:
        if row["statement"]:
            values = by_claim.setdefault(
                row["statement"], {"statement": row["statement"]}
            )
            values[row["predicate"]] = row["object"]["value"]
    return {v[CREATOR]: v for v in by_claim.values()}


@pytest.mark.slow
def test_two_creators_each_keep_their_own_match(real_core):
    """Point 3: the match belongs to one creator, and it says which."""
    result = run_sample("artifacts.csv")
    item = next(r for r in result["results"] if r["id"] == "91005")

    claims = creator_claims(real_core, item["subject"])
    assert claims["Forsythe, William"][MATCHED] == (
        "http://www.wikidata.org/entity/Q706362"
    )
    assert MATCHED not in claims["Okafor, D."]

    history = real_core.get("/history", params={"subject": item["subject"]}).json()
    reconciliation = next(
        s for s in history["snapshots"] if "reconciliation" in s["description"]
    )
    forsythe = claims["Forsythe, William"]["statement"]
    assert reconciliation["statements"] == [forsythe], (
        "point 4: the snapshot names the claim it changed"
    )


@pytest.mark.slow
def test_a_curator_corrects_the_wrong_vera_molnar(real_core):
    """The ambiguous match, corrected on its own claim, citing the snapshot."""
    import enrich_demo.flow as flow_module

    result = run_sample("artifacts.csv")
    item = next(r for r in result["results"] if r["id"] == "91003")
    claim = creator_claims(real_core, item["subject"])["Molnar, Vera"]
    assert claim[MATCHED].endswith("Q2513339"), "the actress, ranked first"

    reconciliation = next(s for s in item["stages"] if s["stage"] == "reconciliation")
    flow_module.run_correction(
        item["subject"],
        ORCID,
        property=MATCHED,
        value="http://www.wikidata.org/entity/Q1658002",
        value_is_iri=True,
        statement=claim["statement"],
        corrects=reconciliation["snapshot"],
        context=context("echolot-curator-ui"),
        client=flow_module.make_client("unused"),
    )

    fixed = creator_claims(real_core, item["subject"])["Molnar, Vera"]
    assert fixed[MATCHED].endswith("Q1658002"), "the artist"
    newest = real_core.get("/history", params={"subject": item["subject"]}).json()[
        "snapshots"
    ][0]
    assert newest["validation_state"] == "human_corrected"
    detail = real_core.get("/snapshot", params={"uri": newest["snapshot"]}).json()
    assert (
        detail["envelope"]["method"]["parameters"]["corrects"]
        == (reconciliation["snapshot"])
    )


@pytest.mark.slow
def test_the_person_sample_gains_its_wikidata_and_viaf_ids(real_core):
    result = run_sample("person.json")
    (only,) = result["results"]

    assert result["snapshots"] == 4
    assert [s["stage"] for s in only["stages"]] == [
        "import",
        "reconciliation",
        "enrichment",
        "quality-check",
    ]
    assert only["verdict"] == "complete"

    history = real_core.get("/history", params={"subject": only["subject"]}).json()
    newest = history["snapshots"][0]
    state = real_core.get(
        "/reconstruct",
        params={"subject": only["subject"], "at": newest["generated_at"]},
    ).json()
    values = {(s["predicate"], s["object"]["value"]) for s in state["statements"]}
    assert (WIKIDATA_ID, BERIO) in values
    assert (VIAF_ID, "110843905") in values
