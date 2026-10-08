"""Pydantic v2 envelope models: the normative producer/Core contract.

Every model sets ``extra="forbid"``, so the generated JSON Schema carries
``additionalProperties: false`` and unknown fields are rejected by both
validation paths (invariant 1, invariant 3).

The envelope is pure data. Nothing here performs I/O or knows which Core it
talks to (invariant 8).
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"
"""The current contract version.

``schema.py`` derives the committed filename and the ``$id`` from this, so the
three representations of the version cannot drift apart.
"""

NonEmptyStr = Annotated[str, Field(min_length=1)]

AbsoluteIri = Annotated[
    str,
    Field(
        # An absolute IRI must carry a scheme; whitespace is never valid in one.
        # Expressed as a pattern rather than Pydantic's AnyUrl so the constraint
        # appears in the generated JSON Schema and no value is normalised: a
        # contract must round-trip its inputs byte for byte.
        pattern=r"^[A-Za-z][A-Za-z0-9+.\-]*:\S*$",
        examples=["https://demo.echolot.eu/entity/person/1"],
    ),
]
"""An absolute IRI (invariant 11). Never a backend-native identifier."""


class _Base(BaseModel):
    """Shared configuration: reject unknown fields everywhere."""

    model_config = ConfigDict(extra="forbid")


class ActivityType(StrEnum):
    RECONCILIATION = "reconciliation"
    ENRICHMENT = "enrichment"
    VALIDATION = "validation"
    IMPORT = "import"
    DELETION = "deletion"


class AgentRole(StrEnum):
    EXECUTED = "executed"
    COMMISSIONED = "commissioned"
    VALIDATED = "validated"


class ValidationState(StrEnum):
    AUTO_ACCEPTED = "auto_accepted"
    HUMAN_ACCEPTED = "human_accepted"
    HUMAN_CORRECTED = "human_corrected"
    PENDING = "pending"


class Activity(_Base):
    """One activity per run (invariant 6), never one per triple.

    ``id`` is an opaque producer-local identifier, such as a Prefect run id.
    Core mints the ``prov:Activity`` IRI from it; invariant 11 constrains only
    the target's identifiers, so this is deliberately not an IRI.
    """

    id: NonEmptyStr
    type: ActivityType
    started_at: AwareDatetime
    ended_at: AwareDatetime
    batch_of: NonEmptyStr | None = None


class SoftwareAgent(_Base):
    """A software agent, which must declare what ran and how it was configured.

    The requirement is structural rather than a validator: ``Agent`` is a
    discriminated union, so ``model`` and ``config_hash`` are required by this
    branch alone and the conditional is expressed in the generated JSON Schema.
    """

    type: Literal["software"]
    role: AgentRole
    id: NonEmptyStr
    model: NonEmptyStr
    config_hash: NonEmptyStr


class PersonAgent(_Base):
    """A human agent, identified by an ORCID once ECCCH AAI/SSO is wired up."""

    type: Literal["person"]
    role: AgentRole
    id: NonEmptyStr


Agent = Annotated[SoftwareAgent | PersonAgent, Field(discriminator="type")]


class Source(_Base):
    """What the activity drew on, pinned to a dump date or revision."""

    id: NonEmptyStr
    snapshot: NonEmptyStr
    harvested_at: AwareDatetime | None = None


class Method(_Base):
    """How the activity reached its result."""

    name: NonEmptyStr
    parameters: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    candidate_rank: int | None = Field(default=None, ge=1)


class Target(_Base):
    """What the activity changed.

    Both fields are absolute IRIs (invariant 11). Each ``statements`` entry is a
    property IRI; the type also admits a statement-instance IRI, which is the
    headroom for the open PWIKI question. See ``docs/revisionfile.md`` entry 1:
    a change in what an entry *means* is a ``schema_version`` bump even though
    the shape is unchanged.
    """

    subject: AbsoluteIri
    statements: list[AbsoluteIri] = Field(default_factory=list)


class Validation(_Base):
    """Whether a human accepted, corrected or has yet to see the result."""

    state: ValidationState
    by: NonEmptyStr | None = None
    at: AwareDatetime | None = None


class Rights(_Base):
    """Optional rights assertion and the basis for it."""

    assertion: NonEmptyStr | None = None
    basis: NonEmptyStr | None = None


class Envelope(_Base):
    """The provenance envelope: one per run, emitted by the service doing the work."""

    schema_version: Literal["1.0"]
    activity: Activity
    agents: list[Agent] = Field(min_length=1)
    target: Target
    method: Method
    validation: Validation
    sources: list[Source] = Field(default_factory=list)
    rights: Rights | None = None
