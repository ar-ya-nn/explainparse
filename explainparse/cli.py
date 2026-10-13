"""Command-line interface:  python -m explainparse FILE [--json] [--tree] [--fixed] [--compare]"""
import argparse
import json
import sys
from .engine import analyze
from .diagnostics import render
from .parser import format_tree
from .grammar import Grammar

def interactive(color):
    print("ExplainParse interactive mode. Type Lumen code, then press Enter on an empty line to check it.")
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
        res = analyze(src)
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
                                 description="Explainable syntax error handling for Lumen.")
    ap.add_argument("file", nargs="?", help="Lumen source file ('-' for stdin)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--tree", action="store_true", help="print the (repaired) parse tree")
    ap.add_argument("--fixed", action="store_true", help="print the source with all fixes applied")
    ap.add_argument("--compare", action="store_true", help="compare with panic-mode recovery")
    ap.add_argument("--grammar", action="store_true", help="print FIRST/FOLLOW sets and table stats")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    if args.grammar:
        print(Grammar().describe())
        return 0
    if not args.file:
        return interactive(sys.stdout.isatty() and not args.no_color)
    src = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    res = analyze(src)

    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
        return 1 if res.diagnostics else 0

    color = sys.stdout.isatty() and not args.no_color
    for d in res.diagnostics:
        print(render(d, src, args.file, color))
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
