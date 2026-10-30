"""``CoreClient``: the single write path for data plus envelope (invariant 2).

Nothing here knows which Core it talks to (invariant 8). It posts to a base URL
and reads back a structured error; swapping the Phase 2 stand-in for NeoWiki in
Stage 2 should be a change of URL and credentials, not of code.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from echolot_prov.models import Envelope

XSD = "http://www.w3.org/2001/XMLSchema#"


class Iri(str):
    """A value that is an IRI rather than text.

    A plain string is always a literal. An IRI is never guessed from a value's
    shape, because a legitimate note like "see https://example.org" would then
    silently become a link instead of the text somebody wrote.
    """

    __slots__ = ()


def iri(value: str) -> Iri:
    """Mark a value as an IRI rather than a literal."""
    return Iri(value)


def term(value: Any) -> dict[str, str]:
    """Render a Python value in the SPARQL 1.1 Results JSON term shape.

    The shape Core already speaks, so no translation happens at the boundary.
    """
    if isinstance(value, Iri):
        return {"type": "uri", "value": str(value)}
    if isinstance(value, bool):
        # Checked before int: in Python, bool is a subclass of int.
        return {
            "type": "literal",
            "value": "true" if value else "false",
            "datatype": f"{XSD}boolean",
        }
    if isinstance(value, int):
        return {"type": "literal", "value": str(value), "datatype": f"{XSD}integer"}
    if isinstance(value, (float, Decimal)):
        return {"type": "literal", "value": str(value), "datatype": f"{XSD}decimal"}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return {
            "type": "literal",
            "value": value.isoformat(),
            "datatype": f"{XSD}dateTime",
        }
    if isinstance(value, date):
        return {"type": "literal", "value": value.isoformat(), "datatype": f"{XSD}date"}
    if isinstance(value, str):
        return {"type": "literal", "value": value}
    raise TypeError(f"cannot express {type(value).__name__} as an RDF term")


def _claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    """One claim in the shape Core reads: an optional identity and its values."""
    values = claim.get("values")
    if not values:
        raise ValueError("a claim needs at least one property in 'values'")
    out: dict[str, Any] = {
        "values": {
            predicate: [term(v) for v in vals] for predicate, vals in values.items()
        }
    }
    if claim.get("statement"):
        out["statement"] = str(claim["statement"])
    return out


class CoreError(RuntimeError):
    """Core refused or could not serve a request."""


class CoreValidationError(CoreError):
    """Core rejected the write, and said exactly why.

    Carries Core's structured errors so a producer can see which field was
    refused rather than guessing from a status code.
    """

    def __init__(self, errors: Sequence[Mapping[str, str]]) -> None:
        self.errors = [dict(e) for e in errors]
        detail = (
            "; ".join(
                f"{e.get('path', '?')}: {e.get('message', '')}" for e in self.errors
            )
            or "no detail given"
        )
        super().__init__(f"Core rejected the write: {detail}")


class CoreClient:
    """The only function in the system that writes.

    ``client`` is injectable: tests pass an ``httpx.MockTransport`` client, or
    the Core app itself through ``TestClient``, so neither a server nor a port
    is needed to exercise the real write path.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._owns_client = client is None

    def write(
        self,
        subject: str,
        statements: Mapping[str, Sequence[Any]],
        envelope: Envelope,
        *,
        claims: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Write data and its provenance together. The only write path.

        ``statements`` are plain values on the subject, property to values.

        ``claims`` are values that need an identity of their own, such as one
        creator among several. Each is a mapping with ``values`` (property to
        values, set on that claim) and, for a claim that already exists, its
        ``statement`` IRI. A claim with no ``statement`` is new: Core mints its
        IRI and returns it in ``claims``, in the order sent.

        Returns Core's response, which carries the new snapshot's IRI. Raises
        :class:`CoreValidationError` if Core refused, in which case nothing was
        written to any graph.
        """
        payload: dict[str, Any] = {
            "envelope": envelope.model_dump(mode="json", exclude_none=True),
            "statements": {
                predicate: [term(v) for v in values]
                for predicate, values in statements.items()
            },
        }
        if claims:
            payload["claims"] = [_claim(c) for c in claims]
        if payload["envelope"]["target"]["subject"] != subject:
            raise ValueError(
                f"subject {subject!r} does not match the envelope's target "
                f"{payload['envelope']['target']['subject']!r}"
            )

        try:
            response = self._client.post("/write", json=payload)
        except httpx.HTTPError as exc:
            raise CoreError(f"could not reach Core: {exc}") from exc

        if response.status_code == 422:
            raise CoreValidationError(response.json().get("errors", []))
        if response.status_code >= 400:
            raise CoreError(f"Core returned {response.status_code}: {response.text}")
        return response.json()

    def close(self) -> None:
        """Close the transport, if this client created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "CoreClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
