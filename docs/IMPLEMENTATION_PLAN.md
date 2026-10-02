# ECHOLOT Provenance Demo: Implementation Plan

Sequenced work for Claude Code. Architectural invariants, stack and conventions
live in `CLAUDE.md` and are not repeated here. Work one phase per session, tick
boxes as tasks complete, and log departures in the Deviations log at the end.

**Current phase:** Phase 1 (Phase 0 complete, 2026-09-24)

---

## Stage 1: Self-contained demo

### Phase 0: Repository scaffold

- [x] uv workspace: root `pyproject.toml` with members `packages/echolot-prov`,
      `services/core`, `services/enrich-demo`; Python 3.12 pinned.
- [x] Package skeleton `packages/echolot-prov/src/echolot_prov/` (importable name
      `echolot_prov`), empty modules per the layout in `CLAUDE.md`.
- [x] `ruff` and `pytest` configured at the root; one placeholder test per member.
- [x] Determine the Prefect version pinned by the Enrich services repository and
      pin the same version (current version in the repo"prefect>=3.7.5.dev5");
      update the Stack section in `CLAUDE.md`.

**Done when:** `uv sync`, `uv run pytest` and `uv run ruff check .` all succeed
from a clean clone.

### Phase 1: Envelope models, schema, fixtures (the contract)

This phase gates everything else.

- [ ] `models.py`, Pydantic v2, `extra="forbid"` on every model:
  - `Activity`: `id`, `type` (enum: `reconciliation`, `enrichment`, `validation`,
    `import`, `deletion`), `started_at`, `ended_at`, `batch_of` (optional).
  - `Agent`: `role` (enum: `executed`, `commissioned`, `validated`), `type`
    (enum: `software`, `person`), `id`; a software agent also requires `model`
    and `config_hash`.
  - `Source`: `id`, `snapshot` (dump date or revision), `harvested_at` (optional).
  - `Method`: `name`, `parameters` (dict), `confidence` (optional float, 0 to 1),
    `candidate_rank` (optional int).
  - `Target`: `subject` (URI), `statements` (list of property identifiers).
  - `Validation`: `state` (enum: `auto_accepted`, `human_accepted`,
    `human_corrected`, `pending`), `by` (optional), `at` (optional).
  - `Rights`: `assertion` (optional), `basis` (optional).
  - `Envelope`: `schema_version` (literal `"1.0"`) plus all of the above.
- [ ] `schema.py`: writes `Envelope.model_json_schema()` to
      `packages/echolot-prov/schema/envelope.schema.json`; runnable as
      `python -m echolot_prov.schema`. Commit the output.
- [ ] Check that the software-agent conditional (`model`, `config_hash` required)
      is expressed in the generated JSON Schema, not only in a Pydantic validator.
      If it is not, express it so both paths agree.
- [ ] `fixtures/valid/`: 6 to 8 envelopes, at least one per activity type.
- [ ] `fixtures/invalid/`: one per failure mode: software agent without `model`,
      `confidence` out of range, malformed timestamp, unknown enum value, missing
      `schema_version`, unknown extra field.
- [ ] Tests: every valid fixture passes and every invalid fixture fails, via
      Pydantic and via `jsonschema` against the committed file. Schema drift test.

**Done when:** all fixture tests and the drift test pass.

### Phase 2: Stand-in Core

Build in this order, each step tested against the fixtures.

- [ ] `namespaces.py`: PROV-O, `dcterms:`, `oco:` and demo base IRIs.
- [ ] `validation.py`: loads the committed schema, validates, returns structured
      errors (path, message) suitable for an HTTP 422 response.
- [ ] `store.py`: pyoxigraph with named graphs `data`, `provenance`,
      `agents-sources`; helper to find the current head snapshot of a subject
      (latest `prov:generatedAtTime`).
- [ ] `provenance.py`: envelope to a `prov:Entity` snapshot with
      `prov:generatedAtTime`, `prov:wasAttributedTo`, `prov:hadPrimarySource`,
      `prov:wasDerivedFrom` (previous head, if any), `dcterms:description`, and
      `oco:hasUpdateQuery` holding the INSERT DATA / DELETE DATA delta. One
      activity record per run, lightweight per-statement links.
- [ ] `POST /write` (body: data statements plus envelope): validate, then apply
      the data change and mint the snapshot atomically. Return the new snapshot
      URI. Invalid envelope returns 422 and writes nothing.
- [ ] `reconstruct.py` and `GET /reconstruct?subject=...&at=...`: walk
      `wasDerivedFrom` back from the head, invert each update query (swap INSERT
      and DELETE), return the entity state at that time.
- [ ] `GET /history?subject=...`: snapshot chain with agent, timestamp, source and
      description per snapshot.

**Done when:** tests show (a) each valid fixture produces exactly one snapshot,
(b) each invalid fixture is rejected with no change to any graph,
(c) a write followed by a second write to the same subject yields a two-link
chain, and (d) reconstruction at a time between the two writes returns the
pre-second-write state.

### Phase 3: Emitter library

- [ ] `context.py`: `Context` built either from the live environment (run and
      task id from `prefect.runtime`, service version via
      `importlib.metadata.version`, config hash as SHA-256 over canonical JSON of
      the config, timestamps, operator from a stubbed identity provider) or from
      a plain dict.
- [ ] Pure function `build_envelope(context, ...) -> Envelope`: no I/O.
- [ ] `emitter.py`: context manager `record_activity(kind=...)` yielding a handle
      with `.used(source, snapshot=...)`, `.method(name, **params)`,
      `.attribute(...)`; on exit, calls `build_envelope` and validates.
- [ ] `client.py`: `CoreClient.write(subject, statements, envelope)`, the only
      write function; posts data and envelope together; surfaces Core's 422
      errors as a typed exception.
- [ ] Unit tests with a dict-built `Context` and a fake Core.

**Done when:** unit tests pass without Prefect running or Core reachable, and an
integration test writes through `CoreClient` to the Phase 2 Core.

### Phase 4: Prefect enrich flow

- [ ] `flow.py`, run 1: take a person name, reconcile against a local lookup
      table (stand-in for Wikidata, no network), write result plus envelope via
      the emitter.
- [ ] Run 2: a curator corrects the result, producing a `validation` activity
      linked by `prov:wasInformedBy` to run 1, with a person agent.

**Done when:** running both flows yields a two-snapshot chain for the subject
with different agents, visible via `GET /history`.

### Phase 5: Curator UI

- [ ] Single static page served by Core: choose a subject, show its snapshot
      timeline from `/history`, click a snapshot to show the reconstructed state
      from `/reconstruct`. Plain HTML and `fetch`.

**Done when:** the pre-correction state from Phase 4 can be reconstructed from
the UI.

### Phase 6: Docker Compose and README

- [ ] `docker-compose.yml`: Core (with UI) and the enrich flow; one
      `docker compose up` brings the demo up.
- [ ] `README.md` with an assumptions block (content below) and the demo script.

Assumptions block for the README:

- *Confirmed:* Enrich services are Python, orchestrated with Prefect. NeoWiki is
  open source, runnable via `make dev`, and its dev stack runs MediaWiki,
  MariaDB, Neo4j and QLever. The RDF projection targeted in Stage 2 is QLever.
  NeoWiki is GPL-2.0 and not yet production ready. In production, MariaDB
  revision slots are the source of truth and the graph store is a projection.
- *Assumed:* all Enrich services are Python (WP4: "still open but probably
  Python"); the schema-as-contract design keeps this reversible. Human identity
  will come from ECCCH AAI/SSO (ORCID) and is stubbed here. C2PA is out of scope
  for metadata provenance.
- *Declared divergence:* the demo's triple store is its only store, so the
  projection-rebuild property is not shown.
- *Placeholders:* the FastAPI Core stands in for NeoWiki.

Demo script:

1. `docker compose up`.
2. Trigger the enrich flow; show the new entity and its first snapshot.
3. Trigger the correction flow; show the second snapshot chained to the first,
   with a different agent.
4. Open the UI, walk the timeline, reconstruct the pre-correction state.
5. POST an invalid envelope from `fixtures/invalid/`; show Core rejecting the
   whole write.

**Done when:** the demo script runs end to end from a clean clone.

---

## Stage 2: Adaptation to NeoWiki (blocked, do not start)

The emitter, schema and fixtures stay unchanged in this stage.

**Prerequisites before any Stage 2 code:**

- [ ] Pin a NeoWiki commit.
- [ ] Read `docs/adr/`, the `src/` save-path hooks, and how NeoWiki writes to
      QLever on save.
- [ ] PWIKI answers: is there a hook after the revision write but inside the same
      transaction? Is there a statement-level write path (T3.2), or only
      page-level?
- [ ] Confirm whether Neo4j or QLever is authoritative for the RDF projection.

### Phase 7: PHP plugin

- [ ] MediaWiki extension in the NeoWiki tree, GPL-2.0-compatible, loaded via
      `Docker/LocalSettings.local.php`.
- [ ] Validates the envelope against the committed JSON Schema, mints the
      snapshot, and writes to QLever in the same transaction as the revision,
      using NeoWiki's own projection mechanism rather than a parallel one.
- [ ] Port `provenance.py` and `reconstruct.py` behaviour; reuse the fixtures as
      the PHP test corpus.

### Phase 8: Emitter adapter

- [ ] Point `CoreClient` at the NeoWiki REST endpoint and add auth. Expected to be
      configuration only; any code change here indicates a leak across invariant 8.

---

## Out of scope for now

- **Sidecar** for non-Python producers: build only if one appears.
- **Export mappers** (EDM, Wikidata) and **coverage reporter** (KPI 4.1 share of
  records with provenance, KPI 4.2 manual sample check): Enrich microservices,
  separate cadence.
- **C2PA media track:** read-only detection of Content Credentials via
  `c2patool`, later optional embedding with an AI-disclosure assertion. Never
  touches the envelope or the RDF.

---

## Open questions

- ~~Prefect version pinned in the Enrich repository (Phase 0).~~ Resolved
  2026-09-24: `prefect>=3.7.5.dev5`, copied verbatim; see `CLAUDE.md` Stack.
- **Where Core reads the committed schema from (Phase 1).** `CLAUDE.md` places
  the contract at `packages/echolot-prov/schema/envelope.schema.json`, which is
  outside `src/echolot_prov/`. It is therefore absent from the installed wheel,
  so Core cannot locate it via `importlib.resources`, and a path relative to the
  repository root will break once Core is containerised in Phase 6. Phase 1 must
  decide how Core resolves the file without weakening invariant 1.
- Stage 2 prerequisites above.

## Deviations log

Record every departure from this plan: date, phase, what changed, why.

| Date | Phase | Change | Reason |
|---|---|---|---|
| 2026-09-24 | 0 | Flow moved from `services/enrich-demo/flow.py` to `services/enrich-demo/enrich_demo/flow.py`; `CLAUDE.md` layout updated. | uv's build backend only discovers modules that are directories containing `__init__.py`, so a bare root-level `.py` module cannot be packaged. |
| 2026-09-24 | 0 | Full runtime dependencies declared now rather than per-phase (pydantic, fastapi, pyoxigraph, rdflib, jsonschema, prefect). | Proves the whole stack resolves and installs on Python 3.12/Windows before Phase 1 starts, rather than discovering a wheel or conflict problem mid-phase. |
| 2026-09-24 | 0 | `fastapi[standard]` used rather than bare `fastapi`, adding uvicorn. | `CLAUDE.md` lists FastAPI but no ASGI server; FastAPI cannot serve without one. Taken as an extra of an already-listed dependency, approved with the Phase 0 plan. |
| 2026-09-24 | 0 | `git init` and `.gitignore` added to Phase 0 (no initial commit; left to the maintainer). | The repository was untracked. Phase 0's "from a clean clone" criterion and later "commit the output" steps presuppose version control. |
| 2026-09-24 | 0 | `packages/echolot-prov` declares `requires-python = ">=3.12"` while the root and both services pin `==3.12.*`. | Applications pin their runtime; a distributable library must not. A hard `==3.12.*` on the emitter would make it uninstallable for an Enrich service running a newer Python, defeating its production-intended status. |
