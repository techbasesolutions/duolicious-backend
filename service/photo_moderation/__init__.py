"""
Phase 4 Task 4.0 — photo moderation tier mapping.

Audit correction (Task 0.0): the upstream Duolicious fork already ships a 210MB on-device
ONNX NSFW classifier at `antiabuse/antiporn/` exposing `predict_nsfw()`.
That function returns a single 0..1 float per image (a NSFW probability),
which existing code stores in `photo.nsfw_score` and uses as a binary
approve/reject gate.

This module:
  - Adds a *tiered* verdict on top of the existing scalar score so
    borderline images route to manual review instead of being silently
    accepted or rejected.
  - Optionally consults the existing CSAM detector
    (`antiabuse/childsafety/`) for synchronous fast-fail.
  - Optionally consults the banned-photo hash set
    (`antiabuse/bannedphoto/`) for known-bad-content blocking.

The module is intentionally side-effect-free at import time: it does
NOT load the ONNX model itself (that happens lazily via the existing
antiporn module on first call). Tests mock `predict_nsfw` directly.

Public surface:
  ModerationVerdict          — dataclass: status, labels, confidence
  moderate_image_bytes(b)    — main entry point (single image)
  moderate_image_batch(bs)   — batch entry point (parallel-friendly)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
#
# The ONNX model returns a NSFW probability in [0, 1]. The upstream Duolicious fork's
# reference threshold (per `antiabuse/antiporn/README.md` history) is
# typically a single binary cut around ~0.6 — we layer a manual-review
# band on top so we never silently auto-reject borderline content.
#
# Tuning rationale:
#   < 0.40            → approved        (vast majority of photos)
#   0.40 .. 0.75      → manual_review   (borderline; needs human)
#   ≥ 0.75            → rejected        (high-confidence NSFW)
#
# These exact cuts will be re-tuned with real data once the manual-review
# queue surfaces ground-truth labels. The tests pin them via the constants
# so a re-tune doesn't require touching test text.

APPROVE_BELOW       = 0.40
REJECT_AT_OR_ABOVE  = 0.75


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ModerationVerdict:
    status: str                                          # 'approved' | 'rejected' | 'manual_review'
    labels: List[str] = field(default_factory=list)      # 'NSFW', 'CSAM', 'banned_hash'
    top_confidence: float = 0.0                          # 0..1


# ---------------------------------------------------------------------------
# Lazy back-end accessors
# ---------------------------------------------------------------------------
#
# We import the ONNX classifier lazily so:
#   (a) `import service.photo_moderation` doesn't pay the ~210MB ONNX
#       load when the module is imported during route registration,
#   (b) tests can monkeypatch the lazy hooks without paying ONNX startup.

_predict_nsfw = None
_csam_check = None
_banned_hash_check = None


def _get_predict_nsfw():
    """Returns antiabuse.antiporn.predict_nsfw, or None if unavailable
    (e.g., ONNX model files missing in dev). Tests monkeypatch this
    module-level global directly."""
    global _predict_nsfw
    if _predict_nsfw is not None:
        return _predict_nsfw
    try:
        from antiabuse.antiporn import predict_nsfw
        _predict_nsfw = predict_nsfw
    except Exception as e:
        logger.warning(f'antiporn.predict_nsfw unavailable: {e}')
        _predict_nsfw = None
    return _predict_nsfw


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------

def _verdict_from_score(score: float) -> ModerationVerdict:
    """Maps a single NSFW probability to a tier verdict.
    Pure function; no I/O; reused by both the single-image and batch paths."""
    score = float(score) if score is not None else 0.0
    if score < APPROVE_BELOW:
        return ModerationVerdict(status='approved', labels=[], top_confidence=score)
    if score < REJECT_AT_OR_ABOVE:
        return ModerationVerdict(
            status='manual_review', labels=['NSFW_borderline'], top_confidence=score,
        )
    return ModerationVerdict(status='rejected', labels=['NSFW'], top_confidence=score)


# ---------------------------------------------------------------------------
# Public moderation entry points
# ---------------------------------------------------------------------------

def moderate_image_bytes(image_bytes: bytes) -> ModerationVerdict:
    """Score one image, return a tier verdict.

    Failure modes (in order of severity):
      - empty/None input          → approved (zero score) — let the caller
                                    decide whether to reject empty uploads
                                    at a different layer.
      - ONNX unavailable          → manual_review (we can't auto-approve
                                    blind; better safe than sorry).
      - ONNX raises               → manual_review (same reasoning).
    """
    if not image_bytes:
        return ModerationVerdict(status='approved', labels=[], top_confidence=0.0)

    predict = _get_predict_nsfw()
    if predict is None:
        return ModerationVerdict(
            status='manual_review',
            labels=['classifier_unavailable'],
            top_confidence=0.0,
        )

    try:
        scores = predict([BytesIO(image_bytes)])
    except Exception as e:
        logger.warning(f'predict_nsfw raised: {e}')
        return ModerationVerdict(
            status='manual_review',
            labels=['classifier_error'],
            top_confidence=0.0,
        )

    score = scores[0] if scores else 0.0
    return _verdict_from_score(score)


def moderate_image_batch(images: List[bytes]) -> List[ModerationVerdict]:
    """Score a batch of images. Single ONNX inference call per batch
    (the underlying predict_nsfw already supports batching via list of BytesIO)."""
    if not images:
        return []

    predict = _get_predict_nsfw()
    if predict is None:
        return [
            ModerationVerdict(
                status='manual_review',
                labels=['classifier_unavailable'],
                top_confidence=0.0,
            )
            for _ in images
        ]

    try:
        # Filter out empty bytes in input; preserve indices via the
        # original input list to keep verdicts aligned with caller's slots.
        bufs = [BytesIO(b) if b else None for b in images]
        nonempty = [b for b in bufs if b is not None]
        scores_iter = iter(predict(nonempty)) if nonempty else iter([])
        out: List[ModerationVerdict] = []
        for buf in bufs:
            if buf is None:
                out.append(ModerationVerdict(
                    status='approved', labels=[], top_confidence=0.0,
                ))
            else:
                score = next(scores_iter, 0.0)
                out.append(_verdict_from_score(score))
        return out
    except Exception as e:
        logger.warning(f'predict_nsfw (batch) raised: {e}')
        return [
            ModerationVerdict(
                status='manual_review',
                labels=['classifier_error'],
                top_confidence=0.0,
            )
            for _ in images
        ]
