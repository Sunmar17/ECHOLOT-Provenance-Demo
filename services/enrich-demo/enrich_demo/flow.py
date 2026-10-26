"""Prefect flows: reconcile a name against a local table, then write.

The point of this module is that provenance is not a step. Nobody calls
"record provenance" after doing the work: the work happens inside
``record_activity`` and the envelope falls out of having done it. That is what
FR3.5 asks for, and it is why the emitter is shaped the way it is.

Orchestration is kept separate from logic. Each ``@flow`` is a thin wrapper that
captures the live environment and calls a plain function, so the logic is
testable in milliseconds and only the integration test pays for Prefect.
"""

import json
import os
from importlib import resources
from typing import Any

from echolot_prov.client import CoreClient
from echolot_prov.context import Context
from echolot_prov.emitter import record_activity
from prefect import flow, task

CREATOR = "http://purl.org/dc/terms/creator"

SERVICE = "echolot-reconciler"
"""The agent's identity in the provenance, not the package it ships in."""

DISTRIBUTION = "echolot-enrich-demo"
MODEL = "name-match-v2"
CURATOR_SERVICE = "echolot-curator-ui"
CURATOR_MODEL = "review-queue-v1"

CORE_URL_ENV = "ECHOLOT_CORE_URL"
DEFAULT_CORE_URL = "http://127.0.0.1:8000"

RECONCILE_CONFIG: dict[str, Any] = {"table": "wikidata-standin", "threshold": 0.6}
"""The configuration whose hash goes into the envelope.

Changing a threshold changes the hash, so two runs that disagree are
distinguishable after the fact rather than looking identical.
"""


def make_client(core_url: str) -> CoreClient:
    """Build the client a flow writes through.

    A seam, not an abstraction: tests replace this to point at a Core served
    in-process, so no flow ever needs a port to be under test.
    """
    return CoreClient(core_url)


def core_url() -> str:
    return os.environ.get(CORE_URL_ENV) or DEFAULT_CORE_URL


# --------------------------------------------------------------------- lookup


def load_table() -> dict[str, Any]:
    """The local stand-in for a Wikidata reconciliation service.

    Read from the package rather than fetched. Invariant 9: no external network
    calls at runtime, so the demo is reproducible and offline.
    """
    text = (
        resources.files("enrich_demo")
        .joinpath("wikidata_standin.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def reconcile(name: str) -> list[dict[str, Any]]:
    """Candidates for a name, best first. Pure and deterministic."""
    table = load_table()
    candidates = table["candidates"].get(name, [])
    return sorted(candidates, key=lambda c: (-c["score"], c["id"]))


# ----------------------------------------------------------------- the work


def run_reconciliation(
    subject: str, name: str, *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """Reconcile a name and write the result with its provenance.

    The write and the envelope leave together through the single write path,
    so there is no moment where the data exists without the record of why.
    """
    table = load_table()

    with record_activity("reconciliation", context=context, subject=subject) as act:
        candidates = reconcile(name)
        if not candidates:
            # A real curation event: we looked and found nothing. Core accepts
            # the empty delta and still records that the search happened.
            act.used(table["_source"], snapshot=table["_snapshot"])
            act.method("name-match", **RECONCILE_CONFIG)
            act.changed({}, {})
            after: dict[str, list[Any]] = {}
        else:
            best = candidates[0]
            after = {CREATOR: [best["label"]]}
            act.used(table["_source"], snapshot=table["_snapshot"])
            act.method(
                "name-match",
                confidence=best["score"],
                candidate_rank=1,
                **RECONCILE_CONFIG,
            )
            act.changed({}, after)

    return client.write(subject, after, act.envelope)


def run_correction(
    subject: str, corrected: str, curator: str, *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """A curator corrects an attribution, as a validation activity.

    The affected property is stated rather than diffed: a curator looking at one
    field has no "before" state of the whole entity to hand. That is what
    ``.statements()`` is for.
    """
    with record_activity("validation", context=context, subject=subject) as act:
        act.method("curator-review", queue="low-confidence")
        act.attribute(role="validated", id=curator)
        act.statements([CREATOR])
        act.validated("human_corrected", by=curator)

    return client.write(subject, {CREATOR: [corrected]}, act.envelope)


# -------------------------------------------------------------- orchestration


@task
def reconcile_task(name: str) -> list[dict[str, Any]]:
    """Exposes the lookup as a Prefect task, so the demo has something to show."""
    return reconcile(name)


@flow(name="reconcile-person")
def reconcile_person(name: str, subject: str, url: str | None = None) -> dict[str, Any]:
    """Run 1: an automated service reconciles a name against the local table.

    ``Context.from_environment`` picks up the Prefect run id here, so the
    activity in the provenance is the actual orchestrated run rather than a
    synthesised identifier.
    """
    context = Context.from_environment(
        SERVICE, MODEL, RECONCILE_CONFIG, distribution=DISTRIBUTION
    )
    reconcile_task(name)
    return run_reconciliation(
        subject, name, context=context, client=make_client(url or core_url())
    )


@flow(name="correct-attribution")
def correct_attribution(
    subject: str, corrected: str, curator: str, url: str | None = None
) -> dict[str, Any]:
    """Run 2: a curator corrects the result, attributed to a person."""
    context = Context.from_environment(
        CURATOR_SERVICE, CURATOR_MODEL, {}, distribution=DISTRIBUTION, operator=curator
    )
    return run_correction(
        subject,
        corrected,
        curator,
        context=context,
        client=make_client(url or core_url()),
    )
