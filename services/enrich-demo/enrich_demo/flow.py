"""A Prefect workflow in three stages, each emitting its own provenance.

The shape this models:

    ONE WORKFLOW RUN = ONE BATCH  (the Prefect flow run id)
        |
        +-- stage 1  reconciliation   WP4 T4.2
        +-- stage 2  enrichment       WP4 T4.3
        +-- stage 3  quality check    WP4 T4.5

Each stage is a Prefect task. Each stage emits one envelope and writes once.
All three carry the same batch id, so a workflow can be traced afterwards.

Nothing here calls "record provenance" as a separate step. The work of each
stage happens inside ``record_activity``, and the envelope falls out of having
done it. That is what FR3.5 asks for.
"""

import json
import os
from importlib import resources
from typing import Any

from echolot_prov.client import CoreClient, iri
from echolot_prov.context import Context
from echolot_prov.emitter import record_activity
from prefect import flow, task

CREATOR = "http://purl.org/dc/terms/creator"
MATCHED = "http://purl.org/dc/terms/references"
QUALITY = "http://purl.org/dc/terms/conformsTo"

SERVICE_RECONCILE = "echolot-reconciler"
SERVICE_ENRICH = "echolot-enricher"
SERVICE_QUALITY = "echolot-quality"
CURATOR_SERVICE = "echolot-curator-ui"

DISTRIBUTION = "echolot-enrich-demo"
CORE_URL_ENV = "ECHOLOT_CORE_URL"
DEFAULT_CORE_URL = "http://127.0.0.1:8000"

RECONCILE_CONFIG: dict[str, Any] = {"table": "wikidata-standin", "threshold": 0.6}
ENRICH_CONFIG: dict[str, Any] = {"copy_facts": True}
QUALITY_CONFIG: dict[str, Any] = {"rule": "required-properties-present"}


def make_client(core_url: str) -> CoreClient:
    """Build the client a stage writes through.

    A seam, not an abstraction: tests replace this to point at a Core served
    in-process, so no stage needs a port to be under test.
    """
    return CoreClient(core_url)


def core_url(url: str | None = None) -> str:
    return url or os.environ.get(CORE_URL_ENV) or DEFAULT_CORE_URL


# --------------------------------------------------------------------- table


def load_table() -> dict[str, Any]:
    """The local stand-in for a Wikidata reconciliation service.

    Read from the package, never fetched. Invariant 9: no external network
    calls at runtime, so the demo is reproducible and works offline.
    """
    text = (
        resources.files("enrich_demo")
        .joinpath("wikidata_standin.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def reconcile(name: str) -> list[dict[str, Any]]:
    """Candidates for a name, best first. Pure and deterministic."""
    candidates = load_table()["candidates"].get(name, [])
    return sorted(candidates, key=lambda c: (-c["score"], c["id"]))


def missing_properties(state: dict[str, Any], required: list[str]) -> list[str]:
    """Which required properties have no value. Pure."""
    return sorted(p for p in required if not state.get(p))


# ---------------------------------------------------------------- the stages


def stage_reconcile(
    subject: str, name: str, *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """Stage 1. Find which real entity a name refers to. WP4 T4.2."""
    table = load_table()
    candidates = reconcile(name)

    with record_activity("reconciliation", context=context, subject=subject) as act:
        act.used(table["_source"], snapshot=table["_snapshot"])
        if not candidates:
            # A real curation event: we looked and found nothing.
            act.method("name-match", **RECONCILE_CONFIG)
            act.changed({}, {})
            after: dict[str, list[Any]] = {}
        else:
            best = candidates[0]
            after = {CREATOR: [best["label"]], MATCHED: [iri(best["id"])]}
            act.method(
                "name-match",
                confidence=best["score"],
                candidate_rank=1,
                **RECONCILE_CONFIG,
            )
            act.changed({}, after)

    written = client.write(subject, after, act.envelope)
    return {
        "stage": "reconciliation",
        "snapshot": written["snapshot"],
        "batch": act.envelope.activity.batch_of,
        "activity": act.envelope.activity.id,
        "candidate": candidates[0] if candidates else None,
        "state": {k: v for k, v in after.items()},
    }


def stage_enrich(
    subject: str,
    candidate: dict[str, Any] | None,
    *,
    context: Context,
    client: CoreClient,
) -> dict[str, Any]:
    """Stage 2. Copy known facts about the matched entity across. WP4 T4.3.

    Uses stage 1's result. This is the step that was missing before: the
    stages were independent, so nothing flowed between them.
    """
    table = load_table()
    facts = (candidate or {}).get("facts", {})
    after = {prop: [value] for prop, value in sorted(facts.items())}

    with record_activity("enrichment", context=context, subject=subject) as act:
        act.used(table["_source"], snapshot=table["_snapshot"])
        act.method(
            "fact-copy",
            confidence=candidate["score"] if candidate else None,
            **ENRICH_CONFIG,
        )
        act.changed({}, after)

    written = client.write(subject, after, act.envelope)
    return {
        "stage": "enrichment",
        "snapshot": written["snapshot"],
        "batch": act.envelope.activity.batch_of,
        "activity": act.envelope.activity.id,
        "added": sorted(after),
        "state": after,
    }


def stage_quality_check(
    subject: str,
    state: dict[str, Any],
    *,
    context: Context,
    client: CoreClient,
) -> dict[str, Any]:
    """Stage 3. Check the record is complete enough to publish. WP4 T4.5.

    Records its own verdict as data, so the judgement is itself provenanced.
    A failing check is not an error: it is a finding worth keeping.
    """
    table = load_table()
    missing = missing_properties(state, table["_required_properties"])
    verdict = "incomplete" if missing else "complete"
    after = {QUALITY: [verdict]}

    with record_activity("validation", context=context, subject=subject) as act:
        act.method(
            "required-properties",
            missing=missing,
            checked=table["_required_properties"],
            **QUALITY_CONFIG,
        )
        act.changed({}, after)
        act.validated("auto_accepted")

    written = client.write(subject, after, act.envelope)
    return {
        "stage": "quality-check",
        "snapshot": written["snapshot"],
        "batch": act.envelope.activity.batch_of,
        "activity": act.envelope.activity.id,
        "verdict": verdict,
        "missing": missing,
    }


def run_correction(
    subject: str, corrected: str, curator: str, *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """A curator corrects an attribution. Not a stage: a separate event.

    The affected property is stated rather than diffed, because a curator
    looking at one field has no "before" state of the whole entity to hand.
    """
    with record_activity("validation", context=context, subject=subject) as act:
        act.method("curator-review", queue="low-confidence")
        act.attribute(role="validated", id=curator)
        act.statements([CREATOR])
        act.validated("human_corrected", by=curator)

    return client.write(subject, {CREATOR: [corrected]}, act.envelope)


# -------------------------------------------------------------- orchestration


def _context(service: str, model: str, config: dict[str, Any]) -> Context:
    """Ambient capture for one stage.

    Built inside the task, so Prefect supplies this stage's own task run id.
    The emitter turns that into the activity id and turns the flow run id into
    the batch id.
    """
    return Context.from_environment(service, model, config, distribution=DISTRIBUTION)


@task(name="reconcile")
def task_reconcile(subject: str, name: str, url: str | None) -> dict[str, Any]:
    return stage_reconcile(
        subject,
        name,
        context=_context(SERVICE_RECONCILE, "name-match-v2", RECONCILE_CONFIG),
        client=make_client(core_url(url)),
    )


@task(name="enrich")
def task_enrich(
    subject: str, candidate: dict[str, Any] | None, url: str | None
) -> dict[str, Any]:
    return stage_enrich(
        subject,
        candidate,
        context=_context(SERVICE_ENRICH, "fact-copy-v1", ENRICH_CONFIG),
        client=make_client(core_url(url)),
    )


@task(name="quality-check")
def task_quality_check(
    subject: str, state: dict[str, Any], url: str | None
) -> dict[str, Any]:
    return stage_quality_check(
        subject,
        state,
        context=_context(SERVICE_QUALITY, "required-properties-v1", QUALITY_CONFIG),
        client=make_client(core_url(url)),
    )


@flow(name="curate-entity")
def curate_entity(name: str, subject: str, url: str | None = None) -> dict[str, Any]:
    """The whole workflow. One run of this is one batch.

    Each stage writes its own provenance as it goes, so a failure in stage 3
    leaves stages 1 and 2 recorded rather than losing the whole run.
    """
    first = task_reconcile(subject, name, url)
    second = task_enrich(subject, first["candidate"], url)

    # What stage 3 judges is everything the earlier stages asserted.
    state = {**first["state"], **second["state"]}
    third = task_quality_check(subject, state, url)

    return {
        "batch": first["batch"],
        "subject": subject,
        "name": name,
        "stages": [first, second, third],
    }


@flow(name="correct-attribution")
def correct_attribution(
    subject: str, corrected: str, curator: str, url: str | None = None
) -> dict[str, Any]:
    """A curator corrects what the workflow produced."""
    context = Context.from_environment(
        CURATOR_SERVICE,
        "review-queue-v1",
        {},
        distribution=DISTRIBUTION,
        operator=curator,
    )
    return run_correction(
        subject, corrected, curator, context=context, client=make_client(core_url(url))
    )
