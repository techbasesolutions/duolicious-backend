"""Regression coverage for the 2026-09-15 incident: a blank value in
.env.production reached `int('')` at cron import and crashed the whole
cron container for about a day, taking down pending deletion,
auto-deactivate, notifications and every cleaner with it. The deploy
workflow only probed the api's /health endpoint, so it reported green
the entire time.

`cronutil.env_int` is the fix for one call site. This file pins its
contract, then guards every module under `service/cron/` (present and
future) against reintroducing the unsafe `int(os.environ.get(NAME,
default))` pattern, which only survives a MISSING variable, not a
present-but-blank one.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
import sys
from pathlib import Path

import pytest

import service.cron as cron_pkg
from service.cron.cronutil import env_int

# ---------------------------------------------------------------------------
# Step 1: env_int itself
# ---------------------------------------------------------------------------

_NAME = 'DUO_CRON_TEST_ENV_INT_VALUE'


def test_env_int_returns_the_default_when_the_variable_is_missing(monkeypatch):
    monkeypatch.delenv(_NAME, raising=False)
    assert env_int(_NAME, 42) == 42


def test_env_int_returns_the_default_when_the_variable_is_blank(monkeypatch):
    monkeypatch.setenv(_NAME, '')
    assert env_int(_NAME, 42) == 42


def test_env_int_returns_the_default_when_the_variable_is_whitespace(monkeypatch):
    monkeypatch.setenv(_NAME, '   ')
    assert env_int(_NAME, 42) == 42


def test_env_int_parses_a_real_number(monkeypatch):
    monkeypatch.setenv(_NAME, '17')
    assert env_int(_NAME, 42) == 17


def test_env_int_raises_for_a_non_numeric_non_blank_value(monkeypatch):
    monkeypatch.setenv(_NAME, 'not-a-number')
    with pytest.raises(ValueError):
        env_int(_NAME, 42)


# ---------------------------------------------------------------------------
# Step 2: every cron module, present and future, must survive a blank value
# for any DUO_ setting it reads at import time.
#
# Modules are discovered with pkgutil.iter_modules rather than listed by
# name, so a cron added later is covered without anyone remembering to add
# it here. The set of environment variable names to blank is discovered the
# same way: each module's own __init__.py source is scanned for names that
# get int()-parsed, either the safe `env_int(NAME, ...)` call or the unsafe
# `int(os.environ.get(NAME, ...))` / `int(os.getenv(NAME, ...))` pattern this
# task removes, rather than hardcoding the fifteen call sites it fixes, so a
# future int-parsing regression under a new name is caught too. This is
# deliberately narrower than "every DUO_ var": a setting like the R2 boto
# endpoint is allowed to be blank-and-broken in its own way, and blanking it
# here would fail the test for a reason this task was never meant to cover.
# ---------------------------------------------------------------------------

_ENV_READ_RE = re.compile(
    r"""(?:env_int\s*\(\s*"""
    r"""|int\s*\(\s*os\.environ\.get\s*\(\s*"""
    r"""|int\s*\(\s*os\.getenv\s*\(\s*)"""
    r"""['"](DUO_[A-Z0-9_]+)['"]"""
)


def _discover_cron_module_names() -> list[str]:
    return sorted(
        info.name for info in pkgutil.iter_modules(cron_pkg.__path__)
    )


def _duo_env_names_read_by(module_name: str) -> set[str]:
    init_path = Path(cron_pkg.__path__[0]) / module_name / '__init__.py'
    source = init_path.read_text()
    return set(_ENV_READ_RE.findall(source))


def _reload_or_import(full_name: str) -> None:
    if full_name in sys.modules:
        importlib.reload(sys.modules[full_name])
    else:
        importlib.import_module(full_name)


def test_every_cron_module_survives_import_with_blank_duo_env_vars(monkeypatch):
    module_names = _discover_cron_module_names()
    assert len(module_names) >= 15, (
        f'expected to discover the cron submodules, got {module_names!r}'
    )

    env_names: set[str] = set()
    for name in module_names:
        env_names |= _duo_env_names_read_by(name)
    assert env_names, 'expected to find at least one DUO_ env var read by a cron module'

    full_names = [f'service.cron.{name}' for name in module_names]

    try:
        with monkeypatch.context() as m:
            for env_name in env_names:
                m.setenv(env_name, '')
            for full_name in full_names:
                try:
                    _reload_or_import(full_name)
                except Exception as exc:
                    pytest.fail(
                        f'{full_name} raised on import with every DUO_ env var '
                        f'blank: {exc!r}'
                    )
    finally:
        # The env vars are back to their real values now that the monkeypatch
        # context has exited; reload every module we touched so later tests
        # see the real, non-blank-driven constants again.
        for full_name in full_names:
            if full_name in sys.modules:
                importlib.reload(sys.modules[full_name])
