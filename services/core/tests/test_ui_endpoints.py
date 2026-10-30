"""The two read endpoints the curator page needs, and the page itself."""

import json

import pytest
from conftest import payload_for, valid_envelopes

VALID = valid_envelopes()


def write_one(client, name: str = "reconciliation-software"):
    envelope = next(e for n, e in VALID if n == name)
    response = client.post(
        "/write", json={"envelope": envelope, "statements": payload_for(envelope)}
    )
    assert response.status_code == 200, response.json()
    return envelope, response.json()


# ------------------------------------------------------------------ subjects


def test_a_fresh_store_lists_no_subjects(client):
    assert client.get("/subjects").json() == {"subjects": [], "labels": {}}


def test_a_written_subject_appears(client):
    envelope, _ = write_one(client)
    assert client.get("/subjects").json()["subjects"] == [envelope["target"]["subject"]]


def test_a_subject_is_listed_once_however_many_snapshots(client):
    """Two writes to one subject are one subject, not two."""
    write_one(client, "reconciliation-software")
    write_one(client, "validation-human-corrected")
    assert len(client.get("/subjects").json()["subjects"]) == 1


# ------------------------------------------------------------------ snapshot


def test_the_stored_envelope_is_the_one_the_producer_sent(client):
    """Core keeps the producer's own words, not only its reading of them."""
    envelope, written = write_one(client)
    detail = client.get("/snapshot", params={"uri": written["snapshot"]}).json()
    assert detail["envelope"] == envelope


def test_the_stored_envelope_still_satisfies_the_contract(client):
    """Storing and returning it must not damage it."""
    from echolot_prov.schema import load_schema
    from jsonschema import Draft202012Validator

    _, written = write_one(client)
    detail = client.get("/snapshot", params={"uri": written["snapshot"]}).json()

    # Against its own version. Core keeps every committed schema precisely so a
    # stored envelope stays valid after the contract moves on.
    validator = Draft202012Validator(
        load_schema(detail["envelope"]["schema_version"]),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    assert not list(validator.iter_errors(detail["envelope"]))


def test_the_snapshot_carries_what_a_curator_reads(client):
    _, written = write_one(client)
    detail = client.get("/snapshot", params={"uri": written["snapshot"]}).json()

    assert detail["generated_at"]
    assert "reconciliation" in detail["description"]
    assert "INSERT DATA" in detail["update_query"]


def test_an_envelope_with_awkward_characters_survives(client):
    """A real note can contain braces and quotes. Storage must not care."""
    envelope = json.loads(json.dumps(next(e for n, e in VALID if n == "import-batch")))
    envelope["rights"]["basis"] = 'Clause 4(b) "} } INSERT DATA {" and a \\ backslash'

    response = client.post(
        "/write", json={"envelope": envelope, "statements": payload_for(envelope)}
    )
    assert response.status_code == 200

    detail = client.get("/snapshot", params={"uri": response.json()["snapshot"]}).json()
    assert detail["envelope"]["rights"]["basis"] == envelope["rights"]["basis"]


def test_an_unknown_snapshot_is_404(client):
    response = client.get(
        "/snapshot", params={"uri": "https://demo.echolot.eu/snapshot/nope"}
    )
    assert response.status_code == 404
    assert response.json()["errors"][0]["path"] == "/uri"


def test_a_snapshot_uri_must_be_an_iri(client):
    assert client.get("/snapshot", params={"uri": "not-an-iri"}).status_code == 422


# ---------------------------------------------------------------------- page


def test_the_page_is_served(client):
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_bare_host_redirects_to_the_page(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


@pytest.mark.parametrize(
    "endpoint", ["/subjects", "/history", "/reconstruct", "/snapshot"]
)
def test_the_page_calls_every_endpoint_it_needs(client, endpoint):
    """A renamed endpoint should break a test, not just the page in a browser."""
    assert endpoint in client.get("/ui/").text


# --------------------------------------------------------------------- batch


def test_an_unknown_batch_is_empty(client):
    body = client.get("/batch", params={"id": "no-such-batch"}).json()
    assert body == {"batch": "no-such-batch", "snapshots": []}


def test_a_batch_groups_the_snapshots_one_workflow_produced(client):
    """A workflow is a batch; each stage is a run inside it."""
    envelope, _ = write_one(client, "import-batch")
    batch_id = envelope["activity"]["batch_of"]

    body = client.get("/batch", params={"id": batch_id}).json()

    assert len(body["snapshots"]) == 1
    row = body["snapshots"][0]
    assert row["subject"] == envelope["target"]["subject"]
    assert row["generated_at"]
    assert row["activity"]


def test_history_reports_the_batch_a_snapshot_belongs_to(client):
    envelope, _ = write_one(client, "import-batch")
    snapshots = client.get(
        "/history", params={"subject": envelope["target"]["subject"]}
    ).json()["snapshots"]
    assert snapshots[0]["batch"] == envelope["activity"]["batch_of"]


def test_a_snapshot_with_no_batch_reports_none(client):
    """Not every write comes from a workflow. A curator correction does not."""
    envelope, _ = write_one(client, "validation-human-corrected")
    assert envelope["activity"].get("batch_of") is None
    snapshots = client.get(
        "/history", params={"subject": envelope["target"]["subject"]}
    ).json()["snapshots"]
    assert snapshots[0]["batch"] is None


def test_the_page_corrects_beside_each_snapshot(client):
    """A correction sits on the value a snapshot wrote, and says which snapshot."""
    html = client.get("/ui/").text
    assert "correctform" not in html, "no general correction bar"
    assert "/correct" in html
    assert 'fix.textContent = "Correct"' in html
    assert "corrects: snap.snapshot" in html
    assert "statement: change.statement" in html


def test_the_page_offers_an_upload_and_both_samples(client):
    """WP4: a curator uploads a CSV or JSON file, one record or a batch."""
    html = client.get("/ui/").text
    assert 'type="file"' in html
    assert "/upload" in html
    assert "/upload/sample" in html
    assert '"person"' in html and '"artifacts"' in html
