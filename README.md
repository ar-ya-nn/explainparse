# ExplainParse

**Explainable error handling for small teaching languages: Lumen, Mini-C and Mini-Java.**

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
python -m explainparse examples/many_errors.lm --why      # ...and show how each fix was chosen
python -m explainparse examples/minic/many_errors.c       # Mini-C (language picked from the extension)
python -m explainparse examples/minijava/semantic_errors.java   # Mini-Java, type and name errors
python -m explainparse examples/many_errors.lm --fixed    # also print the repaired program
python -m explainparse examples/valid.lm --tree           # parse tree
python -m explainparse examples/unclosed_paren.lm --compare   # vs. panic-mode recovery
python -m explainparse --grammar --lang minic             # FIRST/FOLLOW sets, LL(1) table stats
python -m explainparse.server                             # web playground at http://127.0.0.1:8000
python -m unittest discover -s tests                      # 51 tests
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

Caveat: the corpus is small, the errors are synthetic, and these results are for Lumen only. Evaluating Mini-C and Mini-Java, and a study on real student mistakes, are future work.

## What's new

- **Three languages.** A language selector switches between Lumen, Mini-C and Mini-Java. Each language is a plain-text grammar in `languages.py`; FIRST/FOLLOW sets and the LL(1) table are computed from it, and the lexer, repair search, root-cause analysis and explanations are shared. All three grammars are LL(1) with 0 conflicts.
- **Semantic checks.** Once a program parses, a scoped symbol table checks for undeclared variables and functions (with "did you mean" suggestions), duplicate declarations, type mismatches in declarations, assignments, conditions, operators and returns, and wrong argument counts or types in calls. Semantic checks never run on a program with syntax errors, so a guessed repair cannot cause a misleading type error.
- **"How did it decide?"** Every error can show what the parser weighed: where it stopped, what the grammar allowed, how many edits were tried, and a table of the best candidate fixes with their scores (tokens parsed after the edit, edit cost, distance, other adjustments). It also explains when root-cause analysis moved a fix and how the confidence level was set. On the command line, use `--why`.
- **Session history.** The playground saves every program as a session in a sidebar, like a chat history. Sessions are stored only in the browser (localStorage), so code never leaves the user's machine.
- **Redesigned playground.** New layout with the session sidebar, language switch, error cards labelled Lexical / Syntax / Semantic, and a mobile layout.

### Teaching views in the playground

- **Compiler pipeline strip**: Lexer → LL(1) parser → Semantic checks, each with a live status (token count, errors, "waits for a clean parse"). Click a stage to open its view.
- **Tokens**: the lexer's output, line by line and coloured by kind (keywords, identifiers, numbers, strings, operators). Click a token to find it in the code.
- **LL(1) trace**: a step-by-step player for the table-driven parser, showing the stack, the input with the lookahead token, and the action at every step (expand with a table entry, match, accept). The current token is highlighted in the editor, and "Show it in the parse table" jumps to the table cell M[A, a] that was used. Programs with syntax errors are traced after repair, with inserted tokens marked.
- **Grammar**: the productions, the FIRST and FOLLOW sets, and the full LL(1) parse table for the selected language, computed live from the grammar text.
- **🎲 Break it**: injects one realistic mistake into a working program (the same mutations used in the evaluation) so you can watch ExplainParse find it. Undo with Ctrl+Z.
- **Share**: copies a link that opens the current program and language for anyone.
- **Light / dark theme** toggle.

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
7. **Semantic analysis** (`semantic.py`): runs on the parse tree of a program with no syntax errors. Types are worked out bottom-up; an unknown type never causes an error, so one mistake does not cascade.
8. **Baseline** (`parser.py`): Dragon-Book panic-mode recovery, used for comparison.

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

Semantic errors (version 0.2):

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| E301 | undeclared variable | E307 | wrong return type / missing return value |
| E302 | undefined function, or variable used as a function | E308 | wrong number of arguments |
| E303 | declared type does not match the value | E309 | argument of the wrong type |
| E304 | assignment of the wrong type | E310 | declared twice in the same scope |
| E305 | condition is not a boolean | E311 | left side of `=` is not a variable |
| E306 | operator applied to the wrong types | | |

## The Lumen language

```
fn fib(n: int) -> int {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
```

Typed `let` declarations, functions with `->` return types, `if / else if / else`, `while`, `return`, the types `int`, `bool`, `str`, and the usual arithmetic, comparison and logical operators. The full grammar is at the top of `explainparse/grammar.py`.

## Mini-C and Mini-Java

```c
int fib(int n) {                        // Mini-C: int, bool, void; if/else, while, return
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
```

```java
class Main {                            // Mini-Java: one class of static methods;
    static int fib(int n) {             // int, boolean, String, void, arrays in parameters,
        if (n < 2) { return n; }        // System.out.println(...)
        return fib(n - 1) + fib(n - 2);
    }
}
```

Both are deliberately small subsets: bodies of `if`, `else` and `while` must be blocks in braces (this keeps the grammars LL(1)), there is no `for`, and Mini-C has no string variables or pointers. In Mini-C an `int` may be used as a condition, as in C.

## Web playground

`python -m explainparse.server` serves an editor with a language switch, session history, live error squiggles, explanation cards with a "How did it decide?" view, one-click fixes (also available via Ctrl+. in the editor), root-cause highlighting, the parse tree, and the panic-mode comparison.

To publish it for free on GitHub Pages, run `python -m explainparse.build_site` and publish the `site/` folder. On GitHub Pages there is no server, so the page runs the same Python engine in the visitor's browser using Pyodide.

## Project layout

```
explainparse/   languages, lexer, grammar, parser, engine, explain, semantic, extras, diagnostics, cli, evaluate, server, build_site
examples/       demo programs with errors (Lumen), examples/minic/, examples/minijava/;
                examples/corpus/ holds valid Lumen programs for evaluation
tests/          unit tests (python -m unittest)
web/            playground page
results/        evaluation output
```
