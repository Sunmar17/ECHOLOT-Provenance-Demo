"""A Prefect workflow over an uploaded file, each stage emitting its own provenance.

The shape this models:

    ONE UPLOAD = ONE FLOW RUN = ONE BATCH  (the Prefect flow run id)
        |
        +-- for each record in the file, in order:
              stage 0  import           the record as uploaded
              stage 1  reconciliation   WP4 T4.2
              stage 2  enrichment       WP4 T4.3  (people only)
              stage 3  quality check    WP4 T4.5

Each stage is a Prefect task, mapped over the records, so every record-stage is
its own task run and therefore its own activity. All of them carry the batch id
of the upload, so a batch can be traced afterwards.

Nothing here calls "record provenance" as a separate step. The work of each
stage happens inside ``record_activity``, and the envelope falls out of having
done it. That is what FR3.5 asks for.
"""

import json
import os
import unicodedata
from importlib import resources
from typing import Any

from echolot_prov.client import CoreClient, iri
from echolot_prov.context import Context
from echolot_prov.emitter import record_activity
from echolot_prov.models import StatementRef
from prefect import flow, task, unmapped

from enrich_demo.upload import (
    MAX_RECORDS,
    Record,
    Upload,
    to_claims,
    to_statements,
)
from enrich_demo.vocab import (
    CREATOR,
    MATCHED,
    QUALITY,
    WIKIDATA_ID,
    entity_iri,
    field,
)

SERVICE_IMPORT = "echolot-importer"
SERVICE_RECONCILE = "echolot-reconciler"
SERVICE_ENRICH = "echolot-enricher"
SERVICE_QUALITY = "echolot-quality"
CURATOR_SERVICE = "echolot-curator-ui"

DISTRIBUTION = "echolot-enrich-demo"
CORE_URL_ENV = "ECHOLOT_CORE_URL"
DEFAULT_CORE_URL = "http://127.0.0.1:8000"

IMPORT_CONFIG: dict[str, Any] = {"formats": ["csv", "json"], "max_records": MAX_RECORDS}
RECONCILE_CONFIG: dict[str, Any] = {"table": "wikidata-standin", "threshold": 0.6}
ENRICH_CONFIG: dict[str, Any] = {"copy": ["gndId", "viafId"]}
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


def normalise_name(name: str) -> str:
    """``"Family, Given"`` as ``"Given Family"``. Any other form is kept."""
    family, comma, given = name.partition(",")
    if comma and given.strip() and "," not in given:
        return f"{given.strip()} {family.strip()}"
    return name.strip()


def _key(name: str) -> str:
    """Compare names without case, accents or doubled spaces."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


def _candidate(table: dict[str, Any], qid: str, score: float) -> dict[str, Any]:
    entity = table["entities"][qid]
    return {"id": qid, "iri": entity_iri(qid), "label": entity["label"], "score": score}


def reconcile(name: str) -> list[dict[str, Any]]:
    """Candidates for a name as an archive writes it, best first. Pure."""
    table = load_table()
    wanted = _key(normalise_name(name))
    for known, candidates in table["names"].items():
        if _key(known) == wanted:
            ranked = sorted(candidates, key=lambda c: (-c["score"], c["id"]))
            return [_candidate(table, c["id"], c["score"]) for c in ranked]
    return []


def match_gnd(gnd: str) -> dict[str, Any] | None:
    """The entity holding a GND id. An identifier match, so its score is 1.0."""
    table = load_table()
    for qid, entity in sorted(table["entities"].items()):
        if entity.get("gndId") == gnd:
            return _candidate(table, qid, 1.0)
    return None


def missing_properties(state: dict[str, Any], required: list[str]) -> list[str]:
    """Which required properties have no value. Pure."""
    return sorted(p for p in required if not state.get(p))


# ---------------------------------------------------------------- the stages
# Each stage takes what the previous stage returned ("progress") and returns it
# extended, so one record's stages always run in order.


def start(record: Record) -> dict[str, Any]:
    """The progress of a record before any stage has run."""
    return {"record": record, "state": {}, "stages": [], "batch": None}


def _advance(
    progress: dict[str, Any],
    stage: str,
    written: dict[str, Any],
    envelope: Any,
    after: dict[str, list[Any]],
    **extra: Any,
) -> dict[str, Any]:
    return {
        **progress,
        **extra,
        "batch": envelope.activity.batch_of,
        "state": {**progress["state"], **after},
        "stages": [
            *progress["stages"],
            {
                "stage": stage,
                "snapshot": written["snapshot"],
                "activity": envelope.activity.id,
            },
        ],
    }


def stage_import(
    record: Record,
    upload: Upload,
    *,
    context: Context,
    client: CoreClient,
    uploader: str | None = None,
) -> dict[str, Any]:
    """Stage 0. Write the record as it was uploaded.

    The file is the source, named by its content hash. The person who uploaded
    it commissioned the run; the importer executed it.

    Each creator becomes a new claim. Core mints the claims' IRIs and returns
    them, and they travel with the record so the next stage can name them.
    """
    after = to_statements(record)
    claims = to_claims(record)
    # New claims are declared by property: they have no identity until Core
    # mints one.
    declared = sorted(set(after) | ({CREATOR} if claims else set()))

    with record_activity("import", context=context, subject=record.subject) as act:
        act.used(upload.iri, snapshot=upload.digest)
        act.method(
            f"{upload.format}-import",
            file=upload.filename,
            record=record.position,
            **IMPORT_CONFIG,
        )
        if uploader:
            act.attribute(role="commissioned", id=uploader)
        act.statements(declared)

    written = client.write(record.subject, after, act.envelope, claims=claims)
    creators = [
        {"statement": statement, "name": claim["values"][CREATOR][0]}
        for statement, claim in zip(written.get("claims", []), claims, strict=True)
    ]
    state = {**after, **({CREATOR: [c["name"] for c in creators]} if creators else {})}
    return _advance(
        start(record), "import", written, act.envelope, state, creators=creators
    )


def stage_reconcile(
    progress: dict[str, Any], *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """Stage 1. Find which real entity a record refers to. WP4 T4.2.

    A person is matched on its GND id, which is exact. An artifact is matched on
    each creator claim, which is not, so a threshold applies. Each match is
    written on the claim it belongs to, so two creators never share one.
    """
    record: Record = progress["record"]
    table = load_table()
    after: dict[str, list[Any]] = {}
    claims: list[dict[str, Any]] = []
    match = None

    with record_activity(
        "reconciliation", context=context, subject=record.subject
    ) as act:
        act.used(table["_source"], snapshot=table["_snapshot"])
        if record.kind == "agent":
            after, match = _reconcile_person(record, act)
            act.changed({}, after)
        else:
            claims = _reconcile_artifact(progress.get("creators", []), act)
            act.statements(
                [
                    StatementRef(property=MATCHED, statement=c["statement"])
                    for c in claims
                ]
            )

    written = client.write(record.subject, after, act.envelope, claims=claims)
    return _advance(
        progress, "reconciliation", written, act.envelope, after, match=match
    )


def _reconcile_person(
    record: Record, act: Any
) -> tuple[dict[str, list[Any]], dict[str, Any] | None]:
    gnd = record.fields.get("gndId")
    hit = match_gnd(gnd) if gnd else None
    if hit is None:
        # A real curation event: we looked and found nothing.
        act.method("gnd-id-match", on="gndId", gndId=gnd, **RECONCILE_CONFIG)
        return {}, None
    act.method(
        "gnd-id-match",
        confidence=hit["score"],
        candidate_rank=1,
        on="gndId",
        gndId=gnd,
        **RECONCILE_CONFIG,
    )
    return {WIKIDATA_ID: [iri(hit["iri"])]}, hit


def _reconcile_artifact(
    creators: list[dict[str, Any]], act: Any
) -> list[dict[str, Any]]:
    """One claim write per matched creator; an unmatched one is only recorded."""
    threshold = RECONCILE_CONFIG["threshold"]
    matches: list[dict[str, Any]] = []
    unmatched: list[str] = []
    writes: list[dict[str, Any]] = []
    for creator in creators:
        candidates = [c for c in reconcile(creator["name"]) if c["score"] >= threshold]
        if not candidates:
            unmatched.append(creator["name"])
            continue
        best = candidates[0]
        matches.append(
            {
                "name": creator["name"],
                "statement": creator["statement"],
                "candidate": best["id"],
                "label": best["label"],
                "score": best["score"],
            }
        )
        writes.append(
            {"statement": creator["statement"], "values": {MATCHED: [iri(best["iri"])]}}
        )

    # One confidence per run: the weakest match is what a curator should doubt.
    # Each match's own score is in the parameters, beside the claim it names.
    act.method(
        "name-match",
        confidence=min(m["score"] for m in matches) if matches else None,
        candidate_rank=1 if matches else None,
        matches=matches,
        unmatched=unmatched,
        **RECONCILE_CONFIG,
    )
    return writes


def stage_enrich(
    progress: dict[str, Any], *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """Stage 2. Copy authority ids the record lacks from its match. WP4 T4.3.

    Defined for people only. For an artifact nothing is done, so nothing is
    written and no envelope is emitted: provenance records work, not its absence.
    """
    record: Record = progress["record"]
    if record.kind != "agent":
        return {**progress, "skipped": [*progress.get("skipped", []), "enrichment"]}

    table = load_table()
    match = progress.get("match")
    entity = table["entities"].get(match["id"], {}) if match else {}
    after = {
        field(name): [entity[name]]
        for name in ENRICH_CONFIG["copy"]
        if entity.get(name) and not record.fields.get(name)
    }

    with record_activity("enrichment", context=context, subject=record.subject) as act:
        act.used(table["_source"], snapshot=table["_snapshot"])
        act.method(
            "authority-copy",
            entity=match["iri"] if match else None,
            **ENRICH_CONFIG,
        )
        act.changed({}, after)

    written = client.write(record.subject, after, act.envelope)
    return _advance(progress, "enrichment", written, act.envelope, after)


def stage_quality_check(
    progress: dict[str, Any], *, context: Context, client: CoreClient
) -> dict[str, Any]:
    """Stage 3. Check the record is complete enough to publish. WP4 T4.5.

    Records its own verdict as data, so the judgement is itself provenanced.
    A failing check is not an error: it is a finding worth keeping.
    """
    record: Record = progress["record"]
    required = load_table()["_required_properties"][record.kind]
    missing = missing_properties(progress["state"], required)
    verdict = "incomplete" if missing else "complete"
    after = {QUALITY: [verdict]}

    with record_activity("validation", context=context, subject=record.subject) as act:
        act.method(
            "required-properties", missing=missing, checked=required, **QUALITY_CONFIG
        )
        act.changed({}, after)
        act.validated("auto_accepted")

    written = client.write(record.subject, after, act.envelope)
    return _advance(
        progress,
        "quality-check",
        written,
        act.envelope,
        after,
        verdict=verdict,
        missing=missing,
    )


def run_correction(
    subject: str,
    curator: str,
    *,
    property: str,
    value: str,
    value_is_iri: bool = False,
    statement: str | None = None,
    corrects: str | None = None,
    context: Context,
    client: CoreClient,
) -> dict[str, Any]:
    """A curator replaces one value. Not a stage: a separate event.

    The value is either a plain one on the subject (``property``) or one on a
    claim (``statement`` and ``property``), such as the match of one creator.
    ``corrects`` names the snapshot whose value is being corrected, so the
    correction says what it disputes as well as what it asserts.

    The affected value is stated rather than diffed, because a curator looking
    at one field has no "before" state of the whole entity to hand.
    """
    new = iri(value) if value_is_iri else value
    reason = {"corrects": corrects} if corrects else {}

    with record_activity("validation", context=context, subject=subject) as act:
        act.method("curator-review", queue="low-confidence", **reason)
        act.attribute(role="validated", id=curator)
        act.statements([StatementRef(property=property, statement=statement)])
        act.validated("human_corrected", by=curator)

    if statement:
        claim = {"statement": statement, "values": {property: [new]}}
        return client.write(subject, {}, act.envelope, claims=[claim])
    return client.write(subject, {property: [new]}, act.envelope)


# -------------------------------------------------------------- orchestration


def _context(service: str, model: str, config: dict[str, Any]) -> Context:
    """Ambient capture for one stage.

    Built inside the task, so Prefect supplies this stage's own task run id.
    The emitter turns that into the activity id and turns the flow run id into
    the batch id.
    """
    return Context.from_environment(service, model, config, distribution=DISTRIBUTION)


@task(name="import")
def task_import(
    record: Record, upload: Upload, uploader: str | None, url: str | None
) -> dict[str, Any]:
    return stage_import(
        record,
        upload,
        uploader=uploader,
        context=_context(SERVICE_IMPORT, "record-import-v1", IMPORT_CONFIG),
        client=make_client(core_url(url)),
    )


@task(name="reconcile")
def task_reconcile(progress: dict[str, Any], url: str | None) -> dict[str, Any]:
    return stage_reconcile(
        progress,
        context=_context(SERVICE_RECONCILE, "name-match-v2", RECONCILE_CONFIG),
        client=make_client(core_url(url)),
    )


@task(name="enrich")
def task_enrich(progress: dict[str, Any], url: str | None) -> dict[str, Any]:
    return stage_enrich(
        progress,
        context=_context(SERVICE_ENRICH, "authority-copy-v1", ENRICH_CONFIG),
        client=make_client(core_url(url)),
    )


@task(name="quality-check")
def task_quality_check(progress: dict[str, Any], url: str | None) -> dict[str, Any]:
    return stage_quality_check(
        progress,
        context=_context(SERVICE_QUALITY, "required-properties-v1", QUALITY_CONFIG),
        client=make_client(core_url(url)),
    )


def summarise(results: list[dict[str, Any]], upload: Upload) -> dict[str, Any]:
    """What one upload produced, in plain JSON for the page."""
    return {
        "batch": next((r["batch"] for r in results if r["batch"]), None),
        "upload": upload.iri,
        "file": upload.filename,
        "records": len(results),
        "snapshots": sum(len(r["stages"]) for r in results),
        "results": [
            {
                "subject": r["record"].subject,
                "kind": r["record"].kind,
                "id": r["record"].id,
                "stages": r["stages"],
                "skipped": r.get("skipped", []),
                "verdict": r.get("verdict"),
                "missing": r.get("missing", []),
            }
            for r in results
        ],
    }


@flow(name="curate-upload")
def curate_upload(
    records: list[Record],
    upload: Upload,
    uploader: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """The whole workflow over one uploaded file. One run of this is one batch.

    Each stage is mapped over the records and given the previous stage's
    results, so one record's stages run in order while records run side by side.
    Each stage writes as it goes, so a failure late in the run leaves the
    earlier stages recorded rather than losing the whole upload.
    """
    imported = task_import.map(
        records, unmapped(upload), unmapped(uploader), unmapped(url)
    )
    reconciled = task_reconcile.map(imported, unmapped(url))
    enriched = task_enrich.map(reconciled, unmapped(url))
    checked = task_quality_check.map(enriched, unmapped(url))
    return summarise(checked.result(), upload)


@flow(name="correct-value")
def correct_value(
    subject: str,
    curator: str,
    property: str,
    value: str,
    value_is_iri: bool = False,
    statement: str | None = None,
    corrects: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """A curator corrects one value the workflow produced."""
    context = Context.from_environment(
        CURATOR_SERVICE,
        "review-queue-v1",
        {},
        distribution=DISTRIBUTION,
        operator=curator,
    )
    return run_correction(
        subject,
        curator,
        property=property,
        value=value,
        value_is_iri=value_is_iri,
        statement=statement,
        corrects=corrects,
        context=context,
        client=make_client(core_url(url)),
    )
