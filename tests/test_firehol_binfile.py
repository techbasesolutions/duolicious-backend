"""FireHOL mmap blocklist (2026-07-29 redesign).

The api must never regress to the per-worker child-process design
(4.9GiB container, EOFError 500s), and the reader must FAIL OPEN:
a missing or corrupt blocklist file degrades to "no IP-reputation
layer", never to blocked sign-ins.
"""
from __future__ import annotations

from pathlib import Path

from antiabuse.firehol import (
    Firehol,
    Reader,
    merge_ranges,
    ranges_from_text,
    write_bin,
)

NETSET = """
# comment line
192.0.2.0/24
198.51.100.7
2001:db8::/32
not-an-ip-line
203.0.113.0/25
"""


def _build(tmp_path: Path) -> Path:
    v4, v6 = ranges_from_text(NETSET)
    path = tmp_path / "blocklist.bin"
    write_bin(path, merge_ranges(v4), merge_ranges(v6))
    return path


def test_roundtrip_membership(tmp_path):
    r = Reader(_build(tmp_path), recheck_seconds=0)

    # v4 inside / outside
    assert r.contains("192.0.2.55")
    assert r.contains("198.51.100.7")
    assert r.contains("203.0.113.90")
    assert not r.contains("203.0.113.200")     # /25 upper half not listed
    assert not r.contains("8.8.8.8")

    # v6 inside / outside
    assert r.contains("2001:db8::1")
    assert r.contains("2001:db8:ffff::1")
    assert not r.contains("2001:db9::1")

    # junk input never raises
    assert not r.contains("not-an-ip")


def test_merge_coalesces_overlaps_and_adjacent():
    merged = merge_ranges([(10, 20), (15, 30), (31, 40), (100, 110)])
    assert merged == [(10, 40), (100, 110)]


def test_missing_file_fails_open(tmp_path):
    r = Reader(tmp_path / "nope.bin", recheck_seconds=0)
    assert not r.contains("192.0.2.1")


def test_corrupt_file_fails_open(tmp_path):
    path = tmp_path / "corrupt.bin"
    path.write_bytes(b"definitely not a blocklist")
    r = Reader(path, recheck_seconds=0)
    assert not r.contains("192.0.2.1")


def test_reload_picks_up_new_file(tmp_path):
    path = _build(tmp_path)
    r = Reader(path, recheck_seconds=0)
    assert r.contains("192.0.2.55")
    assert not r.contains("10.0.0.1")

    v4, v6 = ranges_from_text("10.0.0.0/8\n")
    write_bin(path, merge_ranges(v4), merge_ranges(v6))
    # mtime equality at coarse resolution could mask the rewrite; force it.
    import os
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))

    assert r.contains("10.0.0.1")
    assert not r.contains("192.0.2.55")


def test_matches_surface(tmp_path):
    fh = Firehol(Reader(_build(tmp_path), recheck_seconds=0))
    assert fh.matches("192.0.2.55") == ["blocklist"]
    assert fh.matches("8.8.8.8") == []
