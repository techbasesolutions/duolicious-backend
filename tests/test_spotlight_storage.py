import hashlib
import io

import pytest
from PIL import Image

from service.spotlight import storage as st


def _png(w=1080, h=1080):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), 'white').save(buf, 'PNG')
    return buf.getvalue()


def _jpeg(w=1080, h=1080, colour='white'):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), colour).save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def test_validate_png_accepts_square_and_returns_hash():
    data = _png()
    h = st.validate_card_image(data, 'image/png')
    assert len(h) == 64 and h == st.validate_card_image(data, 'image/png')


def test_validate_card_image_accepts_a_jpeg_and_returns_its_hash():
    """Wave 3d Task 6: Instagram publishes JPEG only, so the card the member
    approves, and both platforms publish, is a JPEG."""
    data = _jpeg()
    h = st.validate_card_image(data, 'image/jpeg')
    assert h == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize('data,reason', [(b'notapng', 'not_png'), (_png(800, 800), 'bad_dimensions')])
def test_validate_png_rejects(data, reason):
    with pytest.raises(st.InvalidImage, match=reason):
        st.validate_card_image(data, 'image/png')


def test_validate_png_rejects_oversize():
    with pytest.raises(st.InvalidImage, match='too_large'):
        st.validate_card_image(_png(), 'image/png', max_bytes=10)


@pytest.mark.parametrize('data,reason', [
    (b'\xff\xd8\xff\xe0' + b'not a jpeg at all', 'not_png'),   # the magic bytes, then junk
    (_jpeg()[:len(_jpeg()) // 2], 'not_png'),                     # a real header, cut off mid scan
    (_jpeg(800, 800), 'bad_dimensions'),
], ids=['junk-after-magic', 'truncated', 'wrong-size'])
def test_validate_card_image_rejects_a_bad_jpeg(data, reason):
    """The same three reasons a PNG is refused with. A truncated JPEG is the
    case a header sniff (and Pillow's `verify()`, which checks little for
    JPEG) lets through; the full decode refuses it."""
    with pytest.raises(st.InvalidImage, match=reason):
        st.validate_card_image(data, 'image/jpeg')


def _jpeg_claiming(width, height):
    """A small, real 1080 JPEG whose baseline SOF header is patched to claim
    other dimensions: a few kilobytes on the wire that a full decode would
    turn into width * height * 3 bytes of pixels."""
    data = bytearray(_jpeg())
    sof = data.index(b'\xff\xc0')
    # FF C0, length (2), precision (1), then height (2) and width (2).
    data[sof + 5:sof + 7] = height.to_bytes(2, 'big')
    data[sof + 7:sof + 9] = width.to_bytes(2, 'big')
    return bytes(data)


@pytest.mark.parametrize('width,height', [(4000, 4000), (12000, 12000)], ids=['large', 'decompression-bomb'])
def test_a_jpeg_claiming_the_wrong_size_is_refused_before_it_is_decoded(monkeypatch, width, height):
    """Fix round 1 (I1). The dimensions are read off the header and refused
    before any full decode: a 19 KB JPEG claiming 12000 by 12000 used to be
    decoded (about 585 MB at peak) before bad_dimensions. `ImageFile.load`
    is made to raise, so a decode would surface as not_png instead. 12000 by
    12000 is past Pillow's decompression bomb warning threshold, which the
    validator turns into a refusal of its own."""
    from PIL import ImageFile

    def no_decode(self):
        raise AssertionError('the image must not be decoded')

    monkeypatch.setattr(ImageFile.ImageFile, 'load', no_decode)
    with pytest.raises(st.InvalidImage, match='bad_dimensions'):
        st.validate_card_image(_jpeg_claiming(width, height), 'image/jpeg')


def test_validate_card_image_rejects_an_oversize_jpeg():
    with pytest.raises(st.InvalidImage, match='too_large'):
        st.validate_card_image(_jpeg(), 'image/jpeg', max_bytes=10)


@pytest.mark.parametrize('data,content_type', [(_png(), 'image/jpeg'), (_jpeg(), 'image/png')],
                         ids=['png-labelled-jpeg', 'jpeg-labelled-png'])
def test_validate_card_image_refuses_bytes_that_do_not_match_their_content_type(data, content_type):
    """A PNG labelled JPEG (or the reverse) is refused: the stored object's
    ContentType and key extension must name what the bytes really are."""
    with pytest.raises(st.InvalidImage, match='not_png'):
        st.validate_card_image(data, content_type)


def test_validate_card_image_refuses_an_unsupported_content_type():
    with pytest.raises(ValueError, match='unsupported_content_type'):
        st.validate_card_image(_jpeg(), 'image/gif')


class _Client:
    """Stub for `_bucket().meta.client` -- just enough to record a
    `generate_presigned_url` call the way `presign` makes it."""
    def __init__(self, bucket):
        self._bucket = bucket

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
        self._bucket.calls.append(('presign', operation, Params, ExpiresIn))
        return f"https://signed/{Params['Key']}"


class _Meta:
    def __init__(self, bucket):
        self.client = _Client(bucket)


class _Bucket:
    def __init__(self, response=None, raise_exc=None):
        self.calls = []
        self.response = response
        self.raise_exc = raise_exc
        self.name = 'test-bucket'
        self.meta = _Meta(self)

    def delete_objects(self, Delete):
        # Fix wave item 1: verbose mode is what makes a deletion confirmable.
        # S3 returns an entry under `Deleted` for every key it removed only
        # when Quiet is off, so a quiet request would confirm nothing and the
        # cleanup batch would retry every key until it abandoned the job.
        assert Delete.get('Quiet') is not True, 'delete_objects must not request quiet mode'
        self.calls.append([o['Key'] for o in Delete['Objects']])
        if self.raise_exc:
            raise self.raise_exc
        return self.response



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


def _stubbed_bucket():
    """A real boto3 `Bucket` resource whose client is wrapped in botocore's
    Stubber, so `put_object` goes through boto3's own parameter handling and
    the test asserts on the exact request S3 would receive."""
    import boto3
    from botocore.stub import Stubber

    s3 = boto3.resource('s3', region_name='us-east-1',
                         aws_access_key_id='x', aws_secret_access_key='y')
    bucket = s3.Bucket('test-bucket')
    return bucket, Stubber(bucket.meta.client)


@pytest.mark.parametrize('content_type', ['image/jpeg', 'image/png'])
def test_put_card_image_is_private_by_default_and_names_its_content_type(monkeypatch, content_type):
    bucket, stubber = _stubbed_bucket()
    stubber.add_response('put_object', {}, {
        'Bucket': 'test-bucket', 'Key': 'k', 'Body': b'x', 'ACL': 'private', 'ContentType': content_type})
    stubber.add_response('put_object', {}, {
        'Bucket': 'test-bucket', 'Key': 'k2', 'Body': b'x', 'ACL': 'public-read', 'ContentType': content_type})
    stubber.activate()
    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: bucket)
    st.put_card_image('k', b'x', content_type)
    st.put_card_image('k2', b'x', content_type, public=True)
    stubber.assert_no_pending_responses()


def test_put_card_image_refuses_an_unsupported_content_type_before_the_bucket(monkeypatch):
    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: (_ for _ in ()).throw(AssertionError('_bucket must not be called')))
    with pytest.raises(ValueError, match='unsupported_content_type'):
        st.put_card_image('k', b'x', 'image/gif')


def test_make_public_sets_public_read_acl_through_the_real_client(monkeypatch):
    """A storage test must use botocore's real client shape, never a
    hand-written stub class that defines the method under test: boto3
    1.35.99's `s3.Object` resource has no `put_object_acl` method (that call
    used to 503 every approval in production), so a hand-rolled stub that
    just adds the method would never have caught it. Stubbing the real
    client is what proves `make_public` calls a method that actually
    exists."""
    import boto3
    from botocore.stub import Stubber

    client = boto3.client('s3', region_name='us-east-1',
                           aws_access_key_id='x', aws_secret_access_key='y')
    stubber = Stubber(client)
    stubber.add_response(
        'put_object_acl', {},
        {'Bucket': 'test-bucket', 'Key': 'k', 'ACL': 'public-read'})
    stubber.activate()

    class _FakeMeta:
        def __init__(self, client):
            self.client = client

    class _FakeBucket:
        name = 'test-bucket'
        meta = _FakeMeta(client)

    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: _FakeBucket())

    st.make_public('k')

    stubber.assert_no_pending_responses()


def test_bucket_and_client_methods_storage_calls_exist_on_real_boto3():
    """Step 4 regression: every method `service/spotlight/storage.py` calls
    on `_bucket()` or on `.Object(...)` must actually exist on boto3
    1.35.99's real resource/client shape -- the `make_public` defect this
    task fixes (`s3.Object` has no `put_object_acl`) would have been caught
    by this sweep before it ever reached production."""
    import boto3

    s3 = boto3.resource('s3', region_name='us-east-1',
                         aws_access_key_id='x', aws_secret_access_key='y')
    bucket = s3.Bucket('test-bucket')

    # storage.put_card_image: _bucket().put_object(...)
    assert hasattr(bucket, 'put_object')
    # storage.delete_images: _bucket().delete_objects(...)
    assert hasattr(bucket, 'delete_objects')
    # storage.presign: bucket.meta.client.generate_presigned_url(...)
    assert hasattr(bucket.meta.client, 'generate_presigned_url')
    # storage.make_public (fixed): bucket.meta.client.put_object_acl(...)
    assert hasattr(bucket.meta.client, 'put_object_acl')
    # the defect this task fixes: s3.Object has no put_object_acl method
    assert not hasattr(bucket.Object('k'), 'put_object_acl')


def test_presign_returns_the_client_presigned_url(monkeypatch):
    b = _Bucket()
    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: b)
    url = st.presign('k', 120)
    assert url == 'https://signed/k'
    assert b.calls == [('presign', 'get_object', {'Bucket': b.name, 'Key': 'k'}, 120)]


def test_put_card_image_unconfigured_raises_and_never_touches_the_bucket(monkeypatch):
    """Fix round 1 (Task 2 review): unlike delete_images, an unconfigured
    store must fail loudly here rather than silently drop the bytes -- a
    dropped upload would let a card's row believe it has a reachable
    image when nothing was ever written."""
    b = _Bucket()
    monkeypatch.setattr(st, '_configured', lambda: False)
    monkeypatch.setattr(st, '_bucket', lambda: b)
    with pytest.raises(RuntimeError, match='storage_unconfigured'):
        st.put_card_image('k', b'x', 'image/jpeg')
    assert b.calls == []


def test_make_public_unconfigured_raises(monkeypatch):
    """Fix round 1. `_bucket` is made to raise if it is ever called, to
    prove the failure happens before any network attempt."""
    monkeypatch.setattr(st, '_configured', lambda: False)
    monkeypatch.setattr(st, '_bucket', lambda: (_ for _ in ()).throw(AssertionError('_bucket must not be called')))
    with pytest.raises(RuntimeError, match='storage_unconfigured'):
        st.make_public('k')


def test_presign_returns_none_when_unconfigured(monkeypatch):
    """Fix round 1 (ruling 5): unlike put_card_image/make_public, an unconfigured
    store degrades presign to None rather than raising -- it is a local,
    purely computed signature (no network call), safe to call from inside a
    transaction the way card_state does, and a raise there would abort that
    transaction just to render a preview link. `_bucket` is made to raise if
    it is ever called, to prove the None answer happens before any network
    attempt."""
    monkeypatch.setattr(st, '_configured', lambda: False)
    monkeypatch.setattr(st, '_bucket', lambda: (_ for _ in ()).throw(AssertionError('_bucket must not be called')))
    assert st.presign('k') is None


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


def test_delete_images_never_requests_quiet_mode(monkeypatch):
    """Fix wave item 1. `delete_images` promises to return only the keys
    storage CONFIRMED gone, and the only confirmation S3 gives is the
    `Deleted` list -- which a quiet request omits entirely. Asking for quiet
    mode would therefore confirm nothing, leave every queue row's image key in
    place, and have the cleanup batch retry each key until it abandoned the
    job. This asserts on the request itself, not on the response, so the bug
    is caught even against a stub that happens to answer verbosely anyway."""
    seen = {}

    class _B:
        def delete_objects(self, Delete):
            seen['quiet'] = Delete.get('Quiet')
            return {'Deleted': [{'Key': o['Key']} for o in Delete['Objects']]}

    monkeypatch.setattr(st, '_configured', lambda: True)
    monkeypatch.setattr(st, '_bucket', lambda: _B())
    assert st.delete_images(['a', 'b']) == ['a', 'b']
    assert seen['quiet'] is not True


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
