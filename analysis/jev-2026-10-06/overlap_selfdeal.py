#!/usr/bin/env python3
"""Join selfdeal.py's payer/payee dids to the finished 2026-10-06 identity set.

selfdeal.py reads ~/.tc-archive.sqlite with two LIKE scans. Those would hold a
read lock on the live archive for the whole scan, so this runs the same queries
against a close1_common.db_snapshot copy and does not modify selfdeal.py.
"""
import csv
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from close1_common import SnapshotError, db_snapshot  # noqa: E402

ARCHIVE = Path("~/.tc-archive.sqlite").expanduser()
DAY_CSV = os.path.join(HERE, "jev_day_20261006.csv")
IDENT_CSV = os.path.join(HERE, "identities_20261006.csv")
OUT_PATH = os.path.join(HERE, "selfdeal-overlap.txt")


def selfdeal_rows(con):
    """Same queries and prints as ~/Desktop/RV/selfdeal.py. Returns matched triples."""
    offers = {}
    pairs = []
    for did, text in con.execute(
        "SELECT did, text FROM messages WHERE room='tclk-offers' AND text LIKE 'tclk1 %'"
    ):
        try:
            j = json.loads(text[6:])
        except Exception:
            continue
        if j.get("type") == "offer" and j.get("id"):
            offers[str(j["id"]).lower()] = (did, j)
        elif j.get("type") == "accept":
            pairs.append((str(j.get("ref", "")).lower(), did, str(j.get("contract", ""))[2:18].lower()))
    completed = {
        r[0].split("-")[-1]
        for r in con.execute(
            "SELECT room FROM messages WHERE room LIKE 'mb-p-tclk-%' AND text LIKE '%\"type\":\"receipt\"%'"
        )
    }
    matched = [(offers[ref][0], payee, cid) for ref, payee, cid in pairs if ref in offers]
    lines = []
    lines.append("accepts with a matching offer: %d of %d" % (len(matched), len(pairs)))
    lines.append("payer == payee: %d" % sum(1 for m in matched if m[0] == m[1]))
    pc = Counter((p, q) for p, q, _ in matched)
    lines.append("top payer->payee pairs (deals each): %s" % [n for _, n in pc.most_common(5)])
    lines.append(
        "identities on both sides: %d" % len({p for p, _, _ in matched} & {q for _, q, _ in matched})
    )
    done = [m for m in matched if m[2] in completed]
    dc = Counter((p, q) for p, q, _ in done)
    lines.append("completed deals: %d; self-deals: %d" % (len(done), sum(1 for m in done if m[0] == m[1])))
    lines.append("top completing pairs (deals each): %s" % [n for _, n in dc.most_common(5)])
    lines.append("distinct pairs among completed: %d" % len(dc))
    return matched, lines


def load_identities():
    lobby = set()
    ever = set()
    with open(IDENT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            did = row["did"]
            lobby.add(did)
            if row["ever_original"] == "yes":
                ever.add(did)
    return lobby, ever


def count_originals(dids):
    """Original-message counts for dids, plus any still-empty final_kind in the day."""
    csv.field_size_limit(10_000_000)
    counts = {did: 0 for did in dids}
    empty_final = 0
    rows = 0
    with open(DAY_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows += 1
            if row["final_kind"] == "":
                empty_final += 1
                continue
            did = row["did"]
            if did in counts and row["final_kind"] == "original":
                counts[did] += 1
    return counts, empty_final, rows


def main():
    stats = {}
    last_err = None
    for attempt in range(1, 6):
        try:
            print("snapshot attempt %d" % attempt, flush=True)
            with db_snapshot(ARCHIVE, stats=stats) as clone:
                print(
                    "snapshot ok lock_s=%.4f clone_s=%.3f mode=%s size=%s"
                    % (stats.get("lock_s", 0), stats.get("clone_s", 0), stats.get("mode"), stats.get("size")),
                    flush=True,
                )
                con = sqlite3.connect("file:%s?mode=ro" % clone, uri=True)
                try:
                    for sql in (
                        "SELECT did, text FROM messages WHERE room='tclk-offers' AND text LIKE 'tclk1 %'",
                        "SELECT room FROM messages WHERE room LIKE 'mb-p-tclk-%' AND text LIKE '%\"type\":\"receipt\"%'",
                    ):
                        plan = [p[-1] for p in con.execute("EXPLAIN QUERY PLAN " + sql)]
                        print("plan %s" % plan, flush=True)
                    print("running selfdeal queries", flush=True)
                    t0 = time.monotonic()
                    matched, printed = selfdeal_rows(con)
                    print("queries done in %.1fs" % (time.monotonic() - t0), flush=True)
                finally:
                    con.close()
            break
        except SnapshotError as e:
            last_err = e
            print("snapshot failed: %s" % e, flush=True)
            time.sleep(1)
    else:
        print("snapshot failed, not reading the live archive")
        return 1

    for line in printed:
        print(line, flush=True)

    payers = {p for p, _, _ in matched}
    payees = {q for _, q, _ in matched}
    either = payers | payees
    print("loading identities", flush=True)
    lobby, ever = load_identities()
    print("counting originals for selfdeal dids", flush=True)
    orig_counts, empty_final, rows = count_originals(either)

    def in_lobby(group):
        return group & lobby

    def ever_n(group):
        return sum(1 for did in group if did in ever)

    def two_n(group):
        return sum(1 for did in group if orig_counts.get(did, 0) >= 2)

    labels_note = (
        "labels: complete day; every message has a final_kind"
        if empty_final == 0
        else "labels: PARTIAL day; overlap uses only scored final_kind and does not treat unscored messages as original"
    )
    lines = []
    lines.append("selfdeal source: ~/Desktop/RV/selfdeal.py queries on a db_snapshot of ~/.tc-archive.sqlite")
    lines.append(labels_note)
    lines.append("day_rows: %d" % rows)
    lines.append("empty_final_kind: %d" % empty_final)
    lines.append("")
    lines.append("--- selfdeal.py ---")
    lines.extend(printed)
    lines.append("")
    lines.append("--- distinct selfdeal dids ---")
    lines.append("payers: %d" % len(payers))
    lines.append("payees: %d" % len(payees))
    lines.append("either: %d" % len(either))
    lines.append("")
    lines.append("--- appear in the lobby day ---")
    lines.append("payers_in_lobby: %d" % len(in_lobby(payers)))
    lines.append("payees_in_lobby: %d" % len(in_lobby(payees)))
    lines.append("either_in_lobby: %d" % len(in_lobby(either)))
    lines.append("")
    lines.append("--- final_kind original among selfdeal dids in the lobby ---")
    lines.append("payers_ever_original: %d" % ever_n(in_lobby(payers)))
    lines.append("payers_original_2plus: %d" % two_n(in_lobby(payers)))
    lines.append("payees_ever_original: %d" % ever_n(in_lobby(payees)))
    lines.append("payees_original_2plus: %d" % two_n(in_lobby(payees)))
    lines.append("either_ever_original: %d" % ever_n(in_lobby(either)))
    lines.append("either_original_2plus: %d" % two_n(in_lobby(either)))
    lines.append("")
    lines.append("--- lobby identities with ever_original that appear in selfdeal ---")
    lines.append("ever_original_in_selfdeal: %d" % len(ever & either))
    text = "\n".join(lines) + "\n"
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(text, flush=True)
    print("wrote %s" % OUT_PATH, flush=True)
    return 0 if last_err is None or matched is not None else 1


if __name__ == "__main__":
    sys.exit(main())
