"""The ExplainParse engine: detect -> search repairs -> localise root cause -> explain -> continue."""
from dataclasses import dataclass, field
from typing import List, Optional
from .lexer import lex
from .parser import LL1Parser, Node
from .grammar import Grammar
from .tokens import Token
from .diagnostics import Diagnostic, apply_edits
from .languages import get_language, Language, LANGUAGES
from . import explain
from . import semantic

WINDOW = 6       # tokens a repair must let the parser advance to be fully trusted
BACK = 3         # how many tokens before the error point repairs may be placed
TYPO_BACK = 12   # how far back to look for misspelled keywords
MAX_ERRORS = 25

# Groups cover every language's keywords (a keyword of another language is just an ID here).
TYPE_GROUP = frozenset(t for l in LANGUAGES.values() for t in l.types)
VALUE_GROUP = frozenset({"INT", "STRING", "ID", "true", "false"})
OP_GROUP = frozenset({"+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"})
STRUCTURAL = frozenset({"fn", "let", "if", "else", "while", "return", "class", "public",
                        "private", "static"})
NO_SPACE_BEFORE = frozenset({";", ")", ",", ":"})
PLACEHOLDER = {"ID": "name", "INT": "0", "STRING": '""'}
PRIORITY = {";": 6, ")": 5, "}": 5, ",": 4, "INT": 3, "int": 3, "+": 2, "ID": 1}

_parsers = {}


def get_parser(lang="lumen"):
    """One LL(1) parser per language, built once from that language's grammar text."""
    lang = get_language(lang)
    if lang.id not in _parsers:
        p = LL1Parser(Grammar(lang.grammar))
        p.lang = lang
        _parsers[lang.id] = p
    return _parsers[lang.id]


def group_of(t):
    if t in TYPE_GROUP:
        return "type"
    if t in VALUE_GROUP:
        return "value"
    if t in OP_GROUP:
        return "operator"
    return t


@dataclass
class Candidate:
    kind: str                 # insert | delete | replace
    pos: int                  # index in the working token list
    terminal: Optional[str]   # for insert/replace
    advanced: int = 0
    score: float = 0.0
    typo: bool = False
    typo_dist: int = 0
    root: Optional[dict] = None
    cost: float = 0.0         # score parts, kept for the "How did it decide?" view
    dist: float = 0.0
    lookahead: bool = False


@dataclass
class AnalysisResult:
    source: str
    diagnostics: List[Diagnostic]
    tree: Optional[Node]
    fixed_source: str
    baseline_errors: int
    tokens: List[Token] = field(default_factory=list)
    language: str = "lumen"
    lex_tokens: List[Token] = field(default_factory=list)

    @property
    def ok(self):
        return not self.diagnostics

    def to_dict(self):
        return {"ok": self.ok, "language": self.language,
                "semantic_errors": len([d for d in self.diagnostics if d.phase == "semantic"]),
                "diagnostics": [d.to_dict() for d in self.diagnostics],
                "fixed_source": self.fixed_source,
                "baseline_errors": self.baseline_errors,
                "explainparse_errors": len([d for d in self.diagnostics if d.phase == "parser"]),
                "lex_tokens": [{"kind": t.kind, "lexeme": t.lexeme, "line": t.line, "col": t.col,
                                "start": t.start, "end": t.end} for t in self.lex_tokens
                               if t.kind != "EOF"],
                "tree": self.tree.to_dict() if self.tree else None}


# ---------------------------------------------------------------- helpers
def damerau(a, b):
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def keyword_typo(word, keywords=None):
    keywords = get_language("lumen").keywords if keywords is None else keywords
    best = None
    for kw in keywords:
        if abs(len(kw) - len(word)) > 2 or len(word) < 2:
            continue
        dist = damerau(word.lower(), kw.lower())
        if dist == 0 and word != kw:
            dist = 1      # wrong capitalisation, e.g. 'string' for Java's 'String'
        elif dist == 0:
            continue
        if dist > 2 or (dist == 2 and len(kw) < 5):
            continue
        if best is None or dist < best[1]:
            best = (kw, dist)
    return best


class Layout:
    """Line/indentation facts about the original source, used for root-cause analysis."""
    def __init__(self, source, tokens):
        self.line_starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self.line_starts.append(i + 1)
        self.indent = {}
        for t in tokens:
            if t.kind != "EOF" and t.line not in self.indent:
                self.indent[t.line] = t.col

    def first_on_line(self, tok):
        if tok.synthetic:
            return False
        if tok.kind == "EOF":
            return True
        return self.indent.get(tok.line) == tok.col

    def indent_of(self, tok):
        return self.indent.get(tok.line, 1)


# ---------------------------------------------------------------- repair search
def _evaluate(parser, kinds, snaps, e, kind, p, t):
    if kind == "insert":
        nk, delta = kinds[:p] + [t] + kinds[p:], 1
    elif kind == "delete":
        nk, delta = kinds[:p] + kinds[p + 1:], -1
    else:
        nk, delta = kinds[:p] + [t] + kinds[p + 1:], 0
    status, reached, _ = parser.run(nk, p, snaps[p], stop=e + WINDOW + delta)
    if status in ("ok", "limit"):
        return WINDOW
    if kind == "insert":
        orig = reached - 1 if reached > p else p
    elif kind == "delete":
        orig = reached + 1
    else:
        orig = reached
    return orig - e


def _score(c, e, expected):
    cost = {"insert": 1.0, "delete": 1.3, "replace": 1.6}[c.kind]
    if c.kind == "insert" and c.terminal in VALUE_GROUP:
        cost = 2.2     # inventing a value is less likely than a stray/missing symbol
    if c.typo:
        cost = -2.5 if c.typo_dist == 1 else -1.5   # a near-keyword is strong evidence
    dist = 0 if c.typo else 0.6 * (e - c.pos)
    c.cost, c.dist = cost, dist
    bonus = 0.5 if (c.kind == "insert" and c.pos == e and c.terminal in expected) else 0
    return min(c.advanced, WINDOW) * 3 - cost - dist + bonus


def _layout_bonus(c, work):
    """';' is usually forgotten at the end of a line, not in the middle of one."""
    if c.kind == "insert" and c.terminal == ";" and 0 < c.pos < len(work):
        prev, nxt = work[c.pos - 1], work[c.pos]
        if nxt.kind == "EOF" or nxt.kind == "}" or nxt.line != prev.line:
            return 0.5
        return -0.8
    return 0.0


def search_repairs(parser, work, kinds, snaps, e, expected, stats=None):
    insertable = sorted(t for t in parser.terminals if t != "EOF")
    keywords = parser.lang.keywords if hasattr(parser, "lang") else None
    cands = []
    tried = [0]

    def consider(kind, p, t, **kw):
        if p not in snaps:
            return
        tried[0] += 1
        c = Candidate(kind, p, t, **kw)
        c.advanced = _evaluate(parser, kinds, snaps, e, kind, p, t)
        if c.advanced >= 1:
            c.score = _score(c, e, expected) + _layout_bonus(c, work)
            if kind == "replace" and not c.typo and work[p].kind in ("ID", "INT", "STRING") \
                    and t not in VALUE_GROUP:
                c.score -= 1.0   # overwriting a name or value with a symbol is unlikely
            if kind in ("delete", "replace") and not c.typo and work[p].kind in STRUCTURAL:
                c.score -= 1.5   # people rarely type a statement keyword by accident
            if kind == "delete" and work[p].kind in (")", "}") and unmatched_closer(work, p):
                c.score += 2.0   # a closer with no opener is very likely a stray
            cands.append(c)

    for p in range(e, max(0, e - BACK) - 1, -1):
        for t in insertable:
            consider("insert", p, t)
        tok = work[p]
        if tok.kind != "EOF" and not tok.synthetic:
            consider("delete", p, None)
            for t in insertable:
                if t != tok.kind and not (t in VALUE_GROUP and tok.kind in VALUE_GROUP):
                    consider("replace", p, t)
    for p in range(max(0, e - TYPO_BACK), e + 1):
        tok = work[p]
        if tok.kind == "ID" and not tok.synthetic:
            hit = keyword_typo(tok.lexeme, keywords)
            if hit:
                consider("replace", p, hit[0], typo=True, typo_dist=hit[1])
    cands.sort(key=lambda c: (c.score, PRIORITY.get(c.terminal, 0), -abs(e - c.pos)), reverse=True)
    _lookahead_rerank(parser, work, kinds, snaps, e, cands, expected)
    if stats is not None:
        stats["tried"] = tried[0]
        stats["viable"] = len([c for c in cands if c.advanced >= 2])
    return [c for c in cands if c.advanced >= 2]


def _apply_kinds(kinds, c):
    if c.kind == "insert":
        return kinds[:c.pos] + [c.terminal] + kinds[c.pos:], 1
    if c.kind == "delete":
        return kinds[:c.pos] + kinds[c.pos + 1:], -1
    return kinds[:c.pos] + [c.terminal] + kinds[c.pos + 1:], 0


def _lookahead_rerank(parser, work, kinds, snaps, e, cands, expected, top=12):
    """Look one edit further. Two mistakes often sit side by side (`= 5` then `retrun x;`):
    the right first edit may only work once a cheap second edit (e.g. a keyword typo fix)
    is made, so score each promising first edit together with its best follow-up."""
    insertable = sorted(t for t in parser.terminals if t != "EOF")
    keywords = parser.lang.keywords if hasattr(parser, "lang") else None
    pool = cands[:top] + [c for c in cands[top:]
                          if c.kind == "insert" and c.pos == e and c.terminal in expected]
    for c in pool:
        if c.advanced >= WINDOW:
            continue
        nk, delta = _apply_kinds(kinds, c)
        nwork = (work[:c.pos] + [None] + work[c.pos:] if c.kind == "insert" else
                 work[:c.pos] + work[c.pos + 1:] if c.kind == "delete" else work)
        ns = {}
        status, e2, _ = parser.run(nk, c.pos, snaps[c.pos], snaps=ns)
        if status == "ok":
            continue
        best_gain = None   # (progress after both edits) * 3 - cost of second edit
        for t in insertable:
            adv2 = _evaluate(parser, nk, ns, e2, "insert", e2, t)
            cost2 = 2.2 if t in VALUE_GROUP else 1.0
            if adv2 >= 1:
                gain = min(e2 - delta - e + adv2, WINDOW) * 3 - cost2
                best_gain = gain if best_gain is None else max(best_gain, gain)
        if nk[e2] != "EOF":
            adv2 = _evaluate(parser, nk, ns, e2, "delete", e2, None)
            if adv2 >= 1:
                gain = min(e2 - delta - e + adv2, WINDOW) * 3 - 1.3
                best_gain = gain if best_gain is None else max(best_gain, gain)
        for p2 in range(max(c.pos, e2 - TYPO_BACK), e2 + 1):
            tok = nwork[p2] if p2 < len(nwork) else None
            if tok is None or tok.kind != "ID" or tok.synthetic or p2 not in ns:
                continue
            hit = keyword_typo(tok.lexeme, keywords)
            if hit:
                adv2 = _evaluate(parser, nk, ns, e2, "replace", p2, hit[0])
                if adv2 >= 1:
                    gain = min(e2 - delta - e + adv2, WINDOW) * 3 - (-2.5 if hit[1] == 1 else -1.5)
                    best_gain = gain if best_gain is None else max(best_gain, gain)
        if best_gain is None:
            continue
        base = c.score - min(c.advanced, WINDOW) * 3     # c's own costs and bonuses
        combined = base + best_gain - 0.5                 # small penalty for needing two edits
        if combined > c.score:
            c.score = combined
            c.lookahead = True
            c.advanced = max(c.advanced, min(WINDOW, int(best_gain // 3) + 1))
    cands.sort(key=lambda c: (c.score, PRIORITY.get(c.terminal, 0), -abs(e - c.pos)), reverse=True)


def outcome_key(c, kinds, e):
    """Two repairs are 'the same fix' if they produce the same token sequence (up to
    interchangeable terminals such as int/bool/str or + / -)."""
    if c.kind == "insert":
        nk = kinds[:c.pos] + [c.terminal] + kinds[c.pos:]
    elif c.kind == "delete":
        nk = kinds[:c.pos] + kinds[c.pos + 1:]
    else:
        nk = kinds[:c.pos] + [c.terminal] + kinds[c.pos + 1:]
    lo = max(0, e - TYPO_BACK - 1)
    return tuple(group_of(k) for k in nk[lo:e + 3])


def unmatched_closer(work, p):
    opener = "(" if work[p].kind == ")" else "{"
    depth = 0
    for t in work[:p]:
        if t.kind == opener:
            depth += 1
        elif t.kind == work[p].kind:
            depth -= 1
    return depth <= 0


def confidence_of(best, cands, kinds, e):
    bk = outcome_key(best, kinds, e)
    rivals = [c for c in cands if outcome_key(c, kinds, e) != bk]
    if not rivals:
        return "high"
    margin = best.score - rivals[0].score
    return "high" if margin >= 2.5 else "medium" if margin >= 1.0 else "low"


# ---------------------------------------------------------------- root causes
def brace_root_cause(work, e, layout):
    """Find which '{' is really unclosed and where its '}' belongs, using indentation."""
    stack, pairs = [], []
    for i in range(e):
        k = work[i].kind
        if k == "{":
            stack.append(i)
        elif k == "}" and stack:
            pairs.append((stack.pop(), i))
    if not stack:
        return None
    outer = stack[-1]
    suspects = [(a, b) for a, b in pairs
                if a > outer and layout.first_on_line(work[b])
                and work[b].col != layout.indent_of(work[a])]
    if suspects:
        a, b = min(suspects, key=lambda ab: ab[1])   # first misattributed closer = root cause
        base = layout.indent_of(work[a])
        for q in range(a + 1, b + 1):
            t = work[q]
            if t.line != work[a].line and layout.first_on_line(t) and t.col <= base and t.kind != "}":
                return {"opener": a, "pos": q, "stolen_closer": b}
    base = layout.indent_of(work[outer])
    for q in range(outer + 1, e + 1):
        t = work[q]
        if t.line != work[outer].line and layout.first_on_line(t) and t.col <= base and t.kind != "}":
            return {"opener": outer, "pos": q, "stolen_closer": None}
    return {"opener": outer, "pos": e, "stolen_closer": None}


def paren_opener(work, e):
    stack = []
    for i in range(e):
        if work[i].kind == "(":
            stack.append(i)
        elif work[i].kind == ")" and stack:
            stack.pop()
    return stack[-1] if stack else None


# ---------------------------------------------------------------- applying repairs
def make_edit(work, c, source, layout, brace_indent=None):
    """Turn a candidate into (new token list, source edit (start,end,text), synthetic token)."""
    if c.kind == "insert":
        t = c.terminal
        nxt = work[c.pos]
        prev = next((w for w in reversed(work[:c.pos])), None)
        text = PLACEHOLDER.get(t, t)
        if t == "}" and nxt.kind != "EOF" and layout.first_on_line(nxt) and c.pos > 0 \
                and work[c.pos - 1].line != nxt.line:
            ind = " " * ((brace_indent or 1) - 1)
            off = layout.line_starts[nxt.line - 1]
            edit = (off, off, ind + "}\n")
            synth = Token(t, "}", nxt.line, max(brace_indent or 1, 1), off, off, synthetic=True)
        else:
            off = prev.end if prev else 0
            line = prev.line if prev else 1
            col = prev.end_col if prev else 1
            gap = source[off:nxt.start] if prev else ""
            if t == "}" and nxt.kind == "EOF":
                ind = " " * ((brace_indent or 1) - 1)
                text_ins = "\n" + ind + "}"
            elif t == "(" and prev.kind == "ID" and gap.strip() == "" and "\n" not in gap:
                edit = (prev.end, nxt.start, "(")    # `lcm a` -> `lcm(a`
                synth = Token(t, "(", line, col, prev.end, prev.end, synthetic=True)
                return work[:c.pos] + [synth] + work[c.pos:], edit, synth
            elif gap and gap.strip() == "" and "\n" not in gap and t not in NO_SPACE_BEFORE \
                    and nxt.kind != "EOF":
                off = nxt.start   # put it right before the next token: `if (x` not `if ( x`
                text_ins = text if t == "(" else text + " "
            else:
                text_ins = text if (t in NO_SPACE_BEFORE or prev is None) else " " + text
            edit = (off, off, text_ins)
            synth = Token(t, text, line, col, off, off, synthetic=True)
        return work[:c.pos] + [synth] + work[c.pos:], edit, synth
    tok = work[c.pos]
    if c.kind == "delete":
        start, end = tok.start, tok.end
        if source[end:end + 1] == " " and (start == 0 or source[start - 1] in " \t"):
            end += 1
        return work[:c.pos] + work[c.pos + 1:], (start, end, ""), None
    text = PLACEHOLDER.get(c.terminal, c.terminal)
    new = Token(c.terminal, text, tok.line, tok.col, tok.start, tok.end)
    return work[:c.pos] + [new] + work[c.pos + 1:], (tok.start, tok.end, text), new


# ---------------------------------------------------------------- "How did it decide?"
def _row(c, work, chosen):
    progress = min(c.advanced, WINDOW)
    other = c.score - (3 * progress - c.cost - c.dist)
    return {"fix": explain.describe_candidate(c, work), "chosen": chosen,
            "progress": progress, "cost": round(c.cost, 2), "distance": round(c.dist, 2),
            "adjust": round(other, 2), "score": round(c.score, 2), "lookahead": c.lookahead}


def decision_info(best, cands, kinds, work, e, expected, conf, stats, moved_from=None):
    """Everything the parser weighed for one error, in a form the UI can show."""
    found = work[e]
    shown, seen = [], set()
    bk = outcome_key(best, kinds, e)
    if best not in cands:
        shown.append(_row(best, work, True))
        seen.add(bk)
    for c in cands:
        k = outcome_key(c, kinds, e)
        if k in seen:
            continue
        seen.add(k)
        shown.append(_row(c, work, k == bk))
        if len(shown) >= 5:
            break
    rival = next((c for c in cands if outcome_key(c, kinds, e) != bk), None)
    margin = round(best.score - rival.score, 2) if rival is not None else None
    steps = [
        f"The LL(1) parser stopped at {explain.describe_token(found)} "
        f"(line {found.line}, column {found.col}): no table entry matches it.",
        f"At that point the grammar allowed {explain.expected_text(expected)}.",
        f"It tried {stats.get('tried', 0)} single-token edits (inserting, deleting or replacing a "
        f"token up to {BACK} tokens back, and fixing misspelled keywords up to {TYPO_BACK} back), "
        f"re-parsing from the saved stack each time. {stats.get('viable', 0)} let parsing "
        f"continue at least 2 tokens.",
        f"Each fix is scored as 3 × tokens parsed after it (up to {WINDOW}) − edit cost − "
        f"distance from the error, plus adjustments for layout, typos and two-edit lookahead.",
    ]
    if moved_from:
        for r in shown:
            if r["fix"] == moved_from and not r["chosen"]:
                r["moved"] = True
        steps.append(f"Root-cause analysis moved the fix: the best-scoring repair was to "
                     f"{moved_from}, but indentation shows where the block really ends.")
        steps.append(f"Confidence is {conf}: several places for the '}}' let parsing continue "
                     f"equally well, and the indentation evidence picks this one.")
        margin = None
    elif margin is None:
        steps.append("No different fix worked, so confidence is high.")
    else:
        steps.append(f"Confidence is {conf}: the chosen fix scores {margin:g} above the best "
                     f"different fix (high if at least 2.5, medium if at least 1.0).")
    return {"kind": "repair", "steps": steps, "candidates": shown, "margin": margin}


# ---------------------------------------------------------------- main entry
def analyze(source: str, lang="lumen", semantic_checks=True) -> AnalysisResult:
    language = get_language(lang)
    parser = get_parser(language)
    tokens, diagnostics = lex(source, language)
    for d in diagnostics:
        d.decision = {"kind": "rule", "steps": [
            "Found by the lexer, while splitting the source into tokens (before parsing).",
            f"The text here cannot form any {language.name} token, so a fixed lexical rule "
            f"applies: {d.title}.",
            "The lexer recovers (as noted in the explanation) so parsing can still continue."]}
    if len(tokens) == 1 and not diagnostics:
        # nothing written yet (only whitespace or comments): nothing to report
        return AnalysisResult(source, [], None, source, 0, tokens, language.id, [])
    layout = Layout(source, tokens)
    baseline = len(parser.panic_mode_errors([t.kind for t in tokens]))
    work = list(tokens)
    edits = [e for d in diagnostics if d.fix for e in d.fix.edits]
    tree = None

    for _ in range(MAX_ERRORS):
        kinds = [t.kind for t in work]
        snaps = {}
        status, e, stack = parser.run(kinds, 0, [parser.start], snaps=snaps)
        if status == "ok":
            break
        expected = parser.expected(stack)
        stats = {}
        cands = search_repairs(parser, work, kinds, snaps, e, expected, stats)
        if cands:
            best = cands[0]
            conf = confidence_of(best, cands, kinds, e)
        elif work[e].kind != "EOF":
            best, conf = Candidate("delete", e, None), "low"
        else:
            best, conf = Candidate("insert", e, sorted(expected)[0]), "low"
        moved_from = None

        # Root-cause localisation for unclosed blocks / parentheses
        brace_indent = None
        if best.kind == "insert" and best.terminal == "}":
            rc = brace_root_cause(work, e, layout)
            if rc:
                brace_indent = layout.indent_of(work[rc["opener"]])
                if rc["pos"] < best.pos and rc["pos"] in snaps:
                    adv = _evaluate(parser, kinds, snaps, e, "insert", rc["pos"], "}")
                    if adv >= min(best.advanced, WINDOW):
                        moved_from = explain.describe_candidate(best, work)
                        old = best
                        best = Candidate("insert", rc["pos"], "}", adv, best.score)
                        best.cost, best.dist = old.cost, old.dist
                        best.score = old.score
                        conf = "high" if conf != "low" else "medium"
                best.root = rc
        elif best.kind == "insert" and best.terminal == ")":
            op = paren_opener(work, e)
            if op is not None:
                best.root = {"opener": op}

        ctx = explain.build_context(parser, work, e, expected)
        new_work, edit, synth = make_edit(work, best, source, layout, brace_indent)
        diag = explain.explain(best, ctx, conf, source, layout, edit, synth, cands)
        diag.decision = decision_info(best, cands, kinds, work, e, expected, diag.confidence,
                                      stats, moved_from)
        if conf != "high":
            bk = outcome_key(best, kinds, e)
            rival = next((c for c in cands if outcome_key(c, kinds, e) != bk), None)
            if rival is not None:
                diag.alternative = explain.describe_candidate(rival, work)
        prev = diagnostics[-1] if diagnostics else None
        if (best.kind == "insert" and best.terminal == ")" and best.root
                and work[best.root["opener"]].synthetic and prev is not None
                and prev.code == "E204" and prev.fix.edits[-1][2].strip() == "("):
            merged = explain.merge_parens(prev, diag, edit, ctx, best, source)
            merged.decision = prev.decision
            if merged.decision:
                merged.decision = dict(merged.decision, steps=merged.decision["steps"] + [
                    "A second search then found the matching ')' was missing too, so both "
                    "fixes are shown as one: the condition needs parentheses."])
            diagnostics[-1] = merged
        else:
            diagnostics.append(diag)
        edits.append(edit)
        work = new_work

    status, _, _, root, _ = parser.parse_tree(work)
    if status == "ok":
        tree = root
    if semantic_checks and tree is not None and not diagnostics:
        # Semantic analysis runs on syntactically correct programs only, so a guessed
        # repair can never cause a misleading type error.
        for d in semantic.check(tree, language, source):
            diagnostics.append(d)
            if d.fix:
                edits.extend(d.fix.edits)
    diagnostics.sort(key=lambda d: (d.line, d.col))
    fixed = apply_edits(source, edits)
    return AnalysisResult(source, diagnostics, tree, fixed, baseline, work, language.id, tokens)
