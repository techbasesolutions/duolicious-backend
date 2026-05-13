"""
Phase 4 Task 4.0 — photo_moderation tier mapping tests.

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest tests/test_photo_moderation.py -v'

We mock `service.photo_moderation._predict_nsfw` (the lazy back-end hook)
directly. Tests do NOT load the 210MB ONNX model.
"""

from __future__ import annotations

import pytest

from service.photo_moderation import (
    APPROVE_BELOW,
    REJECT_AT_OR_ABOVE,
    ModerationVerdict,
    moderate_image_batch,
    moderate_image_bytes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_predict(monkeypatch, scores):
    """Make the lazy predict_nsfw return the given scores (one score per
    BytesIO in the input list). Bypasses the lazy-import path entirely."""
    import service.photo_moderation as pm

    def fake_predict(bufs):
        # Real predict_nsfw returns one float per buf; honour that.
        return list(scores)[: len(bufs)]

    monkeypatch.setattr(pm, '_predict_nsfw', fake_predict)
    return fake_predict


def _patch_predict_unavailable(monkeypatch):
    import service.photo_moderation as pm
    # Force the lazy hook to short-circuit: set None + override _get_predict_nsfw
    monkeypatch.setattr(pm, '_predict_nsfw', None)
    monkeypatch.setattr(pm, '_get_predict_nsfw', lambda: None)


def _patch_predict_raises(monkeypatch, exc=Exception('boom')):
    import service.photo_moderation as pm

    def raising(bufs):
        raise exc

    monkeypatch.setattr(pm, '_predict_nsfw', raising)


# ---------------------------------------------------------------------------
# Single-image entry point
# ---------------------------------------------------------------------------

class TestModerateImageBytes:
    def test_empty_bytes_approved(self):
        v = moderate_image_bytes(b'')
        assert v.status == 'approved'
        assert v.top_confidence == 0.0
        assert v.labels == []

    def test_none_bytes_approved(self):
        v = moderate_image_bytes(None)  # type: ignore[arg-type]
        assert v.status == 'approved'

    def test_low_score_below_approve_threshold_is_approved(self, monkeypatch):
        _patch_predict(monkeypatch, [0.1])
        v = moderate_image_bytes(b'fake-jpeg-bytes')
        assert v.status == 'approved'
        assert v.top_confidence == pytest.approx(0.1)
        assert v.labels == []

    def test_score_just_below_approve_cut(self, monkeypatch):
        _patch_predict(monkeypatch, [APPROVE_BELOW - 0.001])
        v = moderate_image_bytes(b'fake')
        assert v.status == 'approved'

    def test_score_at_approve_cut_is_manual_review(self, monkeypatch):
        # The cut is exclusive on the lower bound (>= APPROVE_BELOW → review).
        _patch_predict(monkeypatch, [APPROVE_BELOW])
        v = moderate_image_bytes(b'fake')
        assert v.status == 'manual_review'

    def test_borderline_score_is_manual_review(self, monkeypatch):
        _patch_predict(monkeypatch, [0.5])
        v = moderate_image_bytes(b'fake')
        assert v.status == 'manual_review'
        assert 'NSFW_borderline' in v.labels
        assert v.top_confidence == pytest.approx(0.5)

    def test_score_just_below_reject_cut(self, monkeypatch):
        _patch_predict(monkeypatch, [REJECT_AT_OR_ABOVE - 0.001])
        v = moderate_image_bytes(b'fake')
        assert v.status == 'manual_review'

    def test_score_at_or_above_reject_cut_is_rejected(self, monkeypatch):
        _patch_predict(monkeypatch, [REJECT_AT_OR_ABOVE])
        v = moderate_image_bytes(b'fake')
        assert v.status == 'rejected'
        assert 'NSFW' in v.labels
        assert v.top_confidence == pytest.approx(REJECT_AT_OR_ABOVE)

    def test_high_score_rejected(self, monkeypatch):
        _patch_predict(monkeypatch, [0.99])
        v = moderate_image_bytes(b'fake')
        assert v.status == 'rejected'

    def test_classifier_unavailable_is_manual_review_not_approved(self, monkeypatch):
        # Critical: never silently auto-approve when the classifier is dark.
        _patch_predict_unavailable(monkeypatch)
        v = moderate_image_bytes(b'fake')
        assert v.status == 'manual_review'
        assert 'classifier_unavailable' in v.labels

    def test_classifier_raising_is_manual_review_not_approved(self, monkeypatch):
        _patch_predict_raises(monkeypatch, RuntimeError('ONNX session crashed'))
        v = moderate_image_bytes(b'fake')
        assert v.status == 'manual_review'
        assert 'classifier_error' in v.labels


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

class TestModerateImageBatch:
    def test_empty_list_returns_empty_list(self):
        assert moderate_image_batch([]) == []

    def test_batch_dispatches_each_score_to_each_image(self, monkeypatch):
        _patch_predict(monkeypatch, [0.1, 0.5, 0.9])
        verdicts = moderate_image_batch([b'a', b'b', b'c'])
        assert [v.status for v in verdicts] == ['approved', 'manual_review', 'rejected']

    def test_batch_with_empty_slots_skips_predict_for_those_slots(self, monkeypatch):
        # Empty bytes should be marked approved without consuming a score.
        _patch_predict(monkeypatch, [0.5, 0.99])  # 2 scores for 2 nonempty inputs
        verdicts = moderate_image_batch([b'', b'real', b'', b'real2'])
        assert verdicts[0].status == 'approved'   # empty
        assert verdicts[1].status == 'manual_review'  # 0.5
        assert verdicts[2].status == 'approved'   # empty
        assert verdicts[3].status == 'rejected'   # 0.99

    def test_batch_classifier_unavailable_marks_each_as_manual_review(self, monkeypatch):
        _patch_predict_unavailable(monkeypatch)
        verdicts = moderate_image_batch([b'a', b'b'])
        assert all(v.status == 'manual_review' for v in verdicts)
        assert all('classifier_unavailable' in v.labels for v in verdicts)

    def test_batch_classifier_raising_marks_each_as_manual_review(self, monkeypatch):
        _patch_predict_raises(monkeypatch)
        verdicts = moderate_image_batch([b'a', b'b'])
        assert all(v.status == 'manual_review' for v in verdicts)
        assert all('classifier_error' in v.labels for v in verdicts)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

class TestVerdictShape:
    def test_returns_moderation_verdict(self, monkeypatch):
        _patch_predict(monkeypatch, [0.1])
        v = moderate_image_bytes(b'fake')
        assert isinstance(v, ModerationVerdict)
        assert v.status in ('approved', 'manual_review', 'rejected')
        assert isinstance(v.labels, list)
        assert isinstance(v.top_confidence, float)
