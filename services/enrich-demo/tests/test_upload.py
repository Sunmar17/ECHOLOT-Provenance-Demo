"""Reading an uploaded file into records. Pure: no Prefect, no Core."""

import json
from importlib import resources

import pytest
from enrich_demo.upload import (
    MAX_RECORDS,
    UploadError,
    label_of,
    parse_upload,
    split_person_list,
    to_claims,
    to_statements,
    upload_source,
)
from enrich_demo.vocab import CREATOR, DATE, LABEL, TITLE, WIKIDATA_ID, field

CSV_HEADER = "kind,id,title,year,personList\n"


def sample(name: str) -> bytes:
    return resources.files("enrich_demo").joinpath("samples", name).read_bytes()


# ------------------------------------------------------------------ formats


def test_a_json_object_is_one_record():
    records = parse_upload("p.json", b'{"id": 7, "type": "person", "familyName": "X"}')
    assert len(records) == 1
    assert records[0].kind == "agent", "an agent DTO has no kind; its type says"
    assert records[0].subject == "https://demo.echolot.eu/sample/agent/7"


def test_a_json_list_is_several_records():
    data = json.dumps(
        [{"id": 1, "kind": "work", "title": "A"}, {"id": 2, "kind": "item"}]
    ).encode()
    assert [r.id for r in parse_upload("x.json", data)] == ["1", "2"]


def test_csv_rows_are_records():
    data = (CSV_HEADER + 'work,1,A,1990,"Berio, Luciano"\nitem,2,B,,\n').encode()
    records = parse_upload("x.csv", data)
    assert [(r.kind, r.id) for r in records] == [("work", "1"), ("item", "2")]
    assert "year" not in records[1].fields, "an empty cell is no value"


def test_format_is_sniffed_when_the_name_says_nothing():
    assert parse_upload("upload", b'{"id": 1, "kind": "work"}')[0].kind == "work"
    assert parse_upload("upload", (CSV_HEADER + "item,1,A,,\n").encode())[0].id == "1"


def test_a_byte_order_mark_is_tolerated():
    data = b"\xef\xbb\xbf" + (CSV_HEADER + "work,1,A,,\n").encode()
    assert parse_upload("x.csv", data)[0].fields["kind"] == "work"


def test_both_samples_parse():
    assert len(parse_upload("person.json", sample("person.json"))) == 1
    records = parse_upload("artifacts.csv", sample("artifacts.csv"))
    assert len(records) == 20
    assert {r.kind for r in records} == {"item", "work"}


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("data", "record", "words"),
    [
        (b"", None, "empty"),
        (b"{not json", None, "not valid JSON"),
        (b"[1, 2]", 1, "JSON object"),
        (b'{"kind": "work"}', 1, "no id"),
        (b'{"id": 1, "kind": "painting"}', 1, "what kind"),
        (b'{"id": 1}', 1, "what kind"),
        (b'[{"id": 1, "kind": "work"}, {"id": 1, "kind": "work"}]', 2, "twice"),
        (b"\xff\xfe", None, "UTF-8"),
    ],
)
def test_a_bad_file_is_refused_with_where(data, record, words):
    with pytest.raises(UploadError) as caught:
        parse_upload("x.json" if data[:1] in (b"{", b"[") else "x", data)
    assert words in caught.value.message
    assert caught.value.record == record


def test_more_than_forty_records_is_refused():
    rows = "".join(f"item,{i},T,,\n" for i in range(MAX_RECORDS + 1))
    with pytest.raises(UploadError, match="at most 40"):
        parse_upload("x.csv", (CSV_HEADER + rows).encode())


def test_forty_records_is_accepted():
    rows = "".join(f"item,{i},T,,\n" for i in range(MAX_RECORDS))
    assert len(parse_upload("x.csv", (CSV_HEADER + rows).encode())) == MAX_RECORDS


def test_an_error_names_the_record_as_a_path():
    error = UploadError("bad", 3)
    assert error.as_dict() == {"path": "/records/3", "message": "bad"}
    assert UploadError("bad").as_dict()["path"] == "/file"


# -------------------------------------------------------------- statements


def test_person_list_splits_names_and_drops_pseudonyms():
    assert split_person_list("Franke, Herbert W. [Both, S.] / Ives, Charles") == [
        "Franke, Herbert W.",
        "Ives, Charles",
    ]
    assert split_person_list("") == []


def test_each_creator_is_its_own_claim():
    """Two creators are two claims, so a match can sit on one of them."""
    record = parse_upload(
        "x.csv", (CSV_HEADER + 'item,1,A,1987,"Berio, L. / Stockhausen, K."\n').encode()
    )[0]
    assert to_claims(record) == [
        {"values": {CREATOR: ["Berio, L."]}},
        {"values": {CREATOR: ["Stockhausen, K."]}},
    ]
    statements = to_statements(record)
    assert CREATOR not in statements, "creators are claims, not plain values"
    assert statements[TITLE] == ["A"]
    assert statements[DATE] == ["1987"]


def test_every_record_gets_a_label_a_person_can_read():
    work = parse_upload("x.csv", (CSV_HEADER + "work,1,Sequenza III,,\n").encode())[0]
    person = parse_upload(
        "p.json", b'{"id": 7, "type": "person", "givenName": "L", "familyName": "B"}'
    )[0]
    bare = parse_upload("x.csv", (CSV_HEADER + "item,9,,,\n").encode())[0]

    assert to_statements(work)[LABEL] == ["Sequenza III"]
    assert label_of(person) == "L B"
    assert label_of(bare) == "item 9"


def test_unmapped_fields_keep_their_own_names_and_identity_is_not_a_property():
    record = parse_upload(
        "p.json", b'{"id": 7, "type": "person", "gndId": "1", "_note": "x"}'
    )[0]
    statements = to_statements(record)
    assert statements[field("gndId")] == ["1"]
    assert field("id") not in statements, "the id is in the subject IRI"
    assert field("_note") not in statements, "underscore keys are notes"


def test_a_wikidata_id_is_written_as_an_entity_iri():
    record = parse_upload(
        "p.json", b'{"id": 7, "type": "person", "wikidataId": "Q221450"}'
    )[0]
    assert to_statements(record)[WIKIDATA_ID] == [
        "http://www.wikidata.org/entity/Q221450"
    ]


# ------------------------------------------------------------------ source


def test_the_same_bytes_name_the_same_upload():
    a = upload_source("a.csv", b"kind,id\n")
    b = upload_source("renamed.csv", b"kind,id\n")
    assert a.iri == b.iri and a.digest == b.digest
    assert a.digest.startswith("sha256:")
    assert upload_source("a.csv", b"kind,id\nitem,1\n").iri != a.iri
