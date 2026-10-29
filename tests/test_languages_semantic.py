import glob
import os
import unittest

from explainparse import analyze
from explainparse.grammar import Grammar
from explainparse.languages import LANGUAGES, get_language, language_for_file
from explainparse.lexer import lex
from explainparse.diagnostics import apply_edits

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples")


def read(*parts):
    with open(os.path.join(EX, *parts), encoding="utf-8") as fh:
        return fh.read()


def codes(res):
    return [d.code for d in res.diagnostics]


class Languages(unittest.TestCase):
    def test_every_grammar_is_ll1(self):
        for lang in LANGUAGES.values():
            self.assertTrue(Grammar(lang.grammar).is_ll1(), lang.name)

    def test_language_lookup(self):
        self.assertEqual(get_language("Mini-C").id, "minic")
        self.assertEqual(get_language("java").id, "minijava")
        self.assertEqual(language_for_file("x/y.java").id, "minijava")
        with self.assertRaises(ValueError):
            get_language("cobol")

    def test_keywords_depend_on_language(self):
        kinds = [t.kind for t in lex("let x", "minic")[0]]
        self.assertEqual(kinds[:2], ["ID", "ID"])          # 'let' is not a Mini-C keyword
        kinds = [t.kind for t in lex("boolean b", "minijava")[0]]
        self.assertEqual(kinds[0], "boolean")

    def test_valid_examples_are_clean(self):
        for lid, sub in (("lumen", ""), ("minic", "minic"), ("minijava", "minijava")):
            src = read(sub, "valid" + LANGUAGES[lid].extension)
            res = analyze(src, lid)
            self.assertEqual(res.diagnostics, [], lid)
            self.assertIsNotNone(res.tree)

    def test_minic_root_cause(self):
        res = analyze(read("minic", "missing_brace_root_cause.c"), "minic")
        self.assertEqual(codes(res), ["E203"])
        d = res.diagnostics[0]
        self.assertEqual(d.line, 7)
        self.assertEqual(d.confidence, "high")

    def test_minijava_errors(self):
        res = analyze(read("minijava", "many_errors.java"), "minijava")
        self.assertEqual(codes(res), ["E201", "E220", "E220", "E202"])
        self.assertIn("'String'", res.diagnostics[2].title)     # capitalisation typo
        self.assertTrue(analyze(res.fixed_source, "minijava").ok)

    def test_messages_name_the_language(self):
        res = analyze("int main() {\n    int x = 1\n    return x;\n}\n", "minic")
        self.assertIn("Mini-C", res.diagnostics[0].why)

    def test_empty_program(self):
        for lid in LANGUAGES:
            self.assertTrue(analyze("  // nothing yet\n", lid).ok, lid)


class Semantic(unittest.TestCase):
    def test_undeclared_with_suggestion(self):
        src = "fn main() -> int {\n    let total: int = 0;\n    return totl;\n}\n"
        res = analyze(src)
        self.assertEqual(codes(res), ["E301"])
        d = res.diagnostics[0]
        self.assertEqual(d.phase, "semantic")
        self.assertIn("'total'", d.title)
        self.assertTrue(analyze(res.fixed_source).ok)

    def test_lumen_semantic_example(self):
        res = analyze(read("semantic_errors.lm"))
        self.assertEqual(codes(res), ["E303", "E301", "E308", "E305", "E306", "E307"])

    def test_minic_semantic_example(self):
        res = analyze(read("minic", "semantic_errors.c"), "minic")
        self.assertEqual(codes(res), ["E302", "E308", "E304", "E301", "E307"])

    def test_minijava_semantic_example(self):
        res = analyze(read("minijava", "semantic_errors.java"), "minijava")
        self.assertEqual(codes(res), ["E307", "E305", "E303", "E309", "E301"])

    def test_c_allows_int_conditions(self):
        src = "int main() {\n    int n = 3;\n    while (n) {\n        n = n - 1;\n    }\n    return n;\n}\n"
        self.assertTrue(analyze(src, "minic").ok)

    def test_scopes(self):
        src = ("fn main() -> int {\n    if (true) {\n        let x: int = 1;\n    }\n"
               "    return x;\n}\n")
        self.assertEqual(codes(analyze(src)), ["E301"])     # x is only visible inside the if block
        dup = "fn main() -> int {\n    let x: int = 1;\n    let x: int = 2;\n    return x;\n}\n"
        self.assertEqual(codes(analyze(dup)), ["E310"])

    def test_no_type_errors_while_syntax_is_broken(self):
        src = "fn main() -> int {\n    let ok: bool = 5\n    return y;\n}\n"
        res = analyze(src)
        self.assertEqual([d.phase for d in res.diagnostics], ["parser"])

    def test_corpus_has_no_false_positives(self):
        for f in glob.glob(os.path.join(EX, "corpus", "*.lm")):
            self.assertTrue(analyze(read(f)).ok, f)

    def test_semantic_off(self):
        self.assertTrue(analyze("fn main() -> int { return y; }", semantic_checks=False).ok)


class Decisions(unittest.TestCase):
    def test_parser_decision_lists_candidates(self):
        res = analyze(read("keyword_typo.lm"))
        dec = res.diagnostics[0].decision
        self.assertEqual(dec["kind"], "repair")
        chosen = [c for c in dec["candidates"] if c["chosen"]]
        self.assertEqual(len(chosen), 1)
        self.assertIn("while", chosen[0]["fix"])
        self.assertEqual(dec["candidates"][0]["score"], max(c["score"] for c in dec["candidates"]))
        self.assertTrue(any("tried" in s for s in dec["steps"]))

    def test_root_cause_move_is_explained(self):
        res = analyze(read("missing_brace_root_cause.lm"))
        steps = " ".join(res.diagnostics[0].decision["steps"])
        self.assertIn("Root-cause analysis moved the fix", steps)

    def test_every_diagnostic_has_a_decision(self):
        for f in glob.glob(os.path.join(EX, "*.lm")):
            for d in analyze(read(f)).diagnostics:
                self.assertTrue(d.decision and d.decision["steps"], (f, d.code))

    def test_decision_is_json_ready(self):
        import json
        json.dumps(analyze(read("many_errors.lm")).to_dict())


if __name__ == "__main__":
    unittest.main()
