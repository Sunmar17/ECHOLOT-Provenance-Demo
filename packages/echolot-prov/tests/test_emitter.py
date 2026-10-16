"""The comparator, and envelopes that are valid by construction."""

from datetime import UTC, datetime

import pytest
from conftest import validate_against_committed_schema
from echolot_prov.context import Context
from echolot_prov.emitter import diff_statements, record_activity
from echolot_prov.models import Envelope

CREATOR = "http://purl.org/dc/terms/creator"
DATE = "http://purl.org/dc/terms/date"
SUBJECT = "https://demo.echolot.eu/entity/person/1"

CONTEXT = Context.from_dict(
    {
        "service": "echolot-reconciler",
        "model": "name-match-v2",
        "config_hash": "sha256:" + "a" * 64,
        "run_id": "prefect-run-1",
    }
)


def fixed_clock(*moments: datetime):
    it = iter(moments)
    return lambda: next(it)


TWO_SECONDS = (
    datetime(2026, 3, 4, 9, 12, 0, tzinfo=UTC),
    datetime(2026, 3, 4, 9, 12, 2, tzinfo=UTC),
)


# ------------------------------------------------------------------ comparator


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ({CREATOR: ["a"]}, {CREATOR: ["a"]}, []),
        ({CREATOR: ["a"]}, {CREATOR: ["b"]}, [CREATOR]),
        ({}, {CREATOR: ["a"]}, [CREATOR]),
        ({CREATOR: ["a"]}, {}, [CREATOR]),
        ({CREATOR: ["a"]}, {CREATOR: ["a", "b"]}, [CREATOR]),
        ({CREATOR: ["a", "b"]}, {CREATOR: ["b", "a"]}, []),  # order only
        (
            {CREATOR: ["a"], DATE: ["1625"]},
            {CREATOR: ["b"], DATE: ["1625"]},
            [CREATOR],
        ),
    ],
    ids=[
        "unchanged",
        "value changed",
        "property added",
        "property removed",
        "value added",
        "order only",
        "one of two changed",
    ],
)
def test_diff_statements(before, after, expected):
    assert diff_statements(before, after) == expected


# ------------------------------------------------------------------- recorder


def test_record_activity_builds_a_valid_envelope():
    with record_activity(
        "reconciliation",
        context=CONTEXT,
        subject=SUBJECT,
        clock=fixed_clock(*TWO_SECONDS),
    ) as act:
        act.used("https://www.wikidata.org/", snapshot="2026-02-01")
        act.method("wikidata-lookup", confidence=0.93, candidate_rank=1, threshold=0.85)
        act.changed({CREATOR: ["Workshop of Rubens"]}, {CREATOR: ["Peter Paul Rubens"]})

    envelope = act.envelope
    assert isinstance(envelope, Envelope)
    validate_against_committed_schema(envelope)


def test_the_executing_agent_comes_from_the_context():
    """A producer should never restate its own identity, model and config hash."""
    with record_activity(
        "enrichment", context=CONTEXT, subject=SUBJECT, clock=fixed_clock(*TWO_SECONDS)
    ) as act:
        act.method("biography-extraction")

    agent = act.envelope.agents[0]
    assert agent.type == "software"
    assert (agent.id, agent.model, agent.config_hash) == (
        CONTEXT.service,
        CONTEXT.model,
        CONTEXT.config_hash,
    )


def test_statements_are_derived_not_declared():
    with record_activity(
        "enrichment", context=CONTEXT, subject=SUBJECT, clock=fixed_clock(*TWO_SECONDS)
    ) as act:
        act.method("m")
        act.changed({CREATOR: ["a"], DATE: ["1625"]}, {CREATOR: ["b"], DATE: ["1625"]})

    assert act.envelope.target.statements == [CREATOR]


def test_timestamps_bracket_the_block():
    with record_activity(
        "import", context=CONTEXT, subject=SUBJECT, clock=fixed_clock(*TWO_SECONDS)
    ) as act:
        act.method("edm-import")

    assert act.envelope.activity.started_at == TWO_SECONDS[0]
    assert act.envelope.activity.ended_at == TWO_SECONDS[1]


def test_a_person_agent_can_be_added():
    with record_activity(
        "validation", context=CONTEXT, subject=SUBJECT, clock=fixed_clock(*TWO_SECONDS)
    ) as act:
        act.method("curator-review")
        act.attribute(role="validated", id="https://orcid.org/0000-0002-1825-0097")
        act.validated("human_corrected", by="https://orcid.org/0000-0002-1825-0097")

    envelope = act.envelope
    validate_against_committed_schema(envelope)
    assert [a.role for a in envelope.agents] == ["executed", "validated"]
    assert envelope.validation.state == "human_corrected"


def test_a_software_agent_must_declare_model_and_config_hash():
    """The contract's conditional, enforced before an envelope is ever built."""
    with (
        pytest.raises(ValueError, match="model and config_hash"),
        record_activity(
            "enrichment",
            context=CONTEXT,
            subject=SUBJECT,
            clock=fixed_clock(*TWO_SECONDS),
        ) as act,
    ):
        act.attribute(role="commissioned", id="other-service", type="software")


def test_a_failed_activity_emits_nothing():
    """The contract cannot express a failed run, so none is invented."""
    with (
        pytest.raises(RuntimeError, match="the work failed"),
        record_activity(
            "enrichment",
            context=CONTEXT,
            subject=SUBJECT,
            clock=fixed_clock(*TWO_SECONDS),
        ) as act,
    ):
        act.method("m")
        raise RuntimeError("the work failed")

    assert act.envelope is None


def test_every_activity_type_builds():
    for kind in ("reconciliation", "enrichment", "validation", "import", "deletion"):
        with record_activity(
            kind, context=CONTEXT, subject=SUBJECT, clock=fixed_clock(*TWO_SECONDS)
        ) as act:
            act.method("m")
        validate_against_committed_schema(act.envelope)


def test_rights_and_batch_are_carried():
    with record_activity(
        "import", context=CONTEXT, subject=SUBJECT, clock=fixed_clock(*TWO_SECONDS)
    ) as act:
        act.method("edm-import")
        act.batch("import-batch-2026-03")
        act.rights(
            assertion="http://rightsstatements.org/vocab/InC/1.0/", basis="agreement"
        )

    envelope = act.envelope
    validate_against_committed_schema(envelope)
    assert envelope.activity.batch_of == "import-batch-2026-03"
    assert envelope.rights.basis == "agreement"
