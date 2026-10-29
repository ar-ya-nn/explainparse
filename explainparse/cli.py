"""Command-line interface:  python -m explainparse FILE [--json] [--tree] [--fixed] [--compare]"""
import argparse
import json
import sys
from .engine import analyze
from .diagnostics import render
from .parser import format_tree
from .grammar import Grammar
from .languages import get_language, language_for_file, LANGUAGES

def show_decision(d):
    dec = d.decision or {}
    out = ["  how it decided:"]
    out += [f"    - {s}" for s in dec.get("steps", [])]
    rows = dec.get("candidates") or []
    if rows:
        out.append(f"    {'fix':<48} {'parsed':>6} {'cost':>6} {'dist':>6} {'other':>6} {'score':>6}")
        for r in rows:
            mark = "→" if r["chosen"] else " "
            out.append(f"  {mark} {r['fix'][:48]:<48} {r['progress']:>6} {r['cost']:>6} "
                       f"{r['distance']:>6} {r['adjust']:>6} {r['score']:>6}")
    return "\n".join(out)


def interactive(color, lang):
    print(f"ExplainParse interactive mode. Type {lang.name} code, then press Enter on an empty line to check it.")
    print("Type :quit to exit.\n")
    while True:
        lines = []
        try:
            while True:
                line = input("... " if lines else ">>> ")
                if line.strip() == ":quit":
                    return 0
                if line == "":
                    if lines:
                        break
                    continue
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        src = "\n".join(lines) + "\n"
        res = analyze(src, lang)
        if not res.diagnostics:
            print("✓ no errors\n")
            continue
        for d in res.diagnostics:
            print(render(d, src, "<input>", color))
            print()
        print("Fixed version:")
        print(res.fixed_source)
def main(argv=None):
    ap = argparse.ArgumentParser(prog="explainparse",
                                 description="Explainable error handling for Lumen, Mini-C and Mini-Java.")
    ap.add_argument("file", nargs="?", help="source file ('-' for stdin)")
    ap.add_argument("--lang", choices=sorted(LANGUAGES), help="language (default: from the file "
                    "extension: .lm Lumen, .c Mini-C, .java Mini-Java)")
    ap.add_argument("--why", action="store_true", help='show "how did it decide?" for each error')
    ap.add_argument("--no-semantic", action="store_true", help="syntax checks only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--tree", action="store_true", help="print the (repaired) parse tree")
    ap.add_argument("--fixed", action="store_true", help="print the source with all fixes applied")
    ap.add_argument("--compare", action="store_true", help="compare with panic-mode recovery")
    ap.add_argument("--grammar", action="store_true", help="print FIRST/FOLLOW sets and table stats")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    lang = get_language(args.lang) if args.lang else (
        language_for_file(args.file) if args.file and args.file != "-" else get_language("lumen"))
    if args.grammar:
        print(f"{lang.name} grammar")
        print(Grammar(lang.grammar).describe())
        return 0
    if not args.file:
        return interactive(sys.stdout.isatty() and not args.no_color, lang)
    src = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    res = analyze(src, lang, semantic_checks=not args.no_semantic)

    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
        return 1 if res.diagnostics else 0

    color = sys.stdout.isatty() and not args.no_color
    for d in res.diagnostics:
        print(render(d, src, args.file, color))
        if args.why:
            print(show_decision(d))
        print()
    n = len(res.diagnostics)
    print(f"{'✓ no errors' if n == 0 else f'{n} error' + ('s' if n != 1 else '')} in {args.file}")
    if args.compare:
        ours = len([d for d in res.diagnostics if d.phase == "parser"])
        print(f"syntax errors reported - panic-mode baseline: {res.baseline_errors}, "
              f"ExplainParse: {ours}")
    if args.fixed:
        print("\n--- fixed source " + "-" * 40)
        print(res.fixed_source)
    if args.tree and res.tree:
        print("\n--- parse tree " + "-" * 42)
        print(format_tree(res.tree), end="")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
