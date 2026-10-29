"""Turns a chosen repair + parser state into a human-readable, explainable diagnostic."""
from dataclasses import dataclass
from typing import List, Optional
from .tokens import Token, describe_terminal, describe_token
from .diagnostics import Diagnostic, Label, Fix

CONSTRUCTS = {
    "func_decl": "function declaration", "var_decl": "variable declaration",
    "if_stmt": "if statement", "while_stmt": "while loop", "return_stmt": "return statement",
    "expr_stmt": "expression statement", "params": "parameter list", "param": "parameter",
    "args": "argument list", "block": "block", "type": "type annotation",
}
STATEMENTS = ("var_decl", "return_stmt", "expr_stmt")

RULES = {
    ("if_stmt", "("): "An if condition must be wrapped in parentheses: if (condition) { ... }",
    ("if_stmt", ")"): "An if condition must be closed with ')' before its block starts.",
    ("while_stmt", "("): "A while condition must be wrapped in parentheses: while (condition) { ... }",
    ("while_stmt", ")"): "A while condition must be closed with ')' before its body starts.",
    ("if_stmt", "{"): "The body of an if statement must be a block in braces: if (cond) { ... }",
    ("while_stmt", "{"): "The body of a while loop must be a block in braces: while (cond) { ... }",
    ("else_body", "{"): "After 'else' Lumen expects a block in braces, or another 'if'.",
    ("func_decl", "("): "A function name must be followed by its parameter list in parentheses, "
                        "even when empty: fn name() -> int { ... }",
    ("func_decl", "->"): "Functions must declare their return type: fn name(params) -> type { ... }",
    ("func_decl", "{"): "A function body must be a block in braces.",
    ("func_decl", "ID"): "'fn' must be followed by the function's name.",
    ("var_decl", ":"): "Variables need a type annotation: let name: type = value;",
    ("var_decl", "="): "Variables must be initialised when declared: let name: type = value;",
    ("var_decl", "ID"): "'let' must be followed by the variable's name.",
    ("param", ":"): "Each parameter needs a type: name: type",
    ("param_tail", ","): "Parameters are separated by commas.",
    ("args_tail", ","): "Arguments in a call are separated by commas.",
    ("params", ","): "Parameters are separated by commas.",
    ("args", ","): "Arguments in a call are separated by commas.",
}


@dataclass
class Context:
    e: int
    found: Token
    prev: Optional[Token]
    expected: set
    chain: List           # nodes from innermost to root
    tokmap: dict
    work: List[Token]
    lang: object = None


def build_context(parser, work, e, expected):
    status, pos, stack, root, tokmap = parser.parse_tree(work)
    chain = []
    if stack:
        node = stack[-1][1]
        while node is not None:
            chain.append(node)
            node = node.parent
    prev = work[e - 1] if e > 0 else None
    return Context(e, work[e], prev, expected, chain, tokmap, work, getattr(parser, "lang", None))


def _lname(ctx):
    return ctx.lang.name if ctx.lang is not None else "Lumen"


# ------------------------------------------------------------------ naming helpers
def _child_lexeme(node, idx=None):
    """The name declared by a node: its first identifier child (e.g. the function name)."""
    if node is None:
        return None
    for ch in node.children:
        if ch.token is not None and ch.token.kind == "ID":
            return ch.token.lexeme
    return None


def construct_label(node):
    sym = node.symbol
    if sym == "func_decl":
        n = _child_lexeme(node, 1)
        return f"declaration of function '{n}'" if n else "function declaration"
    if sym == "var_decl":
        n = _child_lexeme(node, 1)
        return f"declaration of '{n}'" if n else "variable declaration"
    return CONSTRUCTS.get(sym, sym.replace("_", " "))


def innermost(ctx, symbols=None):
    for n in ctx.chain:
        if (symbols is None and n.symbol in CONSTRUCTS and n.symbol != "block") or \
                (symbols is not None and n.symbol in symbols):
            return n
    return None


def snippet(node, ctx, source, limit=48):
    first = node.first_token() if node else None
    last = next((t for t in reversed(ctx.work[:ctx.e]) if not t.synthetic), None)
    if first is None or last is None or first.synthetic or last.end <= first.start:
        return None
    text = " ".join(source[first.start:last.end].split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def paren_purpose(idx, ctx):
    node = ctx.tokmap.get(idx)
    par = node.parent.symbol if node and node.parent else None
    if par == "if_stmt":
        return "the condition of an if statement"
    if par == "while_stmt":
        return "the condition of a while loop"
    if par == "call_tail":
        return f"the arguments of the call to '{ctx.work[idx - 1].lexeme}'"
    if par == "func_decl":
        return f"the parameter list of function '{_child_lexeme(node.parent, 1)}'"
    if par == "call_tail" and ctx.work[idx - 1].kind == "ID":
        return f"the arguments of the call to '{ctx.work[idx - 1].lexeme}'"
    return "a parenthesised expression"


def brace_purpose(idx, ctx):
    node = ctx.tokmap.get(idx)
    block = node.parent if node else None
    owner = block.parent if block else None
    sym = owner.symbol if owner else None
    if sym == "func_decl":
        return f"body of function '{_child_lexeme(owner, 1)}'"
    if block is not None and block.symbol == "program":
        return f"body of class '{_child_lexeme(block)}'"
    return {"if_stmt": "if block", "while_stmt": "while loop body",
            "else_body": "else block"}.get(sym, "block")


def expected_text(expected):
    exp = set(expected)
    parts = []
    from .engine import VALUE_GROUP, OP_GROUP, TYPE_GROUP
    if VALUE_GROUP <= exp:
        parts.append("an expression")
        exp -= VALUE_GROUP | {"(", "!", "-"}
    if len(exp & OP_GROUP) >= 4:
        parts.append("an operator")
        exp -= OP_GROUP
    types = sorted(exp & TYPE_GROUP)
    if len(types) >= 2:
        parts.append("a type (" + ", ".join(types[:-1]) + " or " + types[-1] + ")")
        exp -= TYPE_GROUP
    parts += [describe_terminal(t) for t in sorted(exp)][:4]
    if not parts:
        return "something else"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]


def rule_for(ctx, t):
    overrides = ctx.lang.rules if ctx.lang is not None else {}
    for n in ctx.chain:
        r = overrides.get((n.symbol, t))
        if r is None and (ctx.lang is None or ctx.lang.id == "lumen" or
                          (n.symbol, t) not in _LUMEN_ONLY):
            r = RULES.get((n.symbol, t))
        if r:
            return r.replace("Lumen", _lname(ctx))
    return None


# rules whose wording shows Lumen syntax; other languages use their own overrides
_LUMEN_ONLY = {("func_decl", "("), ("func_decl", "->"), ("func_decl", "ID"), ("var_decl", ":"),
               ("var_decl", "="), ("var_decl", "ID"), ("param", ":")}


# ------------------------------------------------------------------ main
def explain(c, ctx, confidence, source, layout, edit, synth, cands) -> Diagnostic:
    from .engine import group_of
    work, found = ctx.work, ctx.found
    labels = []
    root = None

    if c.kind == "insert":
        nxt = work[c.pos]
        prev = work[c.pos - 1] if c.pos > 0 else None
        a_line = synth.line
        a_col = synth.col
        t, grp = c.terminal, group_of(c.terminal)
        found_label = (Label(found.line, found.col, max(len(found.lexeme), 1),
                             "the parser noticed the problem here", False)
                       if found.line != a_line and not found.synthetic else None)
        line_note = f"line {a_line}"

        if t == ";":
            node = innermost(ctx, STATEMENTS)
            what = construct_label(node) if node else "statement"
            snip = snippet(node, ctx, source) if node else None
            code, title = "E201", f"missing ';' at the end of the {what}"
            why = (f"Every statement in {_lname(ctx)} ends with ';'. "
                   + (f"`{snip}` is a complete {CONSTRUCTS.get(node.symbol, 'statement')}, but "
                      if snip else "The statement here looked complete, but ")
                   + f"instead of ';' the parser found {describe_token(found)}"
                   + (", which starts something new on the next line." if found_label else "."))
            labels.append(Label(a_line, a_col, 1, "expected ';' here"))
            fixd = f"insert ';' at the end of line {a_line}"

        elif t == ")" and c.root:
            op = work[c.root["opener"]]
            purpose = paren_purpose(c.root["opener"], ctx)
            code, title = "E202", f"unclosed '(' in {purpose}"
            why = (f"The '(' on line {op.line} starts {purpose}, but the parser reached "
                   f"{describe_token(found)} before finding the matching ')'.")
            labels.append(Label(op.line, op.col, 1, "this '(' is never closed", False))
            labels.append(Label(a_line, a_col, 1, "expected ')' here"))
            fixd = f"insert ')' on line {a_line}"
            if op.line != a_line:
                root = {"line": op.line, "col": op.col, "note": f"the '(' opened here"}

        elif t == "}" and c.root:
            rc = c.root
            op = work[rc["opener"]]
            purpose = brace_purpose(rc["opener"], ctx)
            code = "E203"
            title = f"unclosed '{{': the {purpose} (line {op.line}) is never closed"
            labels.append(Label(op.line, op.col, 1, f"this '{{' opens the {purpose}", False))
            if rc.get("stolen_closer") is not None and c.pos == rc["pos"]:
                sc = work[rc["stolen_closer"]]
                why = (f"The parser only noticed at line {found.line} ({describe_token(found)}), "
                       f"but the real mistake is earlier. The indentation shows the {purpose} "
                       f"ends before line {synth.line}, yet it has no '}}' there, so the '}}' on "
                       f"line {sc.line} (meant for the enclosing block) closed it instead, and "
                       f"every brace after that was matched to the wrong block.")
                labels.append(Label(sc.line, sc.col, 1, "this '}' was meant for the outer block", False))
                labels.append(Label(synth.line, synth.col, 1, "expected '}' before this line"))
                fixd = f"insert '}}' before line {synth.line}"
                root = {"line": synth.line, "col": synth.col,
                        "note": f"the {purpose} should end just before this line"}
            elif c.pos == rc["pos"] and found.kind != "EOF" and synth.line < found.line \
                    or (c.pos == rc["pos"] and synth.col == 1 and nxt.line < found.line):
                why = (f"The {purpose} opened on line {op.line} is still open when the parser "
                       f"reaches {describe_token(found)} on line {found.line}. Based on "
                       f"indentation, the block's contents end before line {nxt.line}, so the "
                       f"missing '}}' most likely belongs there.")
                labels.append(Label(nxt.line, synth.col, 1, "expected '}' before this line"))
                fixd = f"insert '}}' before line {nxt.line}"
                root = {"line": nxt.line, "col": synth.col, "note": "the block should be closed here"}
            else:
                end = "the file ended" if found.kind == "EOF" else f"the parser reached {describe_token(found)}"
                why = (f"Every '{{' needs a matching '}}'. The {purpose} opened on line {op.line} "
                       f"is still open when {end}.")
                if synth.start == layout.line_starts[synth.line - 1] and found.kind != "EOF":
                    labels.append(Label(synth.line, synth.col, 1, "expected '}' before this line"))
                else:
                    labels.append(Label(a_line, a_col, 1, "expected '}' here"))
                fixd = "insert '}' at the end of the file" if found.kind == "EOF" else \
                    f"insert '}}' on line {synth.line}"

        elif grp == "type":
            code, title = "E206", "missing type"
            why = (f"A type is required after {describe_token(prev) if prev else 'this point'}. "
                   f"{_lname(ctx)}'s types are "
                   f"{ctx.lang.types_text if ctx.lang else 'int, bool and str'}, but the parser found "
                   f"{describe_token(nxt)}.")
            labels.append(Label(a_line, a_col, 1, "expected a type here"))
            fixd = f"insert a type such as 'int' on line {a_line}"
            confidence = "medium" if confidence == "low" else confidence

        elif grp == "value":
            code, title = "E205", "missing expression"
            why = (f"After {describe_token(prev) if prev else 'this point'} the parser needs a value "
                   f"(a number, string, variable, function call or parenthesised expression), "
                   f"but found {describe_token(nxt)}.")
            labels.append(Label(a_line, a_col, 1, "expected an expression here"))
            fixd = f"insert a value here (e.g. 0) on line {a_line}"

        elif grp == "operator":
            code, title = "E207", "missing operator between two values"
            why = (f"{describe_token(prev).capitalize()} and {describe_token(nxt)} sit next to each "
                   f"other with nothing combining them. {_lname(ctx)} needs an operator such as +, -, *, "
                   f"== or && between two values.")
            labels.append(Label(a_line, a_col, 1, "expected an operator here"))
            fixd = f"insert an operator (e.g. '+') on line {a_line}"
            confidence = "medium" if confidence == "low" else confidence

        else:
            node = innermost(ctx)
            where = f" in this {construct_label(node)}" if node else ""
            code, title = "E204", f"missing {describe_terminal(t)}{where}"
            rule = rule_for(ctx, t)
            if rule:
                why = (rule if rule.endswith(".") else rule + ".") + \
                      f" Instead, the parser found {describe_token(found)}."
            else:
                why = (f"The grammar requires {describe_terminal(t)} here, "
                       f"but the parser found {describe_token(found)}.")
            labels.append(Label(a_line, a_col, 1, f"expected {describe_terminal(t)} here"))
            fixd = f"insert {describe_terminal(t)} on line {a_line}"
        if found_label and code not in ("E203",):
            labels.append(found_label)

    elif c.kind == "delete":
        tok = work[c.pos]
        node = innermost(ctx)
        where = f" in this {construct_label(node)}" if node else ""
        labels.append(Label(tok.line, tok.col, max(len(tok.lexeme), 1), "remove this"))
        fixd = f"remove {describe_token(tok)} on line {tok.line}"
        balance = sum(1 if w.kind == tok.kind.replace(")", "(").replace("}", "{") else
                      -1 if w.kind == tok.kind else 0 for w in work[:c.pos + 1])
        if tok.kind in (")", "}") and balance < 0:
            opener = "(" if tok.kind == ")" else "{"
            code, title = "E211", f"unmatched '{tok.kind}'"
            why = (f"This '{tok.kind}' has no '{opener}' to close: every earlier '{opener}' is "
                   f"already closed. It is probably a leftover or duplicate.")
        elif c.pos > 0 and work[c.pos - 1].kind == tok.kind and not work[c.pos - 1].synthetic:
            code, title = "E212", f"duplicated {describe_token(tok)}"
            why = (f"{describe_token(tok).capitalize()} appears twice in a row. The second one "
                   f"cannot appear here, and removing it lets the rest of the program parse.")
        else:
            code, title = "E210", f"unexpected {describe_token(tok)}{where}"
            why = (f"{describe_token(tok).capitalize()} cannot appear at this point. The parser "
                   f"expected {expected_text(ctx.expected)}. Removing it lets the rest of the "
                   f"program parse normally.")

    else:  # replace
        tok = work[c.pos]
        labels.append(Label(tok.line, tok.col, max(len(tok.lexeme), 1),
                            f"did you mean '{c.terminal}'?" if c.typo else
                            f"expected {describe_terminal(c.terminal)}"))
        if c.typo:
            code = "E220"
            title = f"'{tok.lexeme}' is not a keyword. Did you mean '{c.terminal}'?"
            why = (f"Because '{tok.lexeme}' is not a keyword, the parser read it as a variable "
                   f"name, which is why the code after it stopped making sense"
                   + (f" (the error surfaced at {describe_token(found)} on line {found.line})"
                      if c.pos != ctx.e else "")
                   + f". It is {c.typo_dist} edit{'s' if c.typo_dist > 1 else ''} away from "
                     f"the keyword '{c.terminal}'.")
            fixd = f"replace '{tok.lexeme}' with '{c.terminal}'"
            if c.pos != ctx.e and found.line != tok.line:
                labels.append(Label(found.line, found.col, max(len(found.lexeme), 1),
                                    "the parser noticed the problem here", False))
                root = {"line": tok.line, "col": tok.col, "note": f"misspelled keyword '{tok.lexeme}'"}
        else:
            code = "E221"
            title = f"expected {describe_terminal(c.terminal)}, found {describe_token(tok)}"
            why = rule_for(ctx, c.terminal) or (
                f"{describe_token(tok).capitalize()} does not fit here. Replacing it with "
                f"{describe_terminal(c.terminal)} makes the rest of the program parse.")
            fixd = f"replace {describe_token(tok)} with {describe_terminal(c.terminal)}"

    primary = next((l for l in labels if l.primary), labels[0])
    return Diagnostic(code, title, primary.line, primary.col, why, labels,
                      Fix(fixd, [edit]), confidence, "parser", root)


def describe_candidate(c, work):
    if c.kind == "insert":
        prev = work[c.pos - 1] if c.pos > 0 else work[c.pos]
        return f"insert {describe_terminal(c.terminal)} after {describe_token(prev)} on line {prev.line}"
    tok = work[c.pos]
    if c.kind == "delete":
        return f"remove {describe_token(tok)} on line {tok.line}"
    return f"replace {describe_token(tok)} with {describe_terminal(c.terminal)} on line {tok.line}"


def merge_parens(open_diag, close_diag, close_edit, ctx, c, source):
    """'(' and ')' both missing around a condition -> one diagnostic instead of two."""
    o_start = open_diag.fix.edits[-1][0]
    c_start = close_edit[0]
    cond = " ".join(source[o_start:c_start].split())
    owner = next((n.symbol for n in ctx.chain if n.symbol in ("if_stmt", "while_stmt")), None)
    kind = "if statement" if owner == "if_stmt" else "while loop" if owner == "while_stmt" else "expression"
    rule = RULES.get((owner, "("), "This expression needs to be wrapped in parentheses.")
    first = next((t for t in ctx.work if not t.synthetic and t.start >= o_start), None)
    line = first.line if first else open_diag.line
    col = first.col if first else open_diag.col
    length = max(c_start - (first.start if first else o_start), 1)
    return Diagnostic(
        "E208", f"the condition of this {kind} must be wrapped in parentheses", line, col,
        f"{rule if rule.endswith('.') else rule + '.'} Both '(' and ')' are missing around `{cond}`.",
        [Label(line, col, length, "wrap this condition in parentheses")],
        Fix(f"write ({cond})", open_diag.fix.edits + [close_edit]),
        "high", "parser")
