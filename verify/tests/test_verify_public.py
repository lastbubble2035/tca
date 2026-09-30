"""Offline tests for close1_verify_public.py on tiny synthetic fixtures. No network, nothing outside verify/.

    python3 -m unittest discover -s verify/tests -v
"""
from __future__ import annotations

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
        self.assertEqual(V.cmd_report(self.args), 0)
        text = V.REPORT.read_text()
        self.assertIn("Reproducible from public data: 1 of 3", text)
        self.assertIn("1 differ", text)

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


if __name__ == "__main__":
    unittest.main()
