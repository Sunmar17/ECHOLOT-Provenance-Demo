"""Ambient capture: an injectable ``Context``, from environment or dict (Phase 3).

A ``Context`` answers "who is running, as what, configured how". It is the half
of an envelope a producer should never have to restate on every run.

Two constructors, per invariant 5: one reads the live environment, the other
takes a plain dict. Everything downstream depends on the dict form, which is why
the emitter is testable with no Prefect and no network.

``Context`` is a frozen dataclass rather than a Pydantic model on purpose. It is
ambient capture, not wire format, and must stay out of the generated schema.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

OPERATOR_ENV = "ECHOLOT_OPERATOR"
"""Where the identity stub looks for the current human.

Stands in for ECCCH AAI/SSO, which will supply an ORCID. Stubbed here and
declared as such in the README's assumptions block.
"""


def config_hash(config: Any) -> str:
    """A stable SHA-256 over a configuration, as ``sha256:<hex>``.

    Canonical JSON (sorted keys, no incidental whitespace) so that the same
    configuration always produces the same hash, whatever order it was built in.
    Without that, two identical runs would look like different configurations
    and the provenance would imply a change that never happened.
    """
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _service_version(service: str) -> str:
    """The installed version of a service, or ``unknown`` if it is not a package."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(service)
    except PackageNotFoundError:
        return "unknown"


def _prefect_ids() -> tuple[str | None, str | None]:
    """Prefect's run and task ids, or ``(None, None)``.

    The import is guarded because a consumer need not have Prefect at all: the
    emitter is orchestrator-agnostic, and a future sidecar for non-Python
    producers would have neither. Outside a flow run Prefect returns ``None``
    rather than raising, so an ordinary script needs no special handling.
    """
    try:
        from prefect.runtime import flow_run, task_run
    except ImportError:
        return None, None
    return flow_run.id, task_run.id


def _stub_operator() -> str | None:
    """The human on whose behalf this runs. Stubbed until ECCCH AAI/SSO exists."""
    return os.environ.get(OPERATOR_ENV) or None


@dataclass(frozen=True)
class Context:
    """Who performed an activity, with what, and under which configuration."""

    service: str
    """The software agent's identifier, such as ``echolot-reconciler``."""

    model: str
    """What actually ran, such as ``name-match-v2``."""

    config_hash: str
    """``sha256:...`` over the configuration that produced the result."""

    service_version: str = "unknown"
    run_id: str | None = None
    task_id: str | None = None
    operator: str | None = None
    """An ORCID, once identity is real. None when nobody human is involved."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Context":
        """Build from a plain dict. The form every unit test uses."""
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown Context fields: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def from_environment(
        cls,
        service: str,
        model: str,
        config: Any,
        *,
        distribution: str | None = None,
        operator: str | None = None,
    ) -> "Context":
        """Build by reading the live environment.

        The only function in this module that touches the outside world, which
        is what keeps everything downstream pure.

        ``service`` is the agent's identity, the name that appears in the
        provenance. ``distribution`` is the installed package to read a version
        from, which is often a different name: a service called
        ``echolot-reconciler`` may ship inside ``echolot-enrich-services``.
        Conflating the two either mislabels the agent or loses the version.
        """
        run_id, task_id = _prefect_ids()
        return cls(
            service=service,
            model=model,
            config_hash=config_hash(config),
            service_version=_service_version(distribution or service),
            run_id=run_id,
            task_id=task_id,
            operator=operator if operator is not None else _stub_operator(),
        )
