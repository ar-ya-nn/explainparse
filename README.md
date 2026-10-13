# ExplainParse

**Explainable syntax error handling for a mini language.**

Most compilers tell you *where* parsing failed. ExplainParse tells you **why** it failed, **where the real mistake is** (often lines earlier), and **how to fix it**, with a confidence score. Each mistake is reported once instead of triggering a cascade of follow-on errors.

```
error[E203]: unclosed '{': the while loop body (line 3) is never closed
 --> examples/missing_brace_root_cause.lm:6:5
  |
3 |     while (i < 10) {
  |                    - this '{' opens the while loop body
...
6 |     return i;
  |     ^ expected '}' before this line
7 | }
  | - this '}' was meant for the outer block
  |
  = why: The parser only noticed at line 9 (keyword 'fn'), but the real mistake is
         earlier. The indentation shows the while loop body ends before line 6, yet it
         has no '}' there, so the '}' on line 7 (meant for the enclosing block) closed
         it instead, and every brace after that was matched to the wrong block.
  = root cause: line 6: the while loop body should end just before this line
  = fix: insert '}' before line 6 (confidence: high)
```

## Quick start

Requires only Python 3.10+. No dependencies.

```bash
python -m explainparse examples/many_errors.lm            # explain every error
python -m explainparse examples/many_errors.lm --fixed    # also print the repaired program
python -m explainparse examples/valid.lm --tree           # parse tree
python -m explainparse examples/unclosed_paren.lm --compare   # vs. panic-mode recovery
python -m explainparse --grammar                          # FIRST/FOLLOW sets, LL(1) table stats
python -m explainparse.server                             # web playground at http://127.0.0.1:8000
python -m unittest discover -s tests                      # 24 tests
python -m explainparse.evaluate examples/corpus --per-type 60   # reproduce the results below
```

## Results

Errors were injected automatically into 8 valid Lumen programs (480 mutants, 8 mutation types, fixed seed) and the first diagnostic was compared with classic panic-mode recovery.

| Mutation | N | Exact line (ours / panic) | Exact repair | Equivalent repair* | Valid repair | Errors reported per bug (ours / panic) |
|---|---|---|---|---|---|---|
| drop_semicolon | 60 | 100% / 100% | 100% | 100% | 100% | 1.00 / 1.85 |
| drop_close_paren | 60 | 100% / 100% | 95% | 95% | 100% | 1.00 / 3.10 |
| **drop_close_brace** | 60 | **100% / 40%** | 100% | 100% | 100% | 1.00 / 1.92 |
| drop_open_paren | 60 | 100% / 100% | 98% | 98% | 100% | 1.00 / 1.23 |
| extra_token | 60 | 100% / 100% | 88% | 88% | 100% | 1.00 / 1.30 |
| keyword_typo | 60 | 100% / 100% | 100% | 100% | 100% | 1.00 / 1.68 |
| drop_operator | 60 | 100% / 100% | 17% | 100% | 100% | 1.00 / 1.00 |
| random_delete | 60 | 100% / 98% | 82% | 90% | 100% | 1.00 / 1.83 |
| **All** | 480 | **100% / 92%** | 85% | 96% | 100% | **1.00 / 1.74** |

\* Up to interchangeable tokens: any operator for a deleted operator, any type for a deleted type.

The confidence score is calibrated: repairs marked *high* were correct 99% of the time, *medium* 95%, *low* 79%.

Caveat: the corpus is small and the errors are synthetic. A study on real student mistakes is future work.

## How it works

```
source ─▶ lexer ─▶ tokens ─▶ LL(1) parser ──ok──▶ parse tree
            │                    │ error at token e
            ▼                    ▼
   lexical diagnostics    repair search ─▶ root-cause analysis ─▶ explanation ─▶ apply repair, continue
```

1. **Lexer** (`lexer.py`): hand-written, with recoverable lexical errors (unterminated strings and comments, malformed numbers, single `&`/`|`, single-quoted strings, invalid characters).
2. **Grammar as data** (`grammar.py`): the Lumen grammar is a BNF text spec. FIRST, FOLLOW and the LL(1) table are computed from it, and conflicts are reported. The current grammar has 37 nonterminals, 38 terminals, 0 conflicts.
3. **Table-driven LL(1) parser** (`parser.py`): records a stack snapshot before every token, so repairs can be tested by resuming from the snapshot instead of reparsing.
4. **Repair search** (`engine.py`): at an error, candidate single-token insertions, deletions and replacements up to 3 tokens back (plus keyword-typo fixes up to 12 tokens back, using Damerau-Levenshtein distance) are each tested by parsing ahead. Candidates are scored on how far parsing gets, edit cost, distance from the error, and layout cues. If no single edit recovers, a **two-edit lookahead** avoids committing to dead ends.
5. **Root-cause analysis**: for a missing `}`, brace pairs are checked against indentation to find the block whose `}` was "stolen" by an outer block, and the fix is moved there. For a missing `)`, the unclosed `(` and its purpose (if condition, call arguments, parameter list) are identified.
6. **Explanations** (`explain.py`): the parse tree at the error gives the enclosing construct (e.g. "the declaration of 'total'"), which feeds plain-English explanations. Equivalent repairs are grouped when computing confidence, and the runner-up fix is shown when confidence is not high.
7. **Baseline** (`parser.py`): Dragon-Book panic-mode recovery, used for comparison.

## Error codes

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| E100 | invalid character / unknown operator | E205 | missing expression |
| E101 | unterminated string | E206 | missing type |
| E102 | malformed number | E207 | missing operator |
| E103 | unterminated comment | E208 | condition missing both parentheses |
| E104 | single-quoted string | E210 | unexpected token |
| E201 | missing `;` | E211 | unmatched `)` or `}` |
| E202 | unclosed `(` | E212 | duplicated token |
| E203 | unclosed `{` (with root cause) | E220 | misspelled keyword |
| E204 | missing token | E221 | wrong token |

## The Lumen language

```
fn fib(n: int) -> int {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
```

Typed `let` declarations, functions with `->` return types, `if / else if / else`, `while`, `return`, the types `int`, `bool`, `str`, and the usual arithmetic, comparison and logical operators. The full grammar is at the top of `explainparse/grammar.py`.

## Web playground

`python -m explainparse.server` serves an editor with live error squiggles, explanation notes, one-click fixes (also available via Ctrl+. in the editor), root-cause highlighting, the parse tree, and the panic-mode comparison.

To publish it for free on GitHub Pages, run `python -m explainparse.build_site` and publish the `site/` folder. On GitHub Pages there is no server, so the page runs the same Python engine in the visitor's browser using Pyodide.

## Project layout

```
explainparse/   lexer, grammar, parser, engine, explain, diagnostics, cli, evaluate, server, build_site
examples/       demo programs with errors; examples/corpus/ holds valid programs for evaluation
tests/          unit tests (python -m unittest)
web/            playground page
results/        evaluation output
```
