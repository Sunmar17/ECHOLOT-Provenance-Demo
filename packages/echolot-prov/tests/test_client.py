"""The single write path: value mapping, error surfacing, and a real write.

Unit tests run against an in-process fake Core, so none of this needs Prefect
running or a Core reachable.
"""

from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import validate_against_committed_schema
from echolot_prov.client import CoreClient, CoreError, CoreValidationError, iri, term
from echolot_prov.context import Context
from echolot_prov.emitter import record_activity

CREATOR = "http://purl.org/dc/terms/creator"
SUBJECT = "https://demo.echolot.eu/entity/person/1"
XSD = "http://www.w3.org/2001/XMLSchema#"

CONTEXT = Context.from_dict(
    {
        "service": "echolot-reconciler",
        "model": "name-match-v2",
        "config_hash": "sha256:" + "a" * 64,
        "run_id": "prefect-run-1",
    }
)


def an_envelope(subject: str = SUBJECT):
    moments = iter(
        [
            datetime(2026, 3, 4, 9, 12, 0, tzinfo=UTC),
            datetime(2026, 3, 4, 9, 12, 2, tzinfo=UTC),
        ]
    )
    with record_activity(
        "reconciliation", context=CONTEXT, subject=subject, clock=lambda: next(moments)
    ) as act:
        act.method("wikidata-lookup", confidence=0.93)
        act.changed({}, {CREATOR: ["Peter Paul Rubens"]})
    return act.envelope


def fake_core(handler) -> CoreClient:
    """A Core that never existed, for tests that must not need one."""
    return CoreClient(
        "http://core",
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://core"
        ),
    )


# ---------------------------------------------------------------- value mapping


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Rubens", {"type": "literal", "value": "Rubens"}),
        (iri("https://x.test/a"), {"type": "uri", "value": "https://x.test/a"}),
        (1625, {"type": "literal", "value": "1625", "datatype": f"{XSD}integer"}),
        (True, {"type": "literal", "value": "true", "datatype": f"{XSD}boolean"}),
        (0.5, {"type": "literal", "value": "0.5", "datatype": f"{XSD}decimal"}),
        (
            date(1625, 6, 28),
            {"type": "literal", "value": "1625-06-28", "datatype": f"{XSD}date"},
        ),
    ],
    ids=["string", "iri", "integer", "boolean", "decimal", "date"],
)
def test_term_mapping(value, expected):
    assert term(value) == expected


def test_a_url_shaped_string_stays_a_literal():
    """An IRI is never inferred from shape: a note that mentions a link is text."""
    assert term("see https://example.org for context")["type"] == "literal"
    assert term("https://example.org")["type"] == "literal"


def test_naive_timestamps_are_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        term(datetime(2026, 3, 4, 9, 12, 0))


def test_unsupported_types_are_refused():
    with pytest.raises(TypeError, match="cannot express"):
        term({"a": 1})


# ----------------------------------------------------------------------- write


def test_write_posts_envelope_and_statements_together():
    """No code path writes data without an envelope (invariant 2)."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"snapshot": "https://demo.echolot.eu/snapshot/x"}
        )

    client = fake_core(handler)
    result = client.write(SUBJECT, {CREATOR: ["Peter Paul Rubens"]}, an_envelope())

    assert result["snapshot"].endswith("/x")
    assert set(seen) == {"envelope", "statements"}
    assert seen["statements"][CREATOR] == [
        {"type": "literal", "value": "Peter Paul Rubens"}
    ]
    assert seen["envelope"]["target"]["subject"] == SUBJECT


def test_what_is_posted_satisfies_the_committed_schema():
    """The wire form, not just the model, must match the contract."""
    validate_against_committed_schema(an_envelope())


def test_a_mismatched_subject_is_caught_before_the_request():
    """Catch a producer bug locally rather than spending a round trip on it."""

    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no request should have been sent")

    with pytest.raises(ValueError, match="does not match the envelope"):
        fake_core(handler).write(
            "https://demo.echolot.eu/entity/other", {}, an_envelope()
        )


def test_core_422_becomes_a_typed_error_carrying_the_paths():
    def handler(request):
        return httpx.Response(
            422,
            json={
                "errors": [
                    {"path": "/schema_version", "message": "unknown schema_version"}
                ]
            },
        )

    with pytest.raises(CoreValidationError) as caught:
        fake_core(handler).write(SUBJECT, {}, an_envelope())

    assert caught.value.errors[0]["path"] == "/schema_version"
    assert "unknown schema_version" in str(caught.value)


def test_other_failures_become_a_core_error():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(CoreError, match="500"):
        fake_core(handler).write(SUBJECT, {}, an_envelope())


def test_an_unreachable_core_becomes_a_core_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(CoreError, match="could not reach Core"):
        fake_core(handler).write(SUBJECT, {}, an_envelope())
