"""FireHOL block-list, mmap edition (2026-07-29).

The previous implementation spawned a refresher CHILD PROCESS from
every gunicorn worker, each child holding a ~1GB pytricia trie: with 4
workers the api container reached 4.9GiB and pushed the 8GB droplet
into swap, which is why the blocklist spent months disabled
(DUO_DISABLE_FIREHOL=true). Queries also crossed a pipe with a 5ms
timeout, and a dead child surfaced as EOFError 500s.

This edition splits the work:

  * BUILDER (service/cron/fireholbuilder, one process for the whole
    deployment): downloads the netsets, collapses them into sorted,
    merged (start, end) integer ranges, and atomically writes ONE
    compact binary file (~5MB). Runs in the cron container, which has
    headroom; the transient parse cost never touches the api.

  * READER (every api worker): mmaps that file read-only and answers
    membership with a binary search. The pages are shared by all
    workers through the page cache, so the per-worker cost is
    approximately zero. No child processes, no RPC, no timeouts.
    The file is re-mmapped when its mtime changes (checked at most
    once per RECHECK_SECONDS).

Fail-open: if the file is missing or malformed the reader matches
nothing (same behaviour the RPC design had before its first refresh
finished) and warns once, so a builder outage can never lock members
out of sign-in.

Binary format (all big-endian):
    6s  magic  b"AHFH1\\0"
    I   number of IPv4 ranges
    I   number of IPv6 ranges
    then v4 ranges as (I start, I end), inclusive, sorted, merged
    then v6 ranges as (16s start, 16s end), inclusive, sorted, merged
"""

import contextlib
import fcntl
import ipaddress
import mmap
import os
import struct
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Tuple, Union
from urllib.request import Request, urlopen

ListName = str
IPAddress = Union[str, ipaddress.IPv4Address, ipaddress.IPv6Address]

MAGIC = b"AHFH1\0"
_HDR = struct.Struct(">6sII")
_V4 = struct.Struct(">II")
_V6_ITEM = 32  # two 16-byte addresses

FIREHOL_LISTS: list[ListName] = [
    "firehol_abusers_30d.netset",
    "firehol_anonymous.netset",
    "stopforumspam_365d.ipset",
]

BIN_PATH = Path(os.environ.get(
    "DUO_FIREHOL_BIN",
    "/tmp/ahavah-firehol/blocklist.bin",
))

RECHECK_SECONDS = 30.0

cache_dir = Path("/tmp/ahavah-firehol")
cache_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Download (builder side)
# ---------------------------------------------------------------------------

def _blocklist_url(name: ListName) -> str:
    return f"https://iplists.firehol.org/files/{name}"


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    fh = open(path, "a+b")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _download_or_load(name: ListName, update_interval: timedelta) -> str:
    """Return the raw list text (downloaded or disk-cached), under a lock."""
    path = cache_dir / name
    lock_path = cache_dir / f"{name}.lock"

    with _exclusive_lock(lock_path):
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age < update_interval.total_seconds():
                print(f"Loading from disk {path}", flush=True)
                return path.read_text(encoding="utf-8", errors="ignore")

        url = _blocklist_url(name)
        print(f"Downloading {url}", flush=True)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as resp:
            raw_bytes = resp.read()
        print(f"Finished downloading {url}", flush=True)
        text = raw_bytes.decode("utf-8", errors="ignore")

        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # atomic on POSIX

        return text


# ---------------------------------------------------------------------------
# Parse + merge (builder side, pure functions — unit-tested directly)
# ---------------------------------------------------------------------------

def ranges_from_text(text: str) -> Tuple[list, list]:
    """Netset text -> ([(lo, hi)] v4, [(lo, hi)] v6) as ints, unsorted."""
    v4: list = []
    v6: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue  # FireHOL files contain occasional quirks
        lo = int(net.network_address)
        hi = int(net.broadcast_address)
        (v4 if net.version == 4 else v6).append((lo, hi))
    return v4, v6


def merge_ranges(ranges: list) -> list:
    """Sort and coalesce overlapping/adjacent (lo, hi) ranges."""
    if not ranges:
        return []
    ranges = sorted(ranges)
    out = [list(ranges[0])]
    for lo, hi in ranges[1:]:
        if lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1][1] = hi
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def write_bin(path: Path, v4: list, v6: list) -> None:
    """Atomically write the binary blocklist file."""
    parts = [_HDR.pack(MAGIC, len(v4), len(v6))]
    for lo, hi in v4:
        parts.append(_V4.pack(lo, hi))
    for lo, hi in v6:
        parts.append(lo.to_bytes(16, "big") + hi.to_bytes(16, "big"))
    blob = b"".join(parts)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def build_blocklist_file(
    lists: Iterable[ListName] = tuple(FIREHOL_LISTS),
    bin_path: Path = BIN_PATH,
    update_interval: timedelta = timedelta(hours=4),
) -> tuple[int, int]:
    """Download every list and (re)write the binary file. Returns counts."""
    all_v4: list = []
    all_v6: list = []
    for name in lists:
        text = _download_or_load(name, update_interval)
        v4, v6 = ranges_from_text(text)
        if not v4 and not v6:
            raise ValueError(f"FireHOL list '{name}' appears to be empty.")
        all_v4.extend(v4)
        all_v6.extend(v6)

    v4 = merge_ranges(all_v4)
    v6 = merge_ranges(all_v6)
    write_bin(bin_path, v4, v6)
    print(
        f"firehol: wrote {bin_path} ({len(v4)} v4 + {len(v6)} v6 ranges)",
        flush=True,
    )
    return len(v4), len(v6)


# ---------------------------------------------------------------------------
# Reader (api side)
# ---------------------------------------------------------------------------

class Reader:
    """mmap-backed membership lookups with periodic freshness checks."""

    def __init__(self, path: Path = BIN_PATH,
                 recheck_seconds: float = RECHECK_SECONDS) -> None:
        self._path = Path(path)
        self._recheck = recheck_seconds
        self._lock = threading.Lock()
        self._mm: mmap.mmap | None = None
        self._mtime: float | None = None
        self._v4_n = 0
        self._v6_n = 0
        self._next_check = 0.0
        self._warned_missing = False
        self._maybe_reload(force=True)

    def _drop(self) -> None:
        if self._mm is not None:
            self._mm.close()
        self._mm = None
        self._mtime = None
        self._v4_n = self._v6_n = 0

    def _maybe_reload(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self._next_check:
            return
        with self._lock:
            if not force and now < self._next_check:
                return
            self._next_check = now + self._recheck
            try:
                st = os.stat(self._path)
            except OSError:
                if self._mm is not None or not self._warned_missing:
                    print(
                        f"WARNING: firehol blocklist file missing at "
                        f"{self._path} — matching nothing (fail-open).",
                        flush=True,
                    )
                    self._warned_missing = True
                self._drop()
                return
            if self._mm is not None and st.st_mtime == self._mtime:
                return
            try:
                with open(self._path, "rb") as fh:
                    mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            except (OSError, ValueError):
                self._drop()
                return
            try:
                magic, v4_n, v6_n = _HDR.unpack_from(mm, 0)
                expected = _HDR.size + v4_n * _V4.size + v6_n * _V6_ITEM
                if magic != MAGIC or len(mm) != expected:
                    raise ValueError("bad header")
            except (struct.error, ValueError):
                print(
                    f"WARNING: firehol blocklist file at {self._path} is "
                    f"malformed — matching nothing (fail-open).",
                    flush=True,
                )
                mm.close()
                self._drop()
                return
            self._drop()
            self._mm = mm
            self._mtime = st.st_mtime
            self._v4_n = v4_n
            self._v6_n = v6_n
            self._warned_missing = False
            print(
                f"firehol: loaded {self._path} "
                f"({v4_n} v4 + {v6_n} v6 ranges)",
                flush=True,
            )

    def _contains_v4(self, mm: mmap.mmap, x: int) -> bool:
        lo_i, hi_i = 0, self._v4_n - 1
        base = _HDR.size
        while lo_i <= hi_i:
            mid = (lo_i + hi_i) // 2
            start, end = _V4.unpack_from(mm, base + mid * _V4.size)
            if x < start:
                hi_i = mid - 1
            elif x > end:
                lo_i = mid + 1
            else:
                return True
        return False

    def _contains_v6(self, mm: mmap.mmap, x: int) -> bool:
        lo_i, hi_i = 0, self._v6_n - 1
        base = _HDR.size + self._v4_n * _V4.size
        while lo_i <= hi_i:
            mid = (lo_i + hi_i) // 2
            off = base + mid * _V6_ITEM
            start = int.from_bytes(mm[off:off + 16], "big")
            end = int.from_bytes(mm[off + 16:off + 32], "big")
            if x < start:
                hi_i = mid - 1
            elif x > end:
                lo_i = mid + 1
            else:
                return True
        return False

    def contains(self, ip: IPAddress) -> bool:
        try:
            addr = ipaddress.ip_address(str(ip))
        except ValueError:
            return False
        self._maybe_reload()
        mm = self._mm
        if mm is None:
            return False
        if addr.version == 4:
            return self._contains_v4(mm, int(addr))
        return self._contains_v6(mm, int(addr))


class Firehol:
    """Same .matches() surface the call sites already use."""

    def __init__(self, reader: Reader | None = None) -> None:
        self._reader = reader or Reader()

    def matches(self, ip: IPAddress) -> list[ListName]:
        return ["blocklist"] if self._reader.contains(ip) else []


firehol = Firehol()
