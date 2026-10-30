"""The single source of namespaces and base IRIs: prov, dcterms, oco, demo.

Nothing elsewhere in Core constructs an IRI from a string literal. Everything
imports from here, so the Stage 2 port has one file to translate and one place
where the minting rules live.

Terms are ``pyoxigraph.NamedNode`` because that type serialises to correctly
escaped N-Triples, which is what keeps update-query construction safe. The
logic that uses them stays plain, so a PHP port swaps the term class and keeps
the algorithms.
"""

import hashlib
import re
from urllib.parse import quote

from echolot_prov.models import ABSOLUTE_IRI_PATTERN
from pyoxigraph import NamedNode

_ABSOLUTE_IRI = re.compile(ABSOLUTE_IRI_PATTERN)


class Namespace:
    """A vocabulary prefix. ``PROV.generatedAtTime`` yields a ``NamedNode``."""

    def __init__(self, base: str) -> None:
        self.base = base

    def __getattr__(self, term: str) -> NamedNode:
        if term.startswith("_"):  # never intercept dunder lookups
            raise AttributeError(term)
        return NamedNode(f"{self.base}{term}")

    def __repr__(self) -> str:
        return f"Namespace({self.base!r})"


RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
PROV = Namespace("http://www.w3.org/ns/prov#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
OCO = Namespace("https://w3id.org/oc/ontology/")

DEMO_BASE = "https://demo.echolot.eu"
"""Base for everything this Core mints.

In Stage 2 this becomes the deployment's configured base IRI. Per invariant 11
the mapping from a backend's native identifiers to IRIs is configuration, and
this constant is where that configuration lands.
"""

ECHOLOT = Namespace(f"{DEMO_BASE}/ns#")
"""Demo-local vocabulary, for the few relations PROV-O, dcterms and oco lack."""

AFFECTED_STATEMENT = ECHOLOT.affectedStatement
"""The per-statement link of invariant 6, from a snapshot to what it changed.

Points at a claim's IRI when the change was to a claim, and at a property IRI
when it was to a plain value on the subject. Named "statement" from the start
so that claims could arrive without changing the RDF shape, which is what
Phase 1.1b relied on.
"""

STATEMENT = ECHOLOT.statement
"""From a subject to one of its claims: ``<subject> echolot:statement <claim>``.

A claim is one value that needs an identity of its own, such as one creator
among several. Its values sit on the claim node, so a match or a correction can
attach to that one claim instead of to the subject as a whole.
"""

STORED_ENVELOPE = ECHOLOT.envelope
"""The envelope exactly as the producer sent it, kept as compact JSON.

Core also converts the envelope into triples. This keeps the original as well,
so a curator can see the producer's own words rather than Core's reading of
them. The two cannot drift, because a released snapshot is never rewritten.
"""

GRAPH_DATA = NamedNode(f"{DEMO_BASE}/graph/data")
GRAPH_PROVENANCE = NamedNode(f"{DEMO_BASE}/graph/provenance")
GRAPH_AGENTS_SOURCES = NamedNode(f"{DEMO_BASE}/graph/agents-sources")

NAMED_GRAPHS = (GRAPH_DATA, GRAPH_PROVENANCE, GRAPH_AGENTS_SOURCES)


def is_absolute_iri(value: str) -> bool:
    """Whether a value is already an IRI, by the contract's own rule."""
    return bool(_ABSOLUTE_IRI.match(value))


def _slug(value: str) -> str:
    """Percent-encode an opaque identifier so it is safe in an IRI path."""
    return quote(value, safe="")


def mint_snapshot(subject: str, activity_id: str, ended_at: str) -> NamedNode:
    """The IRI for a snapshot, derived from what produced it.

    Deterministic rather than a random UUID: invariant 10 makes the graph store
    a rebuildable projection, so replaying the same envelopes must reproduce the
    same identifiers. A UUID would make every rebuild a different graph.
    """
    digest = hashlib.sha256(
        "\n".join((subject, activity_id, ended_at)).encode("utf-8")
    ).hexdigest()[:16]
    return NamedNode(f"{DEMO_BASE}/snapshot/{digest}")


def mint_claim(
    subject: str, activity_id: str, ended_at: str, position: int
) -> NamedNode:
    """The IRI for a new claim, derived from the write that created it.

    Deterministic for the same reason as ``mint_snapshot``: replaying the same
    envelopes in the same order must reproduce the same claim identities, or a
    later envelope naming a claim would point at nothing after a rebuild.
    """
    digest = hashlib.sha256(
        "\n".join((subject, activity_id, ended_at, str(position))).encode("utf-8")
    ).hexdigest()[:16]
    return NamedNode(f"{DEMO_BASE}/claim/{digest}")


def mint_activity(activity_id: str) -> NamedNode:
    """The IRI for an activity, from the producer-local run id."""
    return NamedNode(f"{DEMO_BASE}/activity/{_slug(activity_id)}")


def mint_agent(agent_id: str) -> NamedNode:
    """The IRI for an agent.

    A person is usually already an ORCID IRI and is used as given; a software
    agent is usually a bare name and gets minted under the demo base. See
    ``docs/revisionfile.md`` entry 10: the contract does not constrain these to
    IRIs, so the rule lives here rather than in the schema.
    """
    return (
        NamedNode(agent_id)
        if is_absolute_iri(agent_id)
        else NamedNode(f"{DEMO_BASE}/agent/{_slug(agent_id)}")
    )


def mint_source(source_id: str) -> NamedNode:
    """The IRI for a source, by the same rule as an agent."""
    return (
        NamedNode(source_id)
        if is_absolute_iri(source_id)
        else NamedNode(f"{DEMO_BASE}/source/{_slug(source_id)}")
    )
