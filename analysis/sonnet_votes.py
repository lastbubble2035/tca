#!/usr/bin/env python3
"""sonnet_votes.py - ballot tally and structure for sonnet-2, from tca archives.

Reads ballots and referee receipts from mb-sonnet-2-votes (votes DB) and
registrations and referee receipts from mb-sonnet-2-registration (main DB).
Prints a top-N table under several counting rules and writes a JSON record.

With --entries, every ballot is checked against the contest's accepted entry list, and entries
that never existed are flagged. sonnet-2's lesson: three heavily voted entry IDs were never entries.

Structural observations only. DIDs and X handles are never printed or written.
Python 3.9+. Standard library only.

  python3 sonnet_votes.py                       # defaults below
  python3 sonnet_votes.py --label pre --out snapshots/votes_pre.json
"""
import argparse, collections, hashlib, json, os, re, sqlite3, sys, time

REFEREE = "did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte"  # LAUNCH.md
VOTES_ROOM = "mb-sonnet-2-votes"
REG_ROOM = "mb-sonnet-2-registration"


def rows(path, sql, args=()):
    """Fetch everything at once, then close, to keep the read lock short."""
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=60)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def parse(text):
    if not text or text[0] != "{":
        return None
    try:
        j = json.loads(text)
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def norm_x(u):
    u = (u or "").strip().lower().rstrip("/")
    m = re.search(r"(?:x|twitter)\.com/@?([a-z0-9_]{1,15})", u)
    return m.group(1) if m else ""


def top(counter, n):
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--votes-db", default=os.path.expanduser("~/.tc-sonnet-extra.sqlite"))
    p.add_argument("--main-db", default=os.path.expanduser("~/.tc-archive.sqlite"))
    p.add_argument("--referee", default=REFEREE)
    p.add_argument("--deadline", default="2026-09-18T12:00:00Z")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--label", default="")
    p.add_argument("--entries", default="", help="standings.json (keys of 'totals' plus 'ruled_ineligible') or a text file, "
                                                 "one accepted entry id per line; ballots naming anything else are flagged")
    p.add_argument("--out", default="")
    a = p.parse_args()
    known = None
    if a.entries:
        raw = open(a.entries, encoding="utf-8").read()
        try:
            j = json.loads(raw)
            known = set(j.get("totals", {}).keys()) | set(j.get("ruled_ineligible", []))
        except ValueError:
            known = {l.strip() for l in raw.splitlines() if l.strip()}
    dl = a.deadline[:19]
    if not os.path.exists(a.votes_db):          # votes archived in the main DB instead
        print("votes DB %s not found, reading votes from %s" % (a.votes_db, a.main_db))
        a.votes_db = a.main_db

    # ---------- votes room
    vr = rows(a.votes_db, "SELECT seq, ts, did, text FROM messages WHERE room=? ORDER BY seq", (VOTES_ROOM,))
    if not vr:
        sys.exit("no rows for %s in %s" % (VOTES_ROOM, a.votes_db))
    lo, hi = vr[0][0], vr[-1][0]
    ballots_seen = malformed = late = 0
    last = {}                                   # did -> (seq, ts, entry_id, request_id)
    all_ballots = collections.defaultdict(list)  # did -> [(seq, entry, rid)]
    rcpt = {}                                   # (did, request_id) -> status
    rcpt_status = collections.Counter(); rcpt_reason = collections.Counter()
    per_minute = collections.defaultdict(collections.Counter)  # entry -> minute -> n
    stems = collections.Counter()
    for seq, ts, did, text in vr:
        j = parse(text)
        if not j:
            continue
        t = j.get("type")
        if t == "sonnet.ballot.v1" and j.get("contest_id") == "sonnet-2":
            ballots_seen += 1
            entry = j.get("entry_id"); rid = str(j.get("request_id", ""))
            if not did or j.get("voter_did") != did or not isinstance(entry, str) or not entry:
                malformed += 1; continue
            if (ts or "")[:19] > dl:
                late += 1; continue
            last[did] = (seq, ts, entry, rid)
            all_ballots[did].append((seq, entry, rid))
            per_minute[entry][(ts or "")[:16]] += 1
            m = re.match(r"([a-zA-Z]+)", rid)
            stems[m.group(1).lower() if m else "(none)"] += 1
        elif t == "sonnet.receipts.v1" and did == a.referee:        # batched: one status, many ballots
            st = j.get("status"); why = j.get("reason") or j.get("note") or ""
            for r in (j.get("receipts") or []):
                if isinstance(r, dict) and r.get("sender_did"):
                    rcpt[(r["sender_did"], str(r.get("request_id", "")))] = st
                    rcpt_status[str(st)] += 1; rcpt_reason[(str(st), why)] += 1
        elif t == "sonnet.receipt.v1" and did == a.referee:         # single
            who = j.get("participant_did") or j.get("voter_did") or j.get("sender_did")
            rid = j.get("request_id") or (j.get("in_reply_to") or {}).get("request_id") or ""
            st = j.get("status")
            rcpt_status[str(st)] += 1; rcpt_reason[(str(st), j.get("reason") or j.get("note") or "")] += 1
            if who:
                rcpt[(who, str(rid))] = st

    # effective ballot under ballot receipts: last ballot whose receipt is accepted
    eff_rcpt = {}
    if rcpt:
        for did, bl in all_ballots.items():
            for seq, entry, rid in reversed(bl):
                if rcpt.get((did, rid)) == "accepted":
                    eff_rcpt[did] = entry; break

    last_status = {d: rcpt.get((d, last[d][3])) for d in last}     # status of each DID's last ballot

    # ---------- registration room (optional)
    role = {}; xacct = {}; reg_ok = set(); have_main = os.path.exists(a.main_db)
    reg_batches = collections.Counter()
    if have_main:
        rr = rows(a.main_db,
                  "SELECT did, text FROM messages WHERE room=? AND "
                  "(text LIKE '%sonnet.register.v1%' OR text LIKE '%sonnet.receipt.v1%' "
                  "OR text LIKE '%sonnet.receipts.v1%') ORDER BY seq", (REG_ROOM,))
        for did, text in rr:
            j = parse(text)
            if not j:
                continue
            if j.get("type") == "sonnet.register.v1":
                role[did] = j.get("role")
                x = norm_x(j.get("x_account_url"))
                if x:
                    xacct[did] = x
            elif j.get("type") == "sonnet.receipt.v1" and did == a.referee:
                who = j.get("participant_did") or j.get("sender_did")
                if who:
                    if j.get("status") == "accepted": reg_ok.add(who)
                    else: reg_ok.discard(who)
            elif j.get("type") == "sonnet.receipts.v1" and did == a.referee:
                reg_batches[str(j.get("status"))] += len(j.get("receipts") or [])
                for r in (j.get("receipts") or []):
                    if isinstance(r, dict) and r.get("sender_did"):
                        if j.get("status") == "accepted": reg_ok.add(r["sender_did"])
                        else: reg_ok.discard(r["sender_did"])
        del rr

    # ---------- tallies
    voters = list(last.keys())
    t_all = collections.Counter(last[d][2] for d in voters)
    t_regv = collections.Counter(last[d][2] for d in voters if role.get(d) == "voter")
    t_regok = collections.Counter(last[d][2] for d in voters if role.get(d) == "voter" and d in reg_ok)
    t_rcpt = collections.Counter(eff_rcpt.values())
    # one vote per declared X account per entry; DIDs with no account count singly
    seen_acct = set(); t_acct = collections.Counter()
    for d in voters:
        e = last[d][2]; x = xacct.get(d)
        key = (x, e) if x else (d, e)
        if key in seen_acct:
            continue
        seen_acct.add(key); t_acct[e] += 1

    acct_dids = collections.defaultdict(set)
    for d in voters:
        if d in xacct:
            acct_dids[xacct[d]].add(d)
    multi = {x for x, s in acct_dids.items() if len(s) > 1}

    entries = {}
    for e, n in t_all.items():
        ds = [d for d in voters if last[d][2] == e]
        blocks = collections.Counter(xacct[d] for d in ds if d in xacct)
        entries[e] = {
            "all_dids": n,
            "registered_voter_role": t_regv.get(e, 0),
            "voter_role_with_accepted_registration": t_regok.get(e, 0),
            "ballot_receipt_accepted": t_rcpt.get(e, 0) if rcpt else None,
            "last_ballot_rejected": sum(1 for d in ds if last_status.get(d) == "rejected"),
            "last_ballot_no_receipt_yet": sum(1 for d in ds if last_status.get(d) is None),
            "one_per_x_account": t_acct.get(e, 0),
            "dids_from_multi_did_accounts": sum(1 for d in ds if xacct.get(d) in multi),
            "largest_single_account_block": max(blocks.values()) if blocks else 0,
            "writer_or_organizer_role": sum(1 for d in ds if role.get(d) in ("writer", "organizer")),
            "no_registration_captured": sum(1 for d in ds if d not in role),
            "peak_ballots_in_one_minute": max(per_minute[e].values()) if per_minute[e] else 0,
            "accepted_entry": (e in known) if known is not None else None,
        }

    def names(c):
        return [e for e, _ in top(c, a.top)]

    out = {
        "label": a.label,
        "script_sha256": hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest(),
        "referee_did": a.referee,
        "deadline": a.deadline,
        "votes_room_window": {"first_seq": lo, "last_seq": hi, "first_ts": vr[0][1], "last_ts": vr[-1][1],
                              "messages_archived": len(vr),
                              "captured_vs_seq_range_pct": round(100.0 * len(vr) / (hi - lo + 1), 1)},
        "ballots": {"seen": ballots_seen, "malformed_or_did_mismatch": malformed, "after_deadline": late,
                    "distinct_voting_dids": len(voters),
                    "dids_that_changed_vote": sum(1 for bl in all_ballots.values() if len({e for _, e, _ in bl}) > 1)},
        "referee_ballot_receipts": {"by_status": dict(rcpt_status), "dids_with_accepted_ballot": len(eff_rcpt),
                                    "by_status_and_reason": [[k[0], k[1], v] for k, v in top(rcpt_reason, 12)]},
        "registration_batched_receipts": dict(reg_batches),
        "registration_accepted_dids": {"total_single_plus_batched": len(reg_ok),
                                       "by_last_declared_role": dict(collections.Counter(str(role.get(d)) for d in reg_ok)),
                                       "registering_dids_captured": len(role)},
        "registration_overlay": {"main_db_read": have_main, "voting_dids_with_voter_role": sum(1 for d in voters if role.get(d) == "voter"),
                                 "voting_dids_with_accepted_registration": sum(1 for d in voters if d in reg_ok),
                                 "voting_dids_declaring_x_account": sum(1 for d in voters if d in xacct),
                                 "x_accounts_with_multiple_voting_dids": len(multi),
                                 "largest_account_voting_dids": max((len(s) for s in acct_dids.values()), default=0)},
        "entries_file": a.entries or None,
        "known_entries": len(known) if known is not None else None,
        "ballots_naming_unknown_entries": (sum(1 for bl in all_ballots.values() for _, e, _ in bl if e not in known)
                                           if known is not None else None),
        "dids_whose_last_ballot_names_unknown_entry": (sum(1 for d in voters if last[d][2] not in known)
                                                       if known is not None else None),
        "request_id_stems_top10": top(stems, 10),
        "top_by_rule": {"all_dids": names(t_all), "registered_voter_role": names(t_regv),
                        "voter_role_with_accepted_registration": names(t_regok),
                        "ballot_receipt_accepted": names(t_rcpt) if rcpt else None,
                        "one_per_x_account": names(t_acct)},
        "entries": dict(sorted(entries.items(), key=lambda kv: -kv[1]["all_dids"])),
        "caveats": [
            "The votes room is a ring. Ballots evicted before capture began are absent, so tallies are lower bounds "
            "and a DID's last captured ballot may differ from its last ballot.",
            "The referee's intake order and eligibility review decide the official count. This is an outside reconstruction.",
            "Shared X account means shared declaration at registration. It shows common control of the declaration, "
            "and it names no operator.",
        ],
    }

    w = out["votes_room_window"]
    print("votes room: %d msgs, seq %d..%d, %s%% of range, %s -> %s" %
          (w["messages_archived"], lo, hi, w["captured_vs_seq_range_pct"], w["first_ts"], w["last_ts"]))
    b = out["ballots"]
    print("ballots seen %d | malformed %d | late %d | distinct voting DIDs %d | changed vote %d" %
          (b["seen"], b["malformed_or_did_mismatch"], b["after_deadline"], b["distinct_voting_dids"], b["dids_that_changed_vote"]))
    print("referee ballot receipts:", dict(rcpt_status) or "none captured")
    for (st, why), n in top(rcpt_reason, 8):
        print("   %-9s %7d  %s" % (st, n, why[:90]))
    if reg_batches:
        print("registration room batched receipts:", dict(reg_batches))
    if have_main:
        print("registration: %d DIDs registered | %d DIDs accepted by referee (single+batched) | by last role %s" %
              (len(role), len(reg_ok), dict(collections.Counter(str(role.get(d)) for d in reg_ok))))
    r = out["registration_overlay"]
    print("voter role %d | accepted registration %d | declare X %d | X accounts with >1 voting DID %d | largest %d" %
          (r["voting_dids_with_voter_role"], r["voting_dids_with_accepted_registration"],
           r["voting_dids_declaring_x_account"], r["x_accounts_with_multiple_voting_dids"], r["largest_account_voting_dids"]))
    if known is not None:
        print("accepted entries known: %d | ballots naming unknown entries: %d | DIDs whose last ballot names one: %d" %
              (len(known), out["ballots_naming_unknown_entries"], out["dids_whose_last_ballot_names_unknown_entry"]))
    hdr = ("entry", "all", "voter", "reg_ok", "rcpt_ok", "rej", "norcpt", "per_acct", "multi", "block", "w/o", "noreg", "peak/min", "entry?")
    print("\n%-24s %7s %7s %7s %7s %7s %7s %8s %6s %6s %5s %7s %8s %6s" % hdr)
    for e, _ in top(t_all, max(a.top, 10)):
        x = entries[e]
        print("%-24s %7d %7d %7d %7s %7d %7d %8d %6d %6d %5d %7d %8d %6s" %
              (e[:24], x["all_dids"], x["registered_voter_role"], x["voter_role_with_accepted_registration"],
               "-" if x["ballot_receipt_accepted"] is None else x["ballot_receipt_accepted"],
               x["last_ballot_rejected"], x["last_ballot_no_receipt_yet"],
               x["one_per_x_account"], x["dids_from_multi_did_accounts"], x["largest_single_account_block"],
               x["writer_or_organizer_role"], x["no_registration_captured"], x["peak_ballots_in_one_minute"],
               "-" if x["accepted_entry"] is None else ("yes" if x["accepted_entry"] else "NO")))
    print("\ntop %d by rule:" % a.top)
    for k, v in out["top_by_rule"].items():
        print("  %-40s %s" % (k, v))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=1)
        print("\nwrote", a.out)


if __name__ == "__main__":
    main()
