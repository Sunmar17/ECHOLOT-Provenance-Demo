"""The ``record_activity(...)`` context manager that builds an envelope (Phase 3).

A producer describes what it did while doing it; the envelope falls out. Nobody
assembles one by hand, which is what FR3.5 means by provenance captured
automatically by the service doing the work.

Envelope construction is a pure function (invariant 5). The context manager only
collects, times the run, and calls it on the way out.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from echolot_prov.context import Context
from echolot_prov.models import (
    SCHEMA_VERSION,
    Activity,
    ActivityType,
    AgentRole,
    Envelope,
    Method,
    PersonAgent,
    Rights,
    SoftwareAgent,
    Source,
    Target,
    Validation,
    ValidationState,
)

Statements = Mapping[str, Sequence[Any]]
"""Property IRI to its values, which is how a producer sees an entity."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def diff_statements(before: Statements, after: Statements) -> list[str]:
    """The property IRIs whose values differ between two states of an entity.

    This is the producer's *intent*: what this run set out to change. Core still
    computes the real delta against current state, because only Core knows it,
    and the two legitimately differ when a value written is already current.

    Deriving the list rather than hand-maintaining it removes the failure mode
    where an envelope confidently names properties the run never touched.
    """
    changed: list[str] = []
    for predicate in set(before) | set(after):
        if set(before.get(predicate, ())) != set(after.get(predicate, ())):
            changed.append(predicate)
    return sorted(changed)


def build_envelope(
    context: Context,
    *,
    kind: ActivityType | str,
    subject: str,
    started_at: datetime,
    ended_at: datetime,
    method_name: str,
    statements: Sequence[str] = (),
    parameters: Mapping[str, Any] | None = None,
    confidence: float | None = None,
    candidate_rank: int | None = None,
    sources: Sequence[Source] = (),
    extra_agents: Sequence[SoftwareAgent | PersonAgent] = (),
    validation: Validation | None = None,
    rights: Rights | None = None,
    batch_of: str | None = None,
) -> Envelope:
    """Build an envelope from a context and what happened. Pure: no I/O.

    The executing software agent is derived from the context rather than passed
    in. A service should not have to restate its own identity, model and config
    hash on every run; that is the whole point of ambient capture.
    """
    executor = SoftwareAgent(
        type="software",
        role=AgentRole.EXECUTED,
        id=context.service,
        model=context.model,
        config_hash=context.config_hash,
    )

    return Envelope(
        schema_version=SCHEMA_VERSION,
        activity=Activity(
            id=context.run_id or f"{context.service}-{started_at.isoformat()}",
            type=ActivityType(kind),
            started_at=started_at,
            ended_at=ended_at,
            batch_of=batch_of,
        ),
        agents=[executor, *extra_agents],
        target=Target(subject=subject, statements=list(statements)),
        method=Method(
            name=method_name,
            parameters=dict(parameters or {}),
            confidence=confidence,
            candidate_rank=candidate_rank,
        ),
        validation=validation or Validation(state=ValidationState.AUTO_ACCEPTED),
        sources=list(sources),
        rights=rights,
    )


class ActivityRecorder:
    """Collects what a run did. Yielded by :func:`record_activity`."""

    def __init__(
        self, context: Context, kind: ActivityType | str, subject: str
    ) -> None:
        self._context = context
        self._kind = kind
        self._subject = subject
        self._sources: list[Source] = []
        self._agents: list[SoftwareAgent | PersonAgent] = []
        self._statements: list[str] = []
        self._method: tuple[str, dict[str, Any]] = ("unspecified", {})
        self._confidence: float | None = None
        self._candidate_rank: int | None = None
        self._validation: Validation | None = None
        self._rights: Rights | None = None
        self._batch_of: str | None = None
        self.envelope: Envelope | None = None
        """The built envelope. None until the block exits successfully."""

    def used(
        self, source: str, *, snapshot: str, harvested_at: datetime | None = None
    ) -> None:
        """Record a source this run drew on, pinned to a dump date or revision."""
        self._sources.append(
            Source(id=source, snapshot=snapshot, harvested_at=harvested_at)
        )

    def method(
        self,
        name: str,
        *,
        confidence: float | None = None,
        candidate_rank: int | None = None,
        **parameters: Any,
    ) -> None:
        """Record how the result was reached."""
        self._method = (name, parameters)
        self._confidence = confidence
        self._candidate_rank = candidate_rank

    def attribute(
        self,
        *,
        role: AgentRole | str,
        id: str,
        type: str = "person",
        model: str | None = None,
        config_hash: str | None = None,
    ) -> None:
        """Add an agent beyond the executing service, e.g. a commissioning curator."""
        if type == "software":
            if model is None or config_hash is None:
                raise ValueError("a software agent requires model and config_hash")
            self._agents.append(
                SoftwareAgent(
                    type="software",
                    role=AgentRole(role),
                    id=id,
                    model=model,
                    config_hash=config_hash,
                )
            )
        else:
            self._agents.append(PersonAgent(type="person", role=AgentRole(role), id=id))

    def changed(self, before: Statements, after: Statements) -> None:
        """Derive the affected properties by diffing the entity before and after."""
        self._statements = diff_statements(before, after)

    def statements(self, properties: Sequence[str]) -> None:
        """State the affected properties directly, when no before state exists."""
        self._statements = list(properties)

    def validated(
        self,
        state: ValidationState | str,
        *,
        by: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """Record whether a human accepted, corrected, or has yet to see this."""
        self._validation = Validation(state=ValidationState(state), by=by, at=at)

    def rights(self, *, assertion: str | None = None, basis: str | None = None) -> None:
        """Record a rights assertion and the basis for it."""
        self._rights = Rights(assertion=assertion, basis=basis)

    def batch(self, batch_of: str) -> None:
        """Mark this run as part of a batch."""
        self._batch_of = batch_of

    def _build(self, started_at: datetime, ended_at: datetime) -> Envelope:
        name, parameters = self._method
        return build_envelope(
            self._context,
            kind=self._kind,
            subject=self._subject,
            started_at=started_at,
            ended_at=ended_at,
            method_name=name,
            parameters=parameters,
            statements=self._statements,
            confidence=self._confidence,
            candidate_rank=self._candidate_rank,
            sources=self._sources,
            extra_agents=self._agents,
            validation=self._validation,
            rights=self._rights,
            batch_of=self._batch_of,
        )


@contextmanager
def record_activity(
    kind: ActivityType | str,
    *,
    context: Context,
    subject: str,
    clock: Callable[[], datetime] = _utcnow,
) -> Iterator[ActivityRecorder]:
    """Record one run's provenance while the run happens.

    ``started_at`` is taken on entry and ``ended_at`` on exit, which is what a
    context manager is for here. On a clean exit the envelope is built and
    validated by the models, and left on ``recorder.envelope``.

    This does not write. The caller passes the envelope to
    ``CoreClient.write``, so the single write path stays visible at the call
    site (invariant 2).

    If the body raises, no envelope is built and the exception propagates. The
    contract cannot express a failed run, and inventing a way would be a
    ``schema_version`` decision rather than a local one. See
    ``docs/revisionfile.md`` entry 15.
    """
    recorder = ActivityRecorder(context, kind, subject)
    started_at = clock()
    yield recorder
    recorder.envelope = recorder._build(started_at, clock())
