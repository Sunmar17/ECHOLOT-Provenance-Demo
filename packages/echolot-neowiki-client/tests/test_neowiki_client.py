"""``NeoWikiClient``: envelopes stored as revisions of JSON pages.

No wiki is needed. ``FakeWiki`` answers the few Action API calls the client
makes, behind an ``httpx.MockTransport``, and can be told to misbehave in the
ways a real wiki does: an edit conflict, an expired token, a wrong password.

Envelopes here are deliberately minimal, not schema-valid fixtures: this
client never builds or validates an envelope, it only reads
``target.subject`` and ``activity``, so a synthetic dict with just those
fields is a faithful test double.
"""

import re
from urllib.parse import parse_qs

import httpx
import pytest
from echolot_neowiki_client.client import (
    BASE_IRI_ENV,
    MAX_ATTEMPTS,
    PASSWORD_ENV,
    URL_ENV,
    USER_ENV,
    NeoWikiClient,
    NeoWikiError,
    page_title,
)

BASE = "https://demo.echolot.eu/sample"
WIKI = "http://wiki.test/w"
PASSWORD = "s3cret-demo-password"


def envelope(
    activity_type: str = "reconciliation",
    activity_id: str = "run-1",
    subject: str = f"{BASE}/entity/person/1",
) -> dict:
    return {
        "activity": {"type": activity_type, "id": activity_id},
        "target": {"subject": subject},
    }


class FakeWiki:
    """Enough of MediaWiki's Action API for the client, and no more."""

    def __init__(
        self,
        *,
        clientlogin: bool = True,
        bot_login: bool = False,
        conflicts: int = 0,
        bad_tokens: int = 0,
    ) -> None:
        self.pages: dict[str, list[dict]] = {}
        self.clientlogin_ok = clientlogin
        self.bot_login_ok = bot_login
        self.conflicts = conflicts
        self.bad_tokens = bad_tokens
        self.edits: list[dict] = []
        self.logins = 0

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def content(self, title: str) -> dict:
        import json

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
            return self.ok({"query": {"tokens": {f"{kind}token": f"{kind}-token+\\"}}})
        if action == "clientlogin":
            self.logins += 1
            good = self.clientlogin_ok and params["password"] == PASSWORD
            return self.ok({"clientlogin": {"status": "PASS" if good else "FAIL"}})
        if action == "login":
            good = self.bot_login_ok and params["lgpassword"] == PASSWORD
            return self.ok({"login": {"result": "Success" if good else "Failed"}})
        if action == "query" and params.get("prop") == "revisions":
            return self.read(params["titles"])
        if action == "edit":
            return self.edit(params)
        raise AssertionError(f"unexpected request: {params}")

    @staticmethod
    def ok(body: dict) -> httpx.Response:
        return httpx.Response(200, json=body)

    @staticmethod
    def error(code: str) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": code, "info": code}})

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
        self.edits.append(params)
        if self.bad_tokens:
            self.bad_tokens -= 1
            return self.error("badtoken")
        if params["token"] != "csrf-token+\\":
            return self.error("badtoken")
        title = params["title"]
        revisions = self.pages.setdefault(title, [])
        if self.conflicts:
            self.conflicts -= 1
            return self.error("editconflict")
        if "createonly" in params and revisions:
            return self.error("articleexists")
        if "baserevid" in params and int(params["baserevid"]) != revisions[-1]["revid"]:
            return self.error("editconflict")
        assert params["contentmodel"] == "json"
        revid = 1000 + sum(len(r) for r in self.pages.values()) + 1
        revisions.append(
            {"revid": revid, "content": params["text"], "summary": params["summary"]}
        )
        return self.ok({"edit": {"result": "Success", "newrevid": revid}})


def client_for(wiki: FakeWiki, base_iri: str = BASE) -> NeoWikiClient:
    return NeoWikiClient(
        WIKI, "AdminName", PASSWORD, base_iri=base_iri, client=wiki.client()
    )


# --------------------------------------------------------------------- titles


def test_a_subject_under_the_base_becomes_a_readable_title():
    assert page_title(f"{BASE}/artifact/102", BASE) == "Provenance/artifact/102"


def test_a_trailing_slash_on_the_base_changes_nothing():
    assert page_title(f"{BASE}/artifact/102", BASE + "/") == "Provenance/artifact/102"


def test_a_subject_outside_the_base_gets_a_stable_hash_title():
    subject = "https://other.example/thing/1"
    title = page_title(subject, BASE)
    assert re.fullmatch(r"Provenance/other/[0-9a-f]{16}", title)
    assert page_title(subject, BASE) == title


@pytest.mark.parametrize(
    "tail",
    [
        "artifact/a%20b",  # MediaWiki refuses %xx in a title
        "artifact/a b",
        "artifact/../x",
        "artifact/./x",
        "artifact//x",
        "artifact/x#y",
        "artifact/x?y",
        "artifact/a~~~b",
        "artifact/" + "x" * 300,
    ],
)
def test_a_segment_mediawiki_would_refuse_falls_back_to_the_hash(tail):
    title = page_title(f"{BASE}/{tail}", BASE)
    assert re.fullmatch(r"Provenance/other/[0-9a-f]{16}", title)


def test_two_subjects_never_share_a_title():
    titles = {page_title(f"{BASE}/artifact/{i}", BASE) for i in range(50)} | {
        page_title(f"https://x.example/{i}", BASE) for i in range(50)
    }
    assert len(titles) == 100


def test_the_history_url_is_the_action_history_page():
    client = client_for(FakeWiki())
    assert (
        client.history_url(f"{BASE}/artifact/102")
        == f"{WIKI}/index.php?title=Provenance/artifact/102&action=history"
    )


# --------------------------------------------------------------------- append


def test_the_first_envelope_creates_the_page():
    wiki = FakeWiki()
    env = envelope()
    subject = env["target"]["subject"]

    saved = client_for(wiki, base_iri="https://example.org").append(env)

    assert saved["page"] == page_title(subject, "https://example.org")
    assert wiki.content(saved["page"]) == {"subject": subject, "envelopes": [env]}
    assert "createonly" in wiki.edits[0]
    activity = env["activity"]
    assert wiki.edits[0]["summary"] == f"{activity['type']} {activity['id']}"


def test_a_second_envelope_is_appended_as_a_new_revision():
    wiki = FakeWiki()
    client = client_for(wiki, base_iri="https://example.org")
    first = envelope(activity_id="run-1")
    second = {**envelope(activity_id="run-2"), "target": first["target"]}

    one = client.append(first)
    two = client.append(second)

    page = one["page"]
    assert two["page"] == page
    assert two["revision"] != one["revision"]
    assert len(wiki.pages[page]) == 2
    assert wiki.content(page)["envelopes"] == [first, second]
    # The save names the revision it read, so a concurrent save is not overwritten.
    assert wiki.edits[1]["baserevid"] == str(one["revision"])


def test_an_edit_conflict_is_retried_against_the_newer_page():
    wiki = FakeWiki(conflicts=1)
    saved = client_for(wiki).append(envelope())
    assert saved["revision"]
    assert len(wiki.edits) == 2


def test_persistent_conflicts_give_up_loudly():
    wiki = FakeWiki(conflicts=MAX_ATTEMPTS + 5)
    with pytest.raises(NeoWikiError) as raised:
        client_for(wiki).append(envelope())
    assert raised.value.code == "editconflict"
    assert len(wiki.edits) == MAX_ATTEMPTS


def test_an_expired_token_triggers_one_fresh_login():
    wiki = FakeWiki(bad_tokens=1)
    client_for(wiki).append(envelope())
    assert wiki.logins == 2


def test_the_session_is_reused_between_writes():
    wiki = FakeWiki()
    client = client_for(wiki)
    client.append(envelope(activity_id="run-1"))
    client.append(envelope(activity_id="run-2"))
    assert wiki.logins == 1


def test_a_bot_password_works_when_clientlogin_does_not():
    wiki = FakeWiki(clientlogin=False, bot_login=True)
    assert client_for(wiki).append(envelope())["revision"]


def test_a_wrong_password_is_refused_and_never_echoed():
    wiki = FakeWiki(clientlogin=False, bot_login=False)
    client = NeoWikiClient(
        WIKI, "AdminName", "wrong-password", base_iri=BASE, client=wiki.client()
    )
    with pytest.raises(NeoWikiError) as raised:
        client.append(envelope())
    assert "wrong-password" not in str(raised.value)
    assert wiki.edits == []


def test_a_page_that_is_not_an_envelope_log_is_not_overwritten():
    wiki = FakeWiki()
    env = envelope()
    title = page_title(env["target"]["subject"], BASE)
    wiki.pages[title] = [{"revid": 7, "content": "just some wikitext", "summary": ""}]

    with pytest.raises(NeoWikiError, match="does not hold JSON"):
        client_for(wiki).append(env)
    assert wiki.edits == []
    assert wiki.pages[title][-1]["content"] == "just some wikitext"


def test_an_unreachable_wiki_is_a_client_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = NeoWikiClient(
        WIKI,
        "u",
        PASSWORD,
        base_iri=BASE,
        client=httpx.Client(transport=httpx.MockTransport(refuse)),
    )
    with pytest.raises(NeoWikiError, match="could not reach NeoWiki"):
        client.append(envelope())


# ---------------------------------------------------------------- environment


def test_nothing_is_configured_by_default(monkeypatch):
    for name in (URL_ENV, USER_ENV, PASSWORD_ENV, BASE_IRI_ENV):
        monkeypatch.delenv(name, raising=False)
    assert NeoWikiClient.from_environment(default_base_iri=BASE) is None


def test_a_url_without_credentials_stops_startup(monkeypatch):
    monkeypatch.setenv(URL_ENV, WIKI)
    monkeypatch.delenv(USER_ENV, raising=False)
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    with pytest.raises(NeoWikiError, match=USER_ENV):
        NeoWikiClient.from_environment(default_base_iri=BASE)


def test_the_environment_builds_a_client_with_its_base_iri(monkeypatch):
    monkeypatch.setenv(URL_ENV, WIKI)
    monkeypatch.setenv(USER_ENV, "AdminName")
    monkeypatch.setenv(PASSWORD_ENV, PASSWORD)
    monkeypatch.setenv(BASE_IRI_ENV, BASE)
    client = NeoWikiClient.from_environment(default_base_iri="https://unused.example")
    assert client is not None
    assert client.page_title(f"{BASE}/artifact/1") == "Provenance/artifact/1"


def test_without_a_base_iri_env_var_the_default_is_used(monkeypatch):
    monkeypatch.setenv(URL_ENV, WIKI)
    monkeypatch.setenv(USER_ENV, "AdminName")
    monkeypatch.setenv(PASSWORD_ENV, PASSWORD)
    monkeypatch.delenv(BASE_IRI_ENV, raising=False)
    client = NeoWikiClient.from_environment(default_base_iri=BASE)
    assert client is not None
    assert client.page_title(f"{BASE}/artifact/1") == "Provenance/artifact/1"
