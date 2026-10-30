# Sample uploads

Both files are **synthetic**. They follow the field names of an archive's REST
records (agent, item, work), but no record, value or staff name is taken from
any archive.

| File | What it is |
|---|---|
| `person.json` | One agent record: a person, with a GND id and no Wikidata id. |
| `artifacts.csv` | 20 artifact records: 10 works and 10 items. |

The people named as creators are real public figures. Their Wikidata, GND and
VIAF ids were fetched from Wikidata and are listed in `../wikidata_standin.json`.
The six real works (Sequenza III, Gesang der Jünglinge, The Unanswered
Question, Central Park in the Dark, TV Buddha, A Mathematical Theory of
Communication) were checked there for their creator and, where Wikidata gives
one, their year. Every item record, and every record about Lena Hartmann,
Studio Kreisel and D. Okafor, is invented.

The CSV is untidy on purpose, the way incoming records are:

- creators written several ways (`L. Berio`, `Shannon, Claude E.`, `Stockhausen, K.`)
- one ambiguous name (`Molnar, Vera`) that ranks the wrong person first
- names with no match (`Hartmann, Lena`, `Studio Kreisel`, `Okafor, D.`)
- a pseudonym in square brackets
- missing years and missing creators, which the quality check reports
