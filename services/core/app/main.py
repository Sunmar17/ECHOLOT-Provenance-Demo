"""FastAPI app: POST /write, GET /reconstruct, GET /history.

A stand-in for NeoWiki. The FastAPI layer is a placeholder and stays thin; the
logic worth keeping lives in ``provenance.py`` and ``reconstruct.py``.

The request body is taken as an untyped ``dict`` on purpose. Typing it as the
Pydantic ``Envelope`` would let FastAPI reject bad input first, in its own error
format, and the committed JSON Schema would never run. Invariant 3 says Core
validates against that schema, so validation is ours to do.
"""

import os
from typing import Any

import pyoxigraph as ox
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from app import provenance, reconstruct, validation
from app.namespaces import is_absolute_iri
from app.store import Store

_PLAIN_STRING = "http://www.w3.org/2001/XMLSchema#string"
"""xsd:string is the implicit datatype of a plain literal; never echoed back."""

app = FastAPI(
    title="ECHOLOT Core (stand-in)",
    description="Validates provenance envelopes and stores them as PROV-O.",
)

store = Store(os.environ.get("ECHOLOT_STORE_PATH") or None)


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

    # The envelope must describe this write, or the provenance it produces is a
    # confident account of something that did not happen.
    declared = set(target.get("statements", []))
    supplied = set(statements)
    if declared != supplied:
        return _errors(
            [
                validation.Error(
                    "/statements",
                    "payload predicates do not match envelope target.statements; "
                    f"only in payload: {sorted(supplied - declared)}; "
                    f"only in envelope: {sorted(declared - supplied)}",
                )
            ]
        )

    try:
        desired = {
            predicate: {term_from_json(v) for v in values}
            for predicate, values in statements.items()
        }
    except ValueError as exc:
        return _errors([validation.Error("/statements", str(exc))])

    subject = ox.NamedNode(target["subject"])
    current = store.current_values(subject, (ox.NamedNode(p) for p in desired))
    delta = provenance.compute_delta(subject, desired, current)
    snapshot, update = provenance.build_write_update(
        envelope, delta, store.head_snapshot(subject)
    )

    store.apply(update)

    return {
        "snapshot": snapshot.value,
        "subject": subject.value,
        "changed": not delta.is_empty,
        "added": len(delta.added),
        "removed": len(delta.removed),
    }


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

    state = reconstruct.reconstruct(store, ox.NamedNode(subject), moment)
    return {
        "subject": subject,
        "at": at,
        "statements": sorted(
            ({"predicate": p.value, "object": term_to_json(o)} for p, o in state),
            key=lambda row: (row["predicate"], row["object"]["value"]),
        ),
    }
