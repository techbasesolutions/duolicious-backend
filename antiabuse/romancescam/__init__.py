"""
Phase 4 Task 4.1 — romance-scam scoring (text-only heuristics).

Pure-function scoring of a single message. Does NOT touch the database, the
chat pipeline, or the network — those are wired in by the chat-process
integration (deferred per the substrate-first strategy until the
mark-read/translation wiring lands together).

Scoring categories (each contributes additively up to a per-category cap;
total capped at 1.0):

  financial_solicitation : payment-rail keywords (Western Union, gift cards,
                           crypto wallets) and currency-amount mentions
                           near request verbs ("send me $200").
  emergency_pretext      : medical / customs / travel / legal pretexts that
                           a scammer uses as the WHY of the money request.
  investment_scam        : pig-butchering and crypto-trading lead-ins.
  off_platform           : push to migrate the chat to WhatsApp / Telegram /
                           personal email — a top scam tell.
  scripted_opener        : known FTC / Action Fraud opener templates
                           (widowed engineer, military deployment, oil rig).

Trust amplification: a brand-new unverified account sending a flagged
message is treated as more risky than the same message from a long-tenured
gold-tier user. This is applied by `score_with_trust()` after the raw
`analyze_message()` pass, so the per-message heuristic stays testable in
isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Set


# ---------------------------------------------------------------------------
# Pattern banks
# ---------------------------------------------------------------------------
#
# Each entry in *_PATTERNS is `(compiled_regex, weight, label)`. Weight is
# additive within its category; category totals are capped, then the overall
# total is capped at 1.0 in `analyze_message`.

def _c(pat: str) -> re.Pattern:
    return re.compile(pat, re.IGNORECASE)


_FINANCIAL_PATTERNS = [
    # Money-transfer rails
    (_c(r'\bwestern\s*union\b'),                              0.45, 'rail_western_union'),
    (_c(r'\bmoneygram\b'),                                    0.45, 'rail_moneygram'),
    (_c(r'\b(wire\s*transfer|bank\s*transfer)\b'),            0.30, 'rail_wire'),
    (_c(r'\bzelle\b'),                                        0.25, 'rail_zelle'),
    (_c(r'\b(cash\s*app|cashapp)\b'),                         0.25, 'rail_cashapp'),
    (_c(r'\bvenmo\b'),                                        0.25, 'rail_venmo'),
    (_c(r'\bpaypal(\.me)?\b'),                                0.25, 'rail_paypal'),
    # Gift cards (extremely strong scam signal — almost no legitimate dating use)
    (_c(r'\b(itunes|amazon|google\s*play|walmart|steam|sephora)\s+(gift\s+)?cards?\b'),
                                                              0.55, 'rail_gift_card'),
    (_c(r'\bgift\s+cards?\b'),                                0.30, 'rail_gift_card_generic'),
    # Crypto rails — only when paired with send/transfer/invest
    (_c(r'\b(bitcoin|btc|ethereum|eth|usdt|crypto)\b.{0,40}\b(send|transfer|invest|buy)\b'),
                                                              0.40, 'rail_crypto'),
    (_c(r'\b(send|transfer|invest|buy)\b.{0,40}\b(bitcoin|btc|ethereum|eth|usdt|crypto)\b'),
                                                              0.40, 'rail_crypto'),
    # Currency-amount + request verb proximity (USD/EUR/GBP/etc)
    (_c(r'(\$|€|£|¥)\s?\d{2,}'),                              0.20, 'amount_mention'),
    (_c(r'\b\d{2,}\s*(usd|eur|gbp|cad|aud|btc|eth)\b'),       0.20, 'amount_mention'),
    # Direct request verbs — boosts when paired with above
    (_c(r'\b(send|transfer|wire|lend|loan|borrow)\s+(me|us)\b'),     0.20, 'request_verb'),
    (_c(r'\bcan\s+you\s+(help|send|lend|loan|spare|wire)\b'),        0.15, 'request_verb_soft'),
    # `send <amount>` (no "me/us" between) — common in Zelle/Cashapp/Venmo scams
    (_c(r'\b(send|transfer|wire)\s+(\$|€|£|¥)?\s?\d{2,}'),           0.20, 'request_verb_amount'),
]

_EMERGENCY_PATTERNS = [
    # Medical / family-emergency pretext
    (_c(r'\b(in\s+the\s+)?hospital\b.{0,40}\b(money|help|surgery|bills?)\b'),
                                                              0.40, 'emergency_medical'),
    (_c(r'\b(mom|mother|dad|father|son|daughter|sister|brother)\b.{0,30}\b(sick|surgery|hospital|dying)\b'),
                                                              0.45, 'emergency_family'),
    # Customs / shipping pretext (allow "package is held" or "package held")
    (_c(r'\b(stuck\s+at\s+customs|package\s+(is\s+)?(held|stuck|stopped)|release\s+fee|clearance\s+fee)\b'),
                                                              0.55, 'emergency_customs'),
    # Travel / stranded pretext
    (_c(r'\b(stranded|stuck\s+at\s+the\s+airport)\b'),        0.40, 'emergency_travel'),
    (_c(r'\b(flight\s+ticket|hotel\s+bill)\b.{0,40}\b(money|help|send|pay)\b'),
                                                              0.40, 'emergency_travel'),
    # Legal / bail pretext
    (_c(r'\b(lawyer\s+fees?|bail\s+money|legal\s+fees?|attorney\s+fees?)\b'),
                                                              0.45, 'emergency_legal'),
]

_INVESTMENT_PATTERNS = [
    (_c(r'\b(investment\s+opportunity|trading\s+(platform|signal)s?)\b'),  0.45, 'investment_pitch'),
    (_c(r'\b(forex|mining\s+pool|guaranteed\s+returns?|profit\s+sharing)\b'), 0.40, 'investment_pitch'),
    # Pig-butchering opener — "my uncle/aunt taught me trading"
    (_c(r'\bmy\s+(uncle|aunt|cousin|friend)\s+(taught|showed)\s+me\s+(trading|crypto|forex)\b'),
                                                              0.65, 'pig_butchering'),
    (_c(r'\bcrypto\s+profits?\b'),                            0.30, 'crypto_profits'),
]

_OFF_PLATFORM_PATTERNS = [
    # Telegram migration push
    (_c(r'\b(telegram|tg)\b.{0,40}\b(@\w+|number|handle|me)\b'), 0.45, 'migrate_telegram'),
    (_c(r'@\w{4,}\b.{0,40}\btelegram\b'),                     0.45, 'migrate_telegram'),
    # WhatsApp migration push (pair WhatsApp keyword with phone-number pattern)
    (_c(r'\bwhatsapp\b.{0,40}(\+?\d[\d\s\-()]{6,})'),         0.45, 'migrate_whatsapp'),
    (_c(r'(\+?\d[\d\s\-()]{6,}).{0,30}\bwhatsapp\b'),         0.45, 'migrate_whatsapp'),
    # Generic "add me on / message me on / find me on"
    (_c(r'\b(add|message|find|hit)\s+me\s+(on|at)\b'),        0.30, 'migrate_generic'),
    (_c(r'\bemail\s+me\s+(at|on)\b'),                         0.35, 'migrate_email'),
    # Bare phone number in a first-message-ish position
    (_c(r'\bmy\s+(number|phone)\s+is\b.{0,5}\+?\d'),          0.40, 'migrate_phone'),
]

_SCRIPTED_OPENER_PATTERNS = [
    # FTC / Action Fraud catalog of romance-scam opening lines
    (_c(r'\b(widowed|widower|widow)\b.{0,40}\b(engineer|surgeon|doctor|architect)\b'),
                                                              0.50, 'opener_widowed'),
    (_c(r'\b(deployed|deployment|stationed)\b.{0,40}\b(syria|afghanistan|iraq|yemen|kabul)\b'),
                                                              0.55, 'opener_military'),
    (_c(r'\b(oil\s+rig|offshore\s+platform)\b.{0,40}\b(work|engineer|contract)\b'),
                                                              0.55, 'opener_oilrig'),
    (_c(r'\bpeacekeep(ing|er)\b.{0,40}\b(mission|deployment|stationed)\b'),
                                                              0.50, 'opener_peacekeeper'),
]


_ALL_BANKS = [
    ('financial_solicitation', _FINANCIAL_PATTERNS, 0.95),
    ('emergency_pretext',      _EMERGENCY_PATTERNS, 0.85),
    ('investment_scam',        _INVESTMENT_PATTERNS, 0.85),
    ('off_platform',           _OFF_PLATFORM_PATTERNS, 0.75),
    ('scripted_opener',        _SCRIPTED_OPENER_PATTERNS, 0.70),
]


# ---------------------------------------------------------------------------
# Public types + function
# ---------------------------------------------------------------------------

@dataclass
class ScamScore:
    total_score: float                              # 0.0..1.0
    flags: Set[str] = field(default_factory=set)    # 'financial_solicitation', etc.
    matched_patterns: Set[str] = field(default_factory=set)   # specific labels
    category_scores: dict = field(default_factory=dict)       # {'financial_solicitation': 0.6, ...}

    # For convenient assertion in tests (e.g. `assert s.money_request > 0.8`)
    @property
    def money_request(self) -> float:
        return self.category_scores.get('financial_solicitation', 0.0)


def analyze_message(text: str) -> ScamScore:
    """Score a single message's romance-scam likelihood. Returns 0.0 for
    empty input. Score is a non-negative float capped at 1.0."""
    if not text or not text.strip():
        return ScamScore(total_score=0.0)

    flags: Set[str] = set()
    matched: Set[str] = set()
    cat_scores: dict = {}

    for category, bank, cap in _ALL_BANKS:
        cat_total = 0.0
        for pattern, weight, label in bank:
            if pattern.search(text):
                cat_total += weight
                matched.add(label)
        cat_total = min(cat_total, cap)
        if cat_total > 0:
            flags.add(category)
            cat_scores[category] = round(cat_total, 4)

    # Overall: not just a max — multiple categories firing on the same
    # message (e.g. emergency_pretext + financial_solicitation) is the
    # textbook scam template, so we sum with a softer cap.
    total = min(sum(cat_scores.values()), 1.0)

    return ScamScore(
        total_score=round(total, 4),
        flags=flags,
        matched_patterns=matched,
        category_scores=cat_scores,
    )


# ---------------------------------------------------------------------------
# Trust amplification (Step 4)
# ---------------------------------------------------------------------------

# verification_level → trust factor. New/unverified accounts inflate the
# score; gold-tier users dampen it. Tuning rationale:
#   - level=none, account < 24h          : 1.40× (high suspicion)
#   - level=none, account >= 24h         : 1.15×
#   - level=bronze                       : 1.00× (baseline)
#   - level=silver                       : 0.85×
#   - level=gold                         : 0.65×
# Values were chosen so that "ambiguous" raw scores (~0.55) only flip into
# the >0.8 high-confidence band when account-age + verification combine.

_VERIFICATION_FACTORS = {
    'none':   1.15,
    'bronze': 1.00,
    'silver': 0.85,
    'gold':   0.65,
}

NEW_ACCOUNT_HOURS = 24
NEW_ACCOUNT_BOOST = 0.25   # added to factor when account is brand-new


def score_with_trust(
    text: str,
    *,
    account_age_hours: float,
    verification_level: str = 'none',
) -> ScamScore:
    """Apply trust-tier amplification to the raw `analyze_message()` score.

    A brand-new (<24h) unverified account multiplies the raw total by
    ~1.40, vs. ~0.65 for an established gold-tier user. Per-category
    scores ride along proportionally, so a downstream "money request from
    a brand new account" alert can reuse `.money_request`.
    """
    raw = analyze_message(text)

    factor = _VERIFICATION_FACTORS.get(verification_level, 1.15)
    if (
        verification_level == 'none'
        and account_age_hours is not None
        and account_age_hours < NEW_ACCOUNT_HOURS
    ):
        factor += NEW_ACCOUNT_BOOST

    if factor == 1.0 or raw.total_score == 0:
        return raw

    new_total = min(raw.total_score * factor, 1.0)
    new_cats = {k: min(v * factor, 1.0) for k, v in raw.category_scores.items()}
    new_cats = {k: round(v, 4) for k, v in new_cats.items()}

    return ScamScore(
        total_score=round(new_total, 4),
        flags=set(raw.flags),
        matched_patterns=set(raw.matched_patterns),
        category_scores=new_cats,
    )
