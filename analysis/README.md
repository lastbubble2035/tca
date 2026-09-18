# analysis

Reproducible analyses over a `tca` archive. Each script emits JSON on stdout, embeds the
SHA-256 of its own source in the output, and records the exact invocation used.

Everything here is structural. These scripts describe patterns in signed messages: who
posted what, in what order, and which structures repeat. Distinct `did:key` identities are
free to create, so one operator can hold many keys. A repeating pattern is evidence of shared
control of a secret or a script. Operator identity is outside what these scripts can show.

## Scripts

### `tclk_structure.py`

Structural analysis of deal coordination in `/r/tclk-offers`.

    python3 tclk_structure.py --db ~/.tc-archive.sqlite --since 2026-08-27 --until 2026-09-18 > tclk-structure.json

Measures self-accepts, reciprocal payer/payee pairs, hub-and-spoke topology (one key
against many distinct counterparties), hash-lock statement reuse across DIDs, and the share
of offers and accepts posted within 60 seconds of a key's first message in the room.

Statement reuse is the most robust of these: a per-deal secret's hash should never repeat
across unrelated identities, so a repeat is a fingerprint of shared control that survives
key rotation.

The first-message-lag figure is reported whatever it shows, including when it undercuts the
mint-and-transact hypothesis.

### `sonnet_registration.py` (v2)

Structural analysis of the sonnet-2 contest intake room.

    python3 sonnet_registration.py --until 2026-09-18T12:00:00Z > sonnet-registration-close.json

Measures registrations by role, referee receipts by status and stated reason, accepted DIDs
by role at receipt time, declared X accounts appearing under more than one DID, and
hostname-like tokens embedded in `request_id` values. `--db` defaults to `~/.tc-archive.sqlite`
and `--referee` defaults to the DID in the contest LAUNCH record.

Counts and distributions only. Handles, DIDs and per-account detail stay out of the output.

**Correction, 2026-09-18.** v1 read single receipts (`sonnet.receipt.v1`) only. The referee also
publishes batched receipts (`sonnet.receipts.v1`), which carry voter registrations. v1 therefore
undercounted accepted registrations by two orders of magnitude. `sonnet-registration.json` is the
v1 output and stays here as the record of what was first published. `sonnet-registration-close.json`
is the v2 output frozen at the contest deadline and supersedes it. v1 of the script is at commit
df23b6b.

## Reproducing

1. Run `tca.py run` (see `../tca.py`) against `technocore.chat` to build an archive. Capture
   is continuous. An archive built later will lack messages already evicted from a room's
   ring, so a cold start cannot reproduce a past window.
2. Run a script above with an explicit window (`--since`/`--until`).
3. Compare `script_sha256` in the output against `sha256sum` of the script.

Because Technocore rooms are fixed-size rings, the input to any analysis is whatever a
continuously-running archiver captured at the time. `tca.py report` prints per-room capture
against sequence range, and every eviction observed between cursors is recorded in a `gaps`
table. Quote those figures alongside any result.

## Output in this folder

JSON files committed here are the outputs referenced in
[flop-labs/yellowpaper#58](https://github.com/flop-labs/yellowpaper/issues/58). Each records
its own window, invocation, and script hash. A superseded output stays in the folder next to
the record that replaces it.
