"""Envelope to PROV-O snapshot. Ported to PHP in Stage 2: keep it framework-free.

Nothing here imports FastAPI or touches a store. Functions take plain data and
return triples and strings, so the Stage 2 port swaps the term class and keeps
the algorithms.

Two tiers, per invariant 6: one activity and one snapshot record per run, with
lightweight per-statement links. Never one ``prov:Activity`` per triple.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

import pyoxigraph as ox

from app.namespaces import (
    AFFECTED_STATEMENT,
    DCTERMS,
    ECHOLOT,
    GRAPH_AGENTS_SOURCES,
    GRAPH_DATA,
    GRAPH_PROVENANCE,
    OCO,
    PROV,
    RDF,
    STORED_ENVELOPE,
    XSD,
    mint_activity,
    mint_agent,
    mint_snapshot,
    mint_source,
)

Term = ox.NamedNode | ox.BlankNode | ox.Literal
Triple = tuple[Term, Term, Term]

ROLE_PREDICATE = {
    "executed": ECHOLOT.executedBy,
    "commissioned": ECHOLOT.commissionedBy,
    "validated": ECHOLOT.validatedBy,
}
"""One predicate per agent role.

A qualified ``prov:Association`` would be the textbook encoding, but invariant 6
asks for lightweight links and a role-per-predicate stays queryable without
adding a node per agent per run.
"""


def _text(value: str) -> ox.Literal:
    return ox.Literal(value)


def _datetime(value: str) -> ox.Literal:
    return ox.Literal(value, datatype=XSD.dateTime)


def _decimal(value: float) -> ox.Literal:
    return ox.Literal(str(value), datatype=XSD.decimal)


def _integer(value: int) -> ox.Literal:
    return ox.Literal(str(value), datatype=XSD.integer)


# --------------------------------------------------------------------- delta


@dataclass(frozen=True)
class Delta:
    """What actually changed in the data graph.

    Distinct from the envelope's ``Target.statements``, which is the producer's
    *intent*. Writing a value that is already current yields an empty delta;
    recording both is more honest than recording either alone.
    """

    removed: tuple[Triple, ...]
    added: tuple[Triple, ...]

    @property
    def is_empty(self) -> bool:
        return not self.removed and not self.added


def compute_delta(
    subject: ox.NamedNode,
    desired: dict[str, set[Term]],
    current: dict[str, set[Term]],
) -> Delta:
    """Diff desired values against current ones, per predicate.

    A predicate present in ``desired`` with no values deletes all of its current
    values, which is how a ``deletion`` activity is expressed.
    """
    removed: list[Triple] = []
    added: list[Triple] = []
    for predicate, wanted in desired.items():
        p = ox.NamedNode(predicate)
        have = current.get(predicate, set())
        removed.extend((subject, p, o) for o in have - wanted)
        added.extend((subject, p, o) for o in wanted - have)
    return Delta(tuple(_sorted(removed)), tuple(_sorted(added)))


def _sorted(triples: Iterable[Triple]) -> list[Triple]:
    """Deterministic ordering, so the same inputs always produce the same query."""
    return sorted(triples, key=lambda t: (str(t[0]), str(t[1]), str(t[2])))


def _data_block(keyword: str, graph: ox.NamedNode, triples: Iterable[Triple]) -> str:
    body = "".join(f"{s} {p} {o} .\n" for s, p, o in triples)
    return f"{keyword} DATA {{ GRAPH {graph} {{\n{body}}} }}"


def serialise_delta(delta: Delta, graph: ox.NamedNode = GRAPH_DATA) -> str:
    """Render a delta as the canonical update query stored in ``oco:hasUpdateQuery``.

    Both blocks are always emitted, even when empty, so inverting the delta is a
    swap of two keywords rather than a SPARQL parse. Terms are rendered by
    pyoxigraph, which escapes them as N-Triples; no value is ever interpolated
    into the query as a raw string.
    """
    return (
        _data_block("DELETE", graph, delta.removed)
        + ";\n"
        + _data_block("INSERT", graph, delta.added)
    )


# The body is zero or more complete lines, and the closing brace pair must start
# a line of its own. N-Triples escapes a literal's own newlines, so a literal is
# always on one line and a crafted "} }" inside one can never sit at a line
# start. A non-greedy ".*?" here would stop at that in-literal text instead.
_BLOCK = re.compile(r"(DELETE|INSERT) DATA \{ GRAPH <[^>]*> \{\n((?:[^\n]*\n)*?)\} \}")


def parse_delta(query: str) -> Delta:
    """Read a stored update query back into a delta."""
    found: dict[str, tuple[Triple, ...]] = {}
    for keyword, body in _BLOCK.findall(query):
        triples = tuple(
            (q.subject, q.predicate, q.object)
            for q in ox.parse(body, format=ox.RdfFormat.N_TRIPLES)
        )
        found[keyword] = triples
    return Delta(found.get("DELETE", ()), found.get("INSERT", ()))


def invert(delta: Delta) -> Delta:
    """The delta that undoes this one. Reconstruction applies these backwards."""
    return Delta(removed=delta.added, added=delta.removed)


# ------------------------------------------------------------------ snapshot


def describe(envelope: dict) -> str:
    """A one-line human summary, for ``dcterms:description`` and the UI timeline."""
    activity = envelope["activity"]
    method = envelope["method"]
    # Whoever is most responsible for this run. A validation activity has no
    # "executed" agent; the curator who validated it is the actor.
    by_role = {a["role"]: a["id"] for a in envelope["agents"]}
    actor = next(
        (by_role[r] for r in ("executed", "validated", "commissioned") if r in by_role),
        "unknown agent",
    )
    parts = [f"{activity['type']} by {actor}", f"method {method['name']}"]
    if method.get("confidence") is not None:
        parts.append(f"confidence {method['confidence']}")
    parts.append(envelope["validation"]["state"])
    return ", ".join(parts)


def snapshot_triples(
    envelope: dict,
    snapshot: ox.NamedNode,
    previous_head: ox.NamedNode | None,
    update_query: str,
) -> list[Triple]:
    """The provenance record for one run: one snapshot, one activity."""
    target = envelope["target"]
    activity = envelope["activity"]
    method = envelope["method"]
    validation = envelope["validation"]

    subject = ox.NamedNode(target["subject"])
    activity_node = mint_activity(activity["id"])

    out: list[Triple] = [
        (snapshot, RDF.type, PROV.Entity),
        (snapshot, PROV.specializationOf, subject),
        (snapshot, PROV.generatedAtTime, _datetime(activity["ended_at"])),
        (snapshot, PROV.wasGeneratedBy, activity_node),
        (snapshot, DCTERMS.description, _text(describe(envelope))),
        (snapshot, OCO.hasUpdateQuery, _text(update_query)),
        # The producer's own words, kept verbatim beside Core's reading of them.
        # Sorted keys and no spaces, so the same envelope always stores the same
        # bytes. The term object escapes it; never format JSON into a query.
        (
            snapshot,
            STORED_ENVELOPE,
            _text(json.dumps(envelope, sort_keys=True, separators=(",", ":"))),
        ),
        (activity_node, RDF.type, PROV.Activity),
        (activity_node, ECHOLOT.activityType, _text(activity["type"])),
        (activity_node, PROV.startedAtTime, _datetime(activity["started_at"])),
        (activity_node, PROV.endedAtTime, _datetime(activity["ended_at"])),
        (activity_node, ECHOLOT.method, _text(method["name"])),
    ]

    # Snapshot chain (invariant 7): each snapshot derives from the previous head.
    if previous_head is not None:
        out.append((snapshot, PROV.wasDerivedFrom, previous_head))

    if activity.get("batch_of"):
        out.append((activity_node, ECHOLOT.batch, _text(activity["batch_of"])))
    if method.get("confidence") is not None:
        out.append((activity_node, ECHOLOT.confidence, _decimal(method["confidence"])))
    if method.get("candidate_rank") is not None:
        out.append(
            (activity_node, ECHOLOT.candidateRank, _integer(method["candidate_rank"]))
        )

    # Lightweight per-statement links (invariant 6), one per declared statement.
    out.extend(
        (snapshot, AFFECTED_STATEMENT, ox.NamedNode(iri))
        for iri in target.get("statements", [])
    )

    for agent in envelope["agents"]:
        node = mint_agent(agent["id"])
        out.append((snapshot, PROV.wasAttributedTo, node))
        out.append((activity_node, PROV.wasAssociatedWith, node))
        out.append((activity_node, ROLE_PREDICATE[agent["role"]], node))

    for source in envelope.get("sources", []):
        node = mint_source(source["id"])
        out.append((snapshot, PROV.hadPrimarySource, node))

    out.append((snapshot, ECHOLOT.validationState, _text(validation["state"])))
    if validation.get("by"):
        out.append((snapshot, ECHOLOT.validatedByAgent, _text(validation["by"])))
    if validation.get("at"):
        out.append((snapshot, ECHOLOT.validatedAt, _datetime(validation["at"])))

    rights = envelope.get("rights") or {}
    if rights.get("assertion"):
        out.append((snapshot, DCTERMS.rights, _text(rights["assertion"])))
    if rights.get("basis"):
        out.append((snapshot, ECHOLOT.rightsBasis, _text(rights["basis"])))

    return _sorted(out)


def agent_source_triples(envelope: dict) -> list[Triple]:
    """Descriptions of the agents and sources, kept in their own graph."""
    out: list[Triple] = []
    for agent in envelope["agents"]:
        node = mint_agent(agent["id"])
        kind = PROV.SoftwareAgent if agent["type"] == "software" else PROV.Person
        out.append((node, RDF.type, PROV.Agent))
        out.append((node, RDF.type, kind))
        out.append((node, DCTERMS.identifier, _text(agent["id"])))
        if agent["type"] == "software":
            out.append((node, ECHOLOT.model, _text(agent["model"])))
            out.append((node, ECHOLOT.configHash, _text(agent["config_hash"])))

    for source in envelope.get("sources", []):
        node = mint_source(source["id"])
        out.append((node, RDF.type, PROV.Entity))
        out.append((node, DCTERMS.identifier, _text(source["id"])))
        out.append((node, ECHOLOT.snapshot, _text(source["snapshot"])))
        if source.get("harvested_at"):
            out.append((node, PROV.generatedAtTime, _datetime(source["harvested_at"])))

    return _sorted(out)


def build_write_update(
    envelope: dict, delta: Delta, previous_head: ox.NamedNode | None
) -> tuple[ox.NamedNode, str]:
    """The snapshot IRI and the single update that commits data and provenance.

    One string, one ``Store.apply`` call. That single update is the transaction
    boundary invariant 4 requires: the data change and its provenance become
    real together or not at all.
    """
    activity = envelope["activity"]
    snapshot = mint_snapshot(
        envelope["target"]["subject"], activity["id"], activity["ended_at"]
    )
    update_query = serialise_delta(delta)

    operations = [
        serialise_delta(delta),
        _data_block(
            "INSERT",
            GRAPH_PROVENANCE,
            snapshot_triples(envelope, snapshot, previous_head, update_query),
        ),
        _data_block("INSERT", GRAPH_AGENTS_SOURCES, agent_source_triples(envelope)),
    ]
    return snapshot, ";\n".join(operations)
