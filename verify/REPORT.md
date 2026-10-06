# close-1 public verification report

Generated 2026-10-06T20:35:14Z by `verify/close1_verify_public.py` from public data only (archive `https://challenges.technocore.chat/close-1`, venue `https://technocore.chat`; HTTP GET only).

## As of

- **As-of sweep: 2556**: the last sweep with both an archived record and a signed `d-close1-pnl` post. Index covers sweeps 1..2556; signed pnl posts cover 1..2556.
- Posted mark at sweep 2556: `234.31` (the referee's global price rounded to the cent, from the signed pnl post).
- Records contiguous 1..2556: yes.
- index.json sha256 `b32435257c0fb421e9a716ba69ff97f74b997ef2a74e0a05c4a901b043b16876`; pnl export fetched 2026-10-06T20:35:13Z (sha256 `619582dd6c6d9e2044cae9f0fae7c46bbdeb121284b6b6bc785f745a805a9ac7`).

## Referee key and signatures

- Referee DID: `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`
- Source: the venue's server-enforced owner notes `GET https://technocore.chat/kv/room-owners/<room>`, the key technocore.chat requires on every post in a `d-` room: d-close1-pnl → `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`, d-close1-price → `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`. The contest repo does not name the key (its rules say the launch record and seed pin it); no launch record was available to cross-check.
- `d-close1-pnl` export: 2556 lines; **2556 signatures verified, 0 failed**, 0 unsigned, 0 from another key. Message signed: `d-close1-pnl|<nonce>|<text>` (technocore.chat's did:key lane), Ed25519, checked by a built-in RFC 8032 verifier and cross-checked with the `cryptography` package.
- Index `file` vs signed post `file`: **2556 equal, 0 differ**; 0 index sweeps have no signed post.
- Seed `226.14` from the signed seed post, d-close1-price seq 1 (2026-09-25T12:05:22.575364Z); record 1's `input.ref` is `226.14` (agrees).
- Seed post pins package `bae09812e25eb6f1369c611f24964f7ea0acafddfc45301a16f33f941296dafa`; local `close-call/manifest.json` sha256 `bae09812e25eb6f1369c611f24964f7ea0acafddfc45301a16f33f941296dafa` (match).

index signature: none published

## Record hashes

- `full` records: 101; sha256 equals the signed post's `file`: 101; problems: 0
- `redacted` records: 2455; sha256 equals the index's own `sha256`: 2455; problems: 0
- Missing locally: 0
- Limitation: a redacted record hashes to the index's `sha256`, which nobody signed. Its content is bound to the referee's signature only through the full record (hash = signed `file`), which is not public. So redacted records are checked for integrity against the index, not for authenticity.

## Redactions

A trade is one element of a record's `input.trades` (the fold input for that sweep). A redacted trade is an element replaced by `{"redacted": "private room"}`; the matching `output.trades` element is redacted the same way, so its keys, terms and outcome are all hidden.

- Redacted trades, all 2556 records: **499321** of 15241891 trades in 2455 sweeps.
- Up to the as-of sweep 2556: 499321 of 15241891 trades redacted.
- Record counts vs the index's `redacted` field: all agree; input/output redaction positions aligned in every record.
- Per-sweep counts are in `verify/sweeps.csv` (column `redacted`).

## Replay

- Fold: `close-call/close_call_fold.py`, sha256 `19e13cd15dd4e9078b608a94776947bb52bba86367446d0a405c0b05c85173d4` (matches the package manifest, whose own hash the signed seed pins), not modified, imported by path; driven sweep by sweep exactly as its `replay()` does (decimal precision 60), with each sweep's redacted trades removed. Config from `close-call/contest.json`.
- Visible trades replayed to sweep 2556: 14742570; **matched 14726251, mismatched 16319**. A match is an identical outcome object (id, settled/void, reason, maker and taker fee strings).
  - 65 × fold settled, record void 'funds': hidden trades had used the account's free POLF
  - 28 × fold settled, record void 'settled': the id had already settled in a redacted trade
  - 1 × other: fold void/expired, record void/settled
  - 58 × other: fold void/funds, record settled/None
  - 20 × other: fold void/funds, record void/settled
  - 10 × other: fold void/settled, record settled/None
  - 18 × other: fold void/settled, record void/funds
  An id settles at most once and funds are checked against balances, so a skipped redacted trade changes these later visible outcomes. They are knock-on effects of redaction, not referee errors the public data can show.
- First sweep with a redacted trade: 9; first visible-trade mismatch: 333; first signed board with a key the fold cannot reproduce: 19.
- Signed boards (non-empty) reproduced key-for-key: 16 of 2555 exactly (sweeps 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17); 1298 of 2555 with every key exact or consistent under one common global price.
- Fold totals at sweep 2556 (final at the posted mark 234.31): 18790926 owners, fees 1622059101.734640, zero-sum check 0.000000 (visible trades only).
- Replay time 296.0 s under `nice -n 19`.

## Top 25 at sweep 2556

How the board is scored: the referee values open positions at its **unrounded** global price (the volume-weighted price of the last sweep with settled trades) and posts that price rounded to the cent as `mark`. The fold reproduces sweeps 1–17 key-for-key on that rule. A key's score is linear in the global price (slope = its net position), so each key is classed as:

- **exact**: the fold's own global price rounds to the mark and gives the posted score to the cent;
- **yes (mark rounding)**: some global price within mark ± 0.005 gives the posted score, so the key's cash and lots match and only the unrounded price, which hidden volume moves, is unknown;
- **no**: no such price, so the key's account differs from the referee's.

At sweep 2556 the fold's global price does NOT round to the mark; the global-price interval every reproducible key agrees on is [234.30689270, 234.30691938]. Fold = the fold's score at its own global price if that rounds to the mark, else at the mark (6 dp); delta = fold − posted at that price. Redacted-touch: redacted trades hide their keys, so this is inferred. A key's account moves only through trades naming it and its mint. If the key is off, its mint matched and every visible trade naming it matched the record, then a redacted trade must have touched it.

| # | key | posted | fold | reproducible | delta | redacted-touch | first board off |
|---|---|---:|---:|:---:|---:|---|---:|
| 1 | `did:key:z6MksSsc4ny8HFWD6Jh2PpKdLGZngUsica7bdnbP5xvAsj4m` | 1558.46 | 1558.607900 | yes (mark rounding) | 0.147900 | none detected | - |
| 2 | `did:key:z6MksT96nB2cMDpKbqTqdtRXoL1NnMiP3CFT2pgyKcbR5bn2` | 1442.72 | 1442.573700 | yes (mark rounding) | -0.146300 | none detected | - |
| 3 | `did:key:z6MksTEGCUCfsQncGLne6wFhf4hto4ZuJozbJ9PL4rSuHC3n` | 1323.44 | 1323.300300 | yes (mark rounding) | -0.139700 | none detected | - |
| 4 | `did:key:z6MksPKMgp8PMQsUktYs2EKQhMG8Wsqt5tsZB6iVEVDRVcQo` | 1319.49 | 1319.640000 | yes (mark rounding) | 0.150000 | none detected | - |
| 5 | `did:key:z6MksMRmpvBM3gLjZzrpXZuZVoi3Sg6mUZpbc4sgSLnLNjUX` | 1316.11 | 1316.257200 | yes (mark rounding) | 0.147200 | none detected | - |
| 6 | `did:key:z6MkimMKmJ7LC7D5PQijJmDCdroyLqFCNmUCVE93Gv2jt8Pb` | 1306.90 | 1306.757100 | yes (mark rounding) | -0.142900 | none detected | - |
| 7 | `did:key:z6Mkw8HuAX84pVtptarfWgGB1XtGiBkXSPoCgAMoXWsjCCC9` | 1284.37 | 0.000000 | no | -1284.370000 | yes (inferred: every visible trade on this key matched) | 2040 |
| 8 | `did:key:z6Mkqo9aN6PaJbbM2rc9vdMy4URzH7acQV3Kwaycq7qo7Up6` | 1277.12 | 1277.266600 | yes (mark rounding) | 0.146600 | none detected | - |
| 9 | `did:key:z6MksSVWejuGNK6q2KDdB1f5UeLWbNzQ1A8eBg7kt3VKbdyr` | 1270.89 | 1271.039300 | yes (mark rounding) | 0.149300 | none detected | - |
| 10 | `did:key:z6MksG9LfT7s8ym2LbjFMXAbZzFNN93vbe71CvycvWReiWim` | 1247.38 | 1247.522200 | yes (mark rounding) | 0.142200 | none detected | - |
| 11 | `did:key:z6Mks2yiSD3KrYUje6FyrYUJnFGwBvvTss366o7ZD6ZVBREF` | 1245.90 | 1246.047600 | yes (mark rounding) | 0.147600 | none detected | - |
| 12 | `did:key:z6MksTC6PvCGrdLBq7DVBCv2FVZfjDec2B3HN5EJwWP3RCBD` | 1212.53 | 1212.388300 | yes (mark rounding) | -0.141700 | none detected | - |
| 13 | `did:key:z6MksN9eVkstgp9Six7PXvATX7SyiAhEHDHuePqVYgmbiAbK` | 1202.79 | 1202.650800 | yes (mark rounding) | -0.139200 | none detected | - |
| 14 | `did:key:z6MkwepVsT85tx3DmVAGTqHaQkxRD9wLsQuv4uDP8TrmkQvE` | 1194.35 | 1194.217100 | yes (mark rounding) | -0.132900 | none detected | - |
| 15 | `did:key:z6MkrdcD9j1T78yGcfQcCNXEw7KcMHgqnu3WTbSCAU4XNtcA` | 1194.34 | 1194.484200 | yes (mark rounding) | 0.144200 | none detected | - |
| 16 | `did:key:z6MkqoWpu9rApgdNcfxLaGAmzRQnpcf2gSWjT6o4ysLTq5gE` | 1191.51 | 1191.363600 | yes (mark rounding) | -0.146400 | none detected | - |
| 17 | `did:key:z6MkwekbumrfekbFzCWtfZoSeXym4w1WPTEDzEvh8qCVmNWC` | 1187.82 | 1187.967300 | yes (mark rounding) | 0.147300 | none detected | - |
| 18 | `did:key:z6Mkwf2KTVcAFnpMusB7RcYLri8ZfCiwyYM7CkhWX1CaHiSv` | 1186.85 | 1186.711800 | yes (mark rounding) | -0.138200 | none detected | - |
| 19 | `did:key:z6Mkei8LVrYh4McECgrGS3XuJ8vGkPtFeibDPAXYCbgdceep` | 1182.82 | 1182.685100 | yes (mark rounding) | -0.134900 | none detected | - |
| 20 | `did:key:z6Mkp8bjNkaFXWN7izdEzwrHa63GY7e6LReNKA2r7ob9p8Uw` | 1179.36 | 1179.499800 | yes (mark rounding) | 0.139800 | none detected | - |
| 21 | `did:key:z6Mkf6fdgtBCsMmc6GRKg6v5PHzK3G714qfZXyg953FJu9ws` | 1177.48 | 1177.340700 | yes (mark rounding) | -0.139300 | none detected | - |
| 22 | `did:key:z6Mks3EfUiMwfJpaKm6pMoB3a1dM6tMMzHMHYMudXHELK9gH` | 1176.77 | 1176.632000 | yes (mark rounding) | -0.138000 | none detected | - |
| 23 | `did:key:z6MkimH78G5jbHFxe6UALDfFLWLNCjgSrzr97sHJgKt7CqtX` | 1170.72 | 1170.586400 | yes (mark rounding) | -0.133600 | none detected | - |
| 24 | `did:key:z6MksQBTMxDSDddGBRptahQ7spZp45W4AGj5v4shNWxNbyr5` | 1162.60 | 1162.457400 | yes (mark rounding) | -0.142600 | none detected | - |
| 25 | `did:key:z6Mkk1oQxNvYyH3NBj3FoafSHreSX22AeExoHK4hLQPHocfW` | 1161.03 | 0.000000 | no | -1161.030000 | yes (inferred: every visible trade on this key matched) | 2058 |

**Reproducible from public data: 23 of 25 board keys** (0 exact, 23 up to the mark's rounding).

### First visible-trade mismatches

| sweep | id | fold | record |
|---:|---|---|---|
| 333 | `f1f6d4fen133x12201` | `{"id": "f1f6d4fen133x12201", "maker_fee": "0.22473", "outcome": "settled", "taker_fee": "0.22473"}` | `{"id": "f1f6d4fen133x12201", "outcome": "void", "reason": "settled"}` |
| 333 | `f1f6d4fen133x12202` | `{"id": "f1f6d4fen133x12202", "maker_fee": "0.22473", "outcome": "settled", "taker_fee": "0.22473"}` | `{"id": "f1f6d4fen133x12202", "outcome": "void", "reason": "settled"}` |
| 333 | `f1f6d4fen133x12203` | `{"id": "f1f6d4fen133x12203", "maker_fee": "0.22473", "outcome": "settled", "taker_fee": "0.22473"}` | `{"id": "f1f6d4fen133x12203", "outcome": "void", "reason": "settled"}` |
| 333 | `f1f6d4fen133x12204` | `{"id": "f1f6d4fen133x12204", "maker_fee": "0.22473", "outcome": "settled", "taker_fee": "0.22473"}` | `{"id": "f1f6d4fen133x12204", "outcome": "void", "reason": "settled"}` |
| 333 | `f1f6d4fen133x12205` | `{"id": "f1f6d4fen133x12205", "maker_fee": "0.22473", "outcome": "settled", "taker_fee": "0.22473"}` | `{"id": "f1f6d4fen133x12205", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13378` | `{"id": "f1f6d4fen134x13378", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13378", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13379` | `{"id": "f1f6d4fen134x13379", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13379", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13380` | `{"id": "f1f6d4fen134x13380", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13380", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13381` | `{"id": "f1f6d4fen134x13381", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13381", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13382` | `{"id": "f1f6d4fen134x13382", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13382", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13383` | `{"id": "f1f6d4fen134x13383", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13383", "outcome": "void", "reason": "settled"}` |
| 334 | `f1f6d4fen134x13384` | `{"id": "f1f6d4fen134x13384", "maker_fee": "0.22470", "outcome": "settled", "taker_fee": "0.22470"}` | `{"id": "f1f6d4fen134x13384", "outcome": "void", "reason": "settled"}` |
| 335 | `f1f6d4fen135x14559` | `{"id": "f1f6d4fen135x14559", "maker_fee": "0.22468", "outcome": "settled", "taker_fee": "0.22468"}` | `{"id": "f1f6d4fen135x14559", "outcome": "void", "reason": "settled"}` |
| 335 | `f1f6d4fen135x14560` | `{"id": "f1f6d4fen135x14560", "maker_fee": "0.22468", "outcome": "settled", "taker_fee": "0.22468"}` | `{"id": "f1f6d4fen135x14560", "outcome": "void", "reason": "settled"}` |
| 335 | `f1f6d4fen135x14561` | `{"id": "f1f6d4fen135x14561", "maker_fee": "0.22468", "outcome": "settled", "taker_fee": "0.22468"}` | `{"id": "f1f6d4fen135x14561", "outcome": "void", "reason": "settled"}` |

## Final standings

The lock is sweep 2556 (2026-10-04T09:00:00Z). Settlement is sweep 2568, the sweep whose clock is `2026-10-04T10:00:00Z`: the referee posts the settlement price *S* then, and open contracts settle at it. The final board is the signed `d-close1-pnl` post with `t` = `standings` (that post has no sweep number). If it has not been published, the final board is the earliest signed pnl post at or after sweep 2568. Each of its top 3 keys is checked with the same cent and mark-rounding rule as the top 25.

The official final record verifies (decompressed sha256 `b642411aac2a3e336e97ee19aac9228d5d249b76bd41a56d4535f8be3d2f9d27`). Its top 3 are the final board, the signed `d-close1-pnl` #2557 (`t` = `standings`, 2026-10-04T17:33:04Z).

Venue export: `d-close1-pnl` seq 2557 at 2026-10-04T17:33:04.421681Z, `t` = `standings`, S `234.69`.

| # | key prefix | score | places | fee |
|---:|---|---:|---|---:|
| 1 | `z6MksSsc4ny8` | 1576.916300 | 1 | 2220.9839 |
| 2 | `z6MksT96nB2c` | 1424.740300 | 2 | 2515.8249 |
| 3 | `z6MksPKMgp8P` | 1337.545600 | 3 | 1649.2162 |

## Final record

Official final record, streamed from `https://challenges.technocore.chat/close-1/final/` as `final-record.json.gz.part-00`, `final-record.json.gz.part-01` and `final-record.json.gz.part-02`. Decompressed bytes sha256 `b642411aac2a3e336e97ee19aac9228d5d249b76bd41a56d4535f8be3d2f9d27` (matches `b642411aac2a3e336e97ee19aac9228d5d249b76bd41a56d4535f8be3d2f9d27`). Joined gzip sha256 `569a12495d4b5e2478c422490db1d95ed58ea421dc6039bd85028fa20c3952d0`. Gzip JSON, 18790926 rows. Score field `score`, fee field `fees`.

First three rows. The score is the decimal text from the file, not a rounded float.

| # | key prefix | score | fee |
|---:|---|---:|---:|
| 1 | `z6MksSsc4ny8` | 1576.916300 | 2220.9839 |
| 2 | `z6MksT96nB2c` | 1424.740300 | 2515.8249 |
| 3 | `z6MksPKMgp8P` | 1337.545600 | 1649.2162 |

Top 3 scores match `1576.916300` / `1424.740300` / `1337.545600`.

## What could not be verified

- The content of redacted trades (keys, terms, outcomes) and therefore any board score they move.
- Authenticity of redacted records beyond the unsigned index hash (see Record hashes).
- The referee's unrounded global price once hidden volume has settled (the fold sees only visible volume, and the post gives the price to the cent), hence the "mark rounding" class in the table.
- The referee DID against a signed launch record (none was published where this tool could read it); it is taken from the venue's room-owner notes.

_Fetch: 2556 records, 7615201366 bytes listed; 70 downloaded in the last fetch run, 2486 already present, failed []._
