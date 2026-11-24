# ECHOLOT Provenance Demo: Implementation Plan

Sequenced work for Claude Code. Architectural invariants, stack and conventions
live in `CLAUDE.md` and are not repeated here. Work one phase per session, tick
boxes as tasks complete, and log departures in the Deviations log at the end.

**Current phase:** Phase 6 (Phase 0 complete 2026-09-24; Phase 1a and 1b
complete 2026-10-02; Phase 2 complete 2026-10-09; Phase 3 complete 2026-10-15;
Phase 4 complete 2026-10-19; Phase 5 complete 2026-10-21; Phase 1.1a and 4b
complete 2026-10-26; Phase 4c and 1.1b complete 2026-10-29)

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

This phase gates everything else. Split into 1a (the contract itself) and 1b
(the test corpus that proves it), so each is one session.

#### Phase 1a: Models and versioned schema

- [x] `models.py`, Pydantic v2, `extra="forbid"` on every model:
  - `Activity`: `id`, `type` (enum: `reconciliation`, `enrichment`, `validation`,
    `import`, `deletion`), `started_at`, `ended_at`, `batch_of` (optional).
  - `Agent`: `role` (enum: `executed`, `commissioned`, `validated`), `type`
    (enum: `software`, `person`), `id`; a software agent also requires `model`
    and `config_hash`.
  - `Source`: `id`, `snapshot` (dump date or revision), `harvested_at` (optional).
  - `Method`: `name`, `parameters` (dict), `confidence` (optional float, 0 to 1),
    `candidate_rank` (optional int).
  - `Target`: `subject` (absolute IRI), `statements` (list of absolute IRIs).
    Per invariant 11, never native backend ids. Each `statements` entry is a
    property IRI today; the type also admits a statement-instance IRI, which is
    the headroom for the open PWIKI question. See `docs/revisionfile.md`.
  - `Validation`: `state` (enum: `auto_accepted`, `human_accepted`,
    `human_corrected`, `pending`), `by` (optional), `at` (optional).
  - `Rights`: `assertion` (optional), `basis` (optional).
  - `Envelope`: `schema_version` (literal `"1.0"`) plus all of the above.
- [x] `schema.py`: writes `Envelope.model_json_schema()` to
      `src/echolot_prov/schemas/envelope-<schema_version>.schema.json`, deriving
      the filename from the model's `schema_version` literal and injecting a
      versioned `$id`. Runnable as `python -m echolot_prov.schema`. Commit the
      output.
- [x] Overwrite guard in `schema.py`: refuse to rewrite an existing version file
      whose content differs, unless the version was bumped. This is what makes
      "frozen" enforced rather than remembered.
- [x] Check that the software-agent conditional (`model`, `config_hash` required)
      is expressed in the generated JSON Schema, not only in a Pydantic validator.
      If it is not, express it so both paths agree.

**Done when:** `python -m echolot_prov.schema` writes
`envelope-1.0.schema.json` with a matching `$id`, re-running it is a no-op, and
the guard rejects a hand-edited version file.

#### Phase 1b: Fixtures and the test corpus

- [x] `fixtures/valid/`: 6 to 8 envelopes, at least one per activity type.
- [x] `fixtures/invalid/`: one per failure mode: software agent without `model`,
      `confidence` out of range, malformed timestamp, unknown enum value, missing
      `schema_version`, unknown extra field, unknown `schema_version`, `subject`
      not an absolute IRI.
- [x] Tests: every valid fixture passes and every invalid fixture fails, via
      Pydantic and via `jsonschema` against the committed file.
- [x] Schema drift test, against the **current** version file only.
- [x] Version-consistency test: for every `envelope-X.Y.schema.json`, the
      filename version, the `$id` version segment and the `schema_version` const
      all agree.

**Done when:** all fixture tests, the drift test and the consistency test pass.

### Phase 1.1a: the contract gains statement identity

- [x] `StatementRef` model: a `property`, and an optional `statement` naming one
      claim. `Target.statements` becomes a list of them.
- [x] `SCHEMA_VERSION` bumped to 1.1; `envelope-1.1.schema.json` written and
      committed. `envelope-1.0.schema.json` untouched and still frozen.
- [x] Fixtures reorganised by version. Every fixture is checked against its own
      schema; only current-version fixtures go through Pydantic.
- [x] `diff_statements` returns references rather than bare IRIs.
- [x] Core reads both shapes, and refuses a claim identity it cannot yet place.
- [x] Invariants 6 and 11 reworded.

**Done when:** both versions validate through Core's registry, 1.0 stays
byte-frozen, and a 1.1 envelope naming a claim gets a clean 422 rather than a
500. Met.

### Phase 1.1b: claims get their own IRI, and the page shows and corrects them

Prompted by the Phase 4c browser demo: the subject list showed no names,
correction was one general bar, a record with two creators and one match could
not say which creator the match belonged to, and nothing on the page said which
data a provenance record was about.

- [x] `CoreClient.write(..., claims=[...])`. A claim with no `statement` is new
      and Core mints its IRI; one with a `statement` names an existing claim.
      Backward compatible: without claims, the payload is unchanged.
- [x] Core stores `<subject> echolot:statement <claim>` and the claim's values
      on the claim node. New claim IRIs are minted deterministically from
      subject, activity id, end time and position (invariant 10).
- [x] Core checks claims against `Target.statements`: an existing claim by
      property and IRI, a new claim and a plain value by property. A claim that
      is not this subject's is refused. The 1.1 guard is deleted (revisionfile 20).
- [x] One delta per write, built per node; `echolot:affectedStatement` points at
      claim IRIs. `/history` rows carry `changes`, the delta grouped by claim;
      `/reconstruct` rows carry `statement`. Reconstruction works on triples.
- [x] The import writes `rdfs:label`; `/subjects` returns `labels`.
- [x] Each creator becomes a claim; reconciliation writes the match on the claim
      it belongs to.
- [x] The general correction bar is replaced by a Correct button on each value a
      snapshot wrote. A correction names the claim (or property) and records the
      snapshot it corrects as `corrects` in `method.parameters`.
- [x] Every value from the data is HTML-escaped on the page.

**Done when:** names in the list; the match sits on one creator's claim; a
correction from the page replaces the Molnár match on its claim and names the
snapshot it corrects; full suite passes. Met 2026-10-29, checked in a browser;
239 tests pass.

### Phase 2: Stand-in Core

Build in this order, each step tested against the fixtures.

- [x] `namespaces.py`: PROV-O, `dcterms:`, `oco:` and demo base IRIs.
- [x] `validation.py`: a version to schema registry, loaded from the installed
      package via `importlib.resources` and holding every committed version.
      Two-step validation: read `schema_version`, select that version's schema,
      then validate. An unknown or missing `schema_version` is a clean 422, not a
      `KeyError`. Returns structured errors (path, message) for the 422 response.
      Carrying every version is what lets Core serve a migration and re-validate
      replayed envelopes (invariant 1).
- [x] `store.py`: pyoxigraph with named graphs `data`, `provenance`,
      `agents-sources`; helper to find the current head snapshot of a subject
      (latest `prov:generatedAtTime`).
- [x] `provenance.py`: envelope to a `prov:Entity` snapshot with
      `prov:generatedAtTime`, `prov:wasAttributedTo`, `prov:hadPrimarySource`,
      `prov:wasDerivedFrom` (previous head, if any), `dcterms:description`, and
      `oco:hasUpdateQuery` holding the INSERT DATA / DELETE DATA delta. One
      activity record per run, lightweight per-statement links.
- [x] `POST /write` (body: data statements plus envelope): validate, then apply
      the data change and mint the snapshot atomically. Return the new snapshot
      URI. Invalid envelope returns 422 and writes nothing.
- [x] `reconstruct.py` and `GET /reconstruct?subject=...&at=...`: walk
      `wasDerivedFrom` back from the head, invert each update query (swap INSERT
      and DELETE), return the entity state at that time.
- [x] `GET /history?subject=...`: snapshot chain with agent, timestamp, source and
      description per snapshot.

**Done when:** tests show (a) each valid fixture produces exactly one snapshot,
(b) each invalid fixture is rejected with no change to any graph,
(c) a write followed by a second write to the same subject yields a two-link
chain, and (d) reconstruction at a time between the two writes returns the
pre-second-write state.

### Phase 3: Emitter library

- [x] `context.py`: `Context` built either from the live environment (run and
      task id from `prefect.runtime`, service version via
      `importlib.metadata.version`, config hash as SHA-256 over canonical JSON of
      the config, timestamps, operator from a stubbed identity provider) or from
      a plain dict.
- [x] Pure function `build_envelope(context, ...) -> Envelope`: no I/O.
- [x] Before/after comparator: derive `Target.statements` by diffing the entity
      before and after the work, rather than having each producer maintain the
      list by hand. Removes a whole class of producer bugs. Note this is the
      producer's *intent* diff; Core still computes the actual delta against
      current state, because only Core knows it (see the Phase 2 deviations).
- [x] `emitter.py`: context manager `record_activity(kind=...)` yielding a handle
      with `.used(source, snapshot=...)`, `.method(name, **params)`,
      `.attribute(...)`; on exit, calls `build_envelope` and validates.
- [x] `client.py`: `CoreClient.write(subject, statements, envelope)`, the only
      write function; posts data and envelope together; surfaces Core's 422
      errors as a typed exception.
- [x] Unit tests with a dict-built `Context` and a fake Core.

**Done when:** unit tests pass without Prefect running or Core reachable, and an
integration test writes through `CoreClient` to the Phase 2 Core.

### Phase 4: Prefect enrich flow

- [x] `flow.py`, run 1: take a person name, reconcile against a local lookup
      table (stand-in for Wikidata, no network), write result plus envelope via
      the emitter.
- [x] Run 2: a curator corrects the result, producing a `validation` activity
      linked by `prov:wasInformedBy` to run 1, with a person agent.

**Done when:** running both flows yields a two-snapshot chain for the subject
with different agents, visible via `GET /history`.

### Phase 4b: Real workflow, batches, and a UI trigger

Added after review found that Prefect orchestrated nothing.

- [x] Each stage of a workflow emits its own envelope. Three stages:
      reconciliation (T4.2), enrichment (T4.3), quality check (T4.5).
- [x] `Activity.id` comes from the Prefect task run id, and `batch_of` from the
      flow run id. Before this, three stages shared one activity id and Core
      collapsed them into a single activity.
- [x] `GET /batch?id=...` returns every snapshot one workflow produced.
      `GET /history` reports each snapshot's batch.
- [x] The enrich service exposes `POST /run`, and the page has a form that
      calls it. Core stays a store and does not run workflows.
- [x] The page also offers a correction control, calling `POST /correct`. A
      correction is attributed to a person and carries no batch id, because it
      is not a stage of an automated workflow.

**Done when:** one press of Run workflow produces three snapshots that share a
batch id and differ in activity, and the oldest reconstructs to the state after
stage 1 only. Met, checked in a browser.

### Phase 4c: Upload one person (JSON) or a batch of artifacts (CSV)

Added after the WP4 hybrid-curation meeting: a curator uploads a CSV or JSON
file holding one record or a batch of 10 to 40.

- [x] `enrich_demo/upload.py`: one pure reader for both formats. A JSON object,
      a JSON list or CSV rows become the same `Record`. 1 to 40 records; a bad
      record refuses the whole file with its position.
- [x] `enrich_demo/vocab.py`: the enrich side's single source of IRIs, and the
      subject rule `https://demo.echolot.eu/sample/{kind}/{id}` (invariant 11).
- [x] A fourth stage, `import`, writes the record as uploaded. Its source is the
      file, named by its SHA-256; the uploader is a `commissioned` person agent.
- [x] Reconciliation matches a person on its GND id and an artifact on each name
      in its creator list. Enrichment copies missing authority ids, people only;
      for an artifact it emits no envelope. The quality check reads its required
      properties per kind.
- [x] Each stage is mapped over the records with `task.map`, so every
      record-stage has its own task run id and activity. One upload is one flow
      run and one batch.
- [x] Two synthetic samples in `enrich_demo/samples/`: one person (Luciano
      Berio) and 20 artifacts. The stand-in table was rebuilt from ids fetched
      from Wikidata on 2026-10-29.
- [x] `POST /upload` (multipart) and `POST /upload/sample` on the enrich
      service. The page has a file picker and two sample buttons.

**Done when:** both sample buttons work in the browser and the slow test passes.
Met 2026-10-29: the CSV sample gave 20 records, 60 snapshots, one batch and 4
incomplete records; an artifact's first snapshot reconstructs to the record as
uploaded; the person sample gave 4 snapshots ending with its Wikidata and VIAF
ids. 214 fast tests and 3 slow tests pass.

### Phase 5: Curator UI

- [x] Single static page served by Core: choose a subject, show its snapshot
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

## Stage 2: Adaptation to NeoWiki (Phase 7 blocked; interim adapter started 2026-11-12 on request)

The emitter, schema and fixtures stay unchanged in this stage.

**Prerequisites before any Stage 2 code:**

- [x] Pin a NeoWiki commit: release `0.1.0` (commit `92cf0e3`, 2026-10-28),
      running locally. See "Stage 2 preparation" below.
- [ ] Read `docs/adr/`, the `src/` save-path hooks, and how NeoWiki writes to
      QLever on save. Partly done: the revision hook and the Subject storage are
      read; the ADRs are not.
- [ ] PWIKI answers: is there a hook after the revision write but inside the same
      transaction? Is there a statement-level write path (T3.2), or only
      page-level? **The second question blocks Phase 1a**, not just Stage 2: it
      decides whether provenance attaches to a property or to a statement
      instance, and therefore what `Target.statements` must be able to hold.
      See `docs/revisionfile.md`. **Read from the running 0.1.0 instance on
      2026-11-02; the NeoWiki team has not confirmed these yet:**
      - Hook: yes. In MediaWiki 1.43.11, `RevisionFromEditComplete` runs after
        the revision insert and before `endAtomic`, so inside the save
        transaction (`PageUpdater.php` lines 1394 and 1524). NeoWiki projects to
        its graph stores from this hook. That projection is an HTTP call to
        another store, so it is not part of the MariaDB transaction.
      - Write path: a one-statement write exists
        (`PUT /subject/{id}/statements/{property}`), but it still saves a whole
        Subject as one revision. A Statement is keyed by property name and has
        no id; Relations and Subjects have stable ids.
- [x] Confirm whether Neo4j or QLever is authoritative for the RDF projection.
      Neither. The Subject JSON sits in a MariaDB revision slot (`neo`); QLever
      holds a projection that `make rebuild-graph-databases` rebuilds "from
      MariaDB". This matches invariant 10.

### Stage 2 preparation: a local NeoWiki 0.1.0

Done 2026-11-02. No Stage 2 code, no change to Core, the emitter or the tests.
Purpose: test the integration questions against a real instance.

```
 Windows
 ├─ Docker Desktop ── neowiki-mediawiki-1 (MediaWiki 1.43.11, NeoWiki 0.1.0)
 │                    neowiki-db-1        (MariaDB 11.8)
 │                    neowiki-qlever-1    (QLever, SPARQL projection)
 └─ Browser ───────── http://localhost:8484   AdminName / AdminPassword
```

- **Files:** `C:\Users\manoharas\Desktop\neowiki\` (`docker-compose.yml` and
  `.env` from tag 0.1.0). Outside this repository: NeoWiki is GPL-2.0.
- **Why not `make demo`:** `make` is not installed on Windows, and Ubuntu in WSL
  had no internet on 2026-11-03 and had it on 2026-11-04 (cause not found). The commands are the ones in the 0.1.0
  Makefile (`demo`, `install-db`, `import-demo-data`), run by hand.
- **Limit: this instance is read-only reference.** It is the demo image, with no
  NeoWiki source mounted, so it cannot host a change to NeoWiki or a new
  endpoint. Phase 7 needs the dev stack (`make dev`), which bind-mounts the
  NeoWiki source and loads `Docker/LocalSettings.local.php`. Its `bootstrap`
  step runs `git clone` of MediaWiki core (`REL1_43`) and nine submodules from
  the shell that runs `make`. Ubuntu in WSL had no internet on 2026-11-03 and had it on 2026-11-04 (cause not found),
  but containers do (tested 2026-11-03: github.com, packagist.org,
  registry.npmjs.org and deb.debian.org answered 200). The dev stack was set up
  on 2026-11-04 (next bullet).
- **Dev stack (set up 2026-11-04):** NeoWiki 0.1.0 cloned (`--depth 1 --branch
  0.1.0`) into the Ubuntu file system and started with `make dev` from there,
  outside this repository. Compose project `neowiki-neowiki`; it takes port
  8484, so the demo stack above must stay stopped while it runs. Checked: front
  page 200, `AdminName` login passes, REST lists 85 Subjects in 19 Schemas,
  and the NeoWiki source, `LocalSettings.local.php` and `extensions-extra` are
  bind mounts (a file created on the host showed in the container). Start
  and stop from the NeoWiki folder in Ubuntu: `make dev`, `make down` (keeps
  the data), `make remove` (deletes the data). The clone is on a detached HEAD
  at the tag; create a branch before the first commit. No Stage 2 code exists
  yet.
- **Start / stop** (from that folder): `docker compose -p neowiki up -d`,
  `docker compose -p neowiki down` (keeps the data),
  `docker compose -p neowiki down --volumes` (deletes the data).
- **REST API:** `http://localhost:8484/w/rest.php/neowiki/v0/*`. Writes need a
  login and an `X-CSRF-TOKEN` header.

**Test:** create one Sandbox Subject, change its `Description` twice, read it
back. Result: 3 revisions, and `GET subject/{id}?revisionId=` returned `v1`,
`v2` and `v3` for them.

| Question | Finding |
|---|---|
| What does NeoWiki keep per change? | One MediaWiki revision per write: user, time, edit summary, size, sha1. The whole Subject JSON is stored again in revision slot `neo` (content model `NeoWikiSubject`). No delta is stored. |
| Can a past state be read? | Yes. `?revisionId=` returns the Subject as of that revision. |
| Who changed one statement, and why? | Not recorded. A revision has one user and one summary for the whole Subject. A one-statement `PUT` saves the whole Subject. |
| What does QLever hold? | The current state only: 3 triples for the test Subject (label, `Description` = `v3`, type). No history. |
| Does NeoWiki build provenance? | No. Its docs give "chain-of-production provenance and rights" to "the T2.4 model and a T3.4 plug-in", and say its named graphs record "data origin only". |
| Statement ids | None. A claim with an id has to be a Relation or a dependent Subject (`docs/qualifiers-and-references.md`). See revisionfile 24. |

So NeoWiki tracks change as MediaWiki revisions. That is whole-Subject history,
not the statement-level provenance of FR3.5.

### Interim NeoWiki adapter (no PHP)

Started 2026-11-12 on the user's explicit request. Phase 7 stays on hold. The
Core UI stays the place where a curator imports data and makes corrections; the
provenance of those actions is also stored in NeoWiki. Three phases:

| Phase | What | Status |
|---|---|---|
| A | Each envelope is also stored in NeoWiki as a revision of a JSON page; the UI links to that page's history | done 2026-11-12 |
| B | The record itself is stored as a NeoWiki Subject (Schemas for Person and Artifact, creators as dependent Subjects) | proposed |
| C | Core's own store is switched off; the UI's read panels point at NeoWiki | decide after B |

```
 Curator ─► Core UI ─► Enrich service ─► Prefect flow ─► CoreClient.write   (unchanged)
                                                              ▼
                                             Core  POST /write
                                             1. validate envelope        unchanged
                                             2. commit data + PROV-O     unchanged
                                             3. append envelope to NeoWiki   (new, services/core/app/neowiki.py)
                                                              ▼
                              NeoWiki page  Provenance/<path after the base IRI>
                              one revision per envelope; index.php?title=…&action=history
```

**Phase A**

- [x] `services/core/app/neowiki.py`: `NeoWikiSink` (login, CSRF token, append with
      `baserevid` and retry), `page_title` (IRI to title), `history_url`.
- [x] `/write` calls the sink after Core's commit; a sink failure answers `502`
      and says Core committed. `/subjects` gains `neowiki_history`; the page shows
      an "Open history in NeoWiki" link.
- [x] Off unless `ECHOLOT_NEOWIKI_URL`, `ECHOLOT_NEOWIKI_USER`,
      `ECHOLOT_NEOWIKI_PASSWORD` are set; `ECHOLOT_NEOWIKI_BASE_IRI` sets the base
      for page titles (default: Core's demo base). Nothing changes in
      `packages/echolot-prov`, the schema or the fixtures.
- [x] `httpx>=0.28` declared in `services/core` (already in the workspace through
      `echolot-prov`; `uv.lock` gains two lines and no package).
- [x] 33 new tests (a fake MediaWiki on `httpx.MockTransport`); 272 tests pass;
      `ruff check` and `ruff format --check` pass.

**Checked live, 2026-11-12, against the local dev wiki:** the sample person and the
20-artifact sample ran through the real Prefect flow with Core on port 8010 and
the sink on. The wiki held one page per record: 5 revisions for the person (4
stages and one correction) and 3 for each artifact, 65 writes in all, with no
conflict although the flow writes records in parallel. The diff between two
revisions showed only the new envelope. The page's link opened the right history
URL. Core's store and UI worked as before.

**Limits** (revisionfile 26): the NeoWiki copy is a second request and not part of
Core's atomic commit, so invariant 4 does not hold across the two stores; the
envelope sits on its own page, not in a slot on the record's page, because the core
edit API cannot write a slot; the record's data is not in NeoWiki until Phase B.

**Run it** (Core with the sink on; set the variables in the shell that starts Core):

```
ECHOLOT_NEOWIKI_URL=http://localhost:8484/w
ECHOLOT_NEOWIKI_USER=<wiki user>
ECHOLOT_NEOWIKI_PASSWORD=<its password>
ECHOLOT_NEOWIKI_BASE_IRI=https://demo.echolot.eu/sample
uv run uvicorn app.main:app --port 8000
```

### Decision 2026-11-22: NeoWiki is the core, Core is replaced

Given by the user. The NeoWiki team has not confirmed any of it.

- NeoWiki is the expected core. Core (the FastAPI app, the Oxigraph store, `provenance.py`,
  `reconstruct.py` and its UI) is replaced completely once NeoWiki does its jobs.
- Atomicity (invariant 4) is not mandatory at the moment.
- Provenance is stored as JSON, the envelope as it is sent (the Phase A page layout). **Storing it as PROV-O
  is on halt.** NeoWiki Schemas are for the record's data (Person, Artifact). Schemas for the envelope itself
  are a later option.
- Import and correction screens are in the WP4 agentic app. History is in NeoWiki. No new curator UI is
  built.

What NeoWiki 0.1.0 offers, read from its docs and `extension.json` on 2026-11-22. **Not yet checked on the
running wiki:**

| Topic | What the docs say |
|---|---|
| Validation | `POST /neowiki/v0/subject/validate` and `violations` in write replies. A write is blocked only when `$wgNeoWikiEnforceValidation` is on and a constraint has severity `error`. The default severity is `warning`. |
| Schema types | text, monolingualText, url, number, select, relation, boolean, date, dateTime. No regex, no constant, no free-form map, no integer type. Unknown statements are ignored. |
| Claims | Statements have no ids. A claim must be a dependent Subject. Relation ids may be dropped in 0.2 (revision file 24). |
| One revision | `PUT /subject/{id}` with `dependentSubjects` saves them in the same revision. Used for a record's creators. The envelope page is a separate request, so a record and its envelope are two requests. To confirm. |
| Past states | `GET /subject/{id}?revisionId=N`. |
| Ids | 15 characters, not IRIs. The caller may assign one, and `POST /subject-ids` pre-mints a batch. IRIs: `$wgNeoWikiRdfBaseUri` plus `/entity/<id>`. |

| Step | What | Status |
|---|---|---|
| 1 | Record the decision. GitLab: delete 5 issues, add 5 (`docs/gitlab-changes.md`) | done 2026-11-22, documents only |
| 2 | Spike on the dev wiki: the record as a Subject checked by Schemas (creators as dependent Subjects), the link to its envelope page, enforcement, the IRI rule | next. Needs the wiki running and its own plan |
| 3 | A transport that writes the record (a Subject) and the envelope (a JSON page, as sent) to NeoWiki from the library side, outside `packages/echolot-prov` (invariant 8) | blocked by step 2 |
| 4 | Remove Core | blocked by step 3 |
| 5 | Compose stack, README and demo script on NeoWiki | blocked by step 3 |

Invariant 4 is relaxed by the user (a note in `CLAUDE.md`). Invariant 3 moves to the library, because
NeoWiki does not check the envelope. Invariants 6 and 7 are on halt with PROV-O. Invariants 3 and 10 are
reworded after the spike. The others are kept. The Phase A code stays until step 4. Its login, token, retry and title code is
reused in step 3. The earlier Phases B and C of the interim route are replaced by steps 2 to 4.

### Phase 7: PHP plugin

**Deferred (2026-11-22).** Not needed for atomicity or validation under the decision above. Reopen only if
atomicity or server-side validation of the envelope becomes required.

- [ ] MediaWiki extension in the NeoWiki tree, GPL-2.0-compatible, loaded via
      `Docker/LocalSettings.local.php`.
- [ ] Validates the envelope against the committed JSON Schema, mints the
      snapshot, and writes to QLever in the same transaction as the revision,
      using NeoWiki's own projection mechanism rather than a parallel one.
- [ ] Port `provenance.py` and `reconstruct.py` behaviour; reuse the fixtures as
      the PHP test corpus.

### Phase 8: Emitter adapter

**Replaced (2026-11-22)** by step 3 of the decision above: a transport outside `packages/echolot-prov`.
The text below is the original wording.

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
- **Who owns the Stage 2 base IRI** used to mint subject IRIs from NeoWiki
  native identifiers (invariant 11). Needed before Phase 7, not before Phase 1a.
- **The `$id` base URL.** Proposed `https://w3id.org/echolot/schema/envelope/<version>`,
  matching the `oco:` precedent. It is never dereferenced, so w3id registration
  can lag the first release.
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
| 2026-10-02 | 1 | Invariant 1 rewritten: one frozen schema file per version at `src/echolot_prov/schemas/envelope-<version>.schema.json`, inside the package. | A single overwritten file leaves only the newest schema in any build, so Core cannot validate a second version during a migration, nor re-validate envelopes replayed by a projection rebuild (invariant 10). Placing it under `src/` is what gets it into the wheel. |
| 2026-10-02 | 1 | Invariant 11 added: identity is always an absolute IRI; native-id mapping is deployment configuration. | Keeps the envelope independent of the downstream Core (invariant 8) and makes Stage 2's "schema unchanged" promise achievable instead of aspirational. |
| 2026-10-02 | 1 | Phase 1 split into 1a (models, versioned schema, overwrite guard) and 1b (fixtures, drift and consistency tests). | The versioning and identity decisions grew the phase past one session. |
| 2026-10-02 | 1 | Drift test narrowed to the current version; version-consistency test added. | The models only ever generate the current version, so historical files cannot be regenerated for comparison. |
| 2026-10-02 | 1 | Schema release channel is a git tag on this repository, not a separate schema repository. | Gets the pinned, reproducible cross-language release the design needs while keeping the drift test and single source of truth in one checkout. |
| 2026-10-02 | 1b | `jsonschema[format-nongpl]` used rather than bare `jsonschema`, in `services/core` and the root dev group. | Without a format checker, `format: date-time` is advisory, so a malformed timestamp passes `jsonschema` while failing Pydantic and the two validation paths disagree. `rfc3339-validator` was present only transitively via Prefect. The `format-nongpl` extra avoids the GPL `strict-rfc3339`, which matters since NeoWiki is GPL-2.0. |
| 2026-10-02 | 1b | 7 valid and 8 invalid fixtures (plan said 6 to 8 valid, 6 failure modes). | Two failure modes were added by the Phase 1 design: unknown `schema_version` and a `subject` that is not an absolute IRI (invariant 11). |
| 2026-10-09 | 2 | Atomicity delivered as one SPARQL UPDATE rather than a store transaction. | pyoxigraph 0.5.11 exposes no `transaction()`. Verified that one multi-operation update spans all three named graphs and that a failure anywhere in the update string leaves zero quads written, which is the guarantee invariant 4 asks for. |
| 2026-10-09 | 2 | `Target.statements` recorded as producer *intent*, with `oco:hasUpdateQuery` holding the delta Core actually computed. | Only Core knows current state authoritatively; a producer's "before" can be stale by the time its write lands. Writing a value that is already current is a legitimate empty delta, and recording both facts is more honest than recording either alone. |
| 2026-10-09 | 2 | `POST /write` rejects a payload whose predicates differ from `Target.statements`. | Otherwise the stored provenance is a confident account of something that did not happen. The emitter is one producer option, not a guarantee: a sidecar for non-Python producers is explicitly contemplated. |
| 2026-10-09 | 2 | Request body typed as an untyped `dict`, not the Pydantic `Envelope`. | Typing it would let FastAPI reject bad input first in its own format, bypassing the committed JSON Schema entirely and breaking invariant 3. |
| 2026-10-09 | 2 | Snapshot IRIs minted deterministically from subject, activity id and `ended_at`. | Invariant 10 makes the graph store a rebuildable projection; a random UUID would make every rebuild a different graph. |
| 2026-10-09 | 2 | `prov:specializationOf` links a snapshot to its subject; agent roles use one predicate each (`echolot:executedBy` and friends). | A qualified `prov:Association` is the textbook encoding but adds a node per agent per run, against invariant 6's call for lightweight links. |
| 2026-10-09 | 2 | Demo-local vocabulary `echolot:` added alongside prov, dcterms and oco. | PROV-O, Dublin Core and OCO have no term for a per-statement link, an activity type or a config hash. Confined to `namespaces.py`. |
| 2026-10-09 | 2 | `.gitattributes` added pinning `eol=lf`. | Two docs rewritten by a Python script on Windows had silently become CRLF, so every line read as changed and the real edit was invisible in the diff. |
| 2026-10-09 | 3 | Before/after comparator added to Phase 3 scope. | Proposed during Phase 2 planning: deriving `Target.statements` from a diff removes the hand-maintained list and the bugs that come with it. |
| 2026-10-15 | 3 | `httpx` added to `echolot-prov`'s dependencies, its second after pydantic. | `CoreClient` needs a transport and CLAUDE.md's stack names no HTTP client. Injectable, so tests supply `httpx.MockTransport` or the Core app itself through `TestClient`, which is an `httpx.Client` subclass. |
| 2026-10-15 | 3 | Producers pass plain Python values; `iri()` marks an IRI explicitly. | An IRI is never inferred from a value's shape, because a note such as "see https://example.org" would otherwise be stored silently as a link rather than the text somebody wrote. |
| 2026-10-15 | 3 | `Context.from_environment` takes `distribution` separately from `service`. | Found by running the flow by hand: `service` is the agent's identity in the provenance, while the version comes from an installed package that often has a different name. Conflating them either mislabels the agent or loses the version. |
| 2026-10-15 | 3 | The emitter-to-Core integration tests live in `services/core/tests/`. | `echolot-core` depends on `echolot-prov`, so Core's tests may use the emitter; the reverse inverts the dependency and stops the library's suite running on its own. It now passes standalone, 81 tests. |
| 2026-10-15 | 3 | A failed activity emits no envelope. | The contract cannot express a failed run, and inventing a way is a `schema_version` decision rather than a local one. See `docs/revisionfile.md` entry 15. |
| 2026-10-19 | 4 | `prov:wasInformedBy` not emitted; the snapshot chain carries the relationship instead. | The chain already links the two runs through `wasDerivedFrom`, so an activity-level link would be derivable data a producer could state in contradiction to it. Recorded as future scope in `docs/revisionfile.md` entry 16. The schema stays at 1.0 and `provenance.py` is untouched. |
| 2026-10-19 | 4 | Flow logic separated from Prefect orchestration: each `@flow` is a thin wrapper over a plain function. | A flow run costs about ten seconds because Prefect starts and stops a temporary server. The split keeps 129 of 131 tests running in 2 seconds while the two that prove the phase's criterion run real flows. |
| 2026-10-19 | 4 | A `slow` pytest marker registered, and the flow tests import Core although `enrich-demo` does not depend on it. | Acceptable for a placeholder service Stage 2 replaces, and not acceptable for `echolot-prov`, which must install and test standalone for an Enrich team. |
| 2026-10-19 | 4 | `services/enrich-demo/tests/conftest.py` raises Prefect's log level. | Prefect's temporary server logs through a rich console at interpreter shutdown, after pytest has closed the stream, producing a traceback on a run that passed. |
| 2026-10-21 | 5 | Core stores the envelope as received, as one JSON literal per snapshot. | The page's "More details" must show the producer's own words, not Core's reading of them. Core previously converted an envelope to triples and discarded the original. Checked: a 1086-byte envelope stores and reads back byte for byte. |
| 2026-10-21 | 5 | `GET /subjects` added. | The page needs a list to open with. Core had no way to enumerate subjects, so a curator would have had to know an IRI by heart. |
| 2026-10-21 | 5 | `GET /snapshot` added, separate from `/history`. | An envelope is about a kilobyte. Returning one per snapshot would make the timeline slow for no benefit, so it is fetched only when a curator asks. |
| 2026-10-21 | 5 | `GET /` redirects to `/ui/`. | A person who opens the host should reach the page, not a 404. |
| 2026-10-26 | 4b | `Activity.id` now prefers the Prefect task run id over the flow run id. | Checked with three real stages: the flow run id is identical for all of them and only the task run id differs. Using the flow id gave three stages one activity id, and `mint_activity` collapsed them into a single activity. A workflow is a batch; each stage is a run. |
| 2026-10-26 | 4b | The three stages were rewritten as real Prefect tasks that pass results along. | The previous code called `reconcile_task(name)`, threw the result away, then called `reconcile(name)` again inside the stage. Prefect orchestrated nothing and the demo did not show what it claimed. |
| 2026-10-26 | 4b | `POST /run` added to the enrich service, not to Core, and `fastapi` added to `enrich-demo`. | A curator starts an Enrich workflow through the Enrich service. NeoWiki never does. Putting the trigger in Core would make the store depend on a producer. |
| 2026-10-26 | 4b | `GET /batch` added to Core, and `batch` added to each `/history` row. | `batch_of` was in the contract and stored, but nothing set it and no endpoint read it, so a workflow could not be traced. |
| 2026-10-26 | 4b | The lookup table gained facts per candidate and a list of required properties. | Stage 2 had nothing to enrich with and stage 3 had no rule to check against. |
| 2026-10-26 | 4b | A correction control added to the page. | `POST /correct` existed on the enrich service but nothing called it, so the demo's before-and-after pair was unreachable without a terminal. Noticed by review: every snapshot showed "Workshop of Rubens" because no correction could be made. |
| 2026-10-26 | 1.1a | Contract bumped to 1.1: `Target.statements` entries become objects naming a property and, optionally, one claim. | A run changing two claims of the same property left a record nobody could read back: two removals, two additions, no way to pair them. NeoWiki claims have their own identifiers, so Stage 2 could not record what it did. |
| 2026-10-26 | 1.1a | Fixtures reorganised into `fixtures/<version>/`. | The models express one version, so a 1.0 fixture can no longer pass through Pydantic. Every fixture is still checked against its own committed schema, which is what proves Core keeps accepting older envelopes. |
| 2026-10-26 | 1.1a | Core reads both statement shapes but refuses an entry carrying a claim identity. | Narrower than planned. A 1.1 envelope whose claims carry no identity says the same thing a 1.0 one did, so Core applies it and the demo keeps working across the version change. Only the genuinely new capability is deferred. |
| 2026-10-26 | 1.1a | Invariant 6 reworded; invariant 11 extended. | Invariant 6 called the per-statement links "lightweight", and 1.1b gives them their own delta. Invariant 11 now says a statement IRI identifies a claim, never the subject. |
| 2026-10-29 | 4c | The name-and-subject form and `POST /run` replaced by `POST /upload` and `POST /upload/sample`; `curate_entity` replaced by `curate_upload`. | WP4 said input arrives as an uploaded CSV or JSON file, one record or a batch of 10 to 40. Keeping both entry paths would leave the page with two ways to start a workflow, one of which no real curator uses. |
| 2026-10-29 | 4c | Sample data is synthetic, in the shape of the ZKM Archive DTOs; `examples/` stays untracked. | Decided by the user. The ZKM records include staff names and are not cleared for publication. Real public figures are named as creators so reconciliation hits real Wikidata entities; their ids were fetched from Wikidata, not copied from ZKM. See `docs/revisionfile.md` entry 23. |
| 2026-10-29 | 4c | DTO fields without a settled mapping are written under a demo namespace `https://demo.echolot.eu/sample/ns#` with the DTO's own field names. | `examples/README.md` names `model/model.csv`, the archive's mapping to CIDOC-CRM and Linked.Art, but the file is not in the folder. See `docs/revisionfile.md` entry 22. |
| 2026-10-29 | 4c | Enrichment emits no envelope for an artifact. | Enrichment here copies authority ids onto a person. For an artifact nothing is done, and an envelope saying an enrichment ran would record work that did not happen. A person with no match still gets an envelope, because the lookup was made. |
| 2026-10-29 | 4c | The page's "person" tag now means a person decided (`validation_state` starts with `human`), not that a person is named. | Every import now names its uploader as a `commissioned` agent. Under the old rule every import would have shown as a person's work. |
| 2026-10-29 | 1.1b | Per-claim deltas are derived from the one stored `oco:hasUpdateQuery`, not stored separately per claim. | Revisionfile 18 planned a per-statement record holding its own delta. Once claim values have the claim as their subject, grouping the single delta by subject node is unambiguous, so a second copy would only be a chance for the two to disagree. Invariant 6 already allows a link to carry a delta; it does not require it. |
| 2026-10-29 | 1.1b | No `sample:ProvenanceOf` property. | The user proposed it to say which data a provenance record belongs to. Every snapshot already has `prov:specializationOf <subject>`, PROV-O's own term for that, and now names its claims through `echolot:affectedStatement`. The page shows both. A second, non-standard term would duplicate it. |
| 2026-10-29 | 1.1b | Only creators are claims; title, year and ids stay plain values. | A claim is needed where a record holds several values of one property that are reconciled or corrected separately. Making every value a claim would reify the whole data graph for no reader's benefit. |
| 2026-10-29 | 1.1b | `/correct` now takes a property, a value, an optional claim and the snapshot it corrects; the flow is renamed `correct_value`. | A correction sits on the value a snapshot wrote, not on a fixed creator field. The old endpoint could only replace `dcterms:creator`. |
| 2026-10-29 | 1.1b | The page escapes every value it displays. | Since 4c the data comes from uploaded files. The page inserted values as raw HTML, so a crafted CSV cell would have run as script in the curator's browser. |
| 2026-11-02 | Stage 2 prep | A local NeoWiki 0.1.0 instance was set up outside the repository, by hand-run Docker commands instead of `make demo`. No Stage 2 code. | Requested to test the integration questions against a real instance. `make` is not installed on Windows, and Ubuntu in WSL had no internet on 2026-11-03 and had it on 2026-11-04 (cause not found). Stage 2 stays blocked. |
| 2026-11-04 | Stage 2 prep | A NeoWiki 0.1.0 dev stack (`make dev`) was set up in WSL, outside the repository, next to the read-only demo instance. No Stage 2 code. | Phase 7 changes NeoWiki, and the demo image cannot host a change: it mounts no NeoWiki source. The dev stack bind-mounts the source and `LocalSettings.local.php`. The demo stack stays stopped while it runs, because both use port 8484. |
| 2026-11-12 | Stage 2 interim, A | Stage 2 work started, although the plan said "do not start". Only the interim adapter: Core appends each committed envelope to a JSON page in NeoWiki. Phase 7 stays on hold. | Requested explicitly by the user: replace Core with NeoWiki in the interim form, keeping the Core UI for import and corrections. The 5 Nov meeting had already scoped the MVP as API-only, with no extension. |
| 2026-11-12 | Stage 2 interim, A | The adapter lives in Core (`services/core/app/neowiki.py`), not in `packages/echolot-prov`, and `CoreClient` is unchanged. | Invariant 8: nothing specific to NeoWiki may enter the emitter. The wire contract of `POST /write` is also unchanged, so the flow, the UI and the tests keep working with the sink off. |
| 2026-11-12 | Stage 2 interim, A | Envelope stored on a JSON page per record (`Provenance/<path>`), as an appended list, not in a revision slot. | The core MediaWiki edit API has no `slot` parameter (`ApiEditPage.php`, MW 1.43), so a slot needs PHP. Appending makes the history diff show only the new envelope. |
| 2026-11-12 | Stage 2 interim, A | Order in `/write`: validate, commit to Core, then NeoWiki. A NeoWiki failure answers `502` and says Core committed. | Core is complete without NeoWiki, so a failure there loses nothing. The failure is still loud, because a quiet gap would be a record with provenance in one store only. Not atomic: revisionfile 26. |
| 2026-11-12 | Stage 2 interim, A | `httpx>=0.28` declared in `services/core`. | Core now imports `httpx` directly. It was already in the workspace through `echolot-prov` (same specifier, copied verbatim), so `uv.lock` gains two lines and no package. Approved with the plan. |
| 2026-11-22 | Stage 2 | Direction changed: NeoWiki replaces Core completely. Phase 7 is deferred, Phase 8 is replaced by a transport outside the emitter package, and Phases B and C of the interim route are replaced by steps 2 to 4 of the decision. | Requested by the user. Atomicity is not mandatory and the envelope is stored as JSON, so the PHP extension is not needed. Not yet checked on the running wiki. |
| 2026-11-22 | Stage 2 | Invariant 4 relaxed (a note in `CLAUDE.md`). Invariants 3 and 10 wait for the spike. Invariants 6 and 7 are on halt (next row). | `CLAUDE.md` says to ask before an invariant changes. The user relaxed only invariant 4 explicitly. |
| 2026-11-22 | Stage 2 | PROV-O storage on halt. Provenance stays JSON, the envelope as it is sent. NeoWiki Schemas are for the record, not for the envelope. | Decided by the user. It keeps the proven Phase A layout. `provenance.py` and `reconstruct.py` stay in the repository until the "Remove Core" issue decides about them. Revision file 27, item 4. |
| 2026-11-23 | Stage 2 interim, A | `NeoWikiSink` extracted from `services/core/app/neowiki.py` into its own workspace package, `packages/echolot-neowiki-client`, and renamed `NeoWikiClient` (`SinkError` to `NeoWikiError`). Core's `/write` and `/subjects` now import it from there; `main.py`'s `neowiki_sink` variable is renamed `neowiki_client`. Behaviour, the env vars and the wire shape are unchanged. | Requested by the user: the direction in the 2026-11-22 decision is that writing to a backend is not the emitter's job, and this generalises it — a backend's writer should also not be nested inside another backend's app (Core), so each backend gets its own standalone client and Core is just one caller among possible future ones. No invariant changes: the client was already outside `packages/echolot-prov` and already a second request after Core's own commit (invariant 8 and the revisionfile 26 limits both held before and hold now). `base_iri` lost its `DEMO_BASE` default so the new package does not import anything from Core; `NeoWikiClient.from_environment` instead takes the caller's own `default_base_iri`, which Core passes. The pure client tests moved with it into the new package's own `tests/`; `services/core/tests/test_neowiki_sink.py` is replaced by `test_neowiki_integration.py`, keeping only the tests of Core's own behaviour around the client (success, a 502 on failure, the sink-off case) plus one real-client-through-Core smoke test. `CoreClient` and `packages/echolot-prov` are unchanged; pulling `CoreClient` out of the emitter library the same way is a later, separate task. |
