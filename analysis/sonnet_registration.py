#!/usr/bin/env python3
"""sonnet_registration.py (v2) - structural analysis of the sonnet-2 registration room.

Reads a tca archive (see ../tca.py) and reports what the referee's intake room contains.
Emits JSON on stdout.

    python3 sonnet_registration.py > sonnet-registration.json
    python3 sonnet_registration.py --until 2026-09-18T12:00:00Z > sonnet-registration-close.json

v2 change: the referee publishes receipts in two shapes. Single receipts
(sonnet.receipt.v1, one participant_did) and batched receipts (sonnet.receipts.v1,
one status for a list of sender_did entries). v1 read single receipts only, so it
undercounted accepted registrations by two orders of magnitude. v2 reads both.

What it measures:

  roles                 registrations by declared role (writer / voter / organizer).
  receipts              referee receipts, single and batched, by status and stated reason,
                        matched to the registering DID. Role is taken at receipt time.
  accounts_multi_did    declared X accounts that appear under more than one DID.
                        The contest rules require one identity per participant, so this
                        is a rule-relevant observation. Key control is outside the record.
  request_id_prefixes   leading token of each request_id before the first _ or -.
                        Self-declared naming. Some resemble hostnames.

Handles are never published. Only counts and distributions are emitted.
"""
import argparse, collections, hashlib, json, os, re, sqlite3, sys

REFEREE = "did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte"  # contest LAUNCH record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.path.expanduser("~/.tc-archive.sqlite"))
    p.add_argument("--room", default="mb-sonnet-2-registration")
    p.add_argument("--referee", default=REFEREE, help="referee did:key, per the contest LAUNCH record")
    p.add_argument("--until", default="", help="ignore messages after this UTC time, e.g. 2026-09-18T12:00:00Z")
    args = p.parse_args()
    cut = args.until[:19]

    c = sqlite3.connect(args.db)
    roles = collections.Counter(); regs = {}; status_of = {}
    x_by_did = {}; hosts = collections.Counter()
    single = collections.Counter(); batch_msgs = collections.Counter(); batch_rcpts = collections.Counter()
    reasons = collections.Counter(); role_at_accept = {}
    n = 0; first = last = None

    def receipt(who, st, why):
        status_of[who] = st
        reasons[(str(st), why)] += 1
        if st == "accepted":
            role_at_accept[who] = regs.get(who)
        else:
            role_at_accept.pop(who, None)

    for seq, ts, did, text in c.execute(
            "SELECT seq, ts, did, text FROM messages WHERE room=? ORDER BY seq", (args.room,)):
        if cut and (ts or "")[:19] > cut:
            continue
        n += 1
        if first is None: first = (seq, ts)
        last = (seq, ts)
        if not text or text[0] != "{":
            continue
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
        elif did == args.referee and t == "sonnet.receipt.v1":
            single[str(j.get("status"))] += 1
            who = j.get("participant_did") or j.get("sender_did")
            if who: receipt(who, j.get("status"), j.get("reason") or j.get("note") or "")
        elif did == args.referee and t == "sonnet.receipts.v1":
            st = j.get("status"); why = j.get("reason") or j.get("note") or ""
            batch_msgs[str(st)] += 1
            for r in (j.get("receipts") or []):
                if isinstance(r, dict) and r.get("sender_did"):
                    batch_rcpts[str(st)] += 1
                    receipt(r["sender_did"], st, why)
    if not n:
        sys.exit("no messages archived for room %s" % args.room)

    accepted = {d for d, s in status_of.items() if s == "accepted"}
    by_account = collections.defaultdict(set)
    for d, x in x_by_did.items(): by_account[x].add(d)
    multi = {x: s for x, s in by_account.items() if len(s) > 1}
    home = os.path.expanduser("~")

    out = {
        "script_version": 2,
        "script_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
        "invocation": " ".join(["python3", os.path.basename(__file__)] + [a.replace(home, "~") for a in sys.argv[1:]]),
        "room": args.room,
        "referee_did": args.referee,
        "until": args.until or None,
        "window_observed": {"first_ts": first[1], "last_ts": last[1], "first_seq": first[0], "last_seq": last[0]},
        "messages_archived": n,
        "captured_vs_seq_range_pct": round(100.0 * n / (last[0] - first[0] + 1), 1) if not cut else None,
        "registrations_total": sum(roles.values()),
        "distinct_registering_dids": len(regs),
        "registrations_by_role": {str(k): v for k, v in roles.items()},
        "referee_receipts": {
            "single_messages_by_status": dict(single),
            "batched_messages_by_status": dict(batch_msgs),
            "batched_receipts_by_status": dict(batch_rcpts),
            "dids_receipted": len(status_of),
            "dids_accepted": len(accepted),
            "dids_last_status_rejected": sum(1 for s in status_of.values() if s == "rejected"),
            "by_status_and_reason_top": [[k[0], k[1], v] for k, v in reasons.most_common(12)],
        },
        "accepted_dids_by_role_at_receipt": {str(k): v for k, v in collections.Counter(role_at_accept.values()).items()},
        "acceptance_rate_pct_of_registering_dids": round(100.0 * len(accepted) / max(1, len(regs)), 2),
        "declared_x_accounts": len(by_account),
        "accounts_under_multiple_dids": len(multi),
        "accounts_with_multiple_accepted_dids": sum(1 for s in multi.values() if len(s & accepted) > 1),
        "dids_per_account_distribution": sorted((len(s) for s in multi.values()), reverse=True)[:20],
        "request_id_prefix_tokens": dict(hosts.most_common(10)),
        "caveats": [
            "Counts only. No handles, no DIDs, no per-account detail is emitted here.",
            "One declared account across several keys is a rule-relevant observation. Key control is outside the record.",
            "Receipt counts reflect what the referee published in this room during the window. Holes in the capture "
            "make every count a lower bound, except last-status figures, which a missed later receipt can move either way.",
            "Role 'None' under accepted_dids_by_role_at_receipt means the registration record fell in a capture hole.",
            "v1 of this script read single receipts only. Figures from v1 outputs undercount accepted registrations.",
        ],
    }
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
