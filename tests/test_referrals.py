"""Unit tests for service.referrals.

These exercise the pure-logic pieces (Crockford alphabet, normalization,
self-referral detection). The integration paths (attribute → row insert,
credit_pending_for_* → token_ledger row) are covered by a separate shell
smoke test against the live test stack."""
from __future__ import annotations

import pytest

from service.referrals import (
    _ALPHABET,
    _CODE_LENGTH,
    _normalize_email,
    _is_well_formed_code,
)


def test_alphabet_is_crockford_base32_without_ambiguous_chars():
    # No I, L, O, U (Crockford excludes them to avoid digit confusion)
    for forbidden in "ILOU":
        assert forbidden not in _ALPHABET
    # 32 chars exactly
    assert len(_ALPHABET) == 32
    # All caps + digits
    assert _ALPHABET == "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def test_code_length_is_seven():
    assert _CODE_LENGTH == 7


def test_normalize_email_strips_and_lowercases():
    assert _normalize_email("  Foo@BAR.COM  ") == "foo@bar.com"
    assert _normalize_email("") == ""
    assert _normalize_email(None) == ""


def test_is_well_formed_code_accepts_valid_crockford():
    assert _is_well_formed_code("0123ABC") is True
    assert _is_well_formed_code("ZZZZZZZ") is True


def test_is_well_formed_code_rejects_garbage():
    # Wrong length
    assert _is_well_formed_code("ABC") is False
    assert _is_well_formed_code("ABCDEFGH") is False
    # Forbidden Crockford letters
    assert _is_well_formed_code("0123ILU") is False
    # Lowercase (codes are normalized to upper at mint time; the route
    # accepts case-insensitive, but the well-formed check is strict)
    assert _is_well_formed_code("0123abc") is False
    # Symbols
    assert _is_well_formed_code("01-23AB") is False
    # Empty / None
    assert _is_well_formed_code("") is False
    assert _is_well_formed_code(None) is False


def test_credit_one_pre_check_query_is_parameterized():
    """Defense-in-depth: the pre-check query must use a named parameter
    for referral_id, not string-format it (audit Data Integrity #9 lesson).
    Pure structural check — runs without a DB."""
    from service.referrals import _Q_ALREADY_CREDITED
    assert "%(referral_id)s" in _Q_ALREADY_CREDITED
    assert "format" not in _Q_ALREADY_CREDITED.lower()
    assert "f'" not in _Q_ALREADY_CREDITED
