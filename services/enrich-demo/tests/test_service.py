"""The upload endpoints refuse a bad file before any stage runs.

Only the refusals are tested here: they return before the flow starts, so they
need no Prefect and no Core. The accepted path is the slow flow test's job.
"""

import enrich_demo.service as service
from fastapi.testclient import TestClient

client = TestClient(service.app)


def never_run(*_args, **_kwargs):
    raise AssertionError("the flow must not start for a refused file")


def test_a_bad_file_is_refused_with_the_record_that_stopped_it(monkeypatch):
    monkeypatch.setattr(service, "curate_upload", never_run)
    data = b"kind,id,title\nwork,1,A\npainting,2,B\n"
    response = client.post("/upload", files={"file": ("bad.csv", data, "text/csv")})
    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert error["path"] == "/records/2"
    assert "painting" in error["message"]


def test_an_empty_file_is_refused(monkeypatch):
    monkeypatch.setattr(service, "curate_upload", never_run)
    response = client.post("/upload", files={"file": ("x.json", b"", "text/plain")})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/file"


def test_an_unknown_sample_is_refused(monkeypatch):
    monkeypatch.setattr(service, "curate_upload", never_run)
    response = client.post("/upload/sample", json={"sample": "paintings"})
    assert response.status_code == 422


def test_both_samples_reach_the_flow_with_their_records(monkeypatch):
    seen = {}

    def capture(records, upload, uploader, url):
        seen[upload.filename] = (len(records), upload.format, uploader)
        return {"ok": True}

    monkeypatch.setattr(service, "curate_upload", capture)
    for sample in ("person", "artifacts"):
        response = client.post(
            "/upload/sample", json={"sample": sample, "uploader": "https://orcid.org/x"}
        )
        assert response.status_code == 200
    assert seen == {
        "person.json": (1, "json", "https://orcid.org/x"),
        "artifacts.csv": (20, "csv", "https://orcid.org/x"),
    }
