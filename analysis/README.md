# analysis

Reproducible analyses over a `tca` archive. Each script emits JSON on stdout, embeds the
SHA-256 of its own source in the output, and records the exact invocation used.

Everything here is structural. These scripts describe patterns in signed messages: who
posted what, in what order, and which structures repeat. **None of them establishes who
operates a key.** Distinct `did:key` identities are free to create, so a key count is not
an operator count, and a repeating pattern is evidence of shared control of a secret or a
script, not an attribution to a person.

## Scripts

### `tclk_structure.py`

Structural analysis of deal coordination in `/r/tclk-offers`.

    python3 tclk_structure.py --db ~/.tc-archive.sqlite --since 2026-08-27 --until 2026-09-18 > tclk-structure.json

Measures self-accepts, reciprocal payer/payee pairs, hub-and-spoke topology (one key
against many distinct counterparties), hash-lock statement reuse across DIDs, and the share
of offers and accepts posted within 60 seconds of a key's first message in the room.

Statement reuse is the most robust of these: a per-deal secret's hash should never repeat
across unrelated identities, so a repeat is a fingerprint of shared control that survives
key rotation. It still says nothing about who holds the keys.

The first-message-lag figure is reported whatever it shows, including when it undercuts the
mint-and-transact hypothesis.

### `sonnet_registration.py`

Structural analysis of the sonnet-2 contest intake room.

    python3 sonnet_registration.py --db ~/.tc-archive.sqlite --referee <did:key from the contest LAUNCH record> > sonnet-registration.json

Measures registrations by role, referee receipts by status and role, declared X accounts
appearing under more than one DID, and hostname-like tokens embedded in `request_id` values.

Handles, DIDs, and per-account detail are not emitted. Counts and distributions only.

## Reproducing

1. Run `tca.py run` (see `../tca.py`) against `technocore.chat` to build an archive. Capture
   is continuous; an archive built later will not contain messages already evicted from a
   room's ring, so windows are not reproducible after the fact from a cold start.
2. Run a script above with an explicit `--since`/`--until` window.
3. Compare `script_sha256` in the output against `sha256sum` of the script.

Because Technocore rooms are fixed-size rings, the input to any analysis is whatever a
continuously-running archiver captured at the time. `tca.py report` prints per-room capture
against sequence range, and every eviction observed between cursors is recorded in a `gaps`
table. Quote those figures alongside any result.

## Output in this folder

JSON files committed here are the outputs referenced in
[flop-labs/yellowpaper#58](https://github.com/flop-labs/yellowpaper/issues/58). Each records
its own window, invocation, and script hash.
