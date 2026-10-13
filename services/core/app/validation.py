"""Validates envelopes against the committed JSON Schema, returning 422 errors.

Core carries *every* committed contract version, not just the current one: a
migration serves two at once, and a projection rebuild replays every envelope
ever stored (invariant 1). Validation is therefore two steps, read the version
then select its schema, and an unknown version is an ordinary rejection rather
than a crash.
"""

from dataclasses import dataclass
from functools import cache
from typing import Any

from echolot_prov.schema import available_versions, load_schema
from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class Error:
    """One validation failure, shaped for an HTTP 422 body."""

    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


@cache
def registry() -> dict[str, Draft202012Validator]:
    """Every committed version mapped to its validator.

    The format checker is not optional. Without it ``format: date-time`` is an
    annotation rather than an assertion, so a malformed timestamp passes here
    while failing the producer's Pydantic models, and the two enforcement paths
    silently disagree. See ``docs/revisionfile.md`` entry 9.
    """
    return {
        version: Draft202012Validator(
            load_schema(version), format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        for version in available_versions()
    }


def _path_of(error: Any) -> str:
    """A JSON-pointer-ish path for an error, or "/" for a whole-document one."""
    return "/" + "/".join(str(part) for part in error.absolute_path)


def validate(envelope: Any) -> list[Error]:
    """Validate an envelope. An empty list means it is acceptable.

    Returns every failure rather than the first, so a producer fixing an
    envelope sees the whole picture in one response.
    """
    if not isinstance(envelope, dict):
        return [Error("/", "envelope must be a JSON object")]

    version = envelope.get("schema_version")
    if version is None:
        return [Error("/schema_version", "schema_version is required")]
    if not isinstance(version, str):
        return [Error("/schema_version", "schema_version must be a string")]

    validators = registry()
    if version not in validators:
        known = ", ".join(sorted(validators)) or "none"
        return [
            Error(
                "/schema_version",
                f"unknown schema_version {version!r}; this Core carries: {known}",
            )
        ]

    return [
        Error(_path_of(error), error.message)
        for error in sorted(validators[version].iter_errors(envelope), key=str)
    ]
