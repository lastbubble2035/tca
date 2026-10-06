"""Offline tests for close1_verify_public.py on tiny synthetic fixtures. No network, nothing outside verify/.

    python3 -m unittest discover -s verify/tests -v
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import close1_verify_public as V  # noqa: E402

REPO = HERE.parent.parent / "close-call"


# ---- test-only Ed25519 signing (RFC 8032), built on the verifier's own curve arithmetic

def _expand(seed: bytes):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _compress(pt) -> bytes:
    zi = pow(pt[2], V._P - 2, V._P)
    x, y = pt[0] * zi % V._P, pt[1] * zi % V._P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def public(seed: bytes) -> bytes:
    return _compress(V._mul(_expand(seed)[0], V._G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    A = _compress(V._mul(a, V._G))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % V._L
    R = _compress(V._mul(r, V._G))
    h = int.from_bytes(hashlib.sha512(R + A + msg).digest(), "little") % V._L
    return R + ((r + h * a) % V._L).to_bytes(32, "little")


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = V._B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def did_of(seed: bytes) -> str:
    return "did:key:z" + b58encode(b"\xed\x01" + public(seed))


def b64u(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def fake_getter(served: dict, requested: list):
    """A Getter stand-in serving `served[url]` (200) and 404 for anything else."""
    class FakeGetter:
        def __init__(self, *_, **__):
            self.bytes = self.requests = 0

        def get(self, url, dest=None, timeout=0):
            requested.append(url)
            self.requests += 1
            if url not in served:
                return 404, b"Not Found", {}
            return 200, served[url], {}
    return FakeGetter


def export_line(seq: int, seed: bytes, room: str, nonce: int, obj, corrupt: bool = False) -> str:
    text = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    sig = sign(seed, f"{room}|{nonce}|{text}".encode())
    if corrupt:
        sig = sig[:10] + bytes([sig[10] ^ 1]) + sig[11:]
    return json.dumps({"seq": seq, "ts": f"2026-09-25T12:{seq:02d}:00Z", "from": did_of(seed), "text": text,
                       "nonce": nonce, "sig": b64u(sig)})


class Ed25519Tests(unittest.TestCase):
    def test_rfc8032_vector_1(self):
        pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e"
                            "39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertTrue(V.ed25519_verify(pub, b"", sig))
        self.assertFalse(V.ed25519_verify(pub, b"x", sig))
        self.assertFalse(V.ed25519_verify(pub, b"", sig[:-1] + bytes([sig[-1] ^ 1])))

    def test_did_key_roundtrip_and_lane_message(self):
        seed = bytes([7]) * 32
        did = did_of(seed)
        self.assertRegex(did, V.DID_RE)
        self.assertEqual(V.did_public_key(did), public(seed))
        sig = b64u(sign(seed, b"d-close1-pnl|5|{}"))
        ours, theirs = V.verify_sig(did, "d-close1-pnl|5|{}", sig)
        self.assertTrue(ours)
        self.assertIn(theirs, (True, None))
        self.assertFalse(V.verify_sig(did, "d-close1-pnl|6|{}", sig)[0])
        self.assertFalse(V.verify_sig(did_of(bytes([8]) * 32), "d-close1-pnl|5|{}", sig)[0])


class PipelineTests(unittest.TestCase):
    """Four sweeps. 1: full, clean. 2: redacted (one trade between B and C hidden). 3: full, but the
    local file was altered. 4: full, but the signed post names a different file than the index.
    Plus a pnl post with a broken signature and one from a foreign key."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="scratch-", dir=HERE))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        for name, sub in (("RECORDS", "records"), ("VENUE_DIR", "venue"), ("CACHE", "cache")):
            setattr(V, name, cls.tmp / sub)
        V.PROGRESS, V.REPORT, V.SWEEPS_CSV = cls.tmp / "progress.log", cls.tmp / "REPORT.md", cls.tmp / "sweeps.csv"
        cls.ref_seed, cls.other = bytes([1]) * 32, bytes([9]) * 32
        cls.referee = did_of(cls.ref_seed)
        A, B, C = (did_of(bytes([i]) * 32) for i in (2, 3, 4))
        cls.A, cls.B, cls.C = A, B, C
        t1 = {"id": "t1", "maker": A, "side": "buy", "qty": "1", "px": "100.00", "taker": "any", "until": 9,
              "countersigner": B}
        t2 = {"id": "t2", "maker": C, "side": "buy", "qty": "2", "px": "101.00", "taker": "any", "until": 9,
              "countersigner": B}
        inputs = [
            {"close": "100.00", "n": 1, "owners": [A, B, C], "ref": "100.00", "t": "sweep", "trades": []},
            {"close": "100.50", "n": 2, "owners": [], "ref": "100.00", "t": "sweep", "trades": [t1, t2]},
            {"close": "101.00", "n": 3, "owners": [], "ref": "100.50", "t": "sweep", "trades": []},
            {"close": "101.00", "n": 4, "owners": [], "ref": "101.00", "t": "sweep", "trades": []},
        ]
        fold_mod = V.load_fold(REPO)
        fold = fold_mod.Fold()
        records, boards, marks = {}, {}, {}
        with localcontext() as ctx:                 # the referee's board: scores at the unrounded global price
            ctx.prec = 60
            fold.seed("100.00")
            for inp in inputs:
                out = fold.sweep(inp["n"], inp["ref"], inp["close"], inp["owners"], inp["trades"])
                records[inp["n"]] = {"input": inp, "output": out}
                g = fold.global_px
                marks[inp["n"]] = str(V.cents(g))
                sc = {k: a.value_at(g) - fold.mint for k, a in fold.accounts.items()}
                boards[inp["n"]] = [[k, str(V.cents(sc[k]))] for k in sorted(sc, key=lambda k: (-sc[k], k))][:25]
        cls.boards = boards
        enc = lambda o: json.dumps(o, separators=(",", ":"), sort_keys=True).encode()
        full_hash = {n: hashlib.sha256(enc(r)).hexdigest() for n, r in records.items()}
        index, pnl = [], []
        (V.RECORDS / "sweeps").mkdir(parents=True)
        (V.RECORDS / "redacted").mkdir(parents=True)
        for n, rec in records.items():
            if n == 2:
                red = json.loads(json.dumps(rec))
                red["input"]["trades"][1] = {"redacted": "private room"}
                red["output"]["trades"][1] = {"redacted": "private room"}
                data = enc(red)
                path = f"redacted/{full_hash[n]}.json"
                index.append({"n": n, "file": full_hash[n], "path": path, "bytes": len(data), "status": "redacted",
                              "redacted": 1, "sha256": hashlib.sha256(data).hexdigest()})
            else:
                data = enc(rec)
                path = f"sweeps/{full_hash[n]}.json"
                index.append({"n": n, "file": full_hash[n], "path": path, "bytes": len(data), "status": "full"})
            if n == 3:
                data = data.replace(b'"close":"101.00"', b'"close":"101.01"', 1)    # same size, other bytes
            (V.RECORDS / path).write_bytes(data)
            posted_file = "f" * 64 if n == 4 else full_hash[n]
            pnl.append(export_line(n, cls.ref_seed, "d-close1-pnl", 100 + n,
                                   {"t": "pnl", "n": n, "mark": marks[n], "top": boards[n], "file": posted_file}))
        pnl.append(export_line(5, cls.ref_seed, "d-close1-pnl", 105,
                               {"t": "pnl", "n": 5, "mark": "1.00", "top": [], "file": "0" * 64}, corrupt=True))
        pnl.append(export_line(6, cls.other, "d-close1-pnl", 106,
                               {"t": "pnl", "n": 6, "mark": "1.00", "top": [], "file": "0" * 64}))
        (V.RECORDS / "index.json").write_text(json.dumps({"contest": "close-1", "sweeps": index}))
        V.VENUE_DIR.mkdir(parents=True)
        (V.VENUE_DIR / "d-close1-pnl.export.jsonl").write_text("\n".join(pnl) + "\n")
        seed_post = {"t": "seed", "season": "close-1", "price": "100.00", "package": "ab" * 32}
        (V.VENUE_DIR / "d-close1-price.export.jsonl").write_text(
            export_line(1, cls.ref_seed, "d-close1-price", 50, seed_post) + "\n")
        note = "!! UNTRUSTED CONTENT header line\n\n" + cls.referee + "\n"
        for room in ("d-close1-pnl", "d-close1-price"):
            (V.VENUE_DIR / f"room-owners_{room}.txt").write_text(note)
        cls.args = SimpleNamespace(repo=REPO, referee=None, asof=None, every=1, force=False)
        assert V.cmd_verify(cls.args) == 0
        cls.ver = json.loads((V.CACHE / "verify.json").read_text())
        cls.rows = {r["n"]: r for r in cls.ver["rows"]}

    def test_referee_from_owner_note(self):
        self.assertEqual(self.ver["referee"], self.referee)

    def test_signatures(self):
        s = self.ver["pnl"]["stats"]
        self.assertEqual(s["signature verified"], 4)
        self.assertEqual(s["signature FAILED"], 1)
        self.assertEqual(s["not from the referee"], 1)
        self.assertEqual(self.ver["pnl"]["range"], [1, 4])
        self.assertEqual(self.ver["seed"]["px"], "100.00")
        self.assertIn("signed seed post", self.ver["seed"]["source"])

    def test_file_field_comparison(self):
        self.assertEqual([self.rows[n]["file_match"] for n in (1, 2, 3, 4)], [True, True, True, False])
        self.assertEqual(self.ver["file_mismatch"], [4])

    def test_hash_rules(self):
        self.assertEqual(self.rows[1]["hash"], "ok")                 # full: sha256 == posted file
        self.assertEqual(self.rows[2]["hash"], "ok")                 # redacted: sha256 == index sha256
        self.assertNotEqual(self.rows[2]["sha256"], self.rows[2]["index_file"])
        self.assertEqual(self.rows[3]["hash"], "MISMATCH")           # altered bytes
        self.assertEqual(self.rows[4]["hash"], "MISMATCH")           # matches the index, not the signed post
        self.assertEqual(sorted(self.ver["hash_bad"]), [3, 4])

    def test_redaction_count(self):
        self.assertEqual(self.rows[2]["redacted"], 1)
        self.assertEqual(self.rows[2]["index_redacted"], 1)
        self.assertTrue(self.rows[2]["redaction_aligned"])
        self.assertEqual(sum(r.get("redacted", 0) for r in self.rows.values()), 1)

    def test_replay_and_report(self):
        self.assertEqual(V.cmd_replay(self.args), 0)
        rep = json.loads((V.CACHE / "replay.json").read_text())
        self.assertEqual(rep["tally"]["matched"], 1)
        self.assertEqual(rep["tally"].get("mismatched", 0), 0)
        per = {r["n"]: r for r in rep["per_sweep"]}
        self.assertEqual((per[1]["board_exact"], per[1]["board_off"]), (3, 0))     # before the hidden trade
        table = {r["key"]: r for r in rep["table"]}
        # A traded only visibly: its account matches; the hidden volume moved only the unrounded global price
        self.assertEqual(table[self.A]["status"], "consistent")
        self.assertTrue(table[self.A]["reproducible"])
        self.assertEqual(table[self.A]["redacted_touch"], "none detected")
        for k in (self.B, self.C):
            self.assertEqual(table[k]["status"], "off")
            self.assertTrue(table[k]["redacted_touch"].startswith("yes (inferred"))
            self.assertEqual(table[k]["first_board_divergence"], 2)
        saved = V.Getter
        try:
            V.Getter = fake_getter({}, [])
            self.assertEqual(V.cmd_indexsig(SimpleNamespace(referee=None, refresh=False)), 0)
        finally:
            V.Getter = saved
        self.assertEqual(V.cmd_report(self.args), 0)
        text = V.REPORT.read_text()
        self.assertIn("Reproducible from public data: 1 of 3", text)
        self.assertIn("1 differ", text)
        self.assertEqual([l for l in text.splitlines() if l.startswith("index signature:")],
                         ["index signature: none published"])
        self.assertIn("## Final standings", text)
        self.assertIn("not yet available", text)
        self.assertNotIn("| reproduced |", text)

    def test_lock_and_settlement_meta(self):
        self.assertEqual(self.ver["lock_sweep"], 2556)
        self.assertEqual(self.ver["settlement_sweep"], 2568)
        self.assertIsNone(self.ver["final_board"])
        self.assertEqual(self.ver["final_price_time"], "2026-10-04T10:00:00Z")

    def test_final_standings_replay_classifies(self):
        """A settlement post at the as-of sweep: the hidden-trade key is not reproduced, the other is."""
        ver_path = V.CACHE / "verify.json"
        rep_path = V.CACHE / "replay.json"
        saved_ver = ver_path.read_text()
        saved_rep = rep_path.read_text() if rep_path.exists() else None
        saved_force = self.args.force
        try:
            ver = json.loads(saved_ver)
            board = ver["boards"][str(ver["asof"])]
            ver["lock_sweep"] = ver["asof"]
            ver["settlement_sweep"] = ver["asof"]
            ver["final_board"] = {"n": ver["asof"], "mark": board["mark"], "top": board["top"]}
            ver_path.write_text(json.dumps(ver))
            self.args.force = True
            self.assertEqual(V.cmd_replay(self.args), 0)
            fs = json.loads(rep_path.read_text())["final_standings"]
            self.assertTrue(fs["available"])
            by = {r["key"]: r for r in fs["rows"]}
            self.assertEqual(by[self.A]["status"], "consistent")
            self.assertTrue(by[self.A]["reproducible"])
            self.assertEqual(by[self.B]["status"], "off")
            self.assertFalse(by[self.B]["reproducible"])
            self.assertEqual(V.cmd_report(self.args), 0)
            text = V.REPORT.read_text()
            self.assertIn("## Final standings", text)
            self.assertIn("| reproduced |", text)
            self.assertIn("| not reproduced |", text)
            self.assertNotIn("not yet available", text)
        finally:
            ver_path.write_text(saved_ver)
            if saved_rep is None:
                rep_path.unlink(missing_ok=True)
            else:
                rep_path.write_text(saved_rep)
            self.args.force = saved_force

    def test_board_check_exact_at_unrounded_global(self):
        fold = V.load_fold(REPO).Fold()
        with localcontext() as ctx:
            ctx.prec = 60
            fold.seed("100.00")
            fold.sweep(1, "100.00", "100.00", [self.A, self.B], [])
            t = {"id": "x", "maker": self.A, "side": "buy", "qty": "3", "px": "100.01", "taker": "any",
                 "until": 9, "countersigner": self.B}
            u = dict(t, id="y", qty="1", px="100.02")
            fold.sweep(2, "100.00", "100.00", [], [t, u])
            g = fold.global_px                                  # 100.0125: rounds to 100.01
            score = fold.accounts[self.A].value_at(g) - fold.mint
            board = {"mark": str(V.cents(g)), "top": [[self.A, str(V.cents(score))]]}
            checked, g_ok, common = V.board_check(fold, board)
            self.assertTrue(g_ok)
            self.assertEqual(checked[self.A][0], "exact")
            at_mark = fold.accounts[self.A].value_at(Decimal(board["mark"])) - fold.mint
            self.assertNotEqual(V.cents(at_mark), V.cents(score))    # the rounded mark alone would miss it
            board["top"][0][1] = str(V.cents(score) + 1)
            self.assertEqual(V.board_check(fold, board)[0][self.A][0], "off")


class FetchSkipTests(unittest.TestCase):
    """The fetch stage skips files already present with the index size and refetches the rest."""

    def test_resume(self):
        tmp = Path(tempfile.mkdtemp(prefix="scratch-", dir=HERE))
        saved = (V.RECORDS, V.CACHE, V.PROGRESS, V.Getter)
        try:
            V.RECORDS, V.CACHE, V.PROGRESS = tmp / "records", tmp / "cache", tmp / "progress.log"
            a, b = b'{"input":{},"output":{}}', b'{"input":{"n":2},"output":{}}'
            ha, hb = hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()
            index = {"contest": "close-1", "sweeps": [
                {"n": 1, "file": ha, "path": f"sweeps/{ha}.json", "bytes": len(a), "status": "full"},
                {"n": 2, "file": hb, "path": f"sweeps/{hb}.json", "bytes": len(b), "status": "full"}]}
            served = {"index.json": json.dumps(index).encode(), f"sweeps/{ha}.json": a, f"sweeps/{hb}.json": b}
            requested = []

            class FakeGetter:
                def __init__(self, *_, **__):
                    self.bytes = self.requests = 0

                def get(self, url, dest=None, timeout=0):
                    key = url.split("/close-1/", 1)[1]
                    requested.append(key)
                    body = served[key]
                    if dest is None:
                        return 200, body, {}
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(body)
                    return 200, b"", {}

            V.Getter = FakeGetter
            (V.RECORDS / "sweeps").mkdir(parents=True)
            (V.RECORDS / f"sweeps/{ha}.json").write_bytes(a)            # present, right size
            (V.RECORDS / f"sweeps/{hb}.json").write_bytes(b[:-3])       # truncated
            self.assertEqual(V.cmd_fetch(SimpleNamespace(every=1)), 0)
            self.assertEqual(requested, ["index.json", f"sweeps/{hb}.json"])
            self.assertEqual((V.RECORDS / f"sweeps/{hb}.json").read_bytes(), b)
            self.assertIn(f"{ha}  sweeps/{ha}.json", (V.RECORDS / "expected.sha256").read_text())
        finally:
            V.RECORDS, V.CACHE, V.PROGRESS, V.Getter = saved
            shutil.rmtree(tmp, ignore_errors=True)


class IndexSigTests(unittest.TestCase):
    """The signed-index check: a detached signature next to index.json (form a), a signed room post
    carrying index.json's sha256 (form b), neither, and signatures that do not verify."""

    SIG_URL = f"{V.ARCHIVE}/index.json.sig"
    STATE_URL = f"{V.VENUE}/r/d-close1-state/export"
    PRICE_URL = f"{V.VENUE}/r/d-close1-price/export"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scratch-", dir=HERE))
        self.saved = (V.RECORDS, V.VENUE_DIR, V.CACHE, V.PROGRESS, V.Getter)
        V.RECORDS, V.VENUE_DIR, V.CACHE = self.tmp / "records", self.tmp / "venue", self.tmp / "cache"
        V.PROGRESS = self.tmp / "progress.log"
        self.ref_seed = bytes([1]) * 32
        self.referee = did_of(self.ref_seed)
        self.index = json.dumps({"contest": "close-1", "sweeps": [{"n": 1, "file": "a" * 64}]}).encode()
        self.digest = hashlib.sha256(self.index).hexdigest()
        V.RECORDS.mkdir(parents=True)
        (V.RECORDS / "index.json").write_bytes(self.index)
        V.VENUE_DIR.mkdir(parents=True)
        for room in ("d-close1-pnl", "d-close1-price"):
            (V.VENUE_DIR / f"room-owners_{room}.txt").write_text("header\n\n" + self.referee + "\n")
        seed = export_line(1, self.ref_seed, "d-close1-price", 50, {"t": "seed", "price": "100.00"})
        self.served = {self.PRICE_URL: (seed + "\n").encode()}

    def tearDown(self):
        V.RECORDS, V.VENUE_DIR, V.CACHE, V.PROGRESS, V.Getter = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_check(self) -> dict:
        self.requested = []
        V.Getter = fake_getter(self.served, self.requested)
        self.assertEqual(V.cmd_indexsig(SimpleNamespace(referee=None, refresh=True)), 0)
        return json.loads((V.CACHE / "indexsig.json").read_text())

    def test_none_published(self):
        other = export_line(2, self.ref_seed, "d-close1-price", 51, {"t": "price", "sha256": "b" * 64})
        self.served[self.PRICE_URL] += (other + "\n").encode()
        res = self.run_check()
        self.assertEqual(res["line"], "index signature: none published")
        self.assertEqual(self.requested, [self.SIG_URL, f"{V.ARCHIVE}/index.sig", self.STATE_URL, self.PRICE_URL])
        self.assertEqual(V.index_sig_line(), "index signature: none published")

    def test_form_a_valid(self):
        sig = sign(self.ref_seed, self.index)
        encodings = {"base64url": b64u(sig).encode(), "raw": sig, "base58btc": ("z" + b58encode(sig)).encode(),
                     "padded base64": base64.b64encode(sig) + b"\n",
                     "json": json.dumps({"from": self.referee, "sig": b64u(sig)}).encode()}
        for name, body in encodings.items():
            with self.subTest(encoding=name):
                self.served[self.SIG_URL] = body
                self.assertEqual(self.run_check()["line"], "index signature: verified (form a)")
        del self.served[self.SIG_URL]
        self.served[f"{V.ARCHIVE}/index.sig"] = b64u(sig).encode()
        self.assertEqual(self.run_check()["line"], "index signature: verified (form a)")

    def test_form_b_valid(self):
        post = export_line(1, self.ref_seed, "d-close1-state", 7, {"t": "index", "sha256": self.digest})
        self.served[self.STATE_URL] = (post + "\n").encode()
        res = self.run_check()
        self.assertEqual(res["line"], "index signature: verified (form b)")
        self.assertEqual(res["verified"], [{"form": "b", "where": "d-close1-state seq 1"}])
        self.assertTrue((V.VENUE_DIR / "d-close1-state.export.jsonl").exists())

    def test_form_b_needs_the_recomputed_hash_and_the_referee(self):
        stale = hashlib.sha256(self.index + b" ").hexdigest()
        lines = [export_line(1, self.ref_seed, "d-close1-state", 7, {"t": "index", "sha256": stale}),
                 export_line(2, bytes([9]) * 32, "d-close1-state", 8, {"t": "index", "sha256": self.digest})]
        self.served[self.STATE_URL] = ("\n".join(lines) + "\n").encode()
        self.assertEqual(self.run_check()["line"], "index signature: none published")

    def test_tampered_index_bytes_fail(self):
        self.served[self.SIG_URL] = b64u(sign(self.ref_seed, self.index)).encode()
        (V.RECORDS / "index.json").write_bytes(self.index.replace(b'"n": 1', b'"n": 2'))
        line = self.run_check()["line"]
        self.assertTrue(line.startswith("index signature: FAILED (index.json.sig does not verify"), line)

    def test_other_key_or_bad_encoding_fails(self):
        self.served[self.SIG_URL] = b64u(sign(bytes([9]) * 32, self.index)).encode()
        self.assertTrue(self.run_check()["line"].startswith("index signature: FAILED (index.json.sig does not verify"))
        self.served[self.SIG_URL] = b"<html>hello</html>"
        self.assertTrue(self.run_check()["line"].startswith("index signature: FAILED (index.json.sig is not a recognised"))

    def test_form_b_forged_post_fails(self):
        post = export_line(1, self.ref_seed, "d-close1-state", 7, {"t": "index", "sha256": self.digest}, corrupt=True)
        self.served[self.STATE_URL] = (post + "\n").encode()
        line = self.run_check()["line"]
        self.assertEqual(line, "index signature: FAILED (d-close1-state seq 1 carries the index sha256 "
                               "but its signature does not verify)")

    def test_report_line_goes_stale_with_the_index(self):
        self.run_check()
        (V.RECORDS / "index.json").write_bytes(self.index + b"\n")
        self.assertIn("not checked for the current index.json", V.index_sig_line())


class FinalBoardTests(unittest.TestCase):
    """Settlement sweep, which pnl post is the final board, and how the section classifies it."""

    def test_settlement_sweep_is_one_hour_after_the_lock(self):
        contest = json.loads((REPO / "contest.json").read_text())
        self.assertEqual(contest["lock_sweep"], 2556)
        self.assertEqual(contest["final_price_time"], "2026-10-04T10:00:00Z")
        self.assertEqual(V.settlement_sweep(contest), 2568)
        self.assertIsNone(V.settlement_sweep({}))

    def test_selects_earliest_post_at_or_after_settlement(self):
        earlier = {"mark": "1.00", "top": [["a", "1.00"]]}
        at = {"mark": "2.00", "top": [["b", "2.00"]]}
        later = {"mark": "3.00", "top": [["c", "3.00"]]}
        self.assertEqual(V.select_final_board({2556: earlier, 2569: later, 2572: at}, 2568)["n"], 2569)
        self.assertEqual(V.select_final_board({2568: at, 2570: later}, 2568)["n"], 2568)
        self.assertIsNone(V.select_final_board({2556: earlier}, 2568))
        self.assertIsNone(V.select_final_board({}, None))

    def test_section_not_yet_available_without_a_post(self):
        ver = {"asof": 2556, "lock_sweep": 2556, "settlement_sweep": 2568,
               "lock_time": "2026-10-04T09:00:00Z", "final_price_time": "2026-10-04T10:00:00Z",
               "final_board": None}
        rep = {"final_standings": {"available": False, "asof": 2556, "lock_sweep": 2556,
                                  "settlement_sweep": 2568, "rows": []}}
        text = "\n".join(V.final_standings_lines(ver, rep))
        self.assertIn("## Final standings", text)
        self.assertIn("not yet available", text)
        self.assertIn("sweep 2568", text)
        self.assertNotIn("| reproduced |", text)
        self.assertNotIn("| not reproduced |", text)

    def test_section_classifies_exact_and_off(self):
        A, B = (did_of(bytes([i]) * 32) for i in (2, 3))
        fold = V.load_fold(REPO).Fold()
        with localcontext() as ctx:
            ctx.prec = 60
            fold.seed("100.00")
            fold.sweep(1, "100.00", "100.00", [A, B], [])
            board = {"n": 1, "mark": "100.00", "top": [[A, "0.00"], [B, "1.00"]]}
            ver = {"asof": 1, "lock_sweep": 1, "settlement_sweep": 1, "final_board": board,
                   "lock_time": "2026-10-04T09:00:00Z", "final_price_time": "2026-10-04T10:00:00Z"}
            fs = V.build_final_standings(fold, ver)
        self.assertTrue(fs["available"])
        rows = {r["key"]: r for r in fs["rows"]}
        self.assertEqual(rows[A]["status"], "exact")
        self.assertTrue(rows[A]["reproducible"])
        self.assertEqual(rows[A]["posted"], "0.00")
        self.assertEqual(rows[B]["status"], "off")
        self.assertFalse(rows[B]["reproducible"])
        text = "\n".join(V.final_standings_lines(ver, {"final_standings": fs}))
        self.assertIn("## Final standings", text)
        self.assertIn("| reproduced |", text)
        self.assertIn("| not reproduced |", text)
        self.assertNotIn("not yet available", text)
        # before the lock, the same post is not the final board yet
        ver["asof"] = 0
        self.assertFalse(V.build_final_standings(fold, ver)["available"])

    def test_standings_post_without_n_is_the_final_board(self):
        """t=standings, with S and places and no n, is found and used. A post with n still is."""
        seed = bytes([4]) * 32
        referee = did_of(seed)
        A = did_of(bytes([6]) * 32)
        B = did_of(bytes([7]) * 32)
        obj = {"t": "standings", "S": "234.69", "places": [[A, "1576.916300", [1], 1]],
               "next": [[B, "1424.740300"]]}
        self.assertNotIn("n", obj)
        body = (export_line(3, seed, "d-close1-pnl", 11, obj) + "\n").encode()
        posts, stats, problems = V.parse_export(body, "d-close1-pnl", referee)
        self.assertEqual(problems, [])
        self.assertEqual(stats["signature verified"], 1)
        self.assertEqual(posts[0]["t"], "standings")
        self.assertNotIn("n", posts[0])
        self.assertEqual(posts[0]["S"], "234.69")
        earlier = {"mark": "1.00", "top": [["a", "1.00"]]}
        later = {"mark": "3.00", "top": [["c", "3.00"]]}
        chosen = V.select_final_board({2556: earlier, 2569: later}, 2568, posts[0])
        self.assertEqual(chosen["t"], "standings")
        self.assertNotIn("n", chosen)
        self.assertEqual(chosen["mark"], "234.69")
        self.assertEqual(chosen["top"], [[A, "1576.916300"], [B, "1424.740300"]])
        # a pnl post that still carries n is selected exactly as before when no standings post is passed
        self.assertEqual(V.select_final_board({2556: earlier, 2569: later, 2572: {"mark": "2.00", "top": [["b", "2.00"]]}}, 2568)["n"], 2569)
        kept = V.board_from_standings({"t": "standings", "n": 2568, "S": "5.00", "places": [[A, "1.00"]]})
        self.assertEqual(kept["n"], 2568)
        self.assertEqual(kept["mark"], "5.00")
        self.assertEqual(V.select_final_board({2568: later}, 2568, {"t": "standings", "n": 2568, "S": "5.00", "places": [[A, "1.00"]]})["t"], "standings")

        fold = V.load_fold(REPO).Fold()
        with localcontext() as ctx:
            ctx.prec = 60
            fold.seed("100.00")
            fold.sweep(1, "100.00", "100.00", [A], [])
            six_dp = {"mark": "100.00", "top": [[A, "0.000000"]]}
            checked, g_ok, _ = V.board_check(fold, six_dp)
            self.assertTrue(g_ok)
            self.assertEqual(checked[A][0], "exact")
        board = V.board_from_standings({"t": "standings", "S": "100.00", "places": [[A, "0.00"]], "_ts": "2026-10-04T17:33:04Z"})
        self.assertNotIn("n", board)
        ver = {"asof": 2556, "lock_sweep": 2556, "settlement_sweep": 2568, "final_board": board,
               "lock_time": "2026-10-04T09:00:00Z", "final_price_time": "2026-10-04T10:00:00Z"}
        fs = V.build_final_standings(fold, ver)
        self.assertTrue(fs["available"])
        self.assertIsNone(fs["sweep"])
        self.assertEqual(fs["source"], "standings")
        self.assertEqual(fs["mark"], "100.00")
        self.assertEqual(fs["rows"][0]["status"], "exact")
        self.assertTrue(fs["rows"][0]["reproducible"])
        text = "\n".join(V.final_standings_lines(ver, {"final_standings": fs}))
        self.assertIn("## Final standings", text)
        self.assertIn("t` = `standings", text)
        self.assertIn("no sweep number", text)
        self.assertIn("S `100.00`", text)
        self.assertIn("| reproduced |", text)
        self.assertNotIn("not yet available", text)


class FinalRecordTests(unittest.TestCase):
    """Tiny gzip parts. No network and no key directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="final-record-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = V.CACHE, V.PROGRESS, V.REPORT
        V.CACHE = self.tmp / "cache"
        V.CACHE.mkdir()
        V.PROGRESS = self.tmp / "progress.log"
        V.REPORT = self.tmp / "REPORT.md"

    def tearDown(self):
        V.CACHE, V.PROGRESS, V.REPORT = self._saved

    def _did(self, n: int) -> str:
        return "did:key:z6Mk" + f"{n:04d}" + ("A" * 40)

    def _parts(self, body: bytes) -> tuple[Path, str]:
        blob = gzip.compress(body)
        cuts = [0, len(blob) // 3, 2 * len(blob) // 3, len(blob)]
        directory = self.tmp / "parts"
        directory.mkdir()
        for i, name in enumerate(V.FINAL_PART_NAMES):
            (directory / name).write_bytes(blob[cuts[i]:cuts[i + 1]])
        return directory, hashlib.sha256(body).hexdigest()

    def _record(self) -> bytes:
        rows = [
            '{"holder":"%s","pnl":9.50,"fees":1.25,"side":"buy","qty":"3"}' % self._did(1),
            '{"holder":"%s","pnl":8.25,"fees":0.00,"side":"sell","qty":"1"}' % self._did(2),
            '{"holder":"%s","pnl":1.00,"fees":-0.50,"side":"buy","qty":"2"}' % self._did(3),
            '{"holder":"%s","pnl":-3.500,"fees":0.10,"side":"sell","qty":"4"}' % self._did(4),
            '{"holder":"%s","pnl":0.000,"fees":0,"side":"buy","qty":"1"}' % self._did(1),
        ]
        return ("[" + ",".join(rows) + "]").encode()

    def _args(self, directory: Path, digest: str, scores: str, dids: Path | None = None, out: Path | None = None):
        return SimpleNamespace(parts_dir=directory, expect_sha256=digest, expect_scores=scores,
                               dids=dids, ours_out=out)

    def test_fixture_passes_and_report_has_exact_scores(self):
        directory, digest = self._parts(self._record())
        dids = self.tmp / "dids.txt"
        dids.write_text("\n".join([self._did(1), self._did(4), self._did(9)]) + "\n")
        out = self.tmp / "ours.csv"
        code = V.cmd_final_record(self._args(directory, digest, "9.50,8.25,1.00", dids, out))
        self.assertEqual(code, 0)
        rec = json.loads((V.CACHE / "final-record.json").read_text())
        self.assertTrue(rec["sha256_ok"])
        self.assertTrue(rec["top3_ok"])
        self.assertEqual([r["score"] for r in rec["top3"]], ["9.50", "8.25", "1.00"])
        self.assertEqual(rec["score_field"], "pnl")
        self.assertEqual(rec["fee_field"], "fees")
        self.assertEqual(len(rec["top3"][0]["prefix"]), 12)
        text = json.dumps(rec)
        self.assertNotIn(self._did(1), text)
        lines = "\n".join(V.final_record_lines())
        self.assertIn("## Final record", lines)
        self.assertIn("`9.50` / `8.25` / `1.00`", lines)
        self.assertIn("matches", lines)
        body = out.read_text()
        self.assertIn(self._did(4), body)
        self.assertNotIn(self._did(9), body)
        self.assertEqual(body.count(self._did(1)), 2)

    def test_bad_hash_stops(self):
        directory = self.tmp / "parts"
        directory.mkdir()
        for name in V.FINAL_PART_NAMES:
            (directory / name).write_bytes(b"not-gzip")
        code = V.cmd_final_record(self._args(directory, "0" * 64, "9.50,8.25,1.00"))
        self.assertEqual(code, 1)
        rec = json.loads((V.CACHE / "final-record.json").read_text())
        self.assertFalse(rec["sha256_ok"])
        self.assertNotIn("top3", rec)
        self.assertNotEqual(rec["sha256"], "0" * 64)

    def test_wrong_top_score_fails(self):
        directory, digest = self._parts(self._record())
        code = V.cmd_final_record(self._args(directory, digest, "9.51,8.25,1.00"))
        self.assertEqual(code, 1)
        rec = json.loads((V.CACHE / "final-record.json").read_text())
        self.assertTrue(rec["sha256_ok"])
        self.assertFalse(rec["top3_ok"])
        self.assertEqual(rec["top3"][0]["score"], "9.50")

    def test_wrapped_standings_array(self):
        body = b'{"output":{"standings":' + self._record() + b'}}'
        directory, digest = self._parts(body)
        code = V.cmd_final_record(self._args(directory, digest, "9.50,8.25,1.00"))
        self.assertEqual(code, 0)
        rec = json.loads((V.CACHE / "final-record.json").read_text())
        self.assertEqual(rec["sha256_of"], "decompressed")
        self.assertEqual(rec["rows"], 5)
        self.assertEqual([r["score"] for r in rec["top3"]], ["9.50", "8.25", "1.00"])

    def test_verified_record_is_the_final_board(self):
        rec = {
            "sha256": V.FINAL_SHA256, "sha256_ok": True, "top3_ok": True,
            "top3": [
                {"rank": 1, "prefix": "z6MksSsc4ny8", "score": "1576.916300", "fee": "2220.9839", "places": [1]},
                {"rank": 2, "prefix": "z6MksT96nB2c", "score": "1424.740300", "fee": "2515.8249", "places": [2]},
                {"rank": 3, "prefix": "z6MksPKMgp8P", "score": "1337.545600", "fee": "1649.2162", "places": [3]},
            ],
        }
        ver = {"asof": 2556, "lock_sweep": 2556, "settlement_sweep": 2568,
               "lock_time": "2026-10-04T09:00:00Z", "final_price_time": "2026-10-04T10:00:00Z",
               "final_board": None}
        rep = {"final_standings": {"available": False, "asof": 2556, "lock_sweep": 2556,
                                  "settlement_sweep": 2568, "rows": []}}
        text = "\n".join(V.final_standings_lines(ver, rep, None, rec))
        self.assertIn("## Final standings", text)
        self.assertIn("`d-close1-pnl` #2557", text)
        self.assertIn("2026-10-04T17:33:04Z", text)
        self.assertIn("1576.916300", text)
        self.assertIn("1424.740300", text)
        self.assertIn("1337.545600", text)
        self.assertIn("| 1 | `z6MksSsc4ny8` | 1576.916300 | 1 | 2220.9839 |", text)
        self.assertNotIn("not yet available", text)
        bad = {"sha256": "0" * 64, "sha256_ok": False, "top3_ok": False, "top3": []}
        missed = "\n".join(V.final_standings_lines(ver, rep, None, bad))
        self.assertIn("not yet available", missed)

    def test_section_not_checked_without_a_result(self):
        text = "\n".join(V.final_record_lines())
        self.assertIn("not checked", text)
        self.assertNotIn("MISMATCH", text)


if __name__ == "__main__":
    unittest.main()
