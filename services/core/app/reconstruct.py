"""Walks the snapshot chain backwards, inverting each delta (invariant 7).

Reconstruction is a read. It builds the past state in memory and never touches
the store, so asking what an entity looked like last March cannot disturb what
it looks like now.

Like ``provenance.py`` this is production-intended logic bound for a PHP port:
plain data in, plain data out, no framework and no store type in the signatures
beyond the small protocol it reads through.
"""

from datetime import datetime

import pyoxigraph as ox

from app.namespaces import (
    AFFECTED_STATEMENT,
    DCTERMS,
    ECHOLOT,
    OCO,
    PROV,
)
from app.provenance import Delta, Term, invert, parse_delta
from app.store import Store


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp, tolerating a trailing ``Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def apply_delta(state: set[tuple[Term, Term]], delta: Delta) -> set[tuple[Term, Term]]:
    """Apply a delta to an in-memory predicate/object set."""
    out = set(state)
    out -= {(p, o) for _, p, o in delta.removed}
    out |= {(p, o) for _, p, o in delta.added}
    return out


def _one(values: list[Term] | None) -> str | None:
    return values[0].value if values else None


def _all(values: list[Term] | None) -> list[str]:
    return sorted(v.value for v in values) if values else []


def reconstruct(
    store: Store, subject: ox.NamedNode, at: datetime
) -> set[tuple[Term, Term]]:
    """The state of a subject at a past moment.

    Starts from current state and undoes every snapshot generated after ``at``.
    Every snapshot in the chain is examined rather than stopping at the first
    old one, so an out-of-order or equal timestamp cannot truncate the walk.
    """
    state = store.subject_state(subject)

    for snapshot in store.chain(subject):
        props = store.properties_of(snapshot)
        generated = _one(props.get(PROV.generatedAtTime.value))
        if generated is None or parse_timestamp(generated) <= at:
            continue
        query = _one(props.get(OCO.hasUpdateQuery.value))
        if query is None:
            continue
        state = apply_delta(state, invert(parse_delta(query)))

    return state


def _batch_of(store: Store, props: dict[str, list[Term]]) -> str | None:
    """The workflow a snapshot belongs to, if it came from one.

    The batch id sits on the activity, not the snapshot, because a workflow
    groups runs rather than records.
    """
    activity = props.get(PROV.wasGeneratedBy.value)
    if not activity:
        return None
    return _one(store.properties_of(activity[0]).get(ECHOLOT.batch.value))


def history(store: Store, subject: ox.NamedNode) -> list[dict]:
    """The snapshot chain, newest first, with what a curator needs to read it."""
    out: list[dict] = []
    for snapshot in store.chain(subject):
        props = store.properties_of(snapshot)
        out.append(
            {
                "snapshot": snapshot.value,
                "generated_at": _one(props.get(PROV.generatedAtTime.value)),
                "description": _one(props.get(DCTERMS.description.value)),
                "agents": _all(props.get(PROV.wasAttributedTo.value)),
                "sources": _all(props.get(PROV.hadPrimarySource.value)),
                "statements": _all(props.get(AFFECTED_STATEMENT.value)),
                "validation_state": _one(props.get(ECHOLOT.validationState.value)),
                "activity": _one(props.get(PROV.wasGeneratedBy.value)),
                "derived_from": _one(props.get(PROV.wasDerivedFrom.value)),
                "batch": _batch_of(store, props),
            }
        )
    return out
