import io

import pytest
from PIL import Image

from service.spotlight import storage as st


def _png(w=1080, h=1080):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), 'white').save(buf, 'PNG')
    return buf.getvalue()


def test_validate_png_accepts_square_and_returns_hash():
    data = _png()
    h = st.validate_png(data)
    assert len(h) == 64 and h == st.validate_png(data)


@pytest.mark.parametrize('data,reason', [(b'notapng', 'not_png'), (_png(800, 800), 'bad_dimensions')])
def test_validate_png_rejects(data, reason):
    with pytest.raises(st.InvalidImage, match=reason):
        st.validate_png(data)


def test_validate_png_rejects_oversize():
    with pytest.raises(st.InvalidImage, match='too_large'):
        st.validate_png(_png(), max_bytes=10)


class _Bucket:
    def __init__(self, response=None, raise_exc=None):
        self.calls = []
        self.response = response
        self.raise_exc = raise_exc

    def delete_objects(self, Delete):
        self.calls.append([o['Key'] for o in Delete['Objects']])
        if self.raise_exc:
            raise self.raise_exc
        return self.response

    def put_object(self, **kw):
        self.calls.append(('put', kw.get('Key'), kw.get('ACL')))


def test_delete_images_returns_only_confirmed(monkeypatch):
    b = _Bucket(response={'Deleted': [{'Key': 'a'}],
                          'Errors': [{'Key': 'b', 'Code': 'NoSuchKey'}, {'Key': 'c', 'Code': 'AccessDenied'}]})
    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: b)
    assert sorted(st.delete_images(['a', 'b', 'c'])) == ['a', 'b']


def test_delete_images_confirms_nothing_on_exception_or_unconfigured(monkeypatch):
    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: _Bucket(raise_exc=RuntimeError('down')))
    assert st.delete_images(['a']) == []
    monkeypatch.setattr(st, '_configured', lambda: False)
    assert st.delete_images(['a']) == []


def test_put_png_is_private_by_default(monkeypatch):
    b = _Bucket()
    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: b)
    st.put_png('k', b'x')
    st.put_png('k2', b'x', public=True)
    assert b.calls == [('put', 'k', 'private'), ('put', 'k2', 'public-read')]


def test_delete_images_swallows_errors(monkeypatch):
    """Moved from tests/test_spotlight_retention.py (Wave 2 Task 2): a raised
    exception must never propagate, but per the new contract it also
    confirms nothing for that batch rather than the old requested-count
    return."""
    class _B:
        def delete_objects(self, **kw):
            raise RuntimeError('boom')
    monkeypatch.setattr(st, '_bucket', lambda: _B())
    assert st.delete_images(['a', 'b']) == []      # confirms nothing, no raise
    assert st.delete_images([]) == []


def test_delete_images_batches_at_the_api_limit(monkeypatch):
    """Moved from tests/test_spotlight_retention.py (Wave 2 Task 2), updated
    for the new contract. M-d: S3 and Spaces reject a DeleteObjects request
    carrying more than 1000 keys, so a large sweep goes in chunks rather
    than one call that would fail whole."""
    batches = []

    class _B:
        def delete_objects(self, **kw):
            keys = [o['Key'] for o in kw['Delete']['Objects']]
            batches.append(len(keys))
            return {'Deleted': [{'Key': k} for k in keys]}

    monkeypatch.setattr(st, '_bucket', lambda: _B())
    all_keys = [f'k{i}' for i in range(2500)]
    assert sorted(st.delete_images(all_keys)) == sorted(all_keys)
    assert batches == [1000, 1000, 500]


def test_delete_images_is_a_noop_when_unconfigured(monkeypatch):
    """Moved from tests/test_spotlight_retention.py (Wave 2 Task 2). A
    withdrawal, cancellation or retention sweep must never hang (or even
    dial out) against an object store that has blank credentials/bucket (an
    environment not yet given real Spaces/R2 settings) or an endpoint that
    simply is not running (a test container without its mock). `_bucket` is
    made to raise if it is ever called, to prove the no-op skips the network
    entirely rather than merely tolerating a failure from it."""
    monkeypatch.setattr(st, '_configured', lambda: False)
    monkeypatch.setattr(st, '_bucket', lambda: (_ for _ in ()).throw(AssertionError('_bucket must not be called')))
    assert st.delete_images(['a', 'b']) == []
