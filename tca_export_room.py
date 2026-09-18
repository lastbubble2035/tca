#!/usr/bin/env python3
"""tca_export_room.py - freeze one archived room as a reproducible input snapshot.

Writes <room>-snapshot-<UTC>.jsonl.gz (one message per line, seq order) and a manifest
JSON with the window, line count, coverage, recorded gaps and SHA-256 hashes of both
the gzip file and the decompressed JSONL. Streams rows, so memory stays flat.

  python3 tca_export_room.py --room tclk-offers --outdir ~/Desktop/tca-public/release

Line shape: {"seq","ts","from","nonce","text","sig"}. "from" is the did:key, or the nick
for unsigned messages. This is the venue's public message record, unmodified.
Python 3.9+, standard library only.
"""
import argparse, gzip, hashlib, json, os, sqlite3, sys, time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.path.expanduser("~/.tc-archive.sqlite"))
    p.add_argument("--room", required=True)
    p.add_argument("--outdir", default=".")
    p.add_argument("--until", default="", help="ignore messages after this UTC time")
    a = p.parse_args()
    cut = a.until[:19]
    os.makedirs(os.path.expanduser(a.outdir), exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = os.path.join(os.path.expanduser(a.outdir), "%s-snapshot-%s" % (a.room, stamp))
    gz_path = base + ".jsonl.gz"

    c = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True, timeout=60)
    raw = hashlib.sha256(); n = 0; first = last = None; signed = 0
    with gzip.GzipFile(gz_path, "wb", compresslevel=6, mtime=0) as gz:
        for seq, ts, did, nick, nonce, text, sig in c.execute(
                "SELECT seq, ts, did, nick, nonce, text, sig FROM messages WHERE room=? ORDER BY seq", (a.room,)):
            if cut and (ts or "")[:19] > cut:
                continue
            line = (json.dumps({"seq": seq, "ts": ts, "from": did or nick, "nonce": nonce, "text": text, "sig": sig},
                               ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            gz.write(line); raw.update(line); n += 1
            if sig: signed += 1
            if first is None: first = (seq, ts)
            last = (seq, ts)
            if n % 500000 == 0:
                print("  %d lines..." % n, file=sys.stderr, flush=True)
    if not n:
        os.remove(gz_path); sys.exit("no messages archived for room %s" % a.room)
    gaps = c.execute("SELECT COUNT(*), COALESCE(SUM(resumed_seq-after_seq-1),0) FROM gaps WHERE room=?", (a.room,)).fetchone()
    c.close()

    h = hashlib.sha256()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    rng = last[0] - first[0] + 1
    manifest = {
        "room": a.room, "frozen_at": stamp, "until": a.until or None,
        "lines": n, "signed_lines": signed,
        "window": {"first_seq": first[0], "last_seq": last[0], "first_ts": first[1], "last_ts": last[1]},
        "seq_range": rng, "captured_vs_seq_range_pct": round(100.0 * n / rng, 1), "seq_values_absent": rng - n,
        "gap_events_recorded_while_polling": {"count": gaps[0], "seq_values_skipped_at_the_time": gaps[1],
                                              "note": "export sweeps later backfilled part of these"},
        "file": os.path.basename(gz_path), "file_bytes": os.path.getsize(gz_path),
        "sha256_gzip": h.hexdigest(), "sha256_decompressed_jsonl": raw.hexdigest(),
        "line_shape": ["seq", "ts", "from", "nonce", "text", "sig"],
        "verify_signature_over": "<room>|<nonce>|<text>  (Ed25519, did:key multicodec 0xed01)",
        "script_sha256": hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest(),
    }
    json.dump(manifest, open(base + ".manifest.json", "w"), indent=1)
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
