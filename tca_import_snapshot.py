#!/usr/bin/env python3
"""tca_import_snapshot.py - rebuild a tca-schema SQLite archive from a published room snapshot.

Input is the .jsonl.gz written by tca_export_room.py (one message per line:
seq, ts, from, nonce, text, sig). Output is a SQLite file the analysis scripts read with --db.
Prints the SHA-256 of the decompressed JSONL so it can be checked against the manifest.

  python3 tca_import_snapshot.py tclk-offers-snapshot-<stamp>.jsonl.gz --room tclk-offers --out tclk-offers-snapshot.sqlite

Python 3.9+, standard library only.
"""
import argparse, gzip, hashlib, json, os, sqlite3, sys

SCHEMA = """
CREATE TABLE messages(room TEXT, seq INTEGER, ts TEXT, did TEXT, nick TEXT, nonce TEXT, text TEXT,
                      fetched_at TEXT, sig TEXT, PRIMARY KEY(room, seq));
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("snapshot")
    p.add_argument("--room", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--expect-sha256", default="", help="sha256_decompressed_jsonl from the manifest")
    a = p.parse_args()
    if os.path.exists(a.out):
        sys.exit("%s already exists; remove it first" % a.out)
    c = sqlite3.connect(a.out)
    c.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    h = hashlib.sha256(); n = 0; batch = []
    opener = gzip.open if a.snapshot.endswith(".gz") else open
    with opener(a.snapshot, "rb") as f:
        for line in f:
            h.update(line)
            m = json.loads(line)
            who = m.get("from") or None
            did = who if (who or "").startswith("did:") else None
            nick = None if did else who
            batch.append((a.room, m["seq"], m.get("ts"), did, nick, m.get("nonce"), m.get("text"), None, m.get("sig")))
            n += 1
            if len(batch) >= 50000:
                c.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)", batch); batch = []
                if n % 500000 == 0:
                    print("  %d lines..." % n, file=sys.stderr, flush=True)
    if batch:
        c.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)", batch)
    c.execute("CREATE INDEX m_did ON messages(did)")
    c.commit(); c.close()
    digest = h.hexdigest()
    print("lines imported:", n)
    print("sha256_decompressed_jsonl:", digest)
    if a.expect_sha256:
        ok = digest == a.expect_sha256.lower()
        print("matches manifest:", ok)
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()
