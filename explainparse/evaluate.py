"""Mutation-based evaluation: inject realistic syntax errors into valid programs and measure
detection, localisation, repair quality and cascading errors vs. a panic-mode baseline.

    python -m explainparse.evaluate examples/corpus --per-type 40 --seed 7 --out results
"""
import argparse
import glob
import json
import os
import random
from collections import defaultdict
from .lexer import lex
from .engine import analyze, get_parser, group_of

MUTATIONS = ["drop_semicolon", "drop_close_paren", "drop_close_brace", "drop_open_paren",
             "extra_token", "keyword_typo", "drop_operator", "random_delete"]
TYPO_TARGETS = {"while", "return", "bool", "else", "false"}


def mutate(src, kind, rng):
    """Return (mutant_source, line_of_mutation) or None if the program has no suitable site."""
    toks = [t for t in lex(src)[0] if t.kind != "EOF"]

    def delete(t):
        i = toks.index(t)
        before = toks[i - 1].line if i > 0 else t.line
        after = toks[i + 1].line if i + 1 < len(toks) else t.line
        # keep neighbouring words apart: deleting '(' in `f(x` gives `f x`, not `fx`
        glue = " " if (src[t.start - 1:t.start].isalnum() and src[t.end:t.end + 1].isalnum()) else ""
        return src[:t.start] + glue + src[t.end:], set(range(before, after + 1))

    if kind in ("drop_semicolon", "drop_close_paren", "drop_close_brace", "drop_open_paren"):
        want = {"drop_semicolon": ";", "drop_close_paren": ")", "drop_close_brace": "}",
                "drop_open_paren": "("}[kind]
        sites = [t for t in toks if t.kind == want]
        return delete(rng.choice(sites)) if sites else None
    if kind == "drop_operator":
        sites = [t for t in toks if t.kind in {"+", "-", "*", "/", "%", "<", ">", "==", "&&", "||", ">=", "<="}]
        return delete(rng.choice(sites)) if sites else None
    if kind == "random_delete":
        return delete(rng.choice(toks))
    if kind == "extra_token":
        t = rng.choice(toks)
        ins = rng.choice([";", ")", "}", ",", "(", "="])
        return src[:t.start] + ins + src[t.start:], {t.line}
    if kind == "keyword_typo":
        sites = [t for t in toks if t.kind in TYPO_TARGETS]
        if not sites:
            return None
        t = rng.choice(sites)
        w = list(t.lexeme)
        i = rng.randrange(len(w) - 1)
        w[i], w[i + 1] = w[i + 1], w[i]
        return src[:t.start] + "".join(w) + src[t.end:], {t.line}
    raise ValueError(kind)


def evaluate(files, per_type=40, seed=7):
    rng = random.Random(seed)
    parser = get_parser()
    programs = [(f, open(f, encoding="utf-8").read()) for f in files]
    rows = []
    for kind in MUTATIONS:
        made, attempts = 0, 0
        while made < per_type and attempts < per_type * 20:
            attempts += 1
            name, src = rng.choice(programs)
            m = mutate(src, kind, rng)
            if m is None:
                continue
            mutant, site = m
            res = analyze(mutant)
            if not res.diagnostics:
                continue           # mutation happened to produce a valid program
            made += 1
            orig_kinds = [t.kind for t in lex(src)[0]]
            fixed_toks, fixed_lex_errs = lex(res.fixed_source)
            fixed_kinds = [t.kind for t in fixed_toks]
            fixed_valid = not fixed_lex_errs and parser.run(fixed_kinds)[0] == "ok"
            mk = [t.kind for t in lex(mutant)[0]]
            base_err = parser.panic_mode_errors(mk)
            mtoks = lex(mutant)[0]
            base_line = mtoks[base_err[0]].line if base_err else None
            d0 = res.diagnostics[0]
            ours_lines = {d0.line} | ({d0.root_cause["line"]} if d0.root_cause else set())
            rows.append({
                "type": kind, "program": os.path.basename(name), "lines": sorted(site),
                "n_diags": len(res.diagnostics), "n_baseline": len(base_err),
                "loc_exact": bool(site & ours_lines),
                "loc_near": any(abs(a - b) <= 1 for a in site for b in ours_lines),
                "base_loc_exact": base_line in site,
                "base_loc_near": base_line is not None and any(abs(a - base_line) <= 1 for a in site),
                "repair_exact": fixed_kinds == orig_kinds,
                "repair_valid": fixed_valid,
                "repair_equiv": [group_of(k) for k in fixed_kinds] == [group_of(k) for k in orig_kinds],
                "confidence": d0.confidence, "code": d0.code,
            })
    return rows


def summarise(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r["type"]].append(r)
    groups["ALL"] = rows
    pct = lambda rs, k: 100.0 * sum(1 for r in rs if r[k]) / len(rs)
    avg = lambda rs, k: sum(r[k] for r in rs) / len(rs)
    out = {}
    for g, rs in groups.items():
        out[g] = {
            "n": len(rs),
            "loc_exact": pct(rs, "loc_exact"), "base_loc_exact": pct(rs, "base_loc_exact"),
            "loc_near": pct(rs, "loc_near"), "base_loc_near": pct(rs, "base_loc_near"),
            "repair_exact": pct(rs, "repair_exact"), "repair_valid": pct(rs, "repair_valid"),
            "repair_equiv": pct(rs, "repair_equiv"),
            "avg_diags": avg(rs, "n_diags"), "avg_baseline": avg(rs, "n_baseline"),
            "single_diag": 100.0 * sum(1 for r in rs if r["n_diags"] == 1) / len(rs),
        }
    # calibration: is the engine's confidence meaningful?
    calib = {}
    for level in ("high", "medium", "low"):
        rs = [r for r in rows if r["confidence"] == level]
        if rs:
            calib[level] = {"n": len(rs), "repair_equiv": pct(rs, "repair_equiv")}
    return out, calib


def markdown(summary, calib):
    order = [m for m in MUTATIONS if m in summary] + ["ALL"]
    lines = ["| Mutation | N | Exact line (ours / panic) | ±1 line (ours / panic) | Exact repair | "
             "Equivalent repair* | Valid repair | Avg errors reported (ours / panic) | Single diagnostic |",
             "|---|---|---|---|---|---|---|---|---|"]
    for g in order:
        s = summary[g]
        name = f"**{g}**" if g == "ALL" else g
        lines.append(f"| {name} | {s['n']} | {s['loc_exact']:.0f}% / {s['base_loc_exact']:.0f}% | "
                     f"{s['loc_near']:.0f}% / {s['base_loc_near']:.0f}% | {s['repair_exact']:.0f}% | "
                     f"{s['repair_equiv']:.0f}% | {s['repair_valid']:.0f}% | {s['avg_diags']:.2f} / {s['avg_baseline']:.2f} | "
                     f"{s['single_diag']:.0f}% |")
    lines += ["", "\\* Equivalent repair: identical token sequence up to interchangeable tokens "
              "(any operator for a deleted operator, any type for a deleted type).",
              "", "Confidence calibration (share of correct repairs at each confidence level):", "",
              "| Confidence | N | Correct repair |", "|---|---|---|"]
    for lvl, c in calib.items():
        lines.append(f"| {lvl} | {c['n']} | {c["repair_equiv"]:.0f}% |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--per-type", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.corpus, "*.lm")))
    rows = evaluate(files, a.per_type, a.seed)
    summary, calib = summarise(rows)
    md = markdown(summary, calib)
    print(md)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        json.dump({"rows": rows, "summary": summary, "calibration": calib},
                  open(os.path.join(a.out, "results.json"), "w"), indent=2)
        open(os.path.join(a.out, "results.md"), "w").write(md + "\n")


if __name__ == "__main__":
    main()
