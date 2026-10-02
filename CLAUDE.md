# CLAUDE.md

Standing context for Claude Code in this repository. Read this file at the start
of every session, then read `docs/IMPLEMENTATION_PLAN.md` to find the current phase.

## What this repository is

A runnable demonstrator of the ECHOLOT provenance flow (EU ECCCH project,
requirement FR3.5: provenance is captured automatically during all curation
workflows, produced by the services doing the work).

Flow: an Enrich service performs work, emits a provenance envelope, a Core
service validates the envelope and stores it as PROV-O, and a curator reads the
history of an entity and reconstructs a past state.

- **Stage 1 (this repository):** self-contained demo. Core is a FastAPI stand-in
  for NeoWiki.
- **Stage 2 (later, not in scope yet):** the stand-in is replaced by a PHP
  MediaWiki extension in NeoWiki (`github.com/ProfessionalWiki/NeoWiki`, GPL-2.0),
  and the emitter's client is re-pointed at NeoWiki's REST endpoint.

## Production-intended vs placeholder

| Component | Status | Implication |
|---|---|---|
| `packages/echolot-prov` (emitter library) | Production-intended | Library-quality code, stable public API, full tests |
| Pydantic envelope models + generated JSON Schema | Production-intended | The normative contract between producers and Core |
| PROV-O snapshotting and reconstruction logic (`provenance.py`, `reconstruct.py`) | Production-intended logic | Will be ported to PHP: keep it simple, deterministic, free of framework coupling |
| FastAPI app, Oxigraph store, UI, enrich-demo flow, identity stub | Placeholder | Minimal; no premature abstraction |

## Architectural invariants

Do not change any of these without asking first.

1. **The schema is the contract, not the library.** The JSON Schema generated
   from the Pydantic models is committed to the repository. Any change to the
   envelope bumps `schema_version` and regenerates the committed schema file.
2. **Single write path.** `CoreClient.write(subject, statements, envelope)` is
   the only function that writes. No code path writes data without an envelope.
3. **Reject the whole write.** Core validates the envelope against the committed
   JSON Schema and rejects the entire request on failure, with structured errors.
4. **Atomicity.** The data change and its provenance are committed together
   (one SPARQL UPDATE or one store transaction). No data without provenance,
   no provenance without data.
5. **Pure envelope construction.** Building the envelope is a pure function:
   context in, validated JSON out, no I/O. Ambient capture lives in `context.py`
   and is injectable: `Context` can be built from the live environment or from a
   plain dict. This keeps the emitter testable and a future sidecar cheap.
6. **Two-tier PROV-O granularity.** One activity and one snapshot record per run,
   with lightweight per-statement links. Never one `prov:Activity` per triple.
7. **Snapshot chain.** Each snapshot is `prov:wasDerivedFrom` the previous head
   snapshot of the same subject. The delta is stored as `oco:hasUpdateQuery`
   (INSERT DATA / DELETE DATA). Reconstruction walks the chain backwards and
   inverts each delta.
8. **Core-agnostic emitter.** Nothing specific to NeoWiki, FastAPI or Oxigraph
   appears in `packages/echolot-prov`. The emitter must not know which Core it
   talks to.
9. **No external network calls at runtime.** Reconciliation uses a local lookup
   table standing in for Wikidata.
10. **Declared divergence, not simulated.** In the demo the triple store is the
    only store. In production, MariaDB revision slots are the source of truth and
    the graph store is a rebuildable projection. Do not fake a projection rebuild.

## Stack

- Python 3.12, `uv` workspace (one root `pyproject.toml`, members under
  `packages/` and `services/`). **Applications pin, the library does not:** the
  root and both services declare `requires-python = "==3.12.*"` (the demo's
  runtime), while `packages/echolot-prov` declares `requires-python = ">=3.12"`
  so an Enrich service on a newer Python can still depend on it. Never put a
  ceiling on the library.
- Pydantic v2 (models use `extra="forbid"`, so unknown fields are rejected and
  the generated schema carries `additionalProperties: false`).
- FastAPI (stand-in Core), `pyoxigraph` (embedded store, stand-in only; the
  Stage 2 target is QLever), `rdflib` (PROV-O construction, update-query handling),
  `jsonschema` (validation in Core against the committed schema file).
- Prefect for the Enrich flow. Version: `prefect>=3.7.5.dev5`, copied verbatim
  from the Enrich services repository. The specifier names a pre-release, so
  uv's default `if-necessary-or-explicit` mode permits pre-releases for this
  package alone; do not set a global `prerelease` option. The committed
  `uv.lock` freezes the build actually resolved (3.8.7.dev6 as of 2026-09-24).
- UI: static HTML and vanilla JS served by FastAPI. No framework, no build step.
- `pytest`, `ruff`. Docker Compose for the full demo.

Vocabularies: PROV-O (`prov:`), Dublin Core Terms (`dcterms:`), OpenCitations
Ontology (`oco:`, `https://w3id.org/oc/ontology/`). Define all namespaces and
base IRIs in a single module and import them; never hardcode IRIs across files.

## Repository layout

```
ECHOLOT-Provenance/
  CLAUDE.md
  README.md                      # assumptions block + demo script
  pyproject.toml                 # uv workspace root, ruff + pytest config
  uv.lock                        # committed: reproducibility guarantee
  .python-version                # 3.12
  docker-compose.yml
  docs/
    IMPLEMENTATION_PLAN.md
  packages/
    echolot-prov/
      pyproject.toml
      src/echolot_prov/
        models.py                # Pydantic envelope models
        schema.py                # writes JSON Schema from the models
        context.py               # ambient capture, injectable Context
        emitter.py               # record_activity(...) context manager
        client.py                # CoreClient: the only write path
      schema/envelope.schema.json  # generated, committed contract
      fixtures/valid/  fixtures/invalid/
      tests/
  services/
    core/
      app/
        main.py  validation.py  provenance.py  store.py  reconstruct.py
        namespaces.py
        ui/
      tests/
    enrich-demo/
      enrich_demo/
        flow.py                  # nested: uv_build cannot package a bare
                                 # root-level .py module (see Deviations log)
```

## Commands

Established in Phase 0. Update this section if they change.

```
uv sync                                   # install workspace
uv run pytest                             # all tests
uv run ruff check . && uv run ruff format --check .
uv run python -m echolot_prov.schema      # regenerate the committed schema
docker compose up --build                 # full demo
```

## Conventions

- Type hints throughout. Timestamps are timezone-aware UTC, ISO 8601.
- Fixtures under `packages/echolot-prov/fixtures/` are the shared test corpus
  for both the emitter and Core. Every valid fixture must pass and every invalid
  fixture must fail, checked both through Pydantic and through the committed
  JSON Schema.
- A test asserts that the committed schema file equals freshly generated output
  (schema drift check).
- Keep dependencies to those listed above. Ask before adding another.

## Working agreement

- Work on one phase of `docs/IMPLEMENTATION_PLAN.md` per session. Start in plan
  mode, propose the approach, and wait for approval before writing code.
- Tick checkboxes as tasks complete. A phase is done only when its
  "Done when" criteria pass.
- Record every departure from the plan in the plan's Deviations log, with the
  reason.
- If a task conflicts with an invariant above, stop and ask rather than work
  around it.
- Do not start Stage 2 work unless explicitly asked.
