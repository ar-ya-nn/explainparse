import json
import os
import unittest

from explainparse import analyze
from explainparse.extras import grammar_info, trace, break_program
from explainparse.server import handle_analyze, handle_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, "examples", *parts), encoding="utf-8") as fh:
        return fh.read()


class Extras(unittest.TestCase):
    def test_grammar_info(self):
        for lang in ("lumen", "minic", "minijava"):
            g = grammar_info(lang)
            self.assertEqual(g["conflicts"], 0)
            self.assertEqual(g["terminals"][-1], "EOF")
            for nt, row in g["table"].items():
                for t, p in row.items():
                    self.assertEqual(g["productions"][p]["lhs"], nt)
                    self.assertIn(t, g["terminals"])

    def test_trace_accepts_valid_program(self):
        t = trace(read("valid.lm"))
        self.assertTrue(t["ok"])
        self.assertFalse(t["repaired"])
        self.assertEqual(t["steps"][-1]["action"], "accept")
        matches = [s for s in t["steps"] if s["action"] == "match"]
        self.assertEqual(len(matches), len(t["tokens"]))       # every token is matched once

    def test_trace_of_broken_program_uses_repair(self):
        t = trace(read("minijava", "many_errors.java"), "minijava")
        self.assertTrue(t["ok"] and t["repaired"])
        self.assertTrue(any(tok["synthetic"] for tok in t["tokens"]))
        self.assertEqual(t["steps"][-1]["action"], "accept")

    def test_break_program_injects_a_syntax_error(self):
        for lang, src in (("lumen", read("valid.lm")), ("minic", read("minic", "valid.c")),
                          ("minijava", read("minijava", "valid.java"))):
            for seed in range(5):
                b = break_program(src, lang, seed)
                self.assertTrue(b["ok"])
                self.assertNotEqual(b["source"], src)
                self.assertTrue(any(d.phase != "semantic" for d in analyze(b["source"], lang).diagnostics))

    def test_server_tools(self):
        body = json.dumps({"source": read("valid.lm"), "lang": "lumen"}).encode()
        code, payload = handle_tool("/api/trace", body)
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        code, payload = handle_tool("/api/break", json.dumps({"source": read("valid.lm"), "seed": 3}).encode())
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        code, _ = handle_tool("/api/trace", b'{"source": 5}')
        self.assertEqual(code, 400)
        code, payload = handle_analyze(json.dumps({"source": "int main() { return 0; }", "lang": "minic"}).encode())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["lex_tokens"])


if __name__ == "__main__":
    unittest.main()
