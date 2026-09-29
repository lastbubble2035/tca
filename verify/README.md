# close-1 public verifier

An independent, read-only check of the close-1 contest (Close Call, one NVDA future on
technocore.chat) that anyone can run from public data. It never posts or signs anything and
issues only HTTP GETs. It reads no private keys, local archives or databases.

## Run

```sh
cd ~/Desktop/RV && /opt/homebrew/bin/python3 verify/close1_verify_public.py all
```

That fetches (or resumes) the archive, verifies, replays under `nice -n 19` and writes
`verify/REPORT.md`. Progress goes to `verify/progress.log`. A rerun skips records already
present with the right size. It also reuses the cached verify and replay results when none of
their inputs changed; add `--force` to redo them, or `--skip-fetch` to leave the archive alone.
Stages can be run one at a time: `fetch`, `venue`, `verify`, `replay`, `report`.

You need Python 3.10+ (standard library only; the `cryptography` package, if installed, is used to
cross-check the Ed25519 verifier) and a checkout of the contest package next to this folder
(`../close-call`, or pass `--repo`), from
<https://github.com/flop-labs/technocore-close-call-challenge>.

Tests (synthetic fixtures, no network):
`/opt/homebrew/bin/python3 -m unittest discover -s verify/tests -v`

## Data sources

| What | Where | Reads |
|---|---|---|
| Sweep records and their index | `https://challenges.technocore.chat/close-1/index.json` and each listed `path` | 1 + 1,119 (2.75 GB), sequential, at most 2/s |
| Signed pnl posts | `https://technocore.chat/r/d-close1-pnl/export` (raw JSONL, byte-exact) | 1 |
| Referee's signed seed post | `https://technocore.chat/r/d-close1-price/export`, first post | 1 |
| Referee DID | `https://technocore.chat/kv/room-owners/d-close1-pnl` and `.../d-close1-price` | 2 |
| Fold and config | `../close-call/close_call_fold.py`, `contest.json`, `manifest.json` (local checkout) | none |

The venue exports and notes are read once and cached under `verify/venue/` (`venue --refresh` re-reads them).

`challenges.technocore.chat` serves only the static archive: `/r/...` there returns 404. The
rooms are on `technocore.chat`, whose published limits are 600 reads and 300 writes per minute per IP.

## What it checks

1. **Signatures.** Every line of the `d-close1-pnl` export is re-verified as Ed25519 over
   `d-close1-pnl|<nonce>|<text>`, which is technocore.chat's did:key lane (the nonce is kept as its
   digits). The key comes from the referee DID.
2. **Referee DID.** The contest repo does not contain it; its rules say the launch record and seed
   pin it. The tool takes it from the venue's `room-owners` note for the referee's `d-` rooms. That
   is the key technocore.chat itself requires on every post in an owned room. Override with `--referee`.
3. **file fields.** For each sweep, the index's `file` must equal the signed pnl post's `file`.
4. **Hashes.** A `full` record must sha256 to the signed post's `file`. A `redacted` record must
   sha256 to the index's own `sha256`. `records/expected.sha256` lists both, so
   `cd verify/records && shasum -a 256 -c expected.sha256` checks them independently.
5. **Redactions.** A record is `{"input": <the fold's sweep event>, "output": <the fold's sweep result>}`.
   A *trade* is one element of `input.trades`. A *redacted trade* is one replaced by
   `{"redacted": "private room"}`, and the matching `output.trades` element is replaced the same way.
   Counts are checked against the index's `redacted` field.
6. **Replay.** The event stream is: seed (from the referee's first post, the signed seed in
   `d-close1-price`, cross-checked with record 1's `input.ref`), then each record's `input` with its
   redacted trades removed, then a final at the as-of mark. It is written to `verify/cache/events.jsonl`,
   so `python3 close-call/close_call_fold.py verify/cache/events.jsonl --config close-call/contest.json`
   runs the fold's own CLI on it (that needs several GB of RAM). The tool itself imports the fold by
   path, unmodified. It drives `Fold.seed/sweep/final` exactly as `replay()` does (precision 60), but
   one sweep at a time, so each sweep is compared and dropped instead of holding about 5 million
   outcomes in memory.
7. **Comparisons, per sweep.** Each visible trade's fold outcome must equal the record's `output`
   entry, and each signed board's top 25 is checked against the fold's accounts.

## Adaptations (where the data differed from the obvious reading)

- **The board is scored at the unrounded global price.** The pnl post's `mark` is the referee's
  global price (the VWAP of the last sweep with settled trades) rounded to the cent. Scores use
  the unrounded value. Scoring at the posted mark misses keys with large positions by up to
  |position| × 0.005. With the unrounded price the fold reproduces every board key-for-key through
  sweep 17. Once redacted trades settle volume, the unrounded price cannot be recomputed. A key's
  score is linear in it (slope = net position), so a key counts as reproducible if some price within
  mark ± 0.005 gives its posted score. The report also checks that one common price fits every key.
- **Which keys a redacted trade touched is hidden**, because redaction removes the keys. The report
  infers it. A key's account moves only through its mint and trades naming it. If a key cannot be
  reproduced, its mint matched and every visible trade naming it matched the record, then a
  redacted trade must have touched it.
- **Redacted records are bound only to the index.** Their `sha256` comes from the unsigned index;
  only the full record's hash is signed, and full records of redacted sweeps are not public.

## Files

- `close1_verify_public.py`: the tool.
- `records/`: `index.json`, `expected.sha256`, `sweeps/*.json` (full) and `redacted/*.json`.
- `venue/`: the cached exports, room-owner notes and fetch metadata.
- `cache/`: `events.jsonl` (fold input), `expected.jsonl`, `verify.json`, `replay.json`, `fetch.json`.
- `REPORT.md`, `sweeps.csv` (per-sweep hashes, redactions, matches, board classes), `progress.log`.
- `tests/test_verify_public.py`: offline tests.
