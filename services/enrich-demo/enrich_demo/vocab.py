"""Every IRI the enrich side writes, in one place.

Two kinds of thing live here. Vocabulary terms, which a stage writes as
properties. And the subject rule, which turns a record's native id into an IRI.

The subject rule is deployment configuration under a declared base (invariant
11). It is not envelope data and not emitter code: the library never learns how
an archive numbers its records.
"""

from urllib.parse import quote

DCTERMS = "http://purl.org/dc/terms/"
CREATOR = DCTERMS + "creator"
MATCHED = DCTERMS + "references"
QUALITY = DCTERMS + "conformsTo"
TITLE = DCTERMS + "title"
DATE = DCTERMS + "date"

LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
"""The name a person reads instead of an IRI. Written by the import stage."""

WIKIDATA_ENTITY = "http://www.wikidata.org/entity/"
"""Wikidata's concept IRI prefix. An entity IRI, not the page about it."""

SAMPLE_BASE = "https://demo.echolot.eu/sample"
"""The declared base for the sample archive's records and uploads."""

SAMPLE = SAMPLE_BASE + "/ns#"
"""A demo vocabulary that mirrors the archive's own DTO field names.

It stands in until the archive's model mapping to CIDOC-CRM and Linked.Art is
available. Mirroring the field names keeps the swap mechanical: one table here
changes, and no stage does. See docs/revisionfile.md.
"""


def field(name: str) -> str:
    """The demo property for one DTO field, such as ``gndId``."""
    return SAMPLE + quote(name, safe="")


GIVEN_NAME = field("givenName")
FAMILY_NAME = field("familyName")
GND_ID = field("gndId")
VIAF_ID = field("viafId")
WIKIDATA_ID = field("wikidataId")

# Fields that have a settled meaning in dcterms. Every other scalar field is
# written under SAMPLE with its own name.
MAPPED = {"title": TITLE, "year": DATE, "personList": CREATOR}

# Identity of the record, carried by its subject IRI rather than as a property.
NOT_WRITTEN = {"id", "kind"}


def subject_iri(kind: str, native_id: str) -> str:
    """The subject IRI for one record: ``<base>/<kind>/<id>``."""
    return f"{SAMPLE_BASE}/{quote(kind, safe='')}/{quote(native_id, safe='')}"


def upload_iri(digest: str) -> str:
    """The IRI an uploaded file is named by, derived from its content."""
    return f"{SAMPLE_BASE}/upload/{digest[:16]}"


def entity_iri(qid: str) -> str:
    """A Wikidata entity IRI from a bare id such as ``Q221450``."""
    return qid if qid.startswith(("http://", "https://")) else WIKIDATA_ENTITY + qid
