"""An uploaded file, read into records. Pure: bytes in, records out, no I/O.

WP4 expects a curator to upload a CSV or JSON file holding one record or a
batch of 10 to 40. Both formats become the same ``Record``, so no stage needs to
know which one arrived.

A record is either an ``agent`` (a person, group or organisation) or an
artifact (``item`` or ``work``). The field names follow the archive's own DTOs.
"""

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

from echolot_prov.client import iri

from enrich_demo.vocab import (
    CREATOR,
    LABEL,
    MAPPED,
    NOT_WRITTEN,
    WIKIDATA_ID,
    entity_iri,
    subject_iri,
    upload_iri,
)
from enrich_demo.vocab import field as sample_field

KINDS = ("agent", "item", "work")
AGENT_TYPES = ("person", "organization", "group")
MAX_RECORDS = 40
"""The top of the batch size WP4 named."""


class UploadError(ValueError):
    """The file cannot be read as records. Says which record, where it can."""

    def __init__(self, message: str, record: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.record = record

    def as_dict(self) -> dict[str, Any]:
        path = f"/records/{self.record}" if self.record is not None else "/file"
        return {"path": path, "message": self.message}


@dataclass(frozen=True)
class Record:
    """One record from an upload, reduced to its non-empty scalar fields."""

    kind: str
    id: str
    position: int
    """1-based position in the upload, for messages a person can act on."""
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def subject(self) -> str:
        return subject_iri(self.kind, self.id)


@dataclass(frozen=True)
class Upload:
    """The file itself, named by its content so a batch can be traced to it."""

    filename: str
    format: str
    iri: str
    digest: str


def upload_source(filename: str, data: bytes) -> Upload:
    """Identify an uploaded file. The same bytes always give the same IRI."""
    digest = hashlib.sha256(data).hexdigest()
    return Upload(
        filename=filename,
        format=_format(filename, data),
        iri=upload_iri(digest),
        digest="sha256:" + digest,
    )


def parse_upload(filename: str, data: bytes) -> list[Record]:
    """Read every record in a file, or refuse the whole file.

    The whole file is refused on the first bad record, for the same reason Core
    refuses a whole write: a batch that half-ran is harder to explain than one
    that did not run.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadError("the file is not UTF-8 text") from exc
    if not text.strip():
        raise UploadError("the file is empty")

    rows = _json_rows(text) if _format(filename, data) == "json" else _csv_rows(text)
    if not rows:
        raise UploadError("the file holds no records")
    if len(rows) > MAX_RECORDS:
        raise UploadError(
            f"the file holds {len(rows)} records; one upload takes at most "
            f"{MAX_RECORDS}"
        )

    records: list[Record] = []
    seen: set[tuple[str, str]] = set()
    for position, row in enumerate(rows, start=1):
        record = _record(row, position)
        key = (record.kind, record.id)
        if key in seen:
            raise UploadError(f"{record.kind} {record.id} appears twice", position)
        seen.add(key)
        records.append(record)
    return records


def split_person_list(value: str) -> list[str]:
    """The names in a ``personList`` string, in order.

    The archive writes several people as ``"Family, Given / Family, Given"`` and
    puts pseudonyms in square brackets after a name. Pseudonyms are dropped:
    they name the same person, and reconciling them would match them twice.
    """
    without_pseudonyms = re.sub(r"\[[^\]]*\]", "", value)
    names = re.split(r"\s*/\s*|\s*;\s*", without_pseudonyms)
    return [n.strip() for n in names if n.strip()]


def label_of(record: Record) -> str:
    """The name a curator reads: the title, else the person's name, else the id."""
    fields = record.fields
    if fields.get("title"):
        return fields["title"]
    name = " ".join(
        part for part in (fields.get("givenName"), fields.get("familyName")) if part
    )
    return name or f"{record.kind} {record.id}"


def to_statements(record: Record) -> dict[str, list[Any]]:
    """The record's plain values, property IRI to values, for ``client.write``.

    The creators are not here: each is a claim of its own (``to_claims``). The
    label is added, so a curator sees a name rather than an IRI.
    """
    out: dict[str, list[Any]] = {LABEL: [label_of(record)]}
    for name, value in record.fields.items():
        if name in NOT_WRITTEN or name == "personList":
            continue
        if name == "wikidataId":
            out[WIKIDATA_ID] = [iri(entity_iri(value))]
        else:
            out[MAPPED.get(name) or sample_field(name)] = [value]
    return out


def to_claims(record: Record) -> list[dict[str, Any]]:
    """One new claim per person in ``personList``, in the order written.

    A claim has no IRI yet: Core mints it on write. Each person is a claim of
    its own so that a later match, or a correction, can attach to that one
    person and not to the record's creators as a whole.
    """
    return [
        {"values": {CREATOR: [name]}}
        for name in split_person_list(record.fields.get("personList", ""))
    ]


# ---------------------------------------------------------------- internals


def _format(filename: str, data: bytes) -> str:
    """JSON or CSV: by extension, else by the first character of the content."""
    lower = filename.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".csv"):
        return "csv"
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    return "json" if head in (b"{", b"[") else "csv"


def _json_rows(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UploadError(
            f"the file is not valid JSON: {exc.msg}, line {exc.lineno}"
        ) from exc
    rows = parsed if isinstance(parsed, list) else [parsed]
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise UploadError("each record must be a JSON object", position)
    return rows


def _csv_rows(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UploadError("the CSV file has no header row")
    return [row for row in reader if any((v or "").strip() for v in row.values())]


def _record(row: dict[str, Any], position: int) -> Record:
    kind = _kind(row, position)
    native_id = _scalar(row.get("id"))
    if not native_id:
        raise UploadError("the record has no id", position)

    fields: dict[str, str] = {}
    for name, raw in row.items():
        # Keys starting with an underscore are notes about the file, such as a
        # sample's "this is synthetic" marker. Lists and objects are not mapped
        # in this demo, and the import envelope names only what it wrote.
        if not name or name.startswith("_"):
            continue
        value = _scalar(raw)
        if value:
            fields[name] = value
    return Record(kind=kind, id=native_id, position=position, fields=fields)


def _kind(row: dict[str, Any], position: int) -> str:
    """An explicit ``kind`` wins. A JSON agent DTO has none, so its type says."""
    kind = _scalar(row.get("kind"))
    if not kind and _scalar(row.get("type")) in AGENT_TYPES:
        kind = "agent"
    if kind not in KINDS:
        raise UploadError(
            f"cannot tell what kind of record this is (got {kind or 'nothing'!r}); "
            f"expected one of {', '.join(KINDS)}",
            position,
        )
    return kind


def _scalar(value: Any) -> str:
    """A value as trimmed text, or empty if it is missing or not a scalar."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()
