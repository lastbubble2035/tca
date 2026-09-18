#!/usr/bin/env python3
"""sonnet_registration.py — structural analysis of the sonnet-2 registration room.

Reads a tca archive (see ../tca.py) and reports what the referee's intake room contains.
Emits JSON on stdout.

    python3 sonnet_registration.py --db ~/.tc-archive.sqlite > sonnet-registration.json

What it measures:

  roles                 registrations by declared role (writer / voter / organizer).
  receipts              referee receipts by status, matched to the registering DID.
  accounts_multi_did    declared X accounts that appear under more than one DID.
                        The contest rules require one identity per participant, so this
                        is a rule-relevant observation. It shows one declared account
                        across several keys; it does not establish who holds the keys.
  request_id_prefixes   leading token of each request_id before the first _ or -.
                        Self-declared naming, nothing more; some resemble hostnames.

Handles are not published. Only counts and distributions are emitted.
"""
import argparse, collections, hashlib, json, os, re, sqlite3, sys

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.path.expanduser("~/.tc-archive.sqlite"))
    p.add_argument("--room", default="mb-sonnet-2-registration")
    p.add_argument("--referee", required=True, help="referee did:key, per the contest LAUNCH record")
    args = p.parse_args()

    c = sqlite3.connect(args.db)
    rows = list(c.execute(
        "SELECT seq, ts, did, text FROM messages WHERE room=? ORDER BY seq", (args.room,)))
    if not rows: sys.exit(f"no messages archived for room {args.room}")

    roles = collections.Counter(); regs = {}; receipts = {}
    x_by_did = {}; hosts = collections.Counter(); statuses = collections.Counter()
    for seq, ts, did, text in rows:
        try: j = json.loads(text)
        except Exception: continue
        if not isinstance(j, dict): continue
        t = j.get("type")
        if t == "sonnet.register.v1":
            roles[j.get("role")] += 1; regs[did] = j.get("role")
            x = (j.get("x_account_url") or "").lower().rstrip("/")
            if x: x_by_did[did] = x
            m = re.match(r"(?:reg|register)-([a-z][a-z0-9]*)[_-]", str(j.get("request_id", "")))
            if m: hosts[m.group(1)] += 1
        elif t == "sonnet.receipt.v1" and did == args.referee:
            p_did = j.get("participant_did")
            if p_did: receipts[p_did] = j.get("status")
            statuses[j.get("status")] += 1

    accepted = {d for d, s in receipts.items() if s == "accepted"}
    by_account = collections.defaultdict(set)
    for d, x in x_by_did.items(): by_account[x].add(d)
    multi = {x: s for x, s in by_account.items() if len(s) > 1}

    out = {
        "script_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
        "invocation": " ".join(["python3", os.path.basename(__file__)] + sys.argv[1:]),
        "room": args.room,
        "referee_did": args.referee,
        "window_observed": {"first_ts": rows[0][1], "last_ts": rows[-1][1],
                             "first_seq": rows[0][0], "last_seq": rows[-1][0]},
        "messages_archived": len(rows),
        "registrations_total": sum(roles.values()),
        "distinct_registering_dids": len(regs),
        "registrations_by_role": {str(k): v for k, v in roles.items()},
        "referee_receipts": {"dids_receipted": len(receipts), "by_status": {str(k): v for k, v in statuses.items()}},
        "receipts_by_role": {str(r): sum(1 for d, rr in regs.items() if rr == r and d in receipts)
                              for r in roles},
        "declared_x_accounts": len(by_account),
        "accounts_under_multiple_dids": len(multi),
        "accounts_with_multiple_accepted_dids": sum(1 for s in multi.values() if len(s & accepted) > 1),
        "dids_per_account_distribution": sorted((len(s) for s in multi.values()), reverse=True)[:20],
        "request_id_prefix_tokens": dict(hosts.most_common(10)),
        "caveats": [
            "Counts only. No handles, no DIDs, no per-account detail is emitted here.",
            "One declared account across several keys is a rule-relevant observation, "
            "not proof of who controls the keys.",
            "Receipt counts reflect what the referee published in this room during the window.",
        ],
    }
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()

if __name__ == "__main__":
    main()
