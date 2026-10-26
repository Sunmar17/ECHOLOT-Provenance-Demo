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
   from the Pydantic models is committed to the repository, **one file per
   version**, at `src/echolot_prov/schemas/envelope-<version>.schema.json`,
   inside the package so it ships in the wheel. Each file carries a versioned
   `$id`. Any change to the envelope bumps `schema_version` and writes a **new**
   file; a released version file is frozen and never regenerated or edited.
   Core must be able to validate every version it has ever accepted, because a
   migration serves two versions at once and a projection rebuild replays every
   envelope ever stored.
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
6. **Two-tier PROV-O granularity.** One activity and one snapshot record per
   run, with per-statement links beneath them. Never one `prov:Activity` per
   triple: that is the rule this invariant exists to state. From schema 1.1 a
   per-statement link may carry its own delta, so these links are no longer
   "lightweight" in the sense the earlier wording used. The tiering is what
   matters, not the size of the lower tier.
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
11. **Identity is always an absolute IRI.** `Target.subject` is an absolute
    IRI, and so is every `property` and `statement` inside `Target.statements`.
    A statement IRI identifies **one claim**, never the subject that claim is
    about: `<person/42>` and the claim "person 42 has creator X" are different
    things with different identifiers. Mapping a backend's native
    identifiers (NeoWiki page, slot or item ids) to IRIs is a **deployment
    configuration rule** under a declared base IRI: never an envelope field and
    never emitter code. Putting native ids in the envelope would make its
    contents depend on the downstream Core, breaking invariant 8, and would
    force a schema change at Stage 2.

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
    revisionfile.md              # accepted risks and their trigger conditions
  packages/
    echolot-prov/
      pyproject.toml
      src/echolot_prov/
        models.py                # Pydantic envelope models
        schema.py                # writes JSON Schema from the models
        context.py               # ambient capture, injectable Context
        emitter.py               # record_activity(...) context manager
        client.py                # CoreClient: the only write path
        schemas/                 # generated, committed contract; ships in the
          envelope-1.0.schema.json   # wheel. One frozen file per version:
          envelope-1.1.schema.json   # 1.1 names individual claims.
      fixtures/<version>/valid/  fixtures/<version>/invalid/
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
- Fixtures under `packages/echolot-prov/fixtures/<version>/` are the shared test
  corpus for both the emitter and Core, organised by contract version. Two rules,
  and they differ: **every** fixture of **any** version is checked against its
  own committed JSON Schema, which is what proves Core still accepts envelopes a
  producer sent before upgrading; **only** current-version fixtures are also
  checked through Pydantic, because the models express one version at a time.
- A test asserts that the committed schema file for the **current**
  `schema_version` equals freshly generated output (schema drift check). The
  models only ever generate the current version, so historical files cannot be
  regenerated for comparison; a second test asserts that for every
  `envelope-X.Y.schema.json` the filename version, the `$id` version segment and
  the `schema_version` const all agree.
- The schema's cross-language release channel is a git tag on this repository
  (`schema-v1.0`), whose release asset Stage 2's PHP build vendors. The wheel
  carries the schema for Python consumers. Nothing fetches a schema at runtime
  (invariant 9); the `$id` is an identifier, never dereferenced.
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
