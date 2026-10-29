"""Teaching views for the playground: grammar tables, a step-by-step LL(1) trace,
and "Break it", which injects a realistic mistake into a working program."""
import random
from .engine import analyze, get_parser
from .languages import get_language
from .evaluate import mutate, MUTATIONS

BREAK_LABELS = {
    "drop_semicolon": "deleted a ';'", "drop_close_paren": "deleted a ')'",
    "drop_close_brace": "deleted a '}'", "drop_open_paren": "deleted a '('",
    "extra_token": "added a stray symbol", "keyword_typo": "misspelled a keyword",
    "drop_operator": "deleted an operator", "random_delete": "deleted a random token",
}


def _terminal_order(g):
    order = []
    for prods in g.productions.values():
        for p in prods:
            for s in p:
                if s in g.terminals and s not in order:
                    order.append(s)
    named = [t for t in order if t.isupper()]
    words = [t for t in order if not t.isupper() and t[:1].isalpha()]
    symbols = [t for t in order if t not in named and t not in words]
    rest = sorted(g.terminals - set(order))
    return words + [t for t in named if t != "EOF"] + symbols + rest + ["EOF"]


def grammar_info(lang="lumen"):
    language = get_language(lang)
    g = get_parser(language).grammar
    prods, index = [], {}
    for nt, alts in g.productions.items():
        for p in alts:
            index[(nt, p)] = len(prods)
            prods.append({"lhs": nt, "rhs": list(p)})
    table = {nt: {t: index[(nt, p)] for t, p in row.items()} for nt, row in g.table.items()}
    return {
        "language": language.name, "start": g.start,
        "nonterminals": list(g.productions), "terminals": _terminal_order(g),
        "productions": prods, "table": table,
        "first": {nt: sorted(g.first[nt]) for nt in g.productions},
        "follow": {nt: sorted(g.follow[nt]) for nt in g.productions},
        "nullable": sorted(g.nullable), "conflicts": len(g.conflicts),
        "entries": sum(len(r) for r in g.table.values()),
    }


def trace(source, lang="lumen"):
    """LL(1) trace of the program (of the repaired program if it has syntax errors)."""
    language = get_language(lang)
    res = analyze(source, language, semantic_checks=False)
    if res.tree is None or len(res.tokens) <= 1:
        return {"ok": False, "steps": [], "tokens": []}
    steps, truncated = get_parser(language).trace(res.tokens)
    toks = [{"kind": t.kind, "lexeme": t.lexeme, "line": t.line, "col": t.col,
             "start": t.start, "end": t.end, "synthetic": t.synthetic} for t in res.tokens]
    return {"ok": True, "steps": steps, "tokens": toks, "truncated": truncated,
            "terminals": sorted(get_parser(language).terminals),
            "repaired": any(d.phase == "parser" for d in res.diagnostics)}


def break_program(source, lang="lumen", seed=None):
    """Inject one realistic syntax mistake (the same mutations used in the evaluation)."""
    language = get_language(lang)
    rng = random.Random(seed)
    kinds = list(MUTATIONS)
    for _ in range(40):
        kind = rng.choice(kinds)
        m = mutate(source, kind, rng, language)
        if m is None:
            continue
        mutant, lines = m
        if any(d.phase in ("parser", "lexer") for d in analyze(mutant, language, False).diagnostics):
            i = next((k for k, (a, b) in enumerate(zip(source, mutant)) if a != b),
                     min(len(source), len(mutant)))
            return {"ok": True, "source": mutant, "kind": kind, "label": BREAK_LABELS[kind],
                    "line": source[:i].count("\n") + 1}
    return {"ok": False, "source": source}
