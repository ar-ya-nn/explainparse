"""The ExplainParse engine: detect -> search repairs -> localise root cause -> explain -> continue."""
from dataclasses import dataclass, field
from typing import List, Optional
from .lexer import lex
from .parser import LL1Parser, Node
from .tokens import Token, KEYWORDS
from .diagnostics import Diagnostic, apply_edits
from . import explain

WINDOW = 6       # tokens a repair must let the parser advance to be fully trusted
BACK = 3         # how many tokens before the error point repairs may be placed
TYPO_BACK = 12   # how far back to look for misspelled keywords
MAX_ERRORS = 25

TYPE_GROUP = frozenset({"int", "bool", "str"})
VALUE_GROUP = frozenset({"INT", "STRING", "ID", "true", "false"})
OP_GROUP = frozenset({"+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"})
STRUCTURAL = frozenset({"fn", "let", "if", "else", "while", "return"})
NO_SPACE_BEFORE = frozenset({";", ")", ",", ":"})
PLACEHOLDER = {"ID": "name", "INT": "0", "STRING": '""'}
PRIORITY = {";": 6, ")": 5, "}": 5, ",": 4, "INT": 3, "int": 3, "+": 2, "ID": 1}

_parser = None


def get_parser():
    global _parser
    if _parser is None:
        _parser = LL1Parser()
    return _parser


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


@dataclass
class AnalysisResult:
    source: str
    diagnostics: List[Diagnostic]
    tree: Optional[Node]
    fixed_source: str
    baseline_errors: int
    tokens: List[Token] = field(default_factory=list)

    @property
    def ok(self):
        return not self.diagnostics

    def to_dict(self):
        return {"ok": self.ok,
                "diagnostics": [d.to_dict() for d in self.diagnostics],
                "fixed_source": self.fixed_source,
                "baseline_errors": self.baseline_errors,
                "explainparse_errors": len([d for d in self.diagnostics if d.phase == "parser"]),
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


def keyword_typo(word):
    best = None
    for kw in KEYWORDS:
        if abs(len(kw) - len(word)) > 2 or len(word) < 2:
            continue
        dist = damerau(word.lower(), kw)
        if dist == 0 or dist > 2 or (dist == 2 and len(kw) < 5):
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


def search_repairs(parser, work, kinds, snaps, e, expected):
    insertable = sorted(t for t in parser.terminals if t != "EOF")
    cands = []

    def consider(kind, p, t, **kw):
        if p not in snaps:
            return
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
            hit = keyword_typo(tok.lexeme)
            if hit:
                consider("replace", p, hit[0], typo=True, typo_dist=hit[1])
    cands.sort(key=lambda c: (c.score, PRIORITY.get(c.terminal, 0), -abs(e - c.pos)), reverse=True)
    _lookahead_rerank(parser, work, kinds, snaps, e, cands, expected)
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
            hit = keyword_typo(tok.lexeme)
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


# ---------------------------------------------------------------- main entry
def analyze(source: str) -> AnalysisResult:
    parser = get_parser()
    tokens, diagnostics = lex(source)
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
        cands = search_repairs(parser, work, kinds, snaps, e, expected)
        if cands:
            best = cands[0]
            conf = confidence_of(best, cands, kinds, e)
        elif work[e].kind != "EOF":
            best, conf = Candidate("delete", e, None), "low"
        else:
            best, conf = Candidate("insert", e, sorted(expected)[0]), "low"

        # Root-cause localisation for unclosed blocks / parentheses
        brace_indent = None
        if best.kind == "insert" and best.terminal == "}":
            rc = brace_root_cause(work, e, layout)
            if rc:
                brace_indent = layout.indent_of(work[rc["opener"]])
                if rc["pos"] < best.pos and rc["pos"] in snaps:
                    adv = _evaluate(parser, kinds, snaps, e, "insert", rc["pos"], "}")
                    if adv >= min(best.advanced, WINDOW):
                        best = Candidate("insert", rc["pos"], "}", adv, best.score)
                        conf = "high" if conf != "low" else "medium"
                best.root = rc
        elif best.kind == "insert" and best.terminal == ")":
            op = paren_opener(work, e)
            if op is not None:
                best.root = {"opener": op}

        ctx = explain.build_context(parser, work, e, expected)
        new_work, edit, synth = make_edit(work, best, source, layout, brace_indent)
        diag = explain.explain(best, ctx, conf, source, layout, edit, synth, cands)
        if conf != "high":
            bk = outcome_key(best, kinds, e)
            rival = next((c for c in cands if outcome_key(c, kinds, e) != bk), None)
            if rival is not None:
                diag.alternative = explain.describe_candidate(rival, work)
        prev = diagnostics[-1] if diagnostics else None
        if (best.kind == "insert" and best.terminal == ")" and best.root
                and work[best.root["opener"]].synthetic and prev is not None
                and prev.code == "E204" and prev.fix.edits[-1][2].strip() == "("):
            diagnostics[-1] = explain.merge_parens(prev, diag, edit, ctx, best, source)
        else:
            diagnostics.append(diag)
        edits.append(edit)
        work = new_work

    status, _, _, root, _ = parser.parse_tree(work)
    if status == "ok":
        tree = root
    diagnostics.sort(key=lambda d: (d.line, d.col))
    fixed = apply_edits(source, edits)
    return AnalysisResult(source, diagnostics, tree, fixed, baseline, work)
