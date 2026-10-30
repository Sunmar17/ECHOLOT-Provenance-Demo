"""FastAPI app: POST /write, GET /reconstruct, GET /history.

A stand-in for NeoWiki. The FastAPI layer is a placeholder and stays thin; the
logic worth keeping lives in ``provenance.py`` and ``reconstruct.py``.

The request body is taken as an untyped ``dict`` on purpose. Typing it as the
Pydantic ``Envelope`` would let FastAPI reject bad input first, in its own error
format, and the committed JSON Schema would never run. Invariant 3 says Core
validates against that schema, so validation is ours to do.
"""

import json
import os
from pathlib import Path
from typing import Any

import pyoxigraph as ox
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import provenance, reconstruct, validation
from app.namespaces import (
    DCTERMS,
    OCO,
    PROV,
    STATEMENT,
    STORED_ENVELOPE,
    is_absolute_iri,
    mint_claim,
)
from app.store import Store

_PLAIN_STRING = "http://www.w3.org/2001/XMLSchema#string"
"""xsd:string is the implicit datatype of a plain literal; never echoed back."""

app = FastAPI(
    title="ECHOLOT Core (stand-in)",
    description="Validates provenance envelopes and stores them as PROV-O.",
)

store = Store(os.environ.get("ECHOLOT_STORE_PATH") or None)

# The curator UI: plain HTML and fetch, served from this package. Mounted last
# in the file so every API route is registered before it.
UI_DIR = Path(__file__).parent / "ui"
app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


@app.get("/")
def root() -> Any:
    """Send a person who opens the bare host to the page they want."""
    return RedirectResponse("/ui/")


# ------------------------------------------------------------------- terms


def term_to_json(term: provenance.Term) -> dict[str, str]:
    """A term in the SPARQL 1.1 Results JSON shape."""
    if isinstance(term, ox.NamedNode):
        return {"type": "uri", "value": term.value}
    out = {"type": "literal", "value": term.value}
    if term.language:
        out["xml:lang"] = term.language
    elif term.datatype and term.datatype.value != _PLAIN_STRING:
        out["datatype"] = term.datatype.value
    return out


def term_from_json(raw: Any) -> provenance.Term:
    """Read a term in the SPARQL 1.1 Results JSON shape."""
    if not isinstance(raw, dict) or "value" not in raw:
        raise ValueError("each value must be an object with a 'value' key")
    value = raw["value"]
    if not isinstance(value, str):
        raise ValueError("'value' must be a string")
    kind = raw.get("type", "literal")
    if kind == "uri":
        if not is_absolute_iri(value):
            raise ValueError(f"not an absolute IRI: {value!r}")
        return ox.NamedNode(value)
    if kind != "literal":
        raise ValueError(f"unsupported term type {kind!r}")
    if "xml:lang" in raw:
        return ox.Literal(value, language=raw["xml:lang"])
    if "datatype" in raw:
        return ox.Literal(value, datatype=ox.NamedNode(raw["datatype"]))
    return ox.Literal(value)


def _errors(errors: list[validation.Error]) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"errors": [e.as_dict() for e in errors]}
    )


# ------------------------------------------------------------------- write


@app.post("/write")
def write(body: dict) -> Any:
    """The single write path. Validates, then commits data and provenance as one.

    Nothing is written unless every check passes, so a rejected request leaves
    all three graphs exactly as they were (invariant 3).
    """
    envelope = body.get("envelope")
    errors = validation.validate(envelope)
    if errors:
        return _errors(errors)

    statements = body.get("statements", {})
    if not isinstance(statements, dict):
        return _errors(
            [validation.Error("/statements", "statements must be an object")]
        )

    target = envelope["target"]
    subject = ox.NamedNode(target["subject"])
    activity = envelope["activity"]

    claims = body.get("claims", [])
    claim_errors = _check_claims(claims, store.claims_of(subject))
    if claim_errors:
        return _errors(claim_errors)

    # A claim with no identity is new; Core mints it from the write that made
    # it, so replaying the same envelopes reproduces the same IRI.
    claim_iris = [
        ox.NamedNode(c["statement"])
        if c.get("statement")
        else mint_claim(subject.value, activity["id"], activity["ended_at"], i)
        for i, c in enumerate(claims)
    ]

    # The envelope must describe this write, or the provenance it produces is a
    # confident account of something that did not happen. Plain values and new
    # claims are declared by property; an existing claim by property and IRI.
    declared_plain = set(provenance.declared_properties(target))
    declared_named = provenance.declared_claims(target)
    supplied_plain = set(statements) | {
        p for c in claims if not c.get("statement") for p in c["values"]
    }
    supplied_named = {
        (p, c["statement"]) for c in claims if c.get("statement") for p in c["values"]
    }
    if declared_plain != supplied_plain or declared_named != supplied_named:
        only_payload = _pairs(
            supplied_plain - declared_plain, supplied_named - declared_named
        )
        only_envelope = _pairs(
            declared_plain - supplied_plain, declared_named - supplied_named
        )
        return _errors(
            [
                validation.Error(
                    "/statements",
                    "payload and envelope target.statements do not match; "
                    f"only in payload: {only_payload}; "
                    f"only in envelope: {only_envelope}",
                )
            ]
        )

    try:
        desired = _terms(statements)
        desired_claims = [_terms(c["values"]) for c in claims]
    except ValueError as exc:
        return _errors([validation.Error("/statements", str(exc))])

    # One delta per node: the subject, and each claim. A new claim also gains
    # its link from the subject. Together they are still one write.
    linked = store.claims_of(subject)
    deltas = [
        provenance.compute_delta(
            subject, desired, store.current_values(subject, _nodes(desired))
        )
    ]
    for iri, values in zip(claim_iris, desired_claims, strict=True):
        current_claim = store.current_values(iri, _nodes(values))
        deltas.append(provenance.compute_delta(iri, values, current_claim))
        if iri not in linked:
            deltas.append(provenance.Delta((), ((subject, STATEMENT, iri),)))
    delta = provenance.merge_deltas(deltas)

    affected = set(statements) | {iri.value for iri in claim_iris}
    snapshot, update = provenance.build_write_update(
        envelope, delta, store.head_snapshot(subject), affected
    )

    store.apply(update)

    return {
        "snapshot": snapshot.value,
        "subject": subject.value,
        "changed": not delta.is_empty,
        "added": len(delta.added),
        "removed": len(delta.removed),
        "claims": [iri.value for iri in claim_iris],
    }


def _terms(values: dict) -> dict[str, set[provenance.Term]]:
    """Property to values, read from the JSON term shape."""
    return {
        predicate: {term_from_json(v) for v in raw} for predicate, raw in values.items()
    }


def _nodes(values: dict) -> list[ox.NamedNode]:
    return [ox.NamedNode(p) for p in values]


def _pairs(plain: set[str], named: set[tuple[str, str]]) -> list[str]:
    """Differences in the declared check, readable in one error message."""
    return sorted(plain) + sorted(f"{p} on {c}" for p, c in named)


def _check_claims(claims: Any, linked: set[ox.NamedNode]) -> list[validation.Error]:
    """The claims part of a write is well formed and names only this subject's.

    A claim from another subject is refused: it would let one record's write
    change another's data under the wrong provenance.
    """
    if not isinstance(claims, list):
        return [validation.Error("/claims", "claims must be a list")]
    seen: set[str] = set()
    errors: list[validation.Error] = []
    for i, claim in enumerate(claims):
        path = f"/claims/{i}"
        if not isinstance(claim, dict) or not isinstance(claim.get("values"), dict):
            errors.append(validation.Error(path, "a claim needs a 'values' object"))
            continue
        if not claim["values"]:
            errors.append(validation.Error(path, "a claim needs at least one value"))
        statement = claim.get("statement")
        if statement is None:
            continue
        if not isinstance(statement, str) or not is_absolute_iri(statement):
            errors.append(validation.Error(path, "statement must be an absolute IRI"))
        elif ox.NamedNode(statement) not in linked:
            errors.append(
                validation.Error(path, f"{statement} is not a claim of this subject")
            )
        elif statement in seen:
            errors.append(validation.Error(path, f"{statement} appears twice"))
        seen.add(statement)
    return errors


# -------------------------------------------------------------------- read


@app.get("/history")
def get_history(subject: str = Query(...)) -> Any:
    """The snapshot chain for a subject, newest first."""
    if not is_absolute_iri(subject):
        return _errors(
            [validation.Error("/subject", "subject must be an absolute IRI")]
        )
    return {
        "subject": subject,
        "snapshots": reconstruct.history(store, ox.NamedNode(subject)),
    }


@app.get("/reconstruct")
def get_reconstruct(subject: str = Query(...), at: str = Query(...)) -> Any:
    """The state of a subject at a past moment."""
    if not is_absolute_iri(subject):
        return _errors(
            [validation.Error("/subject", "subject must be an absolute IRI")]
        )
    try:
        moment = reconstruct.parse_timestamp(at)
    except ValueError:
        return _errors([validation.Error("/at", f"not an ISO 8601 timestamp: {at!r}")])

    node = ox.NamedNode(subject)
    state = reconstruct.reconstruct(store, node, moment)
    # A row on the subject itself has no claim. A row on a claim names it, and
    # the link from subject to claim is left out: the claim's rows say it.
    rows = [
        {
            "statement": None if s == node else s.value,
            "predicate": p.value,
            "object": term_to_json(o),
        }
        for s, p, o in state
        if not (s == node and p == STATEMENT)
    ]
    return {
        "subject": subject,
        "at": at,
        "statements": sorted(
            rows,
            key=lambda r: (r["statement"] or "", r["predicate"], r["object"]["value"]),
        ),
    }


@app.get("/subjects")
def get_subjects() -> Any:
    """Every subject that has provenance. The UI's starting point.

    ``labels`` gives each subject's name where its data has one, so the page
    can show "Probenvideo" rather than an IRI.
    """
    subjects = store.subjects()
    return {
        "subjects": [s.value for s in subjects],
        "labels": {
            s.value: label for s in subjects if (label := store.label_of(s)) is not None
        },
    }


@app.get("/snapshot")
def get_snapshot(uri: str = Query(...)) -> Any:
    """One snapshot in full, including the envelope its producer sent.

    Separate from ``/history`` so the timeline stays small: a full envelope is
    about a kilobyte, and a curator only wants one at a time.
    """
    if not is_absolute_iri(uri):
        return _errors(
            [validation.Error("/uri", "snapshot uri must be an absolute IRI")]
        )

    properties = store.properties_of(ox.NamedNode(uri))
    if not properties:
        return JSONResponse(
            status_code=404,
            content={"errors": [{"path": "/uri", "message": "no such snapshot"}]},
        )

    def one(predicate: str) -> str | None:
        values = properties.get(predicate)
        return values[0].value if values else None

    stored = one(STORED_ENVELOPE.value)
    return {
        "snapshot": uri,
        "generated_at": one(PROV.generatedAtTime.value),
        "description": one(DCTERMS.description.value),
        "update_query": one(OCO.hasUpdateQuery.value),
        # Parsed, so the page can show it as structured JSON rather than a
        # single long string. Byte-identical to what the producer sent.
        "envelope": json.loads(stored) if stored else None,
    }


@app.get("/batch")
def get_batch(id: str = Query(...)) -> Any:
    """Every snapshot one workflow run produced, newest first.

    This is what makes a workflow traceable. A batch spans stages and can span
    subjects, so each row names its own subject.
    """
    rows = []
    for snapshot in store.snapshots_in_batch(id):
        properties = store.properties_of(snapshot)

        def one(predicate: str, props: dict = properties) -> str | None:
            values = props.get(predicate)
            return values[0].value if values else None

        rows.append(
            {
                "snapshot": snapshot.value,
                "subject": one(PROV.specializationOf.value),
                "generated_at": one(PROV.generatedAtTime.value),
                "description": one(DCTERMS.description.value),
                "activity": one(PROV.wasGeneratedBy.value),
            }
        )
    rows.sort(key=lambda r: (r["generated_at"] or "", r["snapshot"]), reverse=True)
    return {"batch": id, "snapshots": rows}
