"""Core's /write and /subjects, with the NeoWiki client wired in or out.

The client's own behaviour (titles, retries, sessions, login) is covered by
``echolot-neowiki-client``'s own tests; this file only covers what Core does
around it: a success is reported, a failure is a loud 502 that still says
Core committed, and a missing client changes nothing in the response shape.
"""

import json
from urllib.parse import parse_qs

import httpx
from app import main
from conftest import payload_for, valid_envelopes
from echolot_neowiki_client.client import NeoWikiClient, NeoWikiError

VALID = valid_envelopes()


def envelope(name: str = "reconciliation-software") -> dict:
    return next(e for n, e in VALID if n == name)


def post(client, env: dict):
    return client.post("/write", json={"envelope": env, "statements": payload_for(env)})


class RecordingClient:
    """A stand-in NeoWiki client that records what Core hands it."""

    def __init__(self, error: NeoWikiError | None = None) -> None:
        self.error = error
        self.received: list[dict] = []

    def append(self, env: dict) -> dict:
        if self.error:
            raise self.error
        self.received.append(env)
        return {"page": "Provenance/x", "revision": 1, "url": "http://wiki.test/h"}

    def history_url(self, subject: str) -> str:
        return f"http://wiki.test/history?subject={subject}"


def test_a_write_hands_the_client_the_envelope_exactly_as_sent(client, monkeypatch):
    neowiki = RecordingClient()
    monkeypatch.setattr(main, "neowiki_client", neowiki)
    env = envelope()

    response = post(client, env)

    assert response.status_code == 200
    assert neowiki.received == [env]
    assert response.json()["neowiki"]["page"] == "Provenance/x"


def test_a_failing_client_is_a_502_and_core_still_has_the_data(client, monkeypatch):
    monkeypatch.setattr(
        main, "neowiki_client", RecordingClient(NeoWikiError("wiki down"))
    )
    env = envelope()

    response = post(client, env)

    assert response.status_code == 502
    body = response.json()
    assert "wiki down" in body["errors"][0]["message"]
    assert "Core committed" in body["errors"][0]["message"]
    assert body["committed"]["snapshot"]
    history = client.get("/history", params={"subject": env["target"]["subject"]})
    assert len(history.json()["snapshots"]) == 1


def test_a_rejected_envelope_never_reaches_the_client(client, monkeypatch):
    neowiki = RecordingClient()
    monkeypatch.setattr(main, "neowiki_client", neowiki)

    response = client.post("/write", json={"envelope": {"schema_version": "9.9"}})

    assert response.status_code == 422
    assert neowiki.received == []


def test_without_a_client_the_response_and_subjects_are_unchanged(client, monkeypatch):
    monkeypatch.setattr(main, "neowiki_client", None)
    response = post(client, envelope())
    assert "neowiki" not in response.json()
    assert "neowiki_history" not in client.get("/subjects").json()


def test_subjects_name_each_history_page_when_the_client_is_on(client, monkeypatch):
    monkeypatch.setattr(main, "neowiki_client", RecordingClient())
    env = envelope()
    post(client, env)

    links = client.get("/subjects").json()["neowiki_history"]

    assert links == {
        env["target"][
            "subject"
        ]: f"http://wiki.test/history?subject={env['target']['subject']}"
    }


# --------------------------------------------------------------- end to end


class FakeWiki:
    """Just enough of MediaWiki's Action API to prove the real client works

    end to end through Core's /write. Full coverage of the client's own
    behaviour (conflicts, retries, login) lives in echolot-neowiki-client.
    """

    def __init__(self) -> None:
        self.pages: dict[str, list[dict]] = {}

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def content(self, title: str) -> dict:
        return json.loads(self.pages[title][-1]["content"])

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raw = parse_qs(request.content.decode(), keep_blank_values=True)
            params = {k: v[0] for k, v in raw.items()}
        else:
            params = dict(request.url.params)
        action = params["action"]
        if action == "query" and params.get("meta") == "tokens":
            kind = params["type"]
            return self.ok({"query": {"tokens": {f"{kind}token": f"{kind}-token"}}})
        if action == "clientlogin":
            return self.ok({"clientlogin": {"status": "PASS"}})
        if action == "query" and params.get("prop") == "revisions":
            return self.read(params["titles"])
        if action == "edit":
            return self.edit(params)
        raise AssertionError(f"unexpected request: {params}")

    @staticmethod
    def ok(body: dict) -> httpx.Response:
        return httpx.Response(200, json=body)

    def read(self, title: str) -> httpx.Response:
        revisions = self.pages.get(title)
        if not revisions:
            return self.ok({"query": {"pages": [{"title": title, "missing": True}]}})
        last = revisions[-1]
        return self.ok(
            {
                "query": {
                    "pages": [
                        {
                            "title": title,
                            "revisions": [
                                {
                                    "revid": last["revid"],
                                    "slots": {"main": {"content": last["content"]}},
                                }
                            ],
                        }
                    ]
                }
            }
        )

    def edit(self, params: dict) -> httpx.Response:
        title = params["title"]
        revisions = self.pages.setdefault(title, [])
        revid = 1000 + sum(len(r) for r in self.pages.values()) + 1
        revisions.append(
            {"revid": revid, "content": params["text"], "summary": params["summary"]}
        )
        return self.ok({"edit": {"result": "Success", "newrevid": revid}})


def test_end_to_end_through_core_into_a_fake_wiki(client, monkeypatch):
    """Core's /write, the real client, and a wiki that keeps revisions."""
    wiki = FakeWiki()
    monkeypatch.setattr(
        main,
        "neowiki_client",
        NeoWikiClient(
            "http://wiki.test/w",
            "AdminName",
            "irrelevant",
            base_iri="https://example.org",
            client=wiki.client(),
        ),
    )
    first = envelope("reconciliation-software")
    second = {**envelope("validation-human-corrected"), "target": first["target"]}

    one = post(client, first).json()
    two = post(client, second).json()

    page = one["neowiki"]["page"]
    assert two["neowiki"]["page"] == page
    assert wiki.content(page)["envelopes"] == [first, second]
    assert len(wiki.pages[page]) == 2
