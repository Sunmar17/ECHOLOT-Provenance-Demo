"""Writes the committed JSON Schema from the envelope models.

Runnable as ``python -m echolot_prov.schema``.

One file per version, frozen once released (invariant 1). The filename, the
``$id`` and the model's ``schema_version`` all derive from a single constant, so
the three cannot drift apart. Core must be able to validate every version it has
ever accepted, because a migration serves two at once and a projection rebuild
replays every envelope ever stored.
"""

import argparse
import json
from importlib import resources
from pathlib import Path

from echolot_prov.models import SCHEMA_VERSION, Envelope

ID_BASE = "https://w3id.org/echolot/schema/envelope"
"""Base for the versioned ``$id``.

An identifier, never dereferenced: nothing fetches a schema at runtime
(invariant 9). See ``docs/revisionfile.md`` entry 6.
"""

DIALECT = "https://json-schema.org/draft/2020-12/schema"

SCHEMAS_PACKAGE = "echolot_prov.schemas"


def schema_filename(version: str = SCHEMA_VERSION) -> str:
    """The committed filename for a contract version."""
    return f"envelope-{version}.schema.json"


def schema_id(version: str = SCHEMA_VERSION) -> str:
    """The versioned ``$id`` for a contract version."""
    return f"{ID_BASE}/{version}"


def generate(version: str = SCHEMA_VERSION) -> dict:
    """Build the JSON Schema for the current models. Pure: no I/O.

    ``$schema`` and ``$id`` are placed first so the committed file opens with
    its dialect and identity.
    """
    body = Envelope.model_json_schema()
    return {"$schema": DIALECT, "$id": schema_id(version), **body}


def serialise(schema: dict) -> str:
    """Render a schema as the committed file's exact bytes.

    Key order follows Pydantic's deterministic output rather than being sorted,
    so the drift test compares like with like.
    """
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def schemas_dir() -> Path:
    """The writable source directory holding the committed schema files."""
    return Path(__file__).parent / "schemas"


def available_versions() -> list[str]:
    """Every contract version committed in the installed package, ascending.

    Reads through ``importlib.resources`` so this works from an installed wheel
    as well as from a source checkout. Core uses this to build its version to
    schema registry.
    """
    versions: list[str] = []
    for entry in resources.files(SCHEMAS_PACKAGE).iterdir():
        name = entry.name
        if name.startswith("envelope-") and name.endswith(".schema.json"):
            versions.append(name[len("envelope-") : -len(".schema.json")])
    return sorted(versions, key=lambda v: [int(part) for part in v.split(".")])


def load_schema(version: str = SCHEMA_VERSION) -> dict:
    """Load a committed schema by version from the installed package."""
    text = (
        resources.files(SCHEMAS_PACKAGE)
        .joinpath(schema_filename(version))
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


class FrozenSchemaError(RuntimeError):
    """Raised when writing would change a schema file that is already committed."""


def write(*, force: bool = False) -> tuple[Path, bool]:
    """Write the current version's schema file.

    Returns the path and whether anything changed on disk.

    A released version file is frozen: if the target exists and its content
    differs, this refuses rather than overwriting. Bumping ``SCHEMA_VERSION``
    changes the filename, so a legitimate contract change writes a new file and
    never trips the guard.

    ``force`` exists for pre-release iteration, while a version is still being
    designed and has not been tagged. It must not be used on a released version.
    See ``docs/revisionfile.md`` entry 3.
    """
    target = schemas_dir() / schema_filename()
    rendered = serialise(generate())

    if target.exists():
        current = target.read_text(encoding="utf-8")
        if current == rendered:
            return target, False
        if not force:
            raise FrozenSchemaError(
                f"{target.name} is already committed and would change.\n"
                f"If the contract changed, bump SCHEMA_VERSION in models.py so a "
                f"new file is written. Use --force only for pre-release iteration "
                f"on a version that has not been tagged."
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    return target, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a differing, untagged version file (pre-release only)",
    )
    args = parser.parse_args()

    try:
        path, changed = write(force=args.force)
    except FrozenSchemaError as exc:
        print(f"refused: {exc}")
        return 1

    print(f"{'wrote' if changed else 'unchanged'} {path.name} ({schema_id()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
