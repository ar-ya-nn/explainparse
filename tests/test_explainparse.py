import glob
import os
import unittest

from explainparse import analyze
from explainparse.grammar import Grammar, GrammarError
from explainparse.lexer import lex
from explainparse.parser import LL1Parser

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)


def codes(src):
    return [d.code for d in analyze(src).diagnostics]


def reparses(src):
    res = analyze(src)
    return not analyze(res.fixed_source).diagnostics


class GrammarTests(unittest.TestCase):
    def test_lumen_grammar_is_ll1(self):
        g = Grammar()
        self.assertTrue(g.is_ll1(), g.conflicts)

    def test_conflict_is_detected(self):
        g = Grammar("s -> 'a' 'b' | 'a' 'c'")
        self.assertFalse(g.is_ll1())

    def test_left_recursion_is_rejected(self):
        g = Grammar("e -> e '+' 'x' | 'x'")
        self.assertFalse(g.is_ll1())

    def test_undefined_nonterminal(self):
        with self.assertRaises(GrammarError):
            Grammar("s -> missing")

    def test_first_follow(self):
        g = Grammar()
        self.assertEqual(g.first["type"], {"int", "bool", "str"})
        self.assertIn("else", g.follow["block"])
        self.assertIn("params", g.nullable)


class LexerTests(unittest.TestCase):
    def test_tokens(self):
        toks, diags = lex('fn f(x: int) -> bool { return x >= 2 && !false; } // c')
        self.assertEqual(diags, [])
        kinds = [t.kind for t in toks]
        self.assertIn("->", kinds)
        self.assertIn(">=", kinds)
        self.assertIn("&&", kinds)
        self.assertEqual(kinds[-1], "EOF")

    def test_positions(self):
        toks, _ = lex("let x\n  : int")
        colon = [t for t in toks if t.kind == ":"][0]
        self.assertEqual((colon.line, colon.col), (2, 3))

    def test_lexical_errors(self):
        self.assertIn("E101", codes('fn f() -> int { let s: str = "abc\n return 1; }'))
        self.assertIn("E102", codes("fn f() -> int { let 9lives: int = 1; return 1; }"))
        self.assertIn("E103", codes("fn f() -> int { return 1; } /* never closed"))
        self.assertIn("E100", codes("fn f() -> int { return 1 @ ; }"))
        self.assertIn("E104", codes("fn f() -> str { return 'x'; }"))

    def test_unterminated_string_before_semicolon_does_not_cascade(self):
        res = analyze('fn f() -> int {\n    let s: str = "hi;\n    return 0;\n}')
        self.assertEqual([d.code for d in res.diagnostics], ["E101"])
        self.assertIn('"hi";', res.fixed_source)


class ValidProgramTests(unittest.TestCase):
    def test_examples_and_corpus_are_valid(self):
        files = glob.glob(os.path.join(ROOT, "examples", "corpus", "*.lm"))
        files.append(os.path.join(ROOT, "examples", "valid.lm"))
        self.assertGreater(len(files), 5)
        for f in files:
            with self.subTest(f=f):
                res = analyze(open(f, encoding="utf-8").read())
                self.assertEqual(res.diagnostics, [])
                self.assertIsNotNone(res.tree)


class SyntaxErrorTests(unittest.TestCase):
    def test_missing_semicolon_points_at_end_of_previous_line(self):
        res = analyze("fn f() -> int {\n    let a: int = 1\n    return a;\n}")
        d = res.diagnostics[0]
        self.assertEqual(d.code, "E201")
        self.assertEqual(d.line, 2)
        self.assertEqual(d.confidence, "high")
        self.assertIn("let a: int = 1", d.why)

    def test_unclosed_paren_names_its_purpose(self):
        d = analyze("fn f(x: int) -> int {\n    while (x > 1 {\n        x = x - 1;\n    }\n    return x;\n}").diagnostics[0]
        self.assertEqual(d.code, "E202")
        self.assertIn("while loop", d.title)

    def test_missing_brace_root_cause_uses_indentation(self):
        src = ("fn main() -> int {\n    let i: int = 0;\n    while (i < 10) {\n        i = i + 1;\n\n"
               "    return i;\n}\n\nfn helper() -> int {\n    return 1;\n}\n")
        res = analyze(src)
        self.assertEqual(len(res.diagnostics), 1)
        d = res.diagnostics[0]
        self.assertEqual(d.code, "E203")
        self.assertEqual(d.root_cause["line"], 6)   # not line 9 where the parser noticed
        self.assertTrue(reparses(src))

    def test_nested_missing_brace_picks_innermost_culprit(self):
        src = ("fn f(n: int) -> int {\n    while (n > 0) {\n        if (n == 3) {\n            n = 0;\n\n"
               "        n = n - 1;\n    }\n    return n;\n}\n")
        d = analyze(src).diagnostics[0]
        self.assertIn("if block", d.title)
        self.assertEqual(d.root_cause["line"], 6)

    def test_keyword_typo(self):
        res = analyze("fn f() -> int {\n    retrun 1;\n}")
        d = res.diagnostics[0]
        self.assertEqual(d.code, "E220")
        self.assertIn("return", d.title)
        self.assertIn("return 1;", res.fixed_source)

    def test_two_adjacent_mistakes_are_both_fixed(self):
        # regression: missing ';' right before a misspelled keyword used to be
        # "fixed" by replacing the keyword with ';'
        res = analyze("fn main() -> int {\n    let x: int = 5\n    retrun x;\n}\n")
        self.assertEqual([d.code for d in res.diagnostics], ["E201", "E220"])
        self.assertIn("let x: int = 5;\n    return x;", res.fixed_source)

    def test_missing_parens_around_condition_is_one_diagnostic(self):
        res = analyze("fn f(x: int) -> int {\n    if x > 1 {\n        return 1;\n    }\n    return 0;\n}")
        self.assertEqual([d.code for d in res.diagnostics], ["E208"])
        self.assertIn("if (x > 1) {", res.fixed_source)

    def test_unmatched_closer(self):
        d = analyze("fn f() -> int {\n    return (1 + 2));\n}").diagnostics[0]
        self.assertEqual(d.code, "E211")

    def test_missing_type_and_expression(self):
        self.assertIn("E206", codes("fn f() -> int { let x: = 1; return x; }"))
        self.assertIn("E205", codes("fn f() -> int { let x: int = 1 + ; return x; }"))

    def test_low_confidence_offers_alternative(self):
        d = analyze("fn f(w: int) -> int { let a: int = w * ; return a; }").diagnostics[0]
        self.assertNotEqual(d.confidence, "high")
        self.assertIsNotNone(d.alternative)

    def test_multiple_errors_all_reported_and_fixed(self):
        src = open(os.path.join(ROOT, "examples", "many_errors.lm")).read()
        res = analyze(src)
        self.assertGreaterEqual(len(res.diagnostics), 5)
        self.assertTrue(reparses(src))

    def test_fewer_cascading_errors_than_panic_mode(self):
        src = "fn f(x: int) -> int {\n    if (x > 5 {\n        return 1;\n    }\n    return 0;\n}"
        res = analyze(src)
        self.assertLess(len(res.diagnostics), res.baseline_errors + 1)
        self.assertEqual(len(res.diagnostics), 1)

    def test_json_serialisable(self):
        import json
        res = analyze(open(os.path.join(ROOT, "examples", "many_errors.lm")).read())
        json.dumps(res.to_dict())

    def test_never_crashes_on_garbage(self):
        import random
        rng = random.Random(1)
        alphabet = list("(){};:=+-*/<>!,\"' \nabcfnletifwhilereturn0123456789")
        for _ in range(150):
            src = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
            res = analyze(src)               # must not raise
            self.assertIsInstance(res.fixed_source, str)


class EvaluationTests(unittest.TestCase):
    def test_evaluation_runs_and_beats_baseline(self):
        from explainparse.evaluate import evaluate, summarise
        files = glob.glob(os.path.join(ROOT, "examples", "corpus", "*.lm"))
        rows = evaluate(files, per_type=8, seed=3)
        summary, _ = summarise(rows)
        self.assertLessEqual(summary["ALL"]["avg_diags"], summary["ALL"]["avg_baseline"])
        self.assertGreaterEqual(summary["ALL"]["repair_valid"], 95)


if __name__ == "__main__":
    unittest.main()
