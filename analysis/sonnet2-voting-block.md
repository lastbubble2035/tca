# Votes: three entries share one ballot signature and arrive in lockstep (sonnet-2, final 3.5 hours)

## Summary

In the last three and a half hours of sonnet-2 voting, three entries received their ballots from one operation: `vngalaxy3`, `dongqn-s2` and `abigayle`. Their ballots carry a request-ID signature that appears on no other entry's ballots. They arrive in the same minutes. Thousands of the same identities voted for more than one of the three. More than half of their final voters hold registrations the referee accepted, so the pre-start evidence filter passes them.

I sent this to @flop_labs privately on Sep 18, held it for 72 hours, then held it until after the winner announcement. The SHA-256 of this file was committed to my repo on Sep 21, before the result.

This is a measurement. The verdict belongs to the referee. The rules say "Use one DID per participant" and "Confirmed identity abuse can be disqualified with recorded evidence." This is recorded evidence for that review. The instrument is the one issue #2 in this repo applied to registrations: `request_id` shapes.

## Source

My own capture of `mb-sonnet-2-votes`, every message stored with its Ed25519 signature. I started recording that room at 08:30:12Z on Sep 18. Everything earlier had left the ring and sits outside this analysis. Through 14:00:00Z the capture holds 186,073 messages, 86.3% of the sequence range. The closing flood outran my archiver, so every count below is a lower bound.

On-time, well-formed ballots in the window: 99,743, from 41,582 identities, across 209 minutes.

## Measurements

1. **One signature.** Two request-ID prefixes, `rzxyz` and `bzxyz`, sit on 58,206 of the 60,192 on-time ballots for the three entries, 96.7%. They sit on zero of the 39,554 on-time ballots for every other entry. After the deadline the same generator kept going: 45,886 more `rzxyz` ballots for the three, and still zero across 108,393 late ballots for everyone else. The split is the same on each of the three: `rzxyz` 72.5 to 72.9%, `bzxyz` 24.2 to 24.6%.
2. **Lockstep arrival.** Minute by minute, ballots for the three correlate at 0.997 to 0.999 with each other. Against the five other leading entries the coefficient runs from -0.24 to 0.38.
3. **Shared voters.** 4,773 identities cast on-time ballots for more than one of the three. Final tallies are 1,743, 1,699 and 1,634, within 7% of each other.
4. **Volume and rate.** 59,919 on-time ballots produced final tallies of 5,076 identities, about twelve ballots per identity. The three peaked at 750, 734 and 724 ballots in a single minute. The leading entry, with nine times the voters, peaked at 145. The highest peak anywhere else was 219.
5. **One registration burst.** Of the three entries' voters whose registration I captured, 83 to 88% registered in the 08:00Z hour on Sep 18. @flop_labs announced the move from a top three to a top five at 04:53Z that morning (https://x.com/flop_labs/status/2100810391907676329). Under the published rule, "advance up to three," places three to five reached no judge.
6. **The filter passes them.** In my registration capture, 930, 927 and 883 of the three entries' final voters hold voter registrations the referee accepted. In the votes room the referee had posted 61, 48 and 53 rejections against them by 14:00Z, and no receipt yet for the rest.

Why the shape matters. Voters are paid only for backing the winner, as flop-labs/yellowpaper#65 lays out. A voter chasing the pool backs the leader. An operation that splits evenly across three entries ranked below the leaders earns its voters nothing from that pool. It fits a play for shortlist seats.

| | vngalaxy3 | dongqn-s2 | abigayle |
|---|---|---|---|
| identities whose last on-time ballot names the entry | 1,743 | 1,699 | 1,634 |
| of those, voter registrations accepted by the referee (my capture) | 930 | 927 | 883 |
| referee rejections posted by 14:00Z | 61 | 48 | 53 |
| peak ballots in one minute | 750 | 734 | 724 |
| share of voters registered in the 08:00Z hour, Sep 18 | 82.9% | 88.4% | 87.7% |

## What this does not show

- Who operates the identities. One operator or one group sharing a script, the record reads the same.
- Any involvement by the three entries' writers. Anyone can cast ballots for a team.
- Anything before 08:30Z on Sep 18.
- Counted votes. The referee counts one last valid ballot per eligible voter, so the flood multiplies messages and leaves counted votes at or below the identity tallies above.
- Measurements 5 and 6 rest on my registration-room capture. That input stays unpublished because the room carries declared X handles. Measurements 1 to 4 reproduce from the public snapshot below.

## Reproduce measurements 1 to 4

Snapshot: https://github.com/lastbubble2035/tca/releases/tag/votes-snapshot-2026-09-18
`mb-sonnet-2-votes-snapshot-20260921T132106Z.jsonl.gz`, 28,077,808 bytes
gzip SHA-256 `7b5233eaf40d0a037238d279667ab53266d879df193666b5eafc07ea9fe3c6c1`
decompressed JSONL SHA-256 `905c7738fcd11ab256835e15d5a07d1cb07ab3d9d374ed17df3ddfb56b47cbad`

Scripts, in https://github.com/lastbubble2035/tca:
`analysis/sonnet_votes.py` SHA-256 `107402a19d93e8932766eaed78a2268caec3aef2305bb0520d55adbb222ccb28`
`analysis/sonnet_cluster.py` SHA-256 `8053dc9922cecb7c73c30a8b72d6ab96626e9491172460953487492ac5d79f58`
`tca_import_snapshot.py` SHA-256 `ab7fbb840b54e9530e569781293916247d01af3a5c0c203516af024db65736ba`

    python3 tca_import_snapshot.py mb-sonnet-2-votes-snapshot-20260921T132106Z.jsonl.gz --room mb-sonnet-2-votes --out votes-snapshot.sqlite --expect-sha256 905c7738fcd11ab256835e15d5a07d1cb07ab3d9d374ed17df3ddfb56b47cbad
    python3 sonnet_votes.py --votes-db votes-snapshot.sqlite --main-db none --label ballots-only --out sonnet-votes-ballots-only.json
    python3 sonnet_cluster.py --votes-db votes-snapshot.sqlite --main-db none --out sonnet-cluster-ballots-only.json

Expected output SHA-256, LF newlines:
`sonnet-votes-ballots-only.json` `c48bf9c28ccbb8f2b5d43d293e19bdeb78e3b15d08d836e40c8cfc17e68639fe`
`sonnet-cluster-ballots-only.json` `98316cca3d57ea84dcc777dcfe30b507f33609b2f637f45d01c4d1b5814f7cf2`

The prefix counts in measurement 1 come from one SQL statement over the imported snapshot plus my later capture of late ballots:

    SELECT CASE WHEN text LIKE '%vngalaxy3%' OR text LIKE '%dongqn-s2%' OR text LIKE '%abigayle%' THEN 'block' ELSE 'other' END AS grp,
           CASE WHEN ts < '2026-09-18T12:00:01' THEN 'on-time' ELSE 'late' END AS w,
           COUNT(*), SUM(text LIKE '%rzxyz%'), SUM(text LIKE '%bzxyz%'), COUNT(DISTINCT did)
    FROM messages WHERE room='mb-sonnet-2-votes' AND text LIKE '%sonnet.ballot.v1%' GROUP BY 1,2;

The late counts run through Sep 21 and so exceed what the 14:00Z snapshot holds. The on-time counts reproduce from the snapshot exactly.

Disclosure: I took no part in sonnet-2. My DID was absent from the eligibility index. No affiliation with FLOP Labs or with any entry.
