"""
Phase 4 Task 4.1 — romance-scam scoring tests.

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest antiabuse/romancescam/ -v'

These tests live next to the module (matching the existing
`antiabuse/antirude/chat/test_init.py` convention) rather than under
`tests/` because they test a single self-contained module with no fixtures.
"""

from __future__ import annotations

import pytest

from antiabuse.romancescam import (
    NEW_ACCOUNT_BOOST,
    ScamScore,
    analyze_message,
    score_with_trust,
)


# ---------------------------------------------------------------------------
# Empty / benign inputs
# ---------------------------------------------------------------------------

class TestBenign:
    def test_empty_string(self):
        s = analyze_message('')
        assert s.total_score == 0.0
        assert s.flags == set()

    def test_whitespace_only(self):
        s = analyze_message('   \n\t')
        assert s.total_score == 0.0

    def test_friendly_greeting_does_not_flag(self):
        s = analyze_message("Hi! Nice to meet you, how was your day?")
        assert s.total_score == 0.0
        assert s.flags == set()

    def test_legitimate_money_word_in_context_minimal(self):
        # "money" alone with no rail/amount/request shouldn't trip
        s = analyze_message("I'd love to talk about something other than money for once")
        # This is allowed to fire LOW but should never reach the high-confidence band.
        assert s.total_score < 0.5


# ---------------------------------------------------------------------------
# Financial solicitation
# ---------------------------------------------------------------------------

class TestFinancialSolicitation:
    def test_western_union_request(self):
        s = analyze_message(
            "Hi darling, my mom is in the hospital, "
            "can you send me $200 via Western Union?"
        )
        assert s.money_request > 0.8
        assert 'financial_solicitation' in s.flags

    def test_gift_card_request_is_high_confidence(self):
        s = analyze_message("could you buy me an iTunes gift card for $100?")
        assert 'financial_solicitation' in s.flags
        assert s.category_scores['financial_solicitation'] > 0.7

    def test_crypto_send_pattern_flags(self):
        s = analyze_message("can you send some bitcoin to my wallet?")
        assert 'financial_solicitation' in s.flags

    def test_zelle_with_amount(self):
        s = analyze_message("send 250 USD via Zelle to help my surgery")
        assert s.money_request > 0.6

    def test_pure_zelle_mention_low(self):
        # Mentioning Zelle alone (no amount/request verb) should be modest
        s = analyze_message("I started using Zelle last year")
        assert s.money_request < 0.5


# ---------------------------------------------------------------------------
# Emergency pretexts
# ---------------------------------------------------------------------------

class TestEmergencyPretext:
    def test_medical_emergency_with_money(self):
        s = analyze_message("my mom is in the hospital and needs surgery, please help")
        assert 'emergency_pretext' in s.flags

    def test_customs_pretext(self):
        s = analyze_message("my package is held at customs, I need a release fee")
        assert 'emergency_pretext' in s.flags
        assert s.category_scores['emergency_pretext'] > 0.4

    def test_stranded_traveler(self):
        s = analyze_message("I'm stranded at the airport in Istanbul")
        assert 'emergency_pretext' in s.flags

    def test_legal_pretext(self):
        s = analyze_message("I need help with my lawyer fees urgently")
        assert 'emergency_pretext' in s.flags


# ---------------------------------------------------------------------------
# Investment / pig-butchering
# ---------------------------------------------------------------------------

class TestInvestmentScams:
    def test_pig_butchering_uncle_opener(self):
        s = analyze_message(
            "my uncle taught me trading and I make 30% returns every month, "
            "want me to show you?"
        )
        assert 'investment_scam' in s.flags
        assert s.category_scores['investment_scam'] > 0.5

    def test_forex_pitch(self):
        s = analyze_message("I trade forex and have guaranteed returns daily")
        assert 'investment_scam' in s.flags

    def test_innocent_investing_question_low(self):
        # Real users do talk about investing
        s = analyze_message("do you invest in stocks?")
        assert s.total_score < 0.5


# ---------------------------------------------------------------------------
# Off-platform migration push
# ---------------------------------------------------------------------------

class TestOffPlatform:
    def test_telegram_handle_push(self):
        s = analyze_message("let's chat on telegram, my handle is @scamguy123")
        assert 'off_platform' in s.flags

    def test_whatsapp_with_number(self):
        s = analyze_message("text me on whatsapp +1 555 123 4567")
        assert 'off_platform' in s.flags

    def test_email_migration(self):
        s = analyze_message("email me at notreallyme@example.com")
        assert 'off_platform' in s.flags

    def test_mentioning_telegram_in_passing_modest(self):
        # "i don't use telegram" alone shouldn't fire above modest
        s = analyze_message("i don't really use telegram much")
        assert s.total_score < 0.5


# ---------------------------------------------------------------------------
# Scripted openers
# ---------------------------------------------------------------------------

class TestScriptedOpeners:
    def test_widowed_engineer_opener(self):
        s = analyze_message(
            "Hello dear, I am a widowed engineer working on a contract"
        )
        assert 'scripted_opener' in s.flags

    def test_military_deployment_opener(self):
        s = analyze_message("I'm a soldier deployed in Syria")
        assert 'scripted_opener' in s.flags

    def test_oil_rig_opener(self):
        s = analyze_message("I'm an engineer working on an oil rig contract")
        assert 'scripted_opener' in s.flags


# ---------------------------------------------------------------------------
# Combined patterns — textbook scam template
# ---------------------------------------------------------------------------

class TestCombined:
    def test_widowed_engineer_with_emergency_and_money_flags_high(self):
        text = (
            "I am a widowed engineer working on an oil rig. "
            "My package is held at customs, can you send me $500 via Western Union? "
            "Email me at help@example.com"
        )
        s = analyze_message(text)
        assert s.total_score > 0.85
        assert 'scripted_opener'        in s.flags
        assert 'emergency_pretext'      in s.flags
        assert 'financial_solicitation' in s.flags
        assert 'off_platform'           in s.flags

    def test_total_caps_at_one(self):
        # Even with every category firing, total never exceeds 1.0
        text = (
            "widowed engineer deployed in Syria, my mom is in the hospital. "
            "send $500 western union, gift card, bitcoin, paypal.me. "
            "telegram @me, whatsapp +1234567890. "
            "my uncle taught me trading with guaranteed returns."
        )
        s = analyze_message(text)
        assert s.total_score == 1.0


# ---------------------------------------------------------------------------
# Trust amplification
# ---------------------------------------------------------------------------

class TestTrustAmplification:
    BASE_TEXT = "can you send me $200 via Western Union?"

    def test_brand_new_unverified_account_amplifies(self):
        raw = analyze_message(self.BASE_TEXT)
        scored = score_with_trust(
            self.BASE_TEXT, account_age_hours=2, verification_level='none',
        )
        assert scored.total_score > raw.total_score

    def test_gold_account_dampens(self):
        raw = analyze_message(self.BASE_TEXT)
        scored = score_with_trust(
            self.BASE_TEXT, account_age_hours=10000, verification_level='gold',
        )
        assert scored.total_score < raw.total_score

    def test_bronze_baseline_unchanged(self):
        raw = analyze_message(self.BASE_TEXT)
        scored = score_with_trust(
            self.BASE_TEXT, account_age_hours=10000, verification_level='bronze',
        )
        # Bronze factor is 1.00 → identical
        assert scored.total_score == pytest.approx(raw.total_score)

    def test_zero_score_text_stays_zero_regardless_of_trust(self):
        scored = score_with_trust(
            "hi nice to meet you", account_age_hours=1, verification_level='none',
        )
        assert scored.total_score == 0.0

    def test_new_account_boost_only_applied_for_unverified(self):
        # Brand-new bronze user should NOT get the new-account boost (factor stays 1.00).
        scored = score_with_trust(
            self.BASE_TEXT, account_age_hours=1, verification_level='bronze',
        )
        raw = analyze_message(self.BASE_TEXT)
        assert scored.total_score == pytest.approx(raw.total_score)

    def test_amplified_score_caps_at_one(self):
        # A maxed raw score with brand-new unverified user shouldn't exceed 1.0
        text = (
            "widowed engineer deployed in Syria, my mom is in the hospital. "
            "send $500 western union, gift card, bitcoin, paypal.me. "
            "telegram @me, whatsapp +1234567890."
        )
        scored = score_with_trust(text, account_age_hours=1, verification_level='none')
        assert scored.total_score == 1.0


# ---------------------------------------------------------------------------
# Public dataclass shape
# ---------------------------------------------------------------------------

class TestScamScoreShape:
    def test_returns_scamscore_instance(self):
        s = analyze_message("hello")
        assert isinstance(s, ScamScore)

    def test_money_request_is_zero_when_no_financial_match(self):
        s = analyze_message("widowed engineer in Syria")
        assert s.money_request == 0.0

    def test_money_request_property_mirrors_category(self):
        s = analyze_message("send me $300 via Western Union")
        assert s.money_request == s.category_scores.get('financial_solicitation', 0.0)
