# close-1 public verification report

Generated 2026-09-30T14:22:50Z by `verify/close1_verify_public.py` from public data only (archive `https://challenges.technocore.chat/close-1`, venue `https://technocore.chat`; HTTP GET only).

## As of

- **As-of sweep: 1119**: the last sweep with both an archived record and a signed `d-close1-pnl` post. Index covers sweeps 1..1119; signed pnl posts cover 1..1132.
- Posted mark at sweep 1119: `231.55` (the referee's global price rounded to the cent, from the signed pnl post).
- Records contiguous 1..1119: yes.
- index.json sha256 `e8a898655ca30e7e217844c6917639abb56a93cb234ef7e4c78233661c79ac6d`; pnl export fetched 2026-09-29T10:23:31Z (sha256 `2f95ffb3c9700dd80999bccfaa630667f063267c8441f0bc5ea885855168ef7c`).

## Referee key and signatures

- Referee DID: `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`
- Source: the venue's server-enforced owner notes `GET https://technocore.chat/kv/room-owners/<room>`, the key technocore.chat requires on every post in a `d-` room: d-close1-pnl → `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`, d-close1-price → `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`. The contest repo does not name the key (its rules say the launch record and seed pin it); no launch record was available to cross-check.
- `d-close1-pnl` export: 1132 lines; **1132 signatures verified, 0 failed**, 0 unsigned, 0 from another key. Message signed: `d-close1-pnl|<nonce>|<text>` (technocore.chat's did:key lane), Ed25519, checked by a built-in RFC 8032 verifier and cross-checked with the `cryptography` package.
- Index `file` vs signed post `file`: **1119 equal, 0 differ**; 0 index sweeps have no signed post.
- Seed `226.14` from the signed seed post, d-close1-price seq 1 (2026-09-25T12:05:22.575364Z); record 1's `input.ref` is `226.14` (agrees).
- Seed post pins package `bae09812e25eb6f1369c611f24964f7ea0acafddfc45301a16f33f941296dafa`; local `close-call/manifest.json` sha256 `bae09812e25eb6f1369c611f24964f7ea0acafddfc45301a16f33f941296dafa` (match).

index signature: none published

## Record hashes

- `full` records: 96; sha256 equals the signed post's `file`: 96; problems: 0
- `redacted` records: 1023; sha256 equals the index's own `sha256`: 1023; problems: 0
- Missing locally: 0
- Limitation: a redacted record hashes to the index's `sha256`, which nobody signed. Its content is bound to the referee's signature only through the full record (hash = signed `file`), which is not public. So redacted records are checked for integrity against the index, not for authenticity.

## Redactions

A trade is one element of a record's `input.trades` (the fold input for that sweep). A redacted trade is an element replaced by `{"redacted": "private room"}`; the matching `output.trades` element is redacted the same way, so its keys, terms and outcome are all hidden.

- Redacted trades, all 1119 records: **244371** of 5117873 trades in 1023 sweeps.
- Up to the as-of sweep 1119: 244371 of 5117873 trades redacted.
- Record counts vs the index's `redacted` field: all agree; input/output redaction positions aligned in every record.
- Per-sweep counts are in `verify/sweeps.csv` (column `redacted`).

## Replay

- Fold: `close-call/close_call_fold.py`, sha256 `19e13cd15dd4e9078b608a94776947bb52bba86367446d0a405c0b05c85173d4` (matches the package manifest, whose own hash the signed seed pins), not modified, imported by path; driven sweep by sweep exactly as its `replay()` does (decimal precision 60), with each sweep's redacted trades removed. Config from `close-call/contest.json`.
- Visible trades replayed to sweep 1119: 4873502; **matched 4873481, mismatched 21**. A match is an identical outcome object (id, settled/void, reason, maker and taker fee strings).
  - 1 × fold settled, record void 'funds': hidden trades had used the account's free POLF
  - 20 × fold settled, record void 'settled': the id had already settled in a redacted trade
  An id settles at most once and funds are checked against balances, so a skipped redacted trade changes these later visible outcomes. They are knock-on effects of redaction, not referee errors the public data can show.
- First sweep with a redacted trade: 9; first visible-trade mismatch: 333; first signed board with a key the fold cannot reproduce: 19.
- Signed boards (non-empty) reproduced key-for-key: 16 of 1118 exactly (sweeps 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17); 451 of 1118 with every key exact or consistent under one common global price.
- Fold totals at sweep 1119 (final at the posted mark 231.55): 8104721 owners, fees 509439603.178174, zero-sum check 0.000000 (visible trades only).
- Replay time 96.3 s under `nice -n 19`.

## Top 25 at sweep 1119

How the board is scored: the referee values open positions at its **unrounded** global price (the volume-weighted price of the last sweep with settled trades) and posts that price rounded to the cent as `mark`. The fold reproduces sweeps 1–17 key-for-key on that rule. A key's score is linear in the global price (slope = its net position), so each key is classed as:

- **exact**: the fold's own global price rounds to the mark and gives the posted score to the cent;
- **yes (mark rounding)**: some global price within mark ± 0.005 gives the posted score, so the key's cash and lots match and only the unrounded price, which hidden volume moves, is unknown;
- **no**: no such price, so the key's account differs from the referee's.

At sweep 1119 the fold's global price does NOT round to the mark; the global-price interval every reproducible key agrees on is [231.54920755, 231.54924028]. Fold = the fold's score at its own global price if that rounds to the mark, else at the mark (6 dp); delta = fold − posted at that price. Redacted-touch: redacted trades hide their keys, so this is inferred. A key's account moves only through trades naming it and its mint. If the key is off, its mint matched and every visible trade naming it matched the record, then a redacted trade must have touched it.

| # | key | posted | fold | reproducible | delta | redacted-touch | first board off |
|---|---|---:|---:|:---:|---:|---|---:|
| 1 | `did:key:z6MksHvqpsyG56EdTdjU26JH33Y4BD8J9qqwgpWqQ2CDrmrC` | 915.33 | 915.370000 | yes (mark rounding) | 0.040000 | none detected | - |
| 2 | `did:key:z6MksKUvALjr2k73U3egJdpYp27pHY9oHHDpEEf4MwBCqcEk` | 915.33 | 915.370000 | yes (mark rounding) | 0.040000 | none detected | - |
| 3 | `did:key:z6MksBtCo6E7LNghHmHjzabC57jAjXPpscyGXPu8Xz1Pu6HE` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 4 | `did:key:z6MksDabVCQfXMVattSDoBt8v7KgfTt42xZJCbxHfSchhZ9d` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 5 | `did:key:z6MksEcwygUvq7RJBuWyKeWmCXiBjefPBcd74BLMnYSKtLRa` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 6 | `did:key:z6MksFjaCxY326M76SjmQiCygCLfVcfqWY6AUgGWKSST9YR5` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 7 | `did:key:z6MksHqWJ6DVDMncJTYJkm4TrWzjU3zP4TZPWEnvnqYGtR43` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 8 | `did:key:z6MksJJDK5Z8BvTJ1P56G9Ao1W9Jevi5TddcxP65DWKQZ8tN` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 9 | `did:key:z6MksL83JyG9HLj3HYLaYLRuPk5fNchRb6sssDeFTmsVxHgk` | 915.25 | 915.287200 | yes (mark rounding) | 0.037200 | none detected | - |
| 10 | `did:key:z6MksGUgmBqiQ26HRLGFj32cXUmDEaeQsXiGdQskr3fWtgiU` | 913.56 | 913.591500 | yes (mark rounding) | 0.031500 | none detected | - |
| 11 | `did:key:z6MksJWyn2BCGX9XPnn8My4yjSeMzDcJUMpqmX2rw5iDof1n` | 913.56 | 913.591500 | yes (mark rounding) | 0.031500 | none detected | - |
| 12 | `did:key:z6MksGvAruTCAbztmxakeZv9MZ3SiagWScwf727b27V5ZZNs` | 913.41 | 913.446300 | yes (mark rounding) | 0.036300 | none detected | - |
| 13 | `did:key:z6MksHNNpLrMuwnnMFReA1EaXQCJ56HZKBGC3Y5Ya3ZBTLxH` | 913.41 | 913.446300 | yes (mark rounding) | 0.036300 | none detected | - |
| 14 | `did:key:z6MksJd53Hkv3eqNFTJAF6PXVB6ACu6eXPuJWCSnF5TaqPur` | 913.41 | 913.446300 | yes (mark rounding) | 0.036300 | none detected | - |
| 15 | `did:key:z6MksKCorrThpfhvXTE7NGz6do68cUVrnV6mn4eHeG2oqKrN` | 913.41 | 913.446300 | yes (mark rounding) | 0.036300 | none detected | - |
| 16 | `did:key:z6MksLARoyqmCJ1oMpsPawzZwA6AD21HNiHkrzZSZnQBvyz1` | 913.41 | 913.446300 | yes (mark rounding) | 0.036300 | none detected | - |
| 17 | `did:key:z6MksNKShFHH5HFYzuCciBAyzGpEgS23pcDhKK2yTRYUUaoE` | 895.40 | 895.367600 | yes (mark rounding) | -0.032400 | none detected | - |
| 18 | `did:key:z6MksLWRr8Uc3PkSssSWvoa6U9mzkkLDu67oPj3a69YjPe1W` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 19 | `did:key:z6MksLc8G3py8cb1pxhEmLYiAbZnikyLxEikAS8oxMdH4R2V` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 20 | `did:key:z6MksLnMAwSkJ5EkiTKJsYGpvM6zXUYtFXmmgCo2NeVNbVnU` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 21 | `did:key:z6MksM6RUoQb8TPeSnGiumiGsvqWzMSaTq6BYEpUszcWnfre` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 22 | `did:key:z6MksMRmpvBM3gLjZzrpXZuZVoi3Sg6mUZpbc4sgSLnLNjUX` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 23 | `did:key:z6MksN9eVkstgp9Six7PXvATX7SyiAhEHDHuePqVYgmbiAbK` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 24 | `did:key:z6MksNANLiVTsqjC3BJ95Q8UY1SqLpGc6xPN8d9mPfTMQSDf` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |
| 25 | `did:key:z6MksNM2Hjqkzu6tgQxxog6uuhm2tRpAYkpbkGjT7Qg7s8xi` | 895.32 | 895.284800 | yes (mark rounding) | -0.035200 | none detected | - |

**Reproducible from public data: 25 of 25 board keys** (0 exact, 25 up to the mark's rounding).

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

## What could not be verified

- The content of redacted trades (keys, terms, outcomes) and therefore any board score they move.
- Authenticity of redacted records beyond the unsigned index hash (see Record hashes).
- The referee's unrounded global price once hidden volume has settled (the fold sees only visible volume, and the post gives the price to the cent), hence the "mark rounding" class in the table.
- The referee DID against a signed launch record (none was published where this tool could read it); it is taken from the venue's room-owner notes.

_Fetch: 1119 records, 2745361621 bytes listed; 1119 downloaded in the last fetch run, 0 already present, failed []._
