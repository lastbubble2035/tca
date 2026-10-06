#!/usr/bin/env python3
"""close1_verify_public.py: an independent, read-only check of the close-1 contest from public data.

Stages (run them all with `all`):

  fetch    GET https://challenges.technocore.chat/close-1/index.json and every record it lists into
           verify/records/, sequentially and resumably (a file already present with the index's byte
           size is skipped). Writes records/expected.sha256 (full: the posted "file"; redacted: the
           index's own "sha256"), usable with `shasum -a 256 -c`.
  venue    GET, once each and cached: the d-close1-pnl export, the d-close1-price export (for the
           referee's signed seed post) and the room-owners notes of both rooms (the referee DID).
  indexsig Looks for a referee signature over records/index.json: GET index.json.sig and index.sig
           next to it in the archive (404 = not published), and the d-close1-state and d-close1-price
           exports (cached like the venue stage's) for a signed post carrying index.json's sha256.
  verify   Ed25519 signature of every referee post; index "file" == signed pnl post "file" per sweep;
           sha256 of every record; redacted trades per sweep; builds the fold input in verify/cache/.
  replay   Runs close-call/close_call_fold.py (imported by path, unmodified) over the cached input,
           redacted trades skipped, and compares every sweep with the records and the signed boards.
  final-record  Streams the three parts of the official final record (GET only), checks the sha256 of
           the decompressed JSON, and checks the first three scores as exact decimal strings. It does
           not read a key directory. An optional --dids file is a local tally only and is not used
           by report.
  report   Writes verify/REPORT.md and verify/sweeps.csv. When the official final record verifies,
           the Final standings section is that record's top 3, cited as the signed d-close1-pnl
           post #2557 (t=standings, no sweep number). Otherwise the section is that post, or the
           earliest signed pnl post at or after settlement, and it says the board is not yet
           available until one of those is in hand. --refresh re-reads the venue exports first.

Nothing here writes to the network: every request is an HTTP GET. Standard library only (the
`cryptography` package, if installed, is used to cross-check the built-in Ed25519 verifier).
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORDS = HERE / "records"
VENUE_DIR = HERE / "venue"
CACHE = HERE / "cache"
PROGRESS = HERE / "progress.log"
REPORT = HERE / "REPORT.md"
SWEEPS_CSV = HERE / "sweeps.csv"
DEFAULT_REPO = Path(os.environ.get("CLOSE_CALL_REPO", str(HERE.parent / "close-call")))

ARCHIVE = "https://challenges.technocore.chat/close-1"
VENUE = "https://technocore.chat"
PNL_ROOM, PRICE_ROOM, STATE_ROOM = "d-close1-pnl", "d-close1-price", "d-close1-state"
INDEX_SIG_NAMES = ("index.json.sig", "index.sig")
UA = "close1-verify-public/1.0 (read-only)"
MIN_INTERVAL = 0.5          # seconds between requests: at most ~2 reads/s, far under 600/min
CENT = Decimal("0.01")
PATH_RE = re.compile(r"(sweeps|redacted)/[0-9a-f]{64}\.json")
HEX64 = re.compile(r"[0-9a-f]{64}")
DID_RE = re.compile(r"did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}")
FINAL_URL = "https://challenges.technocore.chat/close-1/final/"
FINAL_PART_NAMES = (
    "final-record.json.gz.part-00",
    "final-record.json.gz.part-01",
    "final-record.json.gz.part-02",
)
FINAL_SHA256 = "b642411aac2a3e336e97ee19aac9228d5d249b76bd41a56d4535f8be3d2f9d27"
FINAL_TOP_SCORES = ("1576.916300", "1424.740300", "1337.545600")
FINAL_STANDINGS_SEQ = 2557
FINAL_STANDINGS_TS = "2026-10-04T17:33:04Z"
_NUM = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")
_SIDE_QTY = {"side", "qty", "quantity", "position"}


# ---------------------------------------------------------------- small utilities

def utc(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def log(msg: str, echo: bool = True) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    line = f"{utc()} {msg}"
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
    if echo:
        print(line, flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def dump_json(path: Path, obj) -> None:
    write_atomic(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode())


# ---------------------------------------------------------------- HTTP (GET only)

class Getter:
    """Sequential GETs, paced to MIN_INTERVAL, retrying 408/429/5xx and network errors with capped,
    jittered backoff (honouring a Retry-After header or a seconds figure in a 429 body)."""

    def __init__(self, tries: int = 8):
        self.tries, self.last = tries, 0.0
        self.requests = 0
        self.bytes = 0

    def _pace(self) -> None:
        wait = self.last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()

    def get(self, url: str, dest: Path | None = None, timeout: float = 180) -> tuple[int, bytes, dict]:
        """(status, body, headers); with `dest`, the body is streamed to dest + '.part' and renamed on
        success, and the returned body is empty."""
        status, body, headers = 0, b"", {}
        for attempt in range(self.tries):
            self._pace()
            self.requests += 1
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    status, headers = r.status, dict(r.headers)
                    if dest is None:
                        body = r.read()
                        self.bytes += len(body)
                        return status, body, headers
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_name(dest.name + ".part")
                    n = 0
                    with open(tmp, "wb") as f:
                        for chunk in iter(lambda: r.read(1 << 20), b""):
                            f.write(chunk)
                            n += len(chunk)
                        f.flush()
                        os.fsync(f.fileno())
                    self.bytes += n
                    os.replace(tmp, dest)
                    return status, b"", headers
            except urllib.error.HTTPError as e:
                status, headers = e.code, dict(e.headers or {})
                body = e.read() or b""
            except Exception as e:      # timeouts, resets, DNS, truncated bodies
                status, body, headers = 0, f"{type(e).__name__}: {e}".encode(), {}
            if status in (0, 408, 429) or status >= 500:
                delay = self._retry_after(headers, body) if status == 429 else None
                delay = delay if delay is not None else min(120.0, 2.0 ** attempt) * (0.5 + random.random())
                log(f"GET {url} -> {status or 'network error'}; retry {attempt + 1}/{self.tries} in {delay:.1f}s")
                time.sleep(delay)
                continue
            return status, body, headers
        return status, body, headers

    @staticmethod
    def _retry_after(headers: dict, body: bytes) -> float | None:
        for text in (headers.get("Retry-After"), body.decode(errors="replace")[:500]):
            if text:
                m = re.search(r"(\d+(?:\.\d+)?)\s*(?:s\b|sec|seconds?)", str(text)) or \
                    re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", str(text))
                if m:
                    return min(float(m.group(1)), 300.0)
        return None


# ---------------------------------------------------------------- Ed25519 (RFC 8032), verify only

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s: int, pt):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _same(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    A, R = _decompress(pub), _decompress(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if A is None or R is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
    return _same(_mul(s, _G), _add(R, _mul(h, A)))


try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as _CryptoKey
except Exception:       # optional
    _CryptoKey = None

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def did_public_key(did: str) -> bytes:
    """The raw Ed25519 key in a did:key (multibase base58btc 'z', multicodec 0xed 0x01)."""
    if not DID_RE.fullmatch(did or ""):
        raise ValueError(f"not an Ed25519 did:key: {did!r}")
    raw = b58decode(did[len("did:key:z"):])
    if raw[:2] != b"\xed\x01" or len(raw) != 34:
        raise ValueError(f"not an Ed25519 did:key: {did!r}")
    return raw[2:]


def b64u_decode(sig: str) -> bytes | None:
    if not isinstance(sig, str) or len(sig) != 86 or not re.fullmatch(r"[A-Za-z0-9_-]{86}", sig):
        return None
    return base64.urlsafe_b64decode(sig + "==")


def verify_raw(did: str, message: bytes, raw: bytes | None) -> tuple[bool, bool | None]:
    """(built-in verdict, cryptography's verdict or None when that package is absent)."""
    try:
        pub = did_public_key(did)
    except ValueError:
        return False, (False if _CryptoKey else None)
    if raw is None:
        return False, (False if _CryptoKey else None)
    ours = ed25519_verify(pub, message, raw)
    theirs = None
    if _CryptoKey is not None:
        try:
            _CryptoKey.from_public_bytes(pub).verify(raw, message)
            theirs = True
        except Exception:
            theirs = False
    return ours, theirs


def verify_sig(did: str, message: str, sig: str) -> tuple[bool, bool | None]:
    """A venue post: `sig` is base64url without padding (86 chars) over the UTF-8 message."""
    return verify_raw(did, message.encode("utf-8"), b64u_decode(sig))


def sig_candidates(body: bytes) -> list[bytes]:
    """The 64-byte Ed25519 signatures a detached signature file can be read as: 64 raw bytes, or text
    (optionally a JSON object with a `sig` field, as in the venue's exports) in base64url without
    padding (the venue's own post encoding), padded base64 or base64url, or multibase base58btc
    ('z' + base58, the did:key alphabet)."""
    if len(body) == 64:
        return [body]
    try:
        text = body.decode("ascii").strip()
    except UnicodeDecodeError:
        return []
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            return []
        text = obj.get("sig") if isinstance(obj, dict) else None
        if not isinstance(text, str):
            return []
        text = text.strip()
    out = []
    if re.fullmatch(r"[A-Za-z0-9_-]{86}", text):
        out.append(b64u_decode(text))
    if re.fullmatch(r"[A-Za-z0-9+/_-]{86}==", text):
        out.append(base64.b64decode(text.replace("-", "+").replace("_", "/")))
    if re.fullmatch(r"z[1-9A-HJ-NP-Za-km-z]{80,88}", text):
        raw = b58decode(text[1:])
        if len(raw) == 64:
            out.append(raw)
    return out


# ---------------------------------------------------------------- venue export parsing

def parse_export(body: bytes, room: str, referee: str,
                 rejected: list | None = None) -> tuple[list[dict], Counter, list[str]]:
    """Referee posts from a raw /r/<room>/export body, each signature re-checked over
    `<room>|<nonce>|<text>` exactly as stored. Nonces are kept as their digits (they may exceed 2^53).
    Lines from the referee's DID that are unsigned or fail verification go to `rejected` as (line, reason)."""
    posts, stats, problems = [], Counter(), []
    for lineno, line in enumerate(body.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        stats["lines"] += 1
        try:
            rec = json.loads(line, parse_int=str)
        except ValueError:
            stats["unparseable line"] += 1
            continue
        did = rec.get("from") or rec.get("did")
        if did != referee:
            stats["not from the referee"] += 1
            continue
        nonce, text, sig = rec.get("nonce"), rec.get("text"), rec.get("sig")
        if not sig:
            stats["no signature (not re-verifiable)"] += 1
            if rejected is not None:
                rejected.append((rec, "no signature"))
            continue
        ours, theirs = verify_sig(did, f"{room}|{nonce}|{text}", sig)
        if theirs is not None and theirs != ours:
            problems.append(f"{room} line {lineno}: built-in and cryptography Ed25519 disagree")
        if not ours:
            stats["signature FAILED"] += 1
            problems.append(f"{room} seq {rec.get('seq')}: signature does not verify")
            if rejected is not None:
                rejected.append((rec, "signature does not verify"))
            continue
        stats["signature verified"] += 1
        try:
            post = json.loads(text)
        except (TypeError, ValueError):
            stats["verified but text is not JSON"] += 1
            continue
        if isinstance(post, dict):
            post["_seq"], post["_ts"] = int(rec.get("seq", 0)), rec.get("ts")
            posts.append(post)
    return posts, stats, problems


def owner_from_note(text: str) -> str | None:
    found = DID_RE.findall(text or "")
    return found[0] if len(set(found)) == 1 else None


def room_owners() -> dict:
    """{room: owner DID or None} from the cached room-owners notes."""
    notes = {r: (VENUE_DIR / f"room-owners_{r}.txt").read_text(encoding="utf-8", errors="replace")
             for r in (PNL_ROOM, PRICE_ROOM) if (VENUE_DIR / f"room-owners_{r}.txt").exists()}
    return {r: owner_from_note(t) for r, t in notes.items()}


# ---------------------------------------------------------------- stage: fetch

def load_index() -> dict:
    return json.loads((RECORDS / "index.json").read_text(encoding="utf-8"))


def expected_hash(entry: dict) -> str | None:
    return entry.get("file") if entry.get("status") == "full" else entry.get("sha256")


def cmd_fetch(args) -> int:
    g = Getter()
    status, body, _ = g.get(f"{ARCHIVE}/index.json")
    if status != 200:
        log(f"fetch: index.json -> HTTP {status}; keeping any local copy")
        if not (RECORDS / "index.json").exists():
            return 1
    else:
        json.loads(body)
        write_atomic(RECORDS / "index.json", body)
    idx = load_index()
    sweeps = sorted(idx.get("sweeps", []), key=lambda e: e["n"])
    bad = [e for e in sweeps if not PATH_RE.fullmatch(str(e.get("path"))) or not isinstance(e.get("bytes"), int)]
    if bad:
        log(f"fetch: {len(bad)} index entries have an unexpected path or size; refusing them (first: {bad[0]})")
    sweeps = [e for e in sweeps if e not in bad]
    lines = [f"{expected_hash(e)}  {e['path']}\n" for e in sweeps if expected_hash(e)]
    write_atomic(RECORDS / "expected.sha256", "".join(lines).encode())
    total = sum(e["bytes"] for e in sweeps)
    log(f"fetch: index lists {len(sweeps)} records, {total} bytes (sweeps {sweeps[0]['n']}..{sweeps[-1]['n']})")
    t0, done_bytes, fetched, skipped, failed = time.monotonic(), 0, 0, 0, []
    for i, e in enumerate(sweeps, 1):
        dest = RECORDS / e["path"]
        if dest.exists() and dest.stat().st_size == e["bytes"]:
            skipped += 1
        else:
            ok = False
            for _ in range(3):
                st, _, _ = g.get(f"{ARCHIVE}/{e['path']}", dest=dest)
                if st == 200 and dest.exists() and dest.stat().st_size == e["bytes"]:
                    ok = True
                    break
                log(f"fetch: sweep {e['n']} {e['path']}: HTTP {st}, size "
                    f"{dest.stat().st_size if dest.exists() else 'missing'} (index says {e['bytes']})")
            if ok:
                fetched += 1
            else:
                failed.append(e["n"])
                if dest.exists() and dest.stat().st_size != e["bytes"]:
                    dest.unlink()
        done_bytes += e["bytes"]
        if i % args.every == 0 or i == len(sweeps):
            el = time.monotonic() - t0
            log(f"fetch: {i}/{len(sweeps)} (sweep {e['n']}) {done_bytes / 1e9:.2f}/{total / 1e9:.2f} GB; "
                f"downloaded {fetched}, present {skipped}, failed {len(failed)}; "
                f"{g.bytes / 1e6:.0f} MB over the wire in {el:.0f}s")
    dump_json(CACHE / "fetch.json", {"at": utc(), "records": len(sweeps), "bytes": total, "downloaded": fetched,
                                     "already_present": skipped, "failed": failed, "wire_bytes": g.bytes,
                                     "requests": g.requests})
    log(f"fetch: done; {fetched} downloaded, {skipped} already present, {len(failed)} failed {failed[:20]}")
    return 1 if failed else 0


# ---------------------------------------------------------------- stage: venue

def load_venue_meta() -> dict:
    meta_path = VENUE_DIR / "meta.json"
    return json.loads(meta_path.read_text()) if meta_path.exists() else {}


def venue_read(g: Getter, url: str, dest: Path, meta: dict, refresh: bool) -> int:
    """GET `url` into `dest` once and cache it (re-read with `refresh`). Returns the HTTP status,
    200 for a cached copy; nothing is written on any other status."""
    if dest.exists() and dest.stat().st_size and not refresh:
        log(f"venue: {dest.name} cached ({dest.stat().st_size} bytes, fetched {meta.get(dest.name, {}).get('at')}); not re-read")
        return 200
    status, body, headers = g.get(url, timeout=120)
    if status != 200:
        log(f"venue: GET {url} -> HTTP {status}")
        return status
    write_atomic(dest, body)
    meta[dest.name] = {"url": url, "at": utc(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                       "generation": headers.get("X-Room-Generation") or headers.get("x-room-generation")}
    log(f"venue: {dest.name} {len(body)} bytes")
    return 200


def cmd_venue(args) -> int:
    g = Getter()
    VENUE_DIR.mkdir(parents=True, exist_ok=True)
    reads = [(f"{VENUE}/kv/room-owners/{PNL_ROOM}", VENUE_DIR / f"room-owners_{PNL_ROOM}.txt"),
             (f"{VENUE}/kv/room-owners/{PRICE_ROOM}", VENUE_DIR / f"room-owners_{PRICE_ROOM}.txt"),
             (f"{VENUE}/r/{PNL_ROOM}/export", VENUE_DIR / f"{PNL_ROOM}.export.jsonl"),
             (f"{VENUE}/r/{PRICE_ROOM}/export", VENUE_DIR / f"{PRICE_ROOM}.export.jsonl")]
    meta = load_venue_meta()
    for url, dest in reads:
        if venue_read(g, url, dest, meta, args.refresh) != 200:
            return 1
    dump_json(VENUE_DIR / "meta.json", meta)
    return 0


# ---------------------------------------------------------------- stage: indexsig

def carries_hash(obj, digest: str) -> bool:
    """True if some string value anywhere in `obj` holds `digest` as a whole hex token."""
    if isinstance(obj, str):
        return re.search(rf"(?<![0-9a-f]){digest}(?![0-9a-f])", obj.lower()) is not None
    if isinstance(obj, dict):
        return any(carries_hash(v, digest) for k, v in obj.items() if k not in ("_seq", "_ts"))
    if isinstance(obj, list):
        return any(carries_hash(v, digest) for v in obj)
    return False


def cmd_indexsig(args) -> int:
    """Looks for a referee signature over the local records/index.json:
      form a  a detached signature next to it in the archive (INDEX_SIG_NAMES) over its raw bytes;
      form b  a referee-signed post in STATE_ROOM or PRICE_ROOM whose JSON text carries the sha256 of
              those bytes (recomputed here). The room exports are read and cached like the venue stage's.
    A 404 means "not published". Any published signature that does not verify, or that cannot be
    read, makes the verdict FAILED; otherwise form a wins over form b. Writes cache/indexsig.json."""
    path = RECORDS / "index.json"
    if not path.exists():
        log("indexsig: records/index.json missing; run fetch first")
        return 1
    referee = args.referee or room_owners().get(PNL_ROOM)
    if not referee:
        log("indexsig: no referee DID (room-owners note unreadable and no --referee)")
        return 1
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    g = Getter()
    verified, failed, notes = [], [], []
    for name in INDEX_SIG_NAMES:
        local = RECORDS / name
        status, body, headers = g.get(f"{ARCHIVE}/{name}", timeout=60)
        ctype = str(headers.get("Content-Type") or headers.get("content-type") or "")
        if status in (404, 410) or (status == 200 and ctype.startswith("text/html")):
            notes.append(f"{name}: not published (HTTP {status}{', an HTML page' if status == 200 else ''})")
            if local.exists():
                local.unlink()
            continue
        if status != 200:
            failed.append(f"{name} could not be read: HTTP {status or 'network error'}")
            continue
        write_atomic(local, body)
        cands = sig_candidates(body)
        if not cands:
            failed.append(f"{name} is not a recognised Ed25519 signature encoding")
            continue
        verdicts = [verify_raw(referee, data, c) for c in cands]
        if any(theirs is not None and theirs != ours for ours, theirs in verdicts):
            failed.append(f"{name}: built-in and cryptography Ed25519 disagree")
        elif any(ours for ours, _ in verdicts):
            verified.append({"form": "a", "where": f"{ARCHIVE}/{name}"})
        else:
            failed.append(f"{name} does not verify over index.json (sha256 {digest}) with the referee key")
    VENUE_DIR.mkdir(parents=True, exist_ok=True)
    meta = load_venue_meta()
    for room in (STATE_ROOM, PRICE_ROOM):
        dest = VENUE_DIR / f"{room}.export.jsonl"
        status = venue_read(g, f"{VENUE}/r/{room}/export", dest, meta, args.refresh)
        if status == 404:
            notes.append(f"{room}: no export (HTTP 404)")
            continue
        if status != 200:
            failed.append(f"{room} export could not be read: HTTP {status or 'network error'}")
            continue
        rejected = []
        posts, stats, _ = parse_export(dest.read_bytes(), room, referee, rejected)
        hits = [p for p in posts if carries_hash(p, digest)]
        verified += [{"form": "b", "where": f"{room} seq {p['_seq']}"} for p in hits]
        for rec, why in rejected:
            if digest in str(rec.get("text") or "").lower():
                failed.append(f"{room} seq {rec.get('seq')} carries the index sha256 but has {why}"
                              if why == "no signature" else
                              f"{room} seq {rec.get('seq')} carries the index sha256 but its {why}")
        notes.append(f"{room}: {stats.get('signature verified', 0)} referee posts verified, "
                     f"{len(hits)} carry the index sha256")
    dump_json(VENUE_DIR / "meta.json", meta)
    if failed:
        line = f"index signature: FAILED ({failed[0]}" + (f"; {len(failed) - 1} more" if len(failed) > 1 else "") + ")"
    elif any(v["form"] == "a" for v in verified):
        line = "index signature: verified (form a)"
    elif verified:
        line = "index signature: verified (form b)"
    else:
        line = "index signature: none published"
    dump_json(CACHE / "indexsig.json", {"at": utc(), "index_sha256": digest, "referee": referee, "line": line,
                                        "verified": verified, "failed": failed, "notes": notes,
                                        "requests": g.requests})
    log(f"indexsig: {line}; {'; '.join(notes)}; {g.requests} requests")
    return 0


def index_sig_line() -> str:
    path = CACHE / "indexsig.json"
    if not path.exists():
        return "index signature: not checked (run the indexsig stage)"
    isig = json.loads(path.read_text())
    if not (RECORDS / "index.json").exists() or isig.get("index_sha256") != sha256_file(RECORDS / "index.json"):
        return "index signature: not checked for the current index.json (rerun the indexsig stage)"
    return isig["line"]


# ---------------------------------------------------------------- stage: verify

def pnl_posts(referee: str) -> tuple[dict, Counter, list, list, dict | None]:
    """(pnl posts by sweep, stats, problems, duplicate sweeps, the t=standings post or None).

    The standings post is the final board. It has no sweep number, so it is not entered in by_n.
    If several verify, the one with the greatest seq is kept."""
    body = (VENUE_DIR / f"{PNL_ROOM}.export.jsonl").read_bytes()
    posts, stats, problems = parse_export(body, PNL_ROOM, referee)
    by_n, dup, standings = {}, [], None
    for p in posts:
        if p.get("t") == "standings":
            stats["standings"] += 1
            if standings is None or (p.get("_seq") or 0) >= (standings.get("_seq") or 0):
                standings = p
            continue
        if p.get("t") != "pnl" or type(p.get("n")) is not int:
            stats["verified, not a pnl post"] += 1
            continue
        if p["n"] in by_n:
            dup.append(p["n"])
            continue
        by_n[p["n"]] = p
    return by_n, stats, problems, dup, standings


def seed_post(referee: str) -> tuple[dict | None, Counter, list]:
    path = VENUE_DIR / f"{PRICE_ROOM}.export.jsonl"
    if not path.exists():
        return None, Counter(), [f"{path.name} not fetched"]
    posts, stats, problems = parse_export(path.read_bytes(), PRICE_ROOM, referee)
    seeds = [p for p in posts if p.get("t") == "seed"]
    return (seeds[0] if seeds else None), stats, problems


def is_redacted(t) -> bool:
    return isinstance(t, dict) and "redacted" in t


def fingerprint(args, *extra: Path) -> str:
    """Changes whenever an input to verify/replay changes: index, venue files, every record's size and
    mtime, this script, the options, and any `extra` files (the fold, contest.json)."""
    h = hashlib.sha256()
    h.update(Path(__file__).read_bytes())
    h.update(json.dumps([args.referee, args.asof]).encode())
    for p in [RECORDS / "index.json", *sorted(VENUE_DIR.glob("*")), *extra]:
        if p.exists():
            h.update(p.name.encode() + p.read_bytes())
    for e in sorted(load_index()["sweeps"], key=lambda e: e["n"]):
        p = RECORDS / e["path"]
        st = p.stat() if p.exists() else None
        h.update(f"{e['path']}:{st.st_size if st else -1}:{st.st_mtime_ns if st else -1}\n".encode())
    return h.hexdigest()


def cached(path: Path, fp: str, force: bool) -> bool:
    if force or not path.exists():
        return False
    try:
        return json.loads(path.read_text()).get("fingerprint") == fp
    except ValueError:
        return False


def load_contest(repo: Path) -> dict:
    path = repo / "contest.json"
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def settlement_sweep(contest: dict) -> int | None:
    """Sweep whose clock is `final_price_time`.

    time(n) = first_sweep + (n - 1) * sweep_seconds. That is the sweep at which the rules say
    the referee posts the settlement price S (one hour after the lock). None if the contest
    document does not carry those fields."""
    try:
        t0 = datetime.fromisoformat(str(contest["first_sweep"]).replace("Z", "+00:00"))
        tf = datetime.fromisoformat(str(contest["final_price_time"]).replace("Z", "+00:00"))
        step = int(contest["sweep_seconds"])
        if step <= 0:
            return None
        delta = (tf - t0).total_seconds()
        if delta < 0:
            return None
        return int(delta // step) + 1
    except (KeyError, TypeError, ValueError, OSError):
        return None


def board_from_standings(post: dict | None) -> dict | None:
    """The signed t=standings post as a board.

    That post has S and places and does not carry a sweep number. places are the prize rows
    ([key, score, places spanned, sharing]); next, when present, continues the board in order.
    A post that also carries n is accepted and keeps it. None if this is not that post."""
    if not isinstance(post, dict) or post.get("t") != "standings":
        return None
    s = post.get("S")
    if not isinstance(s, str):
        return None
    top = []
    for key in ("places", "next"):
        block = post.get(key)
        if not isinstance(block, list):
            continue
        for row in block:
            if (isinstance(row, (list, tuple)) and len(row) >= 2
                    and isinstance(row[0], str) and isinstance(row[1], str)):
                top.append([row[0], row[1]])
    if not top:
        return None
    board = {"t": "standings", "mark": s, "S": s, "top": top}
    if type(post.get("n")) is int:
        board["n"] = post["n"]
    if post.get("_ts"):
        board["ts"] = post["_ts"]
    if post.get("_seq") is not None:
        board["seq"] = post["_seq"]
    return board


def venue_standings_post() -> dict | None:
    """The signed t=standings post in the cached d-close1-pnl export, or None.

    That post has no sweep number. The greatest seq wins when several verify."""
    path = VENUE_DIR / f"{PNL_ROOM}.export.jsonl"
    if not path.is_file():
        return None
    referee = room_owners().get(PNL_ROOM)
    if not referee:
        return None
    try:
        _by_n, _stats, _problems, _dup, standings = pnl_posts(referee)
    except (OSError, ValueError):
        return None
    return standings


def select_final_board(by_n: dict, settlement_n: int | None, standings: dict | None = None) -> dict | None:
    """The signed t=standings post if one was published (it need not have a sweep number).

    Otherwise the earliest signed pnl post at or after the settlement sweep, or None if
    neither is available. A pnl post that still carries n is unchanged."""
    board = board_from_standings(standings)
    if board is not None:
        return board
    if settlement_n is None:
        return None
    candidates = [n for n in by_n if type(n) is int and n >= settlement_n]
    if not candidates:
        return None
    n = min(candidates)
    post = by_n[n]
    top = post.get("top") if isinstance(post.get("top"), list) else []
    return {"n": n, "mark": post.get("mark"), "top": top}


def cmd_verify(args) -> int:
    fp = fingerprint(args, args.repo / "contest.json")
    if cached(CACHE / "verify.json", fp, args.force):
        log("verify: inputs unchanged since the last run; reusing cache/verify.json (--force to redo)")
        return 0
    owners = room_owners()
    referee = args.referee or owners.get(PNL_ROOM)
    if not referee:
        log("verify: no referee DID (room-owners note unreadable and no --referee)")
        return 1
    by_n, pstats, problems, dup, standings = pnl_posts(referee)
    seed, sstats, sproblems = seed_post(referee)
    idx = load_index()
    sweeps = sorted(idx["sweeps"], key=lambda e: e["n"])
    log(f"verify: referee {referee}; {len(by_n)} signed pnl posts (n {min(by_n)}..{max(by_n)}), {len(sweeps)} index records")

    # the replay stops at the last sweep with a record and a signed board, with every record before it
    have = [e["n"] for e in sweeps]
    asof = max(n for n in have if n in by_n)
    if args.asof:
        asof = min(asof, args.asof)
    contiguous = have[:asof] == list(range(1, asof + 1))

    CACHE.mkdir(parents=True, exist_ok=True)
    ev_tmp, ex_tmp = CACHE / "events.jsonl.part", CACHE / "expected.jsonl.part"
    rows, hash_bad, missing, file_mismatch, unparseable = [], [], [], [], []
    counts = Counter()
    first_input = None
    t0 = time.monotonic()
    with open(ev_tmp, "w", encoding="utf-8") as ev, open(ex_tmp, "w", encoding="utf-8") as ex:
        ev.write("SEED-PLACEHOLDER\n")
        for i, e in enumerate(sweeps, 1):
            n, status = e["n"], e["status"]
            post = by_n.get(n)
            row = {"n": n, "status": status, "bytes": e["bytes"], "index_file": e["file"],
                   "posted_file": post.get("file") if post else None, "index_redacted": e.get("redacted", 0)}
            row["file_match"] = None if post is None else (post.get("file") == e["file"])
            if row["file_match"] is False:
                file_mismatch.append(n)
            path = RECORDS / e["path"]
            if not path.exists() or path.stat().st_size != e["bytes"]:
                row["hash"] = "missing"
                missing.append(n)
                rows.append(row)
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            row["sha256"] = digest
            if status == "full":
                target = post.get("file") if post else None
                row["hash"] = ("ok" if digest == target else "MISMATCH") if target else \
                    ("ok vs index only (no signed post)" if digest == e["file"] else "MISMATCH vs index")
            else:
                row["hash"] = "ok" if digest == e.get("sha256") else "MISMATCH"
            if not row["hash"].startswith("ok"):
                hash_bad.append(n)
            counts[f"{status}: {row['hash']}"] += 1
            try:
                rec = json.loads(data)
            except ValueError:
                row["parse"] = "FAILED"
                unparseable.append(n)
                rows.append(row)
                continue
            inp, out = rec.get("input") or {}, rec.get("output") or {}
            trades, outs = inp.get("trades") or [], out.get("trades") or []
            red_in = [k for k, t in enumerate(trades) if is_redacted(t)]
            red_out = [k for k, t in enumerate(outs) if is_redacted(t)]
            row.update(trades=len(trades), redacted=len(red_in), redacted_out=len(red_out),
                       redaction_aligned=(red_in == red_out), input_n=inp.get("n"))
            if n == 1:
                first_input = inp
            if n <= asof:
                visible = [t for t in trades if not is_redacted(t)]
                ev.write(json.dumps({**inp, "trades": visible}, separators=(",", ":")) + "\n")
                ex.write(json.dumps({"n": n, "minted": out.get("minted"), "global_price": out.get("global_price"),
                                     "reference": out.get("reference"), "close": out.get("close"),
                                     "visible": [o for o in outs if not is_redacted(o)],
                                     "redacted": len(red_in)}, separators=(",", ":")) + "\n")
            rows.append(row)
            if i % args.every == 0 or i == len(sweeps):
                log(f"verify: {i}/{len(sweeps)} records hashed and parsed ({time.monotonic() - t0:.0f}s)")

    # seed: the referee's signed seed post, cross-checked with record 1's reference (the fold's sweep-1 ref)
    seed_px, seed_src = None, None
    if seed and isinstance(seed.get("price"), str):
        seed_px, seed_src = seed["price"], f"signed seed post, {PRICE_ROOM} seq {seed['_seq']} ({seed['_ts']})"
    elif first_input:
        seed_px, seed_src = first_input.get("ref"), "record 1 input.ref (no signed seed post available)"
    mark = by_n[asof]["mark"]
    with open(ev_tmp, encoding="utf-8") as src, open(CACHE / "events.jsonl.tmp", "w", encoding="utf-8") as dst:
        src.readline()
        dst.write(json.dumps({"t": "seed", "px": seed_px}) + "\n")
        for line in src:
            dst.write(line)
        dst.write(json.dumps({"t": "final", "px": mark}) + "\n")
    os.replace(CACHE / "events.jsonl.tmp", CACHE / "events.jsonl")
    ev_tmp.unlink()
    os.replace(ex_tmp, CACHE / "expected.jsonl")

    manifest_sha = hashlib.sha256((args.repo / "manifest.json").read_bytes()).hexdigest() \
        if (args.repo / "manifest.json").exists() else None
    contest = load_contest(args.repo)
    settle_n = settlement_sweep(contest)
    final_board = select_final_board(by_n, settle_n, standings)
    result = {
        "at": utc(), "fingerprint": fp, "referee": referee, "referee_source": {r: owners.get(r) for r in owners},
        "referee_override": bool(args.referee), "asof": asof, "records_contiguous_to_asof": contiguous,
        "lock_sweep": contest.get("lock_sweep"), "lock_time": contest.get("lock"),
        "settlement_sweep": settle_n, "final_price_time": contest.get("final_price_time"),
        "final_board": final_board,
        "index_sweeps": [have[0], have[-1]], "index_sha256": sha256_file(RECORDS / "index.json"),
        "pnl": {"posts": len(by_n), "range": [min(by_n), max(by_n)], "duplicates": dup, "stats": dict(pstats),
                "problems": problems},
        "price_room": {"stats": dict(sstats), "problems": sproblems},
        "seed": {"px": seed_px, "source": seed_src, "record1_ref": (first_input or {}).get("ref"),
                 "package": seed.get("package") if seed else None, "manifest_sha256": manifest_sha},
        "mark": mark, "board_asof": by_n[asof].get("top"),
        "hash": dict(counts), "hash_bad": hash_bad, "missing": missing, "file_mismatch": file_mismatch,
        "unparseable": unparseable,
        "no_signed_post": [r["n"] for r in rows if r["posted_file"] is None],
        "boards": {str(n): {"mark": p.get("mark"), "top": p.get("top")} for n, p in by_n.items() if n <= asof},
        "rows": rows,
    }
    dump_json(CACHE / "verify.json", result)
    log(f"verify: done; as-of sweep {asof}; hashes {dict(counts)}; file mismatches {len(file_mismatch)}; "
        f"missing {len(missing)}; signatures {dict(pstats)}")
    return 0


# ---------------------------------------------------------------- stage: replay

def load_fold(repo: Path):
    path = repo / "close_call_fold.py"
    spec = importlib.util.spec_from_file_location("close_call_fold", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # dataclasses look their module up while the file executes
    spec.loader.exec_module(mod)
    return mod


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)


HALF_CENT = Decimal("0.005")


def board_check(fold, board: dict) -> tuple[dict, bool, list]:
    """Classify each board key against the fold's current accounts.

    The referee values open positions at its unrounded global price G and posts G rounded to the cent
    as `mark`. A key's score is linear in G (value_at(G) = value_at(0) + position * G), so:
      exact       the fold's own G rounds to the mark and reproduces the posted score to the cent;
      consistent  some G within the mark's rounding (mark ± 0.005) reproduces it: the account matches,
                  only the unrounded global price (which hidden volume moves) is unknown;
      off         no such G: the key's account differs from the referee's.
    Returns ({key: (status, score at the G used, posted)}, fold G rounds to mark, [lo, hi] of the G
    interval every non-off key agrees on; lo > hi means they need different G)."""
    mark = Decimal(board["mark"])
    lo, hi = mark - HALF_CENT, mark + HALF_CENT
    g = fold.global_px
    g_ok = cents(g) == mark
    g_used = g if g_ok else mark
    common = [lo, hi]
    out = {}
    for k, posted in board["top"]:
        p = Decimal(posted)
        acct = fold.accounts.get(k)
        if acct is None:
            out[k] = ("off", None, p)
            continue
        base, q = acct.value_at(Decimal(0)) - fold.mint, acct.position
        score = base + q * g_used
        # Posted pnl scores are cents. A standings score is the same number to 6 dp; compare at the cent.
        if g_ok and cents(score) == cents(p):
            status, iv = "exact", (g, g)
        else:
            if q == 0:
                iv = (lo, hi) if abs(base - p) <= HALF_CENT else None
            else:
                a, b = sorted(((p - HALF_CENT - base) / q, (p + HALF_CENT - base) / q))
                a, b = max(a, lo), min(b, hi)
                iv = (a, b) if a <= b else None
            status = "consistent" if iv else "off"
        if iv:
            common = [max(common[0], iv[0]), min(common[1], iv[1])]
        out[k] = (status, score, p)
    return out, g_ok, common


def build_final_standings(fold, ver: dict) -> dict:
    """Top 3 of the final board, classified with board_check.

    The board is the signed t=standings post when the venue export has one (it has no sweep
    number). Otherwise it is a signed pnl post at or after the settlement sweep. Available only
    once the replay's as-of sweep covers the lock and one of those posts is present."""
    lock, settle, asof = ver.get("lock_sweep"), ver.get("settlement_sweep"), ver.get("asof")
    board = ver.get("final_board")
    out = {"available": False, "lock_sweep": lock, "settlement_sweep": settle, "asof": asof,
           "sweep": None, "mark": None, "source": None, "rows": []}
    covers = type(asof) is int and type(lock) is int and asof >= lock
    is_standings = (isinstance(board, dict) and board.get("t") == "standings"
                    and isinstance(board.get("mark"), str) and isinstance(board.get("top"), list))
    has_sweep = (isinstance(board, dict) and type(board.get("n")) is int and type(settle) is int
                 and board["n"] >= settle and isinstance(board.get("mark"), str))
    if not covers or not (is_standings or has_sweep):
        return out
    checked, g_ok, common = board_check(fold, board)
    six = Decimal("0.000001")
    rows = []
    for rank, (k, posted_score) in enumerate(board.get("top") or [], 1):
        if rank > 3:
            break
        status, score, p = checked[k]
        rows.append({"rank": rank, "key": k, "posted": posted_score,
                     "fold": str(score.quantize(six)) if score is not None else None,
                     "status": status, "reproducible": status != "off",
                     "delta": str((score - p).quantize(six)) if score is not None else None})
    out.update(available=True, sweep=board["n"] if type(board.get("n")) is int else None,
               mark=board.get("mark"), source="standings" if is_standings else "pnl",
               ts=board.get("ts"),
               fold_global_rounds_to_mark=g_ok,
               common_interval=[str(x) for x in common], rows=rows)
    return out


def cmd_replay(args) -> int:
    try:
        if os.getpriority(os.PRIO_PROCESS, 0) < 19:
            os.setpriority(os.PRIO_PROCESS, 0, 19)
    except OSError as e:
        log(f"replay: could not lower priority ({e}); run it as `nice -n 19 ... replay`")
    fp = fingerprint(args, args.repo / "close_call_fold.py", args.repo / "contest.json")
    if cached(CACHE / "replay.json", fp, args.force):
        log("replay: inputs unchanged since the last run; reusing cache/replay.json (--force to redo)")
        return 0
    fold_mod = load_fold(args.repo)
    contest = json.loads((args.repo / "contest.json").read_text(encoding="utf-8"))
    cfg = {k: contest[k] for k in fold_mod.DEFAULTS if k in contest}     # as the fold's own CLI does
    ver = json.loads((CACHE / "verify.json").read_text())
    asof, boards = ver["asof"], ver["boards"]
    top_keys = [k for k, _ in ver["board_asof"]]
    touch = {k: Counter() for k in top_keys}
    first_div = {}
    last_check, last_common, last_g_ok = {}, [Decimal(1), Decimal(0)], False
    per_sweep, mismatches = [], []
    tally = Counter()
    fold = fold_mod.Fold(cfg)
    t0 = time.monotonic()
    log(f"replay: fold {args.repo / 'close_call_fold.py'} (sha256 {sha256_file(args.repo / 'close_call_fold.py')}), "
        f"niceness {os.getpriority(os.PRIO_PROCESS, 0)}, as-of sweep {asof}")
    final = None
    with localcontext() as ctx, open(CACHE / "events.jsonl", encoding="utf-8") as ev, \
            open(CACHE / "expected.jsonl", encoding="utf-8") as ex:
        ctx.prec = 60                                   # as close_call_fold.replay() sets it
        for line in ev:
            event = json.loads(line)
            kind = event.get("t")
            if kind == "seed":
                fold.seed(event.get("px"))
                continue
            if kind == "final":
                final = fold.final(event.get("px"))
                continue
            got = fold.sweep(event.get("n"), event.get("ref"), event.get("close"),
                             event.get("owners", []), event.get("trades", []))
            want = json.loads(ex.readline())
            n = got["sweep"]
            assert want["n"] == n, f"expected.jsonl out of step at sweep {n}"
            row = {"n": n, "visible": len(want["visible"]), "redacted": want["redacted"]}
            if len(got["trades"]) != len(want["visible"]):
                row["count_mismatch"] = True
            match = mism = 0
            for trade, ours, theirs in zip(event.get("trades", []), got["trades"], want["visible"]):
                keys = {trade.get("maker"), trade.get("countersigner")} if isinstance(trade, dict) else set()
                same = ours == theirs
                if same:
                    match += 1
                else:
                    mism += 1
                    kind_ = "id" if ours.get("id") != theirs.get("id") else \
                        "outcome" if (ours.get("outcome"), ours.get("reason")) != (theirs.get("outcome"), theirs.get("reason")) \
                        else "fees"
                    tally[f"mismatch: {kind_}"] += 1
                    if len(mismatches) < 200:
                        mismatches.append({"n": n, "id": ours.get("id"), "fold": ours, "record": theirs,
                                           "maker": trade.get("maker"), "countersigner": trade.get("countersigner")})
                for k in keys & touch.keys():
                    touch[k]["visible"] += 1
                    if not same:
                        touch[k]["mismatch"] += 1
                        touch[k].setdefault("first_mismatch", n)
            tally["visible trades"] += len(want["visible"])
            tally["matched"] += match
            tally["mismatched"] += mism
            tally["unpaired"] += abs(len(got["trades"]) - len(want["visible"]))
            row.update(matched=match, mismatched=mism,
                       minted_ok=got["minted"] == want["minted"],
                       global_fold=got["global_price"], global_record=want["global_price"])
            if not row["minted_ok"]:
                tally["sweeps with a minted-list difference"] += 1
            board = boards.get(str(n))
            if board:
                checked, g_ok, common = board_check(fold, board)
                st = Counter(s for s, _, _ in checked.values())
                for k, (s, _, _) in checked.items():
                    if s == "off":
                        first_div.setdefault(k, n)
                row.update(board_exact=st["exact"], board_consistent=st["consistent"], board_off=st["off"],
                           global_rounds_to_mark=g_ok, common_global_ok=common[0] <= common[1])
                if n == asof:
                    last_check, last_common, last_g_ok = checked, common, g_ok
            per_sweep.append(row)
            if n % args.every == 0 or n == asof:
                log(f"replay: sweep {n}/{asof}; visible trades {tally['visible trades']}, "
                    f"matched {tally['matched']}, mismatched {tally['mismatched']} ({time.monotonic() - t0:.0f}s)")
        final_standings = build_final_standings(fold, ver)
    table = []
    six = Decimal("0.000001")
    for rank, (k, posted) in enumerate(ver["board_asof"], 1):
        status, score, p = last_check[k]
        tc = touch.get(k, Counter())
        if status != "off":
            red = "none detected"
        elif tc.get("mismatch"):
            red = f"likely ({tc['mismatch']} visible trade(s) on this key also differ, first at sweep {tc['first_mismatch']})"
        else:
            red = "yes (inferred: every visible trade on this key matched)"
        table.append({"rank": rank, "key": k, "posted": posted,
                      "fold": str(score.quantize(six)) if score is not None else None, "status": status,
                      "reproducible": status != "off",
                      "delta": str((score - p).quantize(six)) if score is not None else None,
                      "redacted_touch": red, "visible_trades": tc.get("visible", 0),
                      "visible_mismatches": tc.get("mismatch", 0), "first_board_divergence": first_div.get(k)})
    knock = Counter()
    for m in mismatches:
        f_, r_ = m["fold"], m["record"]
        if f_.get("outcome") == "settled" and r_.get("reason") == "settled":
            knock["fold settled, record void 'settled': the id had already settled in a redacted trade"] += 1
        elif f_.get("outcome") == "settled" and r_.get("reason") == "funds":
            knock["fold settled, record void 'funds': hidden trades had used the account's free POLF"] += 1
        else:
            knock[f"other: fold {f_.get('outcome')}/{f_.get('reason')}, record {r_.get('outcome')}/{r_.get('reason')}"] += 1
    dump_json(CACHE / "replay.json", {
        "at": utc(), "fingerprint": fp, "asof": asof, "fold_sha256": sha256_file(args.repo / "close_call_fold.py"),
        "config": cfg, "tally": dict(tally), "mismatches": mismatches, "mismatch_kinds": dict(knock),
        "per_sweep": per_sweep, "table": table,
        "asof_global": {"fold_global_rounds_to_mark": last_g_ok, "common_interval": [str(x) for x in last_common],
                        "common_ok": last_common[0] <= last_common[1]},
        "final_standings": final_standings,
        "final": {k: v for k, v in final.items() if k != "standings"},
        "final_top": final["standings"][:40], "elapsed_s": round(time.monotonic() - t0, 1)})
    log(f"replay: done in {time.monotonic() - t0:.0f}s; {dict(tally)}; board reproducible "
        f"{sum(r['reproducible'] for r in table)}/{len(table)}")
    return 0


# ---------------------------------------------------------------- stage: report

def verified_final_record() -> dict | None:
    """The cached final-record result when its decompressed sha256 is the official digest."""
    path = CACHE / "final-record.json"
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (rec.get("sha256_ok") and rec.get("sha256") == FINAL_SHA256 and rec.get("top3_ok")
            and isinstance(rec.get("top3"), list) and rec["top3"]):
        return rec
    return None


def _places_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    if value is None:
        return ""
    return str(value)


def _top3_with_places(rec: dict) -> list[dict]:
    """Top 3 from the verified record. Places are filled from the cached parts when absent."""
    rows = [dict(r) for r in rec["top3"][:3]]
    if rows and all("places" in r for r in rows):
        return rows
    paths = final_part_paths(CACHE / "final-record")
    if not all(p.is_file() for p in paths):
        return rows
    text = open_final_text(paths)
    try:
        for i, obj in enumerate(iter_json_array(text), 1):
            if i > len(rows):
                break
            if isinstance(obj.get("places"), list):
                rows[i - 1]["places"] = obj["places"]
            if i == 3:
                break
    finally:
        text.close()
    return rows


def final_standings_lines(ver: dict, rep: dict | None, standings_post: dict | None = None,
                          final_record: dict | None = None) -> list[str]:
    """Markdown for the Final standings section.

    When the official final record's decompressed sha256 matches, the board is that record's
    top 3, cited as d-close1-pnl #2557. Otherwise the board is the signed t=standings post
    once a replay has classified it (or, if it is absent, a signed pnl post at or after the
    settlement sweep). Until then the section says the board is not yet available."""
    fs = (rep or {}).get("final_standings") or {
        "available": False, "lock_sweep": ver.get("lock_sweep"), "settlement_sweep": ver.get("settlement_sweep"),
        "asof": ver.get("asof"), "rows": []}
    lock, settle, asof = fs.get("lock_sweep"), fs.get("settlement_sweep"), fs.get("asof")
    when = ver.get("final_price_time") or "final_price_time"
    lines = ["## Final standings\n"]
    lines.append(
        f"The lock is sweep {lock}"
        + (f" ({ver.get('lock_time')})" if ver.get("lock_time") else "")
        + f". Settlement is sweep {settle}, the sweep whose clock is `{when}`: the referee posts "
        f"the settlement price *S* then, and open contracts settle at it. The final board is the "
        f"signed `{PNL_ROOM}` post with `t` = `standings` (that post has no sweep number). If it "
        f"has not been published, the final board is the earliest signed pnl post at or after "
        f"sweep {settle}. Each of its top 3 keys is checked with the same cent and mark-rounding "
        f"rule as the top 25.\n")
    rec = final_record if (isinstance(final_record, dict) and final_record.get("sha256_ok")
                           and final_record.get("sha256") == FINAL_SHA256 and final_record.get("top3_ok")
                           and isinstance(final_record.get("top3"), list) and final_record["top3"]) else None
    if rec is not None:
        lines.append(
            f"The official final record verifies (decompressed sha256 `{rec['sha256']}`). "
            f"Its top 3 are the final board, the signed `{PNL_ROOM}` #{FINAL_STANDINGS_SEQ} "
            f"(`t` = `standings`, {FINAL_STANDINGS_TS}).\n")
        if isinstance(standings_post, dict) and standings_post.get("t") == "standings":
            seq, ts, s = standings_post.get("_seq"), standings_post.get("_ts"), standings_post.get("S")
            lines.append(
                f"Venue export: `{PNL_ROOM}` seq {seq} at {ts}, `t` = `standings`"
                + (f", S `{s}`" if isinstance(s, str) else "")
                + ".\n")
        lines.append("| # | key prefix | score | places | fee |")
        lines.append("|---:|---|---:|---|---:|")
        for row in _top3_with_places(rec):
            lines.append(f"| {row.get('rank')} | `{row.get('prefix')}` | {row.get('score')} | "
                         f"{_places_text(row.get('places'))} | {row.get('fee', '')} |")
        lines.append("")
        return lines
    if not fs.get("available"):
        if type(asof) is int and type(lock) is int and asof < lock:
            why = f"this run's as-of sweep is {asof}, before the lock at sweep {lock}"
        else:
            why = (f"no signed post with t=standings, and no signed pnl post at or after "
                   f"sweep {settle}, is in the venue export")
        lines.append(f"**not yet available** — {why}.\n")
        return lines
    if fs.get("source") == "standings":
        when_ts = f" at {fs['ts']}" if fs.get("ts") else ""
        nbit = (f", sweep {fs['sweep']}" if type(fs.get("sweep")) is int else ", no sweep number")
        lines.append(f"Signed post `t` = `standings`{when_ts}{nbit}, S `{fs.get('mark')}`.\n")
    else:
        lines.append(f"Signed pnl post at sweep {fs.get('sweep')} (settlement sweep {settle}), mark `{fs.get('mark')}`.\n")
    lines.append("| # | key | posted | replay | reproduces | class |")
    lines.append("|---|---|---:|---:|---|---|")
    klass = {"exact": "exact", "consistent": "mark rounding", "off": "off"}
    for r in fs.get("rows") or []:
        word = "reproduced" if r.get("reproducible") else "not reproduced"
        fold = r["fold"] if r.get("fold") is not None else "not minted"
        lines.append(f"| {r['rank']} | `{r['key']}` | {r['posted']} | {fold} | {word} | "
                     f"{klass.get(r.get('status'), r.get('status'))} |")
    lines.append("")
    return lines


def cmd_report(args) -> int:
    if getattr(args, "refresh", False):
        log("report: --refresh re-reading the venue")
        code = cmd_venue(args)
        if code:
            return code
    ver = json.loads((CACHE / "verify.json").read_text())
    rep = json.loads((CACHE / "replay.json").read_text()) if (CACHE / "replay.json").exists() else None
    fetch = json.loads((CACHE / "fetch.json").read_text()) if (CACHE / "fetch.json").exists() else {}
    meta = json.loads((VENUE_DIR / "meta.json").read_text()) if (VENUE_DIR / "meta.json").exists() else {}
    rows = ver["rows"]
    asof = ver["asof"]
    upto = [r for r in rows if r["n"] <= asof]
    full = [r for r in rows if r["status"] == "full"]
    red = [r for r in rows if r["status"] == "redacted"]
    red_trades = sum(r.get("redacted", 0) for r in rows)
    red_trades_asof = sum(r.get("redacted", 0) for r in upto)
    trades_asof = sum(r.get("trades", 0) for r in upto)
    idx_disagree = [r["n"] for r in rows if "redacted" in r and r["redacted"] != r["index_redacted"]]
    unaligned = [r["n"] for r in rows if r.get("redaction_aligned") is False]
    ps = ver["pnl"]["stats"]
    L = []
    w = L.append
    w("# close-1 public verification report\n")
    w(f"Generated {utc()} by `verify/close1_verify_public.py` from public data only "
      f"(archive `{ARCHIVE}`, venue `{VENUE}`; HTTP GET only).\n")
    w("## As of\n")
    w(f"- **As-of sweep: {asof}**: the last sweep with both an archived record and a signed `d-close1-pnl` post. "
      f"Index covers sweeps {ver['index_sweeps'][0]}..{ver['index_sweeps'][1]}; signed pnl posts cover "
      f"{ver['pnl']['range'][0]}..{ver['pnl']['range'][1]}.")
    w(f"- Posted mark at sweep {asof}: `{ver['mark']}` (the referee's global price rounded to the cent, from the signed pnl post).")
    w(f"- Records contiguous 1..{asof}: {'yes' if ver['records_contiguous_to_asof'] else 'NO'}.")
    w(f"- index.json sha256 `{ver['index_sha256']}`; pnl export fetched "
      f"{meta.get(PNL_ROOM + '.export.jsonl', {}).get('at')} (sha256 `{meta.get(PNL_ROOM + '.export.jsonl', {}).get('sha256')}`).\n")
    w("## Referee key and signatures\n")
    src = ver["referee_source"]
    w(f"- Referee DID: `{ver['referee']}`" + (" (given with --referee)" if ver["referee_override"] else ""))
    w(f"- Source: the venue's server-enforced owner notes `GET {VENUE}/kv/room-owners/<room>`, the key "
      f"technocore.chat requires on every post in a `d-` room: {PNL_ROOM} → `{src.get(PNL_ROOM)}`, "
      f"{PRICE_ROOM} → `{src.get(PRICE_ROOM)}`. The contest repo does not name the key (its rules say the "
      "launch record and seed pin it); no launch record was available to cross-check.")
    w(f"- `d-close1-pnl` export: {ps.get('lines', 0)} lines; **{ps.get('signature verified', 0)} signatures verified, "
      f"{ps.get('signature FAILED', 0)} failed**, {ps.get('no signature (not re-verifiable)', 0)} unsigned, "
      f"{ps.get('not from the referee', 0)} from another key. Message signed: `d-close1-pnl|<nonce>|<text>` "
      "(technocore.chat's did:key lane), Ed25519, checked by a built-in RFC 8032 verifier"
      + (" and cross-checked with the `cryptography` package." if _CryptoKey else "."))
    if ver["pnl"]["problems"]:
        w(f"- Signature problems: {ver['pnl']['problems'][:10]}")
    if ver["pnl"]["duplicates"]:
        w(f"- Duplicate pnl posts for sweeps {ver['pnl']['duplicates'][:20]} (first kept).")
    fm, nsp = ver["file_mismatch"], ver["no_signed_post"]
    w(f"- Index `file` vs signed post `file`: **{len([r for r in rows if r['file_match']])} equal, {len(fm)} differ**"
      + (f" (sweeps {fm[:30]})" if fm else "") + f"; {len(nsp)} index sweeps have no signed post"
      + (f" ({nsp[:10]}{' ...' if len(nsp) > 10 else ''})" if nsp else "") + ".")
    sd = ver["seed"]
    w(f"- Seed `{sd['px']}` from the {sd['source']}; record 1's `input.ref` is `{sd['record1_ref']}` "
      f"({'agrees' if sd['px'] == sd['record1_ref'] else 'DISAGREES'}).")
    if sd.get("package"):
        w(f"- Seed post pins package `{sd['package']}`; local `close-call/manifest.json` sha256 `{sd['manifest_sha256']}` "
          f"({'match' if sd['package'] == sd['manifest_sha256'] else 'DIFFERENT'}).")
    w("")
    w(index_sig_line())
    w("")
    w("## Record hashes\n")
    fb = [r["n"] for r in full if not str(r.get("hash", "")).startswith("ok")]
    rb = [r["n"] for r in red if not str(r.get("hash", "")).startswith("ok")]
    w(f"- `full` records: {len(full)}; sha256 equals the signed post's `file`: {sum(r.get('hash') == 'ok' for r in full)}; "
      f"problems: {len(fb)}" + (f" ({fb[:20]})" if fb else ""))
    w(f"- `redacted` records: {len(red)}; sha256 equals the index's own `sha256`: {sum(r.get('hash') == 'ok' for r in red)}; "
      f"problems: {len(rb)}" + (f" ({rb[:20]})" if rb else ""))
    w(f"- Missing locally: {len(ver['missing'])}" + (f" ({ver['missing'][:20]})" if ver["missing"] else ""))
    if ver.get("unparseable"):
        w(f"- Not valid JSON (left out of the replay): {ver['unparseable'][:20]}")
    w("- Limitation: a redacted record hashes to the index's `sha256`, which nobody signed. Its content is "
      "bound to the referee's signature only through the full record (hash = signed `file`), which is not "
      "public. So redacted records are checked for integrity against the index, not for authenticity.\n")
    w("## Redactions\n")
    w("A trade is one element of a record's `input.trades` (the fold input for that sweep). A redacted trade "
      "is an element replaced by `{\"redacted\": \"private room\"}`; the matching `output.trades` element is "
      "redacted the same way, so its keys, terms and outcome are all hidden.\n")
    w(f"- Redacted trades, all {len(rows)} records: **{red_trades}** of {sum(r.get('trades', 0) for r in rows)} trades "
      f"in {sum(1 for r in rows if r.get('redacted'))} sweeps.")
    w(f"- Up to the as-of sweep {asof}: {red_trades_asof} of {trades_asof} trades redacted.")
    w(f"- Record counts vs the index's `redacted` field: {'all agree' if not idx_disagree else f'{len(idx_disagree)} differ ({idx_disagree[:20]})'}; "
      f"input/output redaction positions {'aligned in every record' if not unaligned else f'misaligned in {unaligned[:20]}'}.")
    w("- Per-sweep counts are in `verify/sweeps.csv` (column `redacted`).\n")
    if rep:
        t = rep["tally"]
        try:
            pinned = json.loads((args.repo / "manifest.json").read_text())["files"]["close_call_fold.py"]["sha256"]
        except (OSError, ValueError, KeyError):
            pinned = None
        pin_note = ("matches the package manifest, whose own hash the signed seed pins" if pinned == rep["fold_sha256"]
                    else f"DOES NOT match the manifest's {pinned}")
        w("## Replay\n")
        w(f"- Fold: `close-call/close_call_fold.py`, sha256 `{rep['fold_sha256']}` ({pin_note}), not modified, "
          "imported by path; driven sweep by sweep exactly as its `replay()` does "
          "(decimal precision 60), with each sweep's redacted trades removed. Config from `close-call/contest.json`.")
        w(f"- Visible trades replayed to sweep {asof}: {t.get('visible trades', 0)}; **matched {t.get('matched', 0)}, "
          f"mismatched {t.get('mismatched', 0)}**" + (f", unpaired {t['unpaired']}" if t.get("unpaired") else "")
          + ". A match is an identical outcome object (id, settled/void, reason, maker and taker fee strings).")
        for kind, v in (rep.get("mismatch_kinds") or {}).items():
            w(f"  - {v} × {kind}")
        if rep.get("mismatch_kinds"):
            w("  An id settles at most once and funds are checked against balances, so a skipped redacted trade "
              "changes these later visible outcomes. They are knock-on effects of redaction, not referee errors "
              "the public data can show.")
        if t.get("sweeps with a minted-list difference"):
            w(f"- Sweeps whose minted list differs: {t['sweeps with a minted-list difference']}")
        ps_rows = rep["per_sweep"]
        boards_n = [r for r in ps_rows if "board_off" in r and (r["board_exact"] + r["board_consistent"] + r["board_off"])]
        clean_exact = [r["n"] for r in boards_n if r["board_off"] == 0 and r["board_consistent"] == 0]
        clean_any = [r["n"] for r in boards_n if r["board_off"] == 0 and r.get("common_global_ok")]
        first_bad = next((r["n"] for r in boards_n if r["board_off"]), None)
        first_mis = next((r["n"] for r in ps_rows if r.get("mismatched")), None)
        first_red = next((r["n"] for r in rows if r.get("redacted")), None)
        w(f"- First sweep with a redacted trade: {first_red}; first visible-trade mismatch: {first_mis}; "
          f"first signed board with a key the fold cannot reproduce: {first_bad}.")
        w(f"- Signed boards (non-empty) reproduced key-for-key: {len(clean_exact)} of {len(boards_n)} exactly "
          f"(sweeps {', '.join(map(str, clean_exact[:25]))}{' ...' if len(clean_exact) > 25 else ''}); "
          f"{len(clean_any)} of {len(boards_n)} with every key exact or consistent under one common global price.")
        w(f"- Fold totals at sweep {asof} (final at the posted mark {ver['mark']}): {rep['final']['owners']} owners, "
          f"fees {rep['final']['fees']}, zero-sum check {rep['final']['zero_sum']} (visible trades only).")
        w(f"- Replay time {rep['elapsed_s']} s under `nice -n 19`.\n")
        ag = rep.get("asof_global", {})
        w(f"## Top 25 at sweep {asof}\n")
        w("How the board is scored: the referee values open positions at its **unrounded** global price (the "
          "volume-weighted price of the last sweep with settled trades) and posts that price rounded to the cent "
          "as `mark`. The fold reproduces sweeps 1–17 key-for-key on that rule. A key's score is linear in the "
          "global price (slope = its net position), so each key is classed as:\n")
        w("- **exact**: the fold's own global price rounds to the mark and gives the posted score to the cent;")
        w("- **yes (mark rounding)**: some global price within mark ± 0.005 gives the posted score, so the "
          "key's cash and lots match and only the unrounded price, which hidden volume moves, is unknown;")
        w("- **no**: no such price, so the key's account differs from the referee's.\n")
        w(f"At sweep {asof} the fold's global price {'rounds' if ag.get('fold_global_rounds_to_mark') else 'does NOT round'} "
          f"to the mark; the global-price interval every reproducible key agrees on is "
          f"[{ag.get('common_interval', ['?', '?'])[0][:12]}, {ag.get('common_interval', ['?', '?'])[1][:12]}]"
          f"{'' if ag.get('common_ok') else ' (EMPTY: they need different prices)'}. Fold = the fold's score at its own "
          "global price if that rounds to the mark, else at the mark (6 dp); delta = fold − posted at that price. "
          "Redacted-touch: redacted trades hide their keys, so this is inferred. A key's account moves only "
          "through trades naming it and its mint. If the key is off, its mint matched and every visible trade "
          "naming it matched the record, then a redacted trade must have touched it.\n")
        w("| # | key | posted | fold | reproducible | delta | redacted-touch | first board off |")
        w("|---|---|---:|---:|:---:|---:|---|---:|")
        label = {"exact": "yes (exact)", "consistent": "yes (mark rounding)", "off": "no"}
        for r in rep["table"]:
            w(f"| {r['rank']} | `{r['key']}` | {r['posted']} | {r['fold'] if r['fold'] is not None else 'not minted'} | "
              f"{label[r['status']]} | {r['delta'] if r['delta'] is not None else '-'} | "
              f"{r['redacted_touch']} | {r['first_board_divergence'] or '-'} |")
        n_ok = sum(r["reproducible"] for r in rep["table"])
        n_ex = sum(r["status"] == "exact" for r in rep["table"])
        w(f"\n**Reproducible from public data: {n_ok} of {len(rep['table'])} board keys** "
          f"({n_ex} exact, {n_ok - n_ex} up to the mark's rounding).\n")
        if rep["mismatches"]:
            w("### First visible-trade mismatches\n")
            w("| sweep | id | fold | record |")
            w("|---:|---|---|---|")
            for m in rep["mismatches"][:15]:
                w(f"| {m['n']} | `{m['id']}` | `{json.dumps(m['fold'], sort_keys=True)}` | `{json.dumps(m['record'], sort_keys=True)}` |")
            w("")
    else:
        w("## Replay\n\nNot run.\n")
    seen = venue_standings_post()
    if seen:
        log(f"report: venue standings post seq {seen.get('_seq')} at {seen.get('_ts')}")
    for line in final_standings_lines(ver, rep, seen, verified_final_record()):
        w(line)
    for line in final_record_lines():
        w(line)
    w("## What could not be verified\n")
    w("- The content of redacted trades (keys, terms, outcomes) and therefore any board score they move.")
    w("- Authenticity of redacted records beyond the unsigned index hash (see Record hashes).")
    w("- The referee's unrounded global price once hidden volume has settled (the fold sees only visible volume, "
      "and the post gives the price to the cent), hence the \"mark rounding\" class in the table.")
    w("- The referee DID against a signed launch record (none was published where this tool could read it); "
      "it is taken from the venue's room-owner notes.")
    if fetch:
        w(f"\n_Fetch: {fetch.get('records')} records, {fetch.get('bytes')} bytes listed; "
          f"{fetch.get('downloaded')} downloaded in the last fetch run, {fetch.get('already_present')} already present, "
          f"failed {fetch.get('failed')}._")
    write_atomic(REPORT, ("\n".join(L) + "\n").encode())

    rp = {r["n"]: r for r in (rep["per_sweep"] if rep else [])}
    cols = ["n", "status", "bytes", "hash", "file_match", "trades", "redacted", "index_redacted",
            "visible", "matched", "mismatched", "minted_ok", "board_exact", "board_consistent", "board_off",
            "global_rounds_to_mark", "common_global_ok", "global_fold", "global_record"]
    tmp = SWEEPS_CSV.with_name(SWEEPS_CSV.name + ".part")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({**r, **rp.get(r["n"], {})})
    os.replace(tmp, SWEEPS_CSV)
    log(f"report: wrote {REPORT} and {SWEEPS_CSV}")
    return 0


# ---------------------------------------------------------------- stage: final-record

def refuse_keys(path: Path) -> Path:
    """Stop if `path` is ~/.tc-close1-keys or anything inside it. Does not list that directory."""
    path = Path(path).expanduser()
    keys = Path(os.environ.get("CLOSE1_KEYS", "~/.tc-close1-keys")).expanduser()
    try:
        resolved = path.resolve()
        keys_r = keys.resolve()
    except OSError:
        resolved, keys_r = path, keys
    if resolved == keys_r or keys_r in resolved.parents:
        raise SystemExit("refusing a path under ~/.tc-close1-keys")
    return path


def sha256_concat(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def sha256_decompressed(paths: list[Path]) -> tuple[str, int | None]:
    """(sha256, byte count) of the gunzip of the concatenated parts.

    The count is None when the gzip stream is invalid. Bytes are hashed as they decompress.
    """
    h = hashlib.sha256()
    total = 0
    raw = io.BufferedReader(_PartReader(paths), buffer_size=1 << 20)
    try:
        gz = gzip.GzipFile(fileobj=raw)
    except gzip.BadGzipFile:
        raw.close()
        return h.hexdigest(), None
    try:
        while True:
            try:
                chunk = gz.read(1 << 20)
            except (gzip.BadGzipFile, EOFError, zlib.error):
                return h.hexdigest(), None
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
    finally:
        gz.close()
    return h.hexdigest(), total


class _PartReader(io.RawIOBase):
    """Read several files as one byte stream."""

    def __init__(self, paths: list[Path]):
        self._fh = [open(p, "rb") for p in paths]
        self._i = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        n = 0
        mv = memoryview(b)
        while n < len(mv) and self._i < len(self._fh):
            got = self._fh[self._i].readinto(mv[n:])
            if not got:
                self._fh[self._i].close()
                self._i += 1
                continue
            n += got
        return n

    def close(self) -> None:
        for fh in self._fh:
            try:
                fh.close()
            except OSError:
                pass
        super().close()


def open_final_text(paths: list[Path]):
    """Stream-decompress the concatenation of the part files. One gzip member."""
    raw = io.BufferedReader(_PartReader(paths), buffer_size=1 << 20)
    gz = gzip.GzipFile(fileobj=raw)
    return io.TextIOWrapper(gz, encoding="utf-8", newline="")


def iter_json_array(stream):
    """Yield row objects. Numbers stay the exact decimal text, not floats.

    A top-level array is that array. Any other document is scanned, outside strings, for the first
    array whose first element is an object (the standings list in the official record).
    """
    dec = json.JSONDecoder(parse_float=str, parse_int=str)
    buf, pos, eof = "", 0, False
    in_str, esc = False, False
    while True:
        if pos >= len(buf) and not eof:
            chunk = stream.read(1 << 20)
            if chunk:
                buf += chunk
            else:
                eof = True
        if pos >= len(buf):
            raise ValueError("final record has no row array")
        ch = buf[pos]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            pos += 1
            continue
        if ch == '"':
            in_str = True
            pos += 1
            continue
        if ch == "[":
            q = pos + 1
            while True:
                while q >= len(buf) and not eof:
                    chunk = stream.read(1 << 20)
                    if chunk:
                        buf += chunk
                    else:
                        eof = True
                if q >= len(buf):
                    raise ValueError("final record row array is truncated")
                if buf[q] in " \t\r\n":
                    q += 1
                    continue
                break
            if buf[q] == "{":
                pos += 1
                break
        pos += 1
    while True:
        while pos < len(buf) and buf[pos] in " \t\r\n":
            pos += 1
        if pos >= len(buf):
            if eof:
                raise ValueError("final record JSON array did not end")
            chunk = stream.read(1 << 20)
            if chunk:
                buf += chunk
            else:
                eof = True
            continue
        ch = buf[pos]
        if ch == "]":
            return
        if ch == ",":
            pos += 1
            continue
        try:
            obj, end = dec.raw_decode(buf, pos)
        except json.JSONDecodeError:
            if eof:
                raise
            chunk = stream.read(1 << 20)
            if not chunk:
                eof = True
            else:
                buf += chunk
            continue
        if not isinstance(obj, dict):
            raise ValueError("final record row is not an object")
        yield obj
        pos = end
        if pos > (1 << 20):
            buf = buf[pos:]
            pos = 0


def _is_num(v) -> bool:
    return isinstance(v, str) and _NUM.fullmatch(v) is not None


def pick_key_field(obj: dict) -> str | None:
    hits = [k for k, v in obj.items() if isinstance(v, str) and v.startswith("did:key:")]
    for name in ("key", "did", "account", "owner"):
        if name in hits:
            return name
    return hits[0] if hits else None


def pick_score_field(obj: dict, expect0: str) -> str | None:
    """The field whose text is the first expected score, else a numeric score/pnl/value field."""
    hits = [k for k, v in obj.items() if isinstance(v, str) and v == expect0]
    if len(hits) == 1:
        return hits[0]
    named = [k for k in hits if any(s in k.lower() for s in ("score", "pnl", "value"))]
    if named:
        return named[0]
    if hits:
        return hits[0]
    for name in ("score", "pnl", "value"):
        if _is_num(obj.get(name)):
            return name
    named2 = [k for k, v in obj.items() if _is_num(v) and any(s in k.lower() for s in ("score", "pnl"))]
    return named2[0] if len(named2) == 1 else None


def pick_fee_field(obj: dict) -> str | None:
    for name in ("fee", "fees"):
        if name in obj and _is_num(obj[name]):
            return name
    cands = [k for k, v in obj.items() if "fee" in k.lower() and _is_num(v)]
    return cands[0] if len(cands) == 1 else None


def pick_side_qty(obj: dict, skip: set[str]) -> list[str]:
    out = []
    for k, v in obj.items():
        if k in skip:
            continue
        if v is not None and not isinstance(v, (str, int, float)):
            continue
        low = k.lower()
        if low in _SIDE_QTY or low.endswith("_qty") or low.endswith("_side"):
            out.append(k)
    return out


def key_prefix(did: str, n: int = 12) -> str:
    body = did[len("did:key:"):] if did.startswith("did:key:") else did
    return body[:n]


def load_did_list(path: Path) -> set[str]:
    path = refuse_keys(path)
    out = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("did:key:"):
                out.add(s)
    return out


def final_part_paths(directory: Path) -> list[Path]:
    return [directory / name for name in FINAL_PART_NAMES]


def ensure_final_parts(directory: Path) -> list[Path] | None:
    """Stream each part to disk. A part already present is kept; the hash is what accepts it."""
    directory.mkdir(parents=True, exist_ok=True)
    g = Getter()
    paths = []
    for name in FINAL_PART_NAMES:
        dest = directory / name
        if dest.is_file() and dest.stat().st_size > 0:
            log(f"final-record: using {dest.name} ({dest.stat().st_size} bytes)")
            paths.append(dest)
            continue
        url = FINAL_URL + name
        log(f"final-record: GET {url}")
        status, _body, _headers = g.get(url, dest=dest, timeout=600)
        if status != 200 or not dest.is_file() or dest.stat().st_size == 0:
            log(f"final-record: GET {url} -> {status}")
            return None
        paths.append(dest)
    return paths


def _row_view(obj: dict, key_f: str, score_f: str, fee_f: str | None, extras: list[str], rank: int) -> dict:
    did = obj.get(key_f)
    row = {"rank": rank, "prefix": key_prefix(did) if isinstance(did, str) else "",
           "score": obj.get(score_f)}
    if fee_f:
        row["fee"] = obj.get(fee_f)
    for name in extras:
        if name in obj:
            row[name] = obj[name]
    row["key"] = did
    if isinstance(obj.get("places"), list):
        row["places"] = obj["places"]
    return row


def cmd_final_record(args) -> int:
    """Hash the decompressed JSON, then stream the rows. Optional --dids tallies a caller-supplied list.

    The published result (verify/cache/final-record.json and the report) has prefixes and scores
    only. Full DIDs from --dids are written to --ours-out and are not put in the report."""
    expect_sha = getattr(args, "expect_sha256", None) or FINAL_SHA256
    raw_scores = getattr(args, "expect_scores", None)
    expect_scores = tuple(s.strip() for s in raw_scores.split(",")) if raw_scores else FINAL_TOP_SCORES
    if len(expect_scores) != 3:
        log("final-record: --expect-scores needs three comma-separated decimals")
        return 1
    parts_dir = getattr(args, "parts_dir", None)
    if parts_dir:
        directory = refuse_keys(Path(parts_dir))
        paths = final_part_paths(directory)
        missing = [p.name for p in paths if not p.is_file()]
        if missing:
            log(f"final-record: missing {missing} in {directory}")
            return 1
    else:
        paths = ensure_final_parts(CACHE / "final-record")
        if not paths:
            return 1
    digest, nbytes = sha256_decompressed(paths)
    gzip_sha = sha256_concat(paths)
    result = {"at": utc(), "url": FINAL_URL, "parts": list(FINAL_PART_NAMES),
              "sha256": digest, "sha256_of": "decompressed", "expect_sha256": expect_sha,
              "sha256_ok": nbytes is not None and digest == expect_sha,
              "decompressed_bytes": nbytes, "gzip_sha256": gzip_sha,
              "part_bytes": [p.stat().st_size for p in paths]}
    if nbytes is None or digest != expect_sha:
        why = "gzip decompress failed" if nbytes is None else f"got {digest} expected {expect_sha}"
        log(f"final-record: decompressed sha256 MISMATCH ({why})")
        dump_json(CACHE / "final-record.json", result)
        return 1
    log(f"final-record: decompressed sha256 {digest} matches ({nbytes} bytes)")
    want = None
    dids = getattr(args, "dids", None)
    if dids:
        want = load_did_list(Path(dids))
        log(f"final-record: tallying {len(want)} public dids from {Path(dids).name}")
    ours_out = getattr(args, "ours_out", None)
    if ours_out:
        ours_out = refuse_keys(Path(ours_out))
    text = open_final_text(paths)
    top3 = []
    key_f = score_f = fee_f = None
    extras: list[str] = []
    rows_n = 0
    matched = 0
    seen: set[str] = set()
    sum_score = Decimal(0)
    sum_fee = Decimal(0)
    n_pos = n_neg = n_zero = 0
    best = None
    writer = None
    csv_fh = None
    try:
        for obj in iter_json_array(text):
            rows_n += 1
            if rows_n == 1:
                key_f = pick_key_field(obj)
                score_f = pick_score_field(obj, expect_scores[0])
                fee_f = pick_fee_field(obj)
                if not key_f or not score_f:
                    log(f"final-record: could not find key/score fields in {sorted(obj)}")
                    result.update({"rows": 0, "fields": sorted(obj), "top3_ok": False})
                    dump_json(CACHE / "final-record.json", result)
                    return 1
                extras = pick_side_qty(obj, {key_f, score_f, fee_f} - {None})
                log(f"final-record: fields {sorted(obj)}; key {key_f}; score {score_f}; "
                    f"fee {fee_f or 'none'}; side/qty {extras or 'none'}")
                if ours_out and want is not None:
                    ours_out.parent.mkdir(parents=True, exist_ok=True)
                    csv_fh = ours_out.open("w", newline="", encoding="utf-8")
                    cols = ["rank", "key", "score"] + (["fee"] if fee_f else []) + extras
                    writer = csv.DictWriter(csv_fh, fieldnames=cols, extrasaction="ignore")
                    writer.writeheader()
            if rows_n <= 3:
                top3.append({k: v for k, v in _row_view(obj, key_f, score_f, fee_f, extras, rows_n).items()
                             if k != "key"})
            if want is None:
                if rows_n == 3:
                    # The public check only needs the first three scores. Keep counting rows so the
                    # report can say how long the record is; still one streaming pass.
                    pass
                if rows_n % 1000000 == 0:
                    log(f"final-record: {rows_n} rows")
                continue
            did = obj.get(key_f)
            if did not in want:
                if rows_n % 1000000 == 0:
                    log(f"final-record: {rows_n} rows")
                continue
            matched += 1
            seen.add(did)
            view = _row_view(obj, key_f, score_f, fee_f, extras, rows_n)
            score = Decimal(view["score"])
            sum_score += score
            if score > 0:
                n_pos += 1
            elif score < 0:
                n_neg += 1
            else:
                n_zero += 1
            if fee_f and _is_num(view.get("fee")):
                sum_fee += Decimal(view["fee"])
            if best is None or score > best[0] or (score == best[0] and rows_n < best[1]):
                best = (score, rows_n, view["score"], did)
            if writer:
                writer.writerow({k: view[k] for k in writer.fieldnames})
            if rows_n % 1000000 == 0:
                log(f"final-record: {rows_n} rows")
    finally:
        text.close()
        if csv_fh:
            csv_fh.close()
    scores = [r.get("score") for r in top3]
    top_ok = scores == list(expect_scores)
    result.update({"rows": rows_n, "fields_key": key_f, "score_field": score_f, "fee_field": fee_f,
                   "side_qty_fields": extras, "top3": top3, "top3_ok": top_ok,
                   "expect_scores": list(expect_scores)})
    dump_json(CACHE / "final-record.json", result)
    shown = ", ".join(str(s) for s in scores)
    if not top_ok:
        log(f"final-record: top scores {shown} != {', '.join(expect_scores)}")
        return 1
    log(f"final-record: {rows_n} rows; top 3 {shown}")
    if want is not None:
        no_row = len(want - seen)
        if best:
            best_txt = f"best {best[2]} rank {best[1]} prefix {key_prefix(best[3])}"
        else:
            best_txt = "best none"
        log(f"final-record: rows {matched} / keys {len(want)}; distinct keys {len(seen)}; "
            f"keys with no row {no_row}; {best_txt}; sum scores {format(sum_score, 'f')}; "
            f"sum fees {format(sum_fee, 'f')}; positive {n_pos}; negative {n_neg}; zero {n_zero}")
        if ours_out:
            log(f"final-record: wrote {ours_out}")
    return 0


def final_record_lines() -> list[str]:
    """Markdown for the Final record section. Prefixes and scores only; no local DID tally."""
    path = CACHE / "final-record.json"
    lines = ["## Final record\n"]
    if not path.exists():
        lines.append("not checked (run the `final-record` stage).\n")
        return lines
    rec = json.loads(path.read_text())
    got, exp = rec.get("sha256"), rec.get("expect_sha256") or FINAL_SHA256
    if not rec.get("sha256_ok"):
        lines.append(f"Decompressed bytes sha256 `{got}` (expected `{exp}`) **MISMATCH**.\n")
        return lines
    fee = rec.get("fee_field")
    gzip_note = ""
    if rec.get("gzip_sha256"):
        gzip_note = f" Joined gzip sha256 `{rec['gzip_sha256']}`."
    lines.append(
        f"Official final record, streamed from `{FINAL_URL}` as `{FINAL_PART_NAMES[0]}`, "
        f"`{FINAL_PART_NAMES[1]}` and `{FINAL_PART_NAMES[2]}`. Decompressed bytes sha256 `{got}` "
        f"(matches `{exp}`).{gzip_note} Gzip JSON, {rec.get('rows')} rows. "
        f"Score field `{rec.get('score_field')}`"
        + (f", fee field `{fee}`." if fee else ".")
        + "\n")
    lines.append("First three rows. The score is the decimal text from the file, not a rounded float.\n")
    lines.append("| # | key prefix | score | fee |")
    lines.append("|---:|---|---:|---:|")
    for row in rec.get("top3") or []:
        lines.append(f"| {row['rank']} | `{row['prefix']}` | {row['score']} | {row.get('fee', '')} |")
    lines.append("")
    word = "match" if rec.get("top3_ok") else "DO NOT match"
    exp_s = rec.get("expect_scores") or list(FINAL_TOP_SCORES)
    shown = " / ".join(f"`{s}`" for s in exp_s)
    lines.append(f"Top 3 scores {word} {shown}.\n")
    return lines


# ---------------------------------------------------------------- pipeline

def cmd_all(args) -> int:
    common = ["--repo", str(args.repo), "--every", str(args.every)]
    if args.referee:
        common += ["--referee", args.referee]
    if args.asof:
        common += ["--asof", str(args.asof)]
    if args.force:
        common += ["--force"]
    refresh = ["--refresh"] if args.refresh else []
    steps = [[] if args.skip_fetch else ["fetch"], ["venue"] + refresh, ["indexsig"] + refresh,
             ["nice", "verify"], ["nice", "replay"], ["final-record"], ["report"]]
    for step in steps:
        if not step:
            continue
        niced = step[0] == "nice"
        sub = step[1:] if niced else step
        cmd = (["nice", "-n", "19"] if niced else []) + [sys.executable, str(Path(__file__).resolve())] + sub + common
        log(f"all: running {' '.join(cmd[:3] if niced else cmd[:2])} ... {' '.join(sub)}", echo=True)
        code = subprocess.run(cmd).returncode
        if code and sub[0] != "fetch":
            log(f"all: {sub[0]} exited {code}; stopping")
            return code
        if code:
            log("all: fetch reported failures; continuing with what is present")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["fetch", "venue", "indexsig", "verify", "replay", "report",
                                       "final-record", "all"])
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="contest repo checkout (default ../close-call)")
    ap.add_argument("--referee", help="referee did:key (default: the venue's room-owners note for d-close1-pnl)")
    ap.add_argument("--asof", type=int, help="stop the replay at this sweep")
    ap.add_argument("--every", type=int, default=25, help="progress line every N records/sweeps")
    ap.add_argument("--refresh", action="store_true",
                    help="venue, indexsig, report: re-read the venue exports and notes")
    ap.add_argument("--skip-fetch", action="store_true", help="all: do not touch the archive")
    ap.add_argument("--force", action="store_true", help="verify/replay: redo even if the inputs are unchanged")
    ap.add_argument("--parts-dir", type=Path, help="final-record: directory holding the three part files (no download)")
    ap.add_argument("--expect-sha256", help="final-record: expected sha256 of the concatenated parts")
    ap.add_argument("--expect-scores", help="final-record: three exact score strings, comma-separated")
    ap.add_argument("--dids", type=Path, help="final-record: optional public did:key list to tally; not used by report")
    ap.add_argument("--ours-out", type=Path, help="final-record: csv path for the --dids rows")
    args = ap.parse_args()
    args.repo = args.repo.expanduser().resolve()
    return {"fetch": cmd_fetch, "venue": cmd_venue, "indexsig": cmd_indexsig, "verify": cmd_verify,
            "replay": cmd_replay, "report": cmd_report, "final-record": cmd_final_record,
            "all": cmd_all}[args.stage](args)


if __name__ == "__main__":
    raise SystemExit(main())
