"""pyoxigraph store: named graphs ``data``, ``provenance``, ``agents-sources``.

Every mutation goes through :meth:`Store.apply`, which runs exactly one SPARQL
UPDATE. pyoxigraph 0.5 has no transaction API, so that single update *is* the
transaction boundary invariant 4 requires: a failure anywhere in the update
string leaves nothing written.

Reads are expressed as quad-pattern matches rather than SPARQL wherever
possible. It is less clever and much easier to port.
"""

from collections.abc import Iterable
from pathlib import Path

import pyoxigraph as ox

from app.namespaces import (
    ECHOLOT,
    GRAPH_DATA,
    GRAPH_PROVENANCE,
    NAMED_GRAPHS,
    PROV,
    RDFS,
    STATEMENT,
)

Term = ox.NamedNode | ox.BlankNode | ox.Literal


class Store:
    """The demo's only store. In production this would be a projection."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._store = ox.Store(str(path)) if path else ox.Store()

    # ---------------------------------------------------------------- writing

    def apply(self, update: str) -> None:
        """Run one SPARQL UPDATE. The single mutation point for the whole Core."""
        self._store.update(update)

    # ---------------------------------------------------------------- reading

    def current_values(
        self, subject: ox.NamedNode, predicates: Iterable[ox.NamedNode]
    ) -> dict[str, set[Term]]:
        """Current objects in the data graph, per predicate.

        This is the "before" half of the delta. Only Core can supply it: a
        producer's view of current state may be stale by the time its write
        lands.
        """
        return {
            p.value: {
                q.object
                for q in self._store.quads_for_pattern(subject, p, None, GRAPH_DATA)
            }
            for p in predicates
        }

    def subject_state(self, subject: ox.NamedNode) -> set[tuple[Term, Term]]:
        """Every predicate/object pair currently asserted about a subject."""
        return {
            (q.predicate, q.object)
            for q in self._store.quads_for_pattern(subject, None, None, GRAPH_DATA)
        }

    def claims_of(self, subject: ox.NamedNode) -> set[ox.NamedNode]:
        """The claims a subject currently links to with ``echolot:statement``."""
        return {
            q.object
            for q in self._store.quads_for_pattern(subject, STATEMENT, None, GRAPH_DATA)
            if isinstance(q.object, ox.NamedNode)
        }

    def subject_triples(self, subject: ox.NamedNode) -> set[tuple[Term, Term, Term]]:
        """The subject's own triples and those of every claim it links to.

        A claim belongs to its subject's state, so reconstructing the subject
        reconstructs its claims too.
        """
        nodes = [subject, *self.claims_of(subject)]
        return {
            (q.subject, q.predicate, q.object)
            for node in nodes
            for q in self._store.quads_for_pattern(node, None, None, GRAPH_DATA)
        }

    def label_of(self, subject: ox.NamedNode) -> str | None:
        """The subject's ``rdfs:label``, for showing a name instead of an IRI."""
        for q in self._store.quads_for_pattern(subject, RDFS.label, None, GRAPH_DATA):
            return q.object.value
        return None

    def snapshots_of(self, subject: ox.NamedNode) -> set[ox.NamedNode]:
        """Every snapshot that is a version of this subject."""
        return {
            q.subject
            for q in self._store.quads_for_pattern(
                None, PROV.specializationOf, subject, GRAPH_PROVENANCE
            )
        }

    def head_snapshot(self, subject: ox.NamedNode) -> ox.NamedNode | None:
        """The newest snapshot of a subject, or None if it has no history.

        The head is the snapshot nothing else derives from, rather than the
        latest ``prov:generatedAtTime``. Two snapshots can share a timestamp;
        the derivation chain is unambiguous.
        """
        snapshots = self.snapshots_of(subject)
        if not snapshots:
            return None
        derived_from = {
            q.object
            for snap in snapshots
            for q in self._store.quads_for_pattern(
                snap, PROV.wasDerivedFrom, None, GRAPH_PROVENANCE
            )
        }
        heads = snapshots - derived_from
        # A well-formed chain has exactly one head; sort for determinism if not.
        return sorted(heads, key=lambda n: n.value)[0] if heads else None

    def parent_of(self, snapshot: ox.NamedNode) -> ox.NamedNode | None:
        """The snapshot this one was derived from, if any."""
        for q in self._store.quads_for_pattern(
            snapshot, PROV.wasDerivedFrom, None, GRAPH_PROVENANCE
        ):
            return q.object
        return None

    def chain(self, subject: ox.NamedNode) -> list[ox.NamedNode]:
        """The snapshot chain, newest first, walking ``wasDerivedFrom`` back."""
        out: list[ox.NamedNode] = []
        seen: set[str] = set()
        node = self.head_snapshot(subject)
        while node is not None and node.value not in seen:
            out.append(node)
            seen.add(node.value)
            node = self.parent_of(node)
        return out

    def properties_of(self, snapshot: ox.NamedNode) -> dict[str, list[Term]]:
        """Everything asserted about a snapshot, grouped by predicate."""
        grouped: dict[str, list[Term]] = {}
        for q in self._store.quads_for_pattern(snapshot, None, None, GRAPH_PROVENANCE):
            grouped.setdefault(q.predicate.value, []).append(q.object)
        return grouped

    # ------------------------------------------------------------------ tests

    def counts(self) -> dict[str, int]:
        """Quad count per named graph. Used to prove a rejected write wrote nothing."""
        return {
            g.value: sum(1 for _ in self._store.quads_for_pattern(None, None, None, g))
            for g in NAMED_GRAPHS
        }

    def snapshots_in_batch(self, batch: str) -> list[ox.NamedNode]:
        """Every snapshot produced by one workflow run.

        A workflow is a batch; each of its stages is a run with its own
        activity. The batch id lives on the activity, so this walks activity
        to snapshot through ``prov:wasGeneratedBy``.
        """
        activities = {
            q.subject
            for q in self._store.quads_for_pattern(
                None, ECHOLOT.batch, ox.Literal(batch), GRAPH_PROVENANCE
            )
        }
        found = {
            q.subject
            for activity in activities
            for q in self._store.quads_for_pattern(
                None, PROV.wasGeneratedBy, activity, GRAPH_PROVENANCE
            )
        }
        return sorted(found, key=lambda n: n.value)

    def subjects(self) -> list[ox.NamedNode]:
        """Every subject that has at least one snapshot.

        The UI opens with this, so a curator sees real data instead of an empty
        box. A subject with no snapshot cannot exist: nothing is written
        without provenance (invariant 4).
        """
        found = {
            q.object
            for q in self._store.quads_for_pattern(
                None, PROV.specializationOf, None, GRAPH_PROVENANCE
            )
        }
        return sorted(found, key=lambda n: n.value)
