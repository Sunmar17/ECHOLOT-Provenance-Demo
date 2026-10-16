"""Ambient capture, and the part of it that must work with nothing installed."""

import builtins

import pytest
from echolot_prov.context import OPERATOR_ENV, Context, config_hash


def test_config_hash_is_stable_regardless_of_key_order():
    """Two identical configurations must not look like a change."""
    a = config_hash({"threshold": 0.85, "table": "wikidata"})
    b = config_hash({"table": "wikidata", "threshold": 0.85})
    assert a == b
    assert a.startswith("sha256:")


def test_config_hash_changes_when_the_configuration_does():
    assert config_hash({"threshold": 0.85}) != config_hash({"threshold": 0.9})


def test_from_dict_rejects_unknown_fields():
    """A typo should fail loudly rather than be silently dropped."""
    with pytest.raises(ValueError, match="unknown Context fields"):
        Context.from_dict(
            {"service": "s", "model": "m", "config_hash": "h", "modle": "typo"}
        )


def test_from_dict_needs_no_environment():
    ctx = Context.from_dict(
        {"service": "echolot-reconciler", "model": "v2", "config_hash": "sha256:abc"}
    )
    assert ctx.run_id is None
    assert ctx.service_version == "unknown"


def test_from_environment_works_without_prefect(monkeypatch):
    """The emitter is orchestrator-agnostic: no Prefect, no problem."""
    real_import = builtins.__import__

    def no_prefect(name, *args, **kwargs):
        if name.startswith("prefect"):
            raise ImportError("prefect is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_prefect)
    monkeypatch.delenv(OPERATOR_ENV, raising=False)

    ctx = Context.from_environment("echolot-prov", "name-match-v2", {"k": 1})

    assert ctx.run_id is None and ctx.task_id is None
    assert ctx.service_version != "unknown", "echolot-prov is installed"
    assert ctx.operator is None


def test_from_environment_reads_the_stubbed_operator(monkeypatch):
    monkeypatch.setenv(OPERATOR_ENV, "https://orcid.org/0000-0002-1825-0097")
    ctx = Context.from_environment("echolot-prov", "v1", {})
    assert ctx.operator == "https://orcid.org/0000-0002-1825-0097"


def test_an_explicit_operator_beats_the_environment(monkeypatch):
    monkeypatch.setenv(OPERATOR_ENV, "https://orcid.org/0000-0000-0000-0000")
    ctx = Context.from_environment(
        "echolot-prov", "v1", {}, operator="https://orcid.org/x"
    )
    assert ctx.operator == "https://orcid.org/x"


def test_unknown_service_version_does_not_raise():
    ctx = Context.from_environment("not-an-installed-package", "v1", {})
    assert ctx.service_version == "unknown"


def test_the_agent_identity_is_separate_from_the_distribution():
    """A service's name in provenance need not be the package it ships in."""
    ctx = Context.from_environment(
        "echolot-reconciler", "name-match-v2", {}, distribution="echolot-prov"
    )
    assert ctx.service == "echolot-reconciler", "this is what provenance records"
    assert ctx.service_version != "unknown", "resolved from the distribution"
