#!/usr/bin/env python3
"""sonnet_cluster.py - do several sonnet-2 entries receive ballots as one block?

Tests, for a candidate set of entries against the other leading entries:
  1. minute-level correlation of ballot arrivals (Pearson r, per entry pair)
  2. rotation: in the merged ballot stream of the candidate set, how often consecutive
     ballots switch entry, against the rate independent arrivals would produce
  3. registration timing: when each entry's voting DIDs registered (hour buckets)
  4. first seen: when each entry's voting DIDs first appear anywhere in the archive,
     with the share that first appear between the contest announcement and the identity cutoff
  5. request_id stems per entry, ballots and registrations
  6. DIDs that voted for more than one entry of the candidate set

Structural observations only. No DIDs or handles are printed or written.
Python 3.9+, standard library only.

  python3 sonnet_cluster.py --cluster vngalaxy3,dongqn-s2,abigayle --out snapshots/cluster.json
"""
import argparse, collections, hashlib, json, math, os, re, sqlite3, sys, time

VOTES_ROOM = "mb-sonnet-2-votes"; REG_ROOM = "mb-sonnet-2-registration"


def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=60)


def parse(text):
    if not text or text[0] != "{": return None
    try:
        j = json.loads(text)
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def pearson(a, b):
    n = len(a)
    if n < 3: return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0: return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb), 3)


def stem(rid):
    m = re.match(r"[a-zA-Z]+", str(rid))
    return m.group(0).lower()[:16] if m else "(other)"


def share_top(counter, k=3):
    tot = sum(counter.values()) or 1
    return [[a, n, round(100.0 * n / tot, 1)] for a, n in counter.most_common(k)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--votes-db", default=os.path.expanduser("~/.tc-sonnet-extra.sqlite"))
    p.add_argument("--main-db", default=os.path.expanduser("~/.tc-archive.sqlite"))
    p.add_argument("--cluster", default="vngalaxy3,dongqn-s2,abigayle")
    p.add_argument("--compare", type=int, default=5, help="how many other leading entries to compare against")
    p.add_argument("--deadline", default="2026-09-18T12:00:00Z")
    p.add_argument("--announced", default="2026-09-10T06:38:00Z", help="contest announcement on X")
    p.add_argument("--cutoff", default="2026-09-11T12:00:00Z", help="identity cutoff")
    p.add_argument("--max-dids", type=int, default=3000, help="per-entry cap for archive lookups")
    p.add_argument("--out", default="")
    a = p.parse_args()
    if not os.path.exists(a.votes_db): a.votes_db = a.main_db
    cl = [e.strip() for e in a.cluster.split(",") if e.strip()]
    dl = a.deadline[:19]

    v = ro(a.votes_db)
    vr = v.execute("SELECT seq, ts, did, text FROM messages WHERE room=? ORDER BY seq", (VOTES_ROOM,)).fetchall()
    v.close()
    if not vr: sys.exit("no votes rows")
    ballots = []                                   # (seq, minute, did, entry, rid)
    for seq, ts, did, text in vr:
        j = parse(text)
        if not j or j.get("type") != "sonnet.ballot.v1" or j.get("voter_did") != did: continue
        e = j.get("entry_id")
        if not isinstance(e, str) or not e or (ts or "")[:19] > dl: continue
        ballots.append((seq, (ts or "")[:16], did, e, str(j.get("request_id", ""))))
    last = {}; hist = collections.defaultdict(set)
    for seq, mn, did, e, rid in ballots:
        last[did] = e; hist[did].add(e)
    tally = collections.Counter(last.values())
    others = [e for e, _ in tally.most_common(a.compare + len(cl)) if e not in cl][:a.compare]
    entries = cl + others

    # 1. minute correlation
    minutes = sorted({b[1] for b in ballots})
    idx = {m: i for i, m in enumerate(minutes)}
    series = {e: [0] * len(minutes) for e in entries}
    for seq, mn, did, e, rid in ballots:
        if e in series: series[e][idx[mn]] += 1
    corr = {}
    for i, x in enumerate(entries):
        for y in entries[i + 1:]:
            corr["%s ~ %s" % (x, y)] = pearson(series[x], series[y])
    active = {e: sum(1 for n in series[e] if n) for e in entries}

    # 2. rotation inside the candidate set
    stream = [e for _, _, _, e, _ in ballots if e in cl]
    tot = len(stream)
    switches = sum(1 for i in range(1, tot) if stream[i] != stream[i - 1])
    shares = [stream.count(e) / tot for e in cl] if tot else []
    rotation = {"ballots_in_set": tot,
                "switch_rate": round(switches / (tot - 1), 3) if tot > 1 else None,
                "switch_rate_if_independent": round(1 - sum(s * s for s in shares), 3) if tot else None}

    # 5a. ballot request_id stems
    bstems = {e: collections.Counter() for e in entries}
    for _, _, did, e, rid in ballots:
        if e in bstems: bstems[e][stem(rid)] += 1

    # 3, 4, 5b. archive lookups for each entry's voting DIDs
    reg_hours = {}; first_seen = {}; rstems = {}
    if os.path.exists(a.main_db):
        m = ro(a.main_db)
        for e in entries:
            dids = sorted(d for d, x in last.items() if x == e)[:a.max_dids]
            rh = collections.Counter(); fs = collections.Counter(); rs = collections.Counter()
            window = pre = post = unseen = 0
            for d in dids:
                rows = m.execute("SELECT room, ts, text FROM messages INDEXED BY m_did WHERE did=? "
                                 "ORDER BY rowid LIMIT 400", (d,)).fetchall()
                if not rows:
                    unseen += 1; continue
                t0 = min((r[1] or "") for r in rows)
                fs[t0[:10]] += 1
                if t0[:19] < a.announced[:19]: pre += 1
                elif t0[:19] <= a.cutoff[:19]: window += 1
                else: post += 1
                for room, ts, text in rows:
                    if room != REG_ROOM: continue
                    j = parse(text)
                    if j and j.get("type") == "sonnet.register.v1":
                        rh[(ts or "")[:13]] += 1; rs[stem(j.get("request_id", ""))] += 1
                        break
            n = max(1, len(dids) - unseen)
            reg_hours[e] = share_top(rh, 3); rstems[e] = share_top(rs, 3)
            first_seen[e] = {"dids_checked": len(dids), "not_in_archive": unseen,
                             "first_seen_before_announcement_pct": round(100.0 * pre / n, 1),
                             "first_seen_between_announcement_and_cutoff_pct": round(100.0 * window / n, 1),
                             "first_seen_after_cutoff_pct": round(100.0 * post / n, 1),
                             "top_first_seen_days": share_top(fs, 3)}
        m.close()

    # 6. cross-voting inside the set
    cross = sum(1 for d, s in hist.items() if len(s & set(cl)) > 1)

    out = {
        "script_sha256": hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest(),
        "candidate_set": cl, "compared_against": others,
        "window": {"first_ts": vr[0][1], "last_ts": vr[-1][1], "minutes": len(minutes), "ballots": len(ballots)},
        "last_ballot_tally": {e: tally.get(e, 0) for e in entries},
        "active_minutes": active,
        "minute_correlation": corr,
        "rotation_in_candidate_set": rotation,
        "dids_voting_for_more_than_one_entry_in_set": cross,
        "ballot_request_id_stems_top3": {e: share_top(bstems[e]) for e in entries},
        "registration_request_id_stems_top3": rstems,
        "registration_hour_top3": reg_hours,
        "first_seen_in_archive": first_seen,
        "caveats": [
            "The votes room is a ring. Only ballots inside the captured window are tested.",
            "First seen means first message by that DID in this archive, which began 2026-08-28 and holds gaps. "
            "A DID can be older than its first captured message.",
            "Correlation and rotation describe arrival patterns. Operator identity is outside the record.",
        ],
    }

    print("window %s -> %s | %d minutes | %d ballots" % (vr[0][1], vr[-1][1], len(minutes), len(ballots)))
    print("\nlast-ballot tally:", out["last_ballot_tally"])
    print("\nminute correlation (Pearson r):")
    for k, r in corr.items():
        x, y = k.split(" ~ ")
        print("  %-34s %6s%s" % (k, r, "  <- inside set" if x in cl and y in cl else ""))
    print("\nrotation inside set:", rotation)
    print("DIDs voting for more than one entry in the set:", cross)
    for e in entries:
        print("\n[%s]%s" % (e, "  (candidate)" if e in cl else ""))
        print("  ballot request_id stems:", out["ballot_request_id_stems_top3"][e])
        if e in first_seen:
            print("  registration request_id stems:", rstems[e])
            print("  registration hour top3:", reg_hours[e])
            print("  first seen:", first_seen[e])
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=1)
        print("\nwrote", a.out)


if __name__ == "__main__":
    main()
