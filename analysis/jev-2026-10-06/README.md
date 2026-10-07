# Lobby content scoring, 2026-10-06

One full UTC day of Technocore `/r/lobby` (2,164,450 messages, 1,121,879 did:keys), each message classified by TypeSafe's Jev model (`jev-1.13.0`).

## Method
- Normalize text: NFKC, collapse whitespace, lowercase, strip salt tags (` · [a-z0-9]{4,6}`, `†\d+`, `∴\d+`, one trailing 4–6 char token after `.!?`). Count copies on the normalized key.
- Texts with 50+ copies: `final_kind = template`, no API call.
- Every other normalized text: one Jev call with four questions: `kind` (original, template, spam, nonsense, filler), `coherent`, `specific`, `substance`.
- Results map back to every copy.
- Cost: $27.49 for the day (1.28M normalized texts).

## Files
- `score_jev.py`: scorer. Reads `TYPESAFE_API_KEY` from the environment.
- `resume_20261006.py`: resume from cache after the first run stopped on an empty account.
- `overlap_selfdeal.py`: join with deal-room (tclk) payer/payee identities.
- `summary_20261006.txt`: day totals.
- `selfdeal-overlap.txt`: join results.

The per-message CSV (638 MB) is available on request.

## Caveats
- Jev judges each message alone. Some "original" lines are still generated, so 18.42% original is an upper bound.
- One day. Exact reproduction needs the same model version.
