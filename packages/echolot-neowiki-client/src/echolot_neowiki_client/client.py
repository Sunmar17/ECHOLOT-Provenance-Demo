"""``NeoWikiClient``: writes a provenance envelope into NeoWiki.

NeoWiki 0.1.0 has no endpoint for provenance, and the core MediaWiki edit API
cannot write a revision slot (``ApiEditPage`` has no ``slot`` parameter; see
``docs/revisionfile.md`` entry 26 in the ECHOLOT-Provenance repository). What
the Action API *can* do is save a JSON page, and MediaWiki then gives that
page a history and a diff for free.

One page per record, ``Provenance/<path after the declared base IRI>``. Its
content is ``{"subject": <IRI>, "envelopes": [...]}``. Each write appends one
envelope as one revision, so the history diff shows exactly the new envelope
and the current page shows the whole log.

This client only ever appends an envelope it is handed: it never builds or
validates one, and it does not know who called it or what else that caller
wrote elsewhere. A caller that needs its own write and this one to be atomic
has to arrange that itself; this client only promises to raise loudly, never
to swallow a failure, so a caller can decide what "the wiki write failed"
should mean for it.
"""

import hashlib
import json
import os
import re
import threading
from typing import Any
from urllib.parse import quote

import httpx

URL_ENV = "ECHOLOT_NEOWIKI_URL"
"""The wiki's script path, for example ``http://localhost:8484/w``."""
USER_ENV = "ECHOLOT_NEOWIKI_USER"
PASSWORD_ENV = "ECHOLOT_NEOWIKI_PASSWORD"
BASE_IRI_ENV = "ECHOLOT_NEOWIKI_BASE_IRI"
"""The declared base under which subject IRIs map to page titles."""

PAGE_PREFIX = "Provenance/"
MAX_ATTEMPTS = 3
"""One write, plus retries after an edit conflict or an expired token."""

# MediaWiki refuses ``%xx`` in a title, so ``urllib.parse.quote`` cannot be used
# to make an IRI path safe. A segment is kept only if it is plain.
_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")
_MAX_TITLE = 200  # MediaWiki's limit is 255 bytes; stay well under it.


class NeoWikiError(RuntimeError):
    """NeoWiki could not be reached, or refused the request.

    ``code`` is MediaWiki's own error code when it gave one, such as
    ``editconflict``. The message never contains the password.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def page_title(subject: str, base_iri: str) -> str:
    """The page that holds one subject's provenance. Deterministic and pure.

    The path after ``base_iri`` becomes the title: with the base
    ``https://demo.echolot.eu/sample``, ``.../sample/artifact/102`` is
    ``Provenance/artifact/102``. An IRI outside the base, or with a segment
    MediaWiki would refuse, gets a hash title instead, so the mapping is total.
    """
    base = base_iri.rstrip("/") + "/"
    if subject.startswith(base):
        segments = subject[len(base) :].split("/")
        plain = all(
            _SEGMENT.fullmatch(s) and "~~~" not in s and set(s) != {"."}
            for s in segments
        )
        title = PAGE_PREFIX + "/".join(segments)
        if plain and len(title) <= _MAX_TITLE:
            return title
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
    return f"{PAGE_PREFIX}other/{digest}"


def _summary(envelope: dict[str, Any]) -> str:
    """The edit summary a person reads in the history list."""
    activity = envelope.get("activity", {})
    return f"{activity.get('type', 'activity')} {activity.get('id', '')}".strip()


class NeoWikiClient:
    """Appends envelopes to JSON pages through the MediaWiki Action API.

    ``client`` is injectable, as in ``CoreClient``: tests pass an
    ``httpx.Client`` on a ``MockTransport``, so no wiki is needed.
    """

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        *,
        base_iri: str,
        client: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._root = base_url.rstrip("/")
        self._api_url = self._root + "/api.php"
        self._user = user
        self._password = password
        self._base_iri = base_iri
        # The client keeps the session cookie, so one login serves every call.
        self._client = client or httpx.Client(timeout=timeout)
        self._csrf: str | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls, *, default_base_iri: str) -> "NeoWikiClient | None":
        """The client the environment asks for, or ``None`` when it asks for none.

        ``default_base_iri`` is the caller's own fallback (such as Core's demo
        base) used when ``ECHOLOT_NEOWIKI_BASE_IRI`` is not set; this package
        has no base IRI of its own to assume.
        """
        url = os.environ.get(URL_ENV)
        if not url:
            return None
        user = os.environ.get(USER_ENV)
        password = os.environ.get(PASSWORD_ENV)
        if not user or not password:
            raise NeoWikiError(
                f"{URL_ENV} is set, but {USER_ENV} or {PASSWORD_ENV} is not"
            )
        return cls(
            url,
            user,
            password,
            base_iri=os.environ.get(BASE_IRI_ENV) or default_base_iri,
        )

    # ------------------------------------------------------------------ public

    def page_title(self, subject: str) -> str:
        return page_title(subject, self._base_iri)

    def history_url(self, subject: str) -> str:
        """Where a person reads this subject's provenance history."""
        title = quote(self.page_title(subject), safe="/:")
        return f"{self._root}/index.php?title={title}&action=history"

    def append(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Store one envelope as one new revision of its subject's page.

        Reads the page, appends, and saves with the revision it read as the base.
        A concurrent save makes MediaWiki answer ``editconflict``; the read and
        append are then repeated against the newer page.
        """
        subject = envelope["target"]["subject"]
        title = self.page_title(subject)
        last: NeoWikiError | None = None
        for _ in range(MAX_ATTEMPTS):
            try:
                self._ensure_session()
                revision, document = self._read(title, subject)
                document["envelopes"].append(envelope)
                saved = self._edit(title, document, revision, _summary(envelope))
            except NeoWikiError as exc:
                last = exc
                if exc.code == "badtoken":
                    self._reset_session()
                    continue
                if exc.code in ("editconflict", "articleexists"):
                    continue
                raise
            return {
                "page": title,
                "revision": saved["newrevid"],
                "url": self.history_url(subject),
            }
        raise NeoWikiError(
            f"gave up on {title} after {MAX_ATTEMPTS} attempts: {last}",
            code=last.code if last else None,
        )

    # ----------------------------------------------------------------- session

    def _reset_session(self) -> None:
        with self._lock:
            self._csrf = None

    def _ensure_session(self) -> None:
        with self._lock:
            if self._csrf is not None:
                return
            self._login()
            self._csrf = self._call(action="query", meta="tokens", type="csrf")[
                "query"
            ]["tokens"]["csrftoken"]

    def _login(self) -> None:
        """Log in as the configured user.

        ``clientlogin`` is tried first. It does not accept a bot password, so a
        failure there falls back to ``login``, which does.
        """
        token = self._login_token()
        body = self._call(
            post=True,
            action="clientlogin",
            username=self._user,
            password=self._password,
            logintoken=token,
            loginreturnurl=self._root,
        )
        if body.get("clientlogin", {}).get("status") == "PASS":
            return
        body = self._call(
            post=True,
            action="login",
            lgname=self._user,
            lgpassword=self._password,
            lgtoken=self._login_token(),
        )
        if body.get("login", {}).get("result") != "Success":
            raise NeoWikiError(f"NeoWiki login failed for user {self._user!r}")

    def _login_token(self) -> str:
        return self._call(action="query", meta="tokens", type="login")["query"][
            "tokens"
        ]["logintoken"]

    # ------------------------------------------------------------------- pages

    def _read(self, title: str, subject: str) -> tuple[int | None, dict[str, Any]]:
        """The page's latest revision id and content, or a fresh document."""
        body = self._call(
            action="query",
            prop="revisions",
            titles=title,
            rvprop="ids|content",
            rvslots="main",
        )
        page = body["query"]["pages"][0]
        if page.get("missing"):
            return None, {"subject": subject, "envelopes": []}
        revision = page["revisions"][0]
        try:
            document = json.loads(revision["slots"]["main"]["content"])
        except ValueError as exc:
            raise NeoWikiError(f"page {title} exists but does not hold JSON") from exc
        if not isinstance(document, dict) or not isinstance(
            document.get("envelopes"), list
        ):
            raise NeoWikiError(f"page {title} does not hold an envelope log")
        return revision["revid"], document

    def _edit(
        self, title: str, document: dict[str, Any], base: int | None, summary: str
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "action": "edit",
            "title": title,
            "text": json.dumps(document, indent=2, ensure_ascii=False),
            "contentmodel": "json",
            "summary": summary,
            "token": self._csrf,
        }
        # A new page must not silently replace one created a moment ago, and an
        # existing page must not be saved over a revision this write never saw.
        if base is None:
            params["createonly"] = "1"
        else:
            params["baserevid"] = str(base)
            params["nocreate"] = "1"
        edit = self._call(post=True, **params).get("edit", {})
        if edit.get("result") != "Success" or "newrevid" not in edit:
            raise NeoWikiError(f"NeoWiki did not save {title}: {edit}")
        return edit

    # --------------------------------------------------------------------- http

    def _call(self, *, post: bool = False, **params: Any) -> dict[str, Any]:
        params = {"format": "json", "formatversion": "2", **params}
        try:
            if post:
                response = self._client.post(self._api_url, data=params)
            else:
                response = self._client.get(self._api_url, params=params)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NeoWikiError(f"could not reach NeoWiki: {exc}") from exc
        if not isinstance(body, dict):
            raise NeoWikiError(
                "NeoWiki answered with something that is not a JSON object"
            )
        if "error" in body:
            error = body["error"]
            raise NeoWikiError(
                f"NeoWiki refused: {error.get('code')}: {error.get('info')}",
                code=error.get("code"),
            )
        return body
