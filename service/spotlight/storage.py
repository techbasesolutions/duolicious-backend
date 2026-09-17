"""Spaces object client for spotlight card images (spec 3.1, 3.6; Wave 2 F06/F09
part 1): validated uploads, confirmed deletions, private-until-approved objects.

Deletion is always best-effort: a storage-side failure must never block a
cancellation, a retention sweep, a removal being marked done, or (Wave 1
F03) a member withdrawal. Every failure is printed, never raised -- and,
per `_bucket`'s bounded connect/read timeouts and `_configured`'s
unconfigured-environment no-op below, "failure" can never mean "blocks
forever" either. Since Wave 2 Task 5 a failure is not a loss either: the
single caller of `delete_images` is the cleanup batch, which retries the
keys this did not confirm and leaves them on their rows meanwhile.

Uploads (`put_card_image`) are private by default (Wave 2 F09: a card must not be
publicly reachable before a member has approved it); an admin action that
already has approval in hand passes `public=True`, and `make_public` flips
an already-uploaded object over once approval lands. `presign` hands out a
short-lived read URL for a private object (an admin preview, or a member's
own approval screen) without ever making the object itself public.

Unlike deletion, the two actual writes here (`put_card_image`, `make_public`) fail
loudly -- `RuntimeError('storage_unconfigured')` -- when the object store is
unconfigured, rather than silently no-op'ing: a dropped upload or ACL
change would let a card's row believe it has a reachable image when it does
not, which deletion's silent skip never risks. `presign` is not a write --
it is a local, purely computed signature, safe to call from inside a
transaction -- so it degrades to returning None instead (fix round 1,
ruling 5).
"""
from __future__ import annotations

import hashlib
import io
import warnings

from PIL import Image


class InvalidImage(ValueError):
    """Raised by `validate_card_image` when the uploaded bytes are not a
    decodable, correctly sized image of the declared content type under the
    byte limit. `str(e)` is one of: not_png, bad_dimensions, too_large --
    the same three reasons the upload route reports back to its caller.
    `not_png` kept its name when JPEG cards arrived (Wave 3d Task 6): it is
    the reason the admin already knows, and it now means "not a valid image
    of the declared type"."""


# Wave 3d Task 6: Instagram content publishing accepts JPEG only, and the
# member approves the very bytes both platforms publish, so a card is a JPEG.
# PNG stays accepted for an admin deployed before that change. Each entry is
# the content type's magic bytes, Pillow's format name and the key extension.
CARD_IMAGE_TYPES = {
    'image/jpeg': (b'\xff\xd8\xff', 'JPEG', 'jpg'),
    'image/png': (b'\x89PNG\r\n\x1a\n', 'PNG', 'png'),
}


def card_image_extension(content_type: str) -> str:
    """The key extension for a card of this content type."""
    if content_type not in CARD_IMAGE_TYPES:
        raise ValueError('unsupported_content_type')
    return CARD_IMAGE_TYPES[content_type][2]


def _configured() -> bool:
    """False when the object-store credentials or bucket name are blank --
    `.env.production.template` ships them blank until a deployment is given
    real Spaces/R2 values, and a test environment can point them at a mock
    endpoint that simply isn't running. `delete_images` skips the network
    call entirely in that case rather than attempting (and, per `_bucket`'s
    timeouts, eventually failing) a call against nothing."""
    from service.person import R2_ACCESS_KEY_ID, R2_BUCKET_NAME
    return bool(R2_ACCESS_KEY_ID) and bool(R2_BUCKET_NAME)


_bucket_cache = None


def _bucket():
    """The same credentials and endpoint `service/api/admin/spotlight_routes.py`
    uploads spotlight cards through (`service.person`'s R2/Spaces settings),
    but its own client -- bounded with a short connect timeout, a longer
    read timeout, and no retries, so an unreachable or slow endpoint fails
    fast instead of blocking whichever transaction called `delete_images`
    (a withdrawal, a cancellation, a retention sweep) potentially for
    minutes.

    Built once and cached at module level (fix round 1): `delete_images`
    used to call this once per 1000-key batch, rebuilding the boto3
    resource each time for no reason -- credentials and the endpoint never
    change within a process. Resolved lazily on first use (imported here,
    not at module scope) so tests can monkeypatch this function directly;
    a monkeypatch always wins since it replaces this whole function, cache
    and all."""
    global _bucket_cache
    if _bucket_cache is None:
        import boto3
        from botocore.config import Config
        from service.person import R2_ACCESS_KEY_ID, R2_ACCESS_KEY_SECRET, R2_BUCKET_NAME, BOTO_ENDPOINT_URL
        s3 = boto3.resource(
            's3',
            endpoint_url=BOTO_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_ACCESS_KEY_SECRET,
            config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 1}),
        )
        _bucket_cache = s3.Bucket(R2_BUCKET_NAME)
    return _bucket_cache


def validate_card_image(data: bytes, content_type: str, *, size=(1080, 1080),
                        max_bytes=5_000_000) -> str:
    """Reject anything that is not a clean, correctly sized image of the
    declared `content_type` (`image/jpeg` or `image/png`) before it ever
    reaches the object store, and hand back the sha256 hex digest of the
    accepted bytes for `put_card_image`'s caller to key the upload on. An
    unsupported content type raises `ValueError('unsupported_content_type')`,
    a caller bug rather than a bad upload.

    The byte-size check runs first and cheaply, ahead of any decode -- an
    oversized file is rejected on `len(data)` alone. The magic bytes must
    match the declared type, so a PNG labelled JPEG (or the reverse) is never
    stored under the wrong ContentType and key extension. `Image.verify()`
    catches a truncated or corrupt PNG that a naive header sniff would miss;
    Pillow invalidates the image object after `verify()`, so the dimensions
    and format are read off a second, fresh open. Pillow's `verify()` checks
    next to nothing in a JPEG, so a JPEG is also fully decoded (`load()`),
    which refuses one cut off mid scan.

    Fix round 1 (I1): that decode runs only after the format and the
    dimensions, read off the header, have both passed. A few kilobytes of
    JPEG can claim 12000 by 12000 pixels, and decoding it first cost about
    585 MB at peak before the size check refused it. Pillow only warns
    (`DecompressionBombWarning`) for an image between its pixel limit and
    twice that; inside this call the warning is an error, and an image that
    large is refused as `bad_dimensions`, since no card is that size."""
    if content_type not in CARD_IMAGE_TYPES:
        raise ValueError('unsupported_content_type')
    magic, expected_format, _ = CARD_IMAGE_TYPES[content_type]
    if len(data) > max_bytes:
        raise InvalidImage('too_large')
    if not data.startswith(magic):
        raise InvalidImage('not_png')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            Image.open(io.BytesIO(data)).verify()
            img = Image.open(io.BytesIO(data))
            width, height = img.size
            fmt = img.format
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as e:
            raise InvalidImage('bad_dimensions') from e
        except Exception as e:
            raise InvalidImage('not_png') from e
        if fmt != expected_format:
            raise InvalidImage('not_png')
        if (width, height) != tuple(size):
            raise InvalidImage('bad_dimensions')
        if fmt == 'JPEG':
            try:
                img.load()
            except Exception as e:
                raise InvalidImage('not_png') from e
    return hashlib.sha256(data).hexdigest()


def put_card_image(key: str, data: bytes, content_type: str, *, public: bool = False) -> None:
    """Upload one rendered card, stored with its own `content_type`
    (`image/jpeg` or `image/png`, as `validate_card_image` accepted it).
    Private by default (Wave 2 F09): a card
    must not be reachable by anyone before the member pictured in it has
    approved it. `public=True` is for the one call site that already has
    approval in hand at upload time; every other caller flips visibility
    later with `make_public`.

    Fix round 1: raises `RuntimeError('storage_unconfigured')` up front,
    before ever touching `_bucket()`, when the object store has no
    credentials or bucket name. Unlike `delete_images` (whose unconfigured
    no-op is safe -- there is nothing left to clean up either way), a
    silent no-op here would let a card's row believe it has a rendered
    image when no bytes were ever written, so this fails loudly instead."""
    if content_type not in CARD_IMAGE_TYPES:
        raise ValueError('unsupported_content_type')
    if not _configured():
        raise RuntimeError('storage_unconfigured')
    _bucket().put_object(Key=key, Body=data, ACL='public-read' if public else 'private',
                          ContentType=content_type)


def make_public(key: str) -> None:
    """Flip an already-uploaded, private-by-default object public once the
    member's approval has landed. Never re-uploads the bytes -- an ACL
    change on the existing object.

    Fix round 1: raises `RuntimeError('storage_unconfigured')` up front,
    before ever touching `_bucket()`, when the object store is unconfigured
    -- the same fail-loudly reasoning as `put_card_image`: a card must not be
    treated as approved-and-public when nothing was actually made public."""
    if not _configured():
        raise RuntimeError('storage_unconfigured')
    bucket = _bucket()
    bucket.meta.client.put_object_acl(Bucket=bucket.name, Key=key, ACL='public-read')


def presign(key: str, seconds: int = 900) -> str | None:
    """A short-lived signed read URL for a private object -- an admin
    preview, or a member's own approval screen -- without ever making the
    object itself public.

    Fix round 1 (ruling 5): unlike `put_card_image` and `make_public`, an
    unconfigured store returns None here rather than raising. Signing a URL
    is a local, purely computed operation (boto3 builds and signs it against
    the credentials it already holds; nothing crosses the network to do it),
    so it is safe to call from inside a transaction -- `card_state` does,
    every time it reads a rendered card. Raising here would abort that
    transaction just to render a preview link; returning None lets the
    caller degrade to no preview instead."""
    if not _configured():
        return None
    bucket = _bucket()
    return bucket.meta.client.generate_presigned_url(
        'get_object', Params={'Bucket': bucket.name, 'Key': key}, ExpiresIn=seconds)


# S3 (and Spaces) reject a DeleteObjects request carrying more than 1000 keys,
# so a large sweep is sent in chunks rather than one oversized call that would
# fail whole. A failing chunk is logged and the rest still go.
BATCH_SIZE = 1000


def delete_images(keys: list[str]) -> list[str]:
    """Delete each key from the object store. Returns only the keys
    CONFIRMED deleted -- a key in the response's `Deleted` list, or one
    whose `Errors[].Code` is `NoSuchKey` (already gone counts as deleted) --
    never the count requested. Every other per-key error, a batch that
    raises outright, and an unconfigured object store all confirm nothing
    for the keys involved; nothing here ever raises.

    That confirmation depends on VERBOSE mode, which is why the request
    below carries no `Quiet` flag (fix wave item 1). S3 only lists a removed
    key under `Deleted` when quiet mode is off; a quiet request answers with
    errors alone, so every key would come back unconfirmed, every queue row
    would keep its image key, and the cleanup batch would retry each one
    until it abandoned the job. Verbose mode also reports a key that never
    existed as deleted, so the `NoSuchKey` branch below is belt and braces
    rather than the usual path.

    Wave 2 Task 5 closed the known follow-up this docstring used to record:
    there is now exactly ONE production caller,
    `service.spotlight.cleanup.run_cleanup_batch`, and it calls this with no
    transaction open. Everything that used to delete inline (a withdrawal, a
    cancellation, the retention sweep, a removal marked done, a superseded
    upload) enqueues a `cleanup_job` inside its own transaction instead, and
    the confirmed keys this returns are what let that batch clear the rows'
    image columns. Nothing else should call this directly."""
    if not keys:
        return []
    if not _configured():
        print('spotlight.storage.delete_images: object store not configured, skipping')
        return []
    confirmed: list[str] = []
    for start in range(0, len(keys), BATCH_SIZE):
        batch = keys[start:start + BATCH_SIZE]
        try:
            response = _bucket().delete_objects(
                Delete={'Objects': [{'Key': k} for k in batch]})
        except Exception as e:
            print(f'spotlight.storage.delete_images: failed to delete {len(batch)} object(s): {e!r}')
            continue
        response = response or {}
        confirmed.extend(o['Key'] for o in response.get('Deleted') or [])
        for err in response.get('Errors') or []:
            if err.get('Code') == 'NoSuchKey':
                confirmed.append(err['Key'])
            else:
                print(f"spotlight.storage.delete_images: error deleting {err.get('Key')}: {err.get('Message')}")
    return confirmed
