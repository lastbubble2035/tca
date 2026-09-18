#!/usr/bin/env python3
"""tclk_structure.py — structural analysis of tclk deal coordination in /r/tclk-offers.

Reads a tca archive (see ../tca.py) and reports structural observations about
who offers, who accepts, and which structures repeat. Emits JSON on stdout.

    python3 tclk_structure.py --db ~/.tc-archive.sqlite --since 2026-08-27 --until 2026-09-18 > out.json

What it measures, and what each measurement does NOT establish:

  self_accepts        one key accepting its own offer. Evidence of a single key on both
                      sides of a deal. Does not establish who operates the key.
  reciprocal_pairs    payer->payee pairs that recur. A recurring pair is a structural
                      pattern; it is not proof that the two keys share an operator.
  hub_identities      one key appearing across many distinct counterparties. Hub-and-spoke
                      topology. Again structural, not attributive.
  statement_reuse     the same hash-lock statement posted by 2+ distinct DIDs. A per-deal
                      secret's hash should not repeat across unrelated identities, so a
                      repeat is a fingerprint of shared control that survives key rotation.
                      It shows shared control of a secret, not the identity of a controller.
  first_message_lag   share of offers/accepts posted within 60s of that key's first-ever
                      message in the room. Reported whatever the result.

Claims derived from this script should stay structural. Operator attribution is out of scope.
"""
import argparse, collections, hashlib, json, os, sqlite3, sys

def frames(c, room, since, until):
    q = "SELECT seq, ts, did, text FROM messages WHERE room=? AND text LIKE 'tclk1 %'"
    a = [room]
    if since: q += " AND ts >= ?"; a.append(since)
    if until: q += " AND ts <= ?"; a.append(until + "T23:59:59Z")
    for seq, ts, did, text in c.execute(q + " ORDER BY seq", a):
        try: j = json.loads(text[6:])
        except Exception: continue
        if not isinstance(j, dict): continue
        yield seq, ts, did, j

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.path.expanduser("~/.tc-archive.sqlite"))
    p.add_argument("--room", default="tclk-offers")
    p.add_argument("--since"); p.add_argument("--until")
    p.add_argument("--top", type=int, default=10)
    args = p.parse_args()

    c = sqlite3.connect(args.db)
    offers = {}          # offer id -> (did, seq)
    accepts = []         # (offer_did, accept_did, contract)
    first_seen = {}      # did -> first seq in room
    lag = {"offer_within_60s": 0, "offers": 0, "accept_within_60s": 0, "accepts": 0}
    first_ts = {}
    statements = collections.defaultdict(set)   # statement -> dids
    types = collections.Counter()
    window = [None, None]

    for seq, ts, did, j in frames(c, args.room, args.since, args.until):
        if did and did not in first_ts: first_ts[did] = ts
        window[0] = window[0] or ts; window[1] = ts
        t = j.get("type") or "(none)"; types[t] += 1
        def fresh():
            f = first_ts.get(did)
            return bool(f) and (ts[:19] <= f[:19] or (ts > f and _secs(f, ts) <= 60))
        if t == "offer" and j.get("id"):
            offers[str(j["id"]).lower()] = did
            lag["offers"] += 1; lag["offer_within_60s"] += fresh()
        elif t == "accept":
            ref = str(j.get("ref", "")).lower()
            cid = str(j.get("contract", ""))
            accepts.append((offers.get(ref), did, cid))
            lag["accepts"] += 1; lag["accept_within_60s"] += fresh()
            if j.get("statement"): statements[str(j["statement"])].add(did)
        elif t == "lock" and j.get("statement"):
            statements[str(j["statement"])].add(did)

    resolved = [(a, b) for a, b, _ in accepts if a]
    pair = collections.Counter(resolved)
    self_accepts = sum(n for (a, b), n in pair.items() if a == b)
    counterparties = collections.defaultdict(set)
    for a, b in resolved:
        if a != b: counterparties[a].add(b); counterparties[b].add(a)
    reuse = {s: sorted(d) for s, d in statements.items() if len(d) > 1}

    out = {
        "script_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
        "invocation": " ".join(["python3", os.path.basename(__file__)] + sys.argv[1:]),
        "room": args.room,
        "window_observed": {"first_ts": window[0], "last_ts": window[1]},
        "frames_by_type": dict(types.most_common()),
        "accepts_total": len(accepts),
        "accepts_with_matching_offer_in_archive": len(resolved),
        "self_accept_events": self_accepts,
        "distinct_payer_payee_pairs": len(pair),
        "top_pairs_by_volume": [n for _, n in pair.most_common(args.top)],
        "identities_on_both_sides": len({a for a, _ in resolved} & {b for _, b in resolved}),
        "hub_identities_top": [
            {"counterparties": len(v)} for _, v in
            sorted(counterparties.items(), key=lambda kv: -len(kv[1]))[:args.top]],
        "statement_reuse_across_dids": len(reuse),
        "statement_reuse_max_dids": max((len(v) for v in reuse.values()), default=0),
        "first_message_lag": lag,
        "caveats": [
            "Structural observations only. None of these counts establishes operator identity.",
            "Distinct keys are free to create; key counts are not operator counts.",
            "Accepts without a matching offer in this archive are excluded from pair analysis.",
        ],
    }
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()

def _secs(a, b):
    from datetime import datetime
    f = "%Y-%m-%dT%H:%M:%S"
    try: return (datetime.strptime(b[:19], f) - datetime.strptime(a[:19], f)).total_seconds()
    except Exception: return 1e9

if __name__ == "__main__":
    main()
