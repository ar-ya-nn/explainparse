"""Semantic analysis on the parse tree: a scoped symbol table, undeclared names and type checks.

It runs only on programs that parse without errors, and works for all three languages
because their grammars share nonterminal names (func_decl, var_decl, if_stmt, expr, ...).
Types are tracked internally as int / bool / str / void (plus "[]" for Java arrays) and
shown in each language's own spelling. An unknown type (None) never causes an error, so one
mistake does not trigger a cascade of follow-on type errors."""
from typing import Dict, List, Optional
from .diagnostics import Diagnostic, Label, Fix
from .tokens import Token

ARITH = {"-", "*", "/", "%"}
COMPARE = {"<", ">", "<=", ">="}
EQUALITY = {"==", "!="}
LOGIC = {"&&", "||"}
BIN_CHAINS = {"or_expr": "or_tail", "and_expr": "and_tail", "eq_expr": "eq_tail",
              "rel_expr": "rel_tail", "add_expr": "add_tail", "mul_expr": "mul_tail"}


def _damerau(a, b):
    from .engine import damerau
    return damerau(a, b)


def kids(node):
    return [c for c in node.children if not c.is_empty()]


def child(node, symbol):
    return next((c for c in node.children if c.symbol == symbol), None)


def tok(node, kind="ID") -> Optional[Token]:
    return next((c.token for c in node.children if c.token is not None and c.token.kind == kind),
                None)


def last_token(node):
    if node.token is not None:
        return node.token
    for c in reversed(node.children):
        t = last_token(c)
        if t is not None:
            return t
    return None


def descend(node):
    """Skip single-child chains (stmt -> if_stmt, else_body -> block, ...)."""
    while node.token is None and len(kids(node)) == 1 and kids(node)[0].token is None:
        node = kids(node)[0]
    return node


class Func:
    def __init__(self, name, params, ret, token, builtin=False):
        self.name, self.params, self.ret, self.token, self.builtin = name, params, ret, token, builtin


class Checker:
    def __init__(self, lang, source):
        self.lang, self.source = lang, source
        self.diags: List[Diagnostic] = []
        self.scopes: List[Dict[str, tuple]] = [{}]      # name -> (type, declaring token)
        self.scope_names: List[str] = ["global"]
        self.funcs: Dict[str, Func] = {}
        self.current: Optional[Func] = None
        for name, ret in lang.builtins.items():
            self.funcs[name] = Func(name, None, ret, None, builtin=True)

    # ------------------------------------------------------------ helpers
    def tn(self, t):
        return self.lang.type_name(t)

    def a_tn(self, t):
        name = self.tn(t)
        return ("an " if name[:1] in "aeiou" else "a ") + name

    def type_of_decl(self, node):
        """Declared type of a var_decl / param / func_decl node."""
        tnode = child(node, "type")
        if tnode is None or not tnode.children or tnode.children[0].token is None:
            return None, None
        ttok = tnode.children[0].token
        canon = self.lang.canonical.get(ttok.kind)
        dims = child(node, "dims")
        if canon and dims is not None and not dims.is_empty():
            canon += "[]"
        return canon, ttok

    def compatible(self, want, got):
        if want is None or got is None or want == got:
            return True
        if self.lang.int_as_bool and {want, got} == {"int", "bool"}:
            return True
        return False

    def report(self, code, title, at, why, labels=None, fix=None, confidence="high", steps=()):
        labels = labels or [Label(at.line, at.col, max(len(at.lexeme), 1), "here")]
        self.diags.append(Diagnostic(code, title, at.line, at.col, why, labels, fix, confidence,
                                     "semantic", decision={"kind": "rule", "steps": list(steps)}))

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def visible_names(self):
        names = set()
        for scope in self.scopes:
            names |= set(scope)
        return names

    def closest(self, name, pool):
        best = None
        for cand in pool:
            if cand == name:
                continue
            d = _damerau(name.lower(), cand.lower())
            limit = 1 if len(name) <= 2 else 2
            if d <= limit and (best is None or d < best[1]):
                best = (cand, d)
        return best

    def declare(self, name_tok, typ, what="variable"):
        scope = self.scopes[-1]
        if name_tok.lexeme in scope:
            prev = scope[name_tok.lexeme][1]
            self.report("E310", f"'{name_tok.lexeme}' is declared twice in the same scope", name_tok,
                        f"'{name_tok.lexeme}' was already declared on line {prev.line} in this "
                        f"{'function' if len(self.scopes) > 1 else 'program'}. A {what} can only be "
                        f"declared once per scope; to change its value, assign to it instead.",
                        [Label(name_tok.line, name_tok.col, len(name_tok.lexeme), "declared again here"),
                         Label(prev.line, prev.col, len(prev.lexeme), "first declared here", False)],
                        steps=[f"The symbol table already holds '{name_tok.lexeme}' in the "
                               f"{self.scope_names[-1]} scope (line {prev.line}).",
                               "Redeclaring a name in the same scope is not allowed."])
            return
        scope[name_tok.lexeme] = (typ, name_tok)

    # ------------------------------------------------------------ program structure
    def run(self, tree):
        funcs = []
        self._collect(tree, funcs)
        for f in funcs:
            name_tok = tok(f)
            ret, _ = self.type_of_decl(f)
            params = []
            for p in self._params(f):
                pt, _ = self.type_of_decl(p)
                params.append((tok(p), pt))
            if name_tok.lexeme in self.funcs and not self.funcs[name_tok.lexeme].builtin:
                prev = self.funcs[name_tok.lexeme].token
                self.report("E310", f"function '{name_tok.lexeme}' is defined twice", name_tok,
                            f"A function named '{name_tok.lexeme}' is already defined on line "
                            f"{prev.line}. Each function needs a unique name.",
                            [Label(name_tok.line, name_tok.col, len(name_tok.lexeme), "defined again here"),
                             Label(prev.line, prev.col, len(prev.lexeme), "first defined here", False)],
                            steps=["All functions are entered into the symbol table before any "
                                   "body is checked, so calls may come before definitions.",
                                   f"'{name_tok.lexeme}' was entered twice."])
                continue
            self.funcs[name_tok.lexeme] = Func(name_tok.lexeme, params, ret, name_tok)
        self._items(tree)
        return self.diags

    def _collect(self, node, out):
        if node.symbol == "func_decl":
            out.append(node)
            return
        if node.symbol in ("block", "stmt"):
            return
        for c in node.children:
            self._collect(c, out)

    def _params(self, func):
        out = []

        def walk(n):
            if n.symbol == "param":
                out.append(n)
                return
            for c in n.children:
                walk(c)
        p = child(func, "params")
        if p is not None:
            walk(p)
        return out

    def _items(self, node):
        """Walk top-level items in order: functions and (in Lumen) global statements."""
        if node.symbol == "func_decl":
            self.function(node)
            return
        if node.symbol == "stmt":
            self.stmt(node)
            return
        for c in node.children:
            self._items(c)

    def function(self, f):
        name_tok = tok(f)
        fn = self.funcs.get(name_tok.lexeme)
        if fn is None or fn.token is not name_tok:
            fn = Func(name_tok.lexeme, [], self.type_of_decl(f)[0], name_tok)
        self.current = fn
        self.scopes.append({})
        self.scope_names.append(f"function '{name_tok.lexeme}'")
        for ptok, ptype in fn.params:
            if ptok is not None:
                self.declare(ptok, ptype, "parameter")
        self.block(child(f, "block"), new_scope=False)
        self.scopes.pop()
        self.scope_names.pop()
        self.current = None

    def block(self, node, new_scope=True):
        if node is None:
            return
        if new_scope:
            self.scopes.append({})
            self.scope_names.append("block")
        sl = child(node, "stmt_list")
        while sl is not None and sl.children:
            self.stmt(sl.children[0])
            sl = sl.children[1] if len(sl.children) > 1 else None
        if new_scope:
            self.scopes.pop()
            self.scope_names.pop()

    # ------------------------------------------------------------ statements
    def stmt(self, node):
        node = descend(node)
        sym = node.symbol
        if sym == "block":
            self.block(node)
        elif sym == "var_decl":
            self.var_decl(node)
        elif sym == "if_stmt":
            self.condition(node, "if statement")
            self.block(child(node, "block"))
            ep = child(node, "else_part")
            if ep is not None and not ep.is_empty():
                body = descend(child(ep, "else_body"))
                if body.symbol == "if_stmt":
                    self.stmt(body)
                else:
                    self.block(body)
        elif sym == "while_stmt":
            self.condition(node, "while loop")
            self.block(child(node, "block"))
        elif sym == "return_stmt":
            self.return_stmt(node)
        elif sym == "expr_stmt":
            self.expr(child(node, "expr"))

    def var_decl(self, node):
        name_tok = tok(node)
        declared, ttok = self.type_of_decl(node)
        init = child(node, "init")
        e = child(init, "expr") if init is not None else child(node, "expr")
        if e is not None:
            got = self.expr(e)
            if declared == "void":
                self.report("E303", f"'{name_tok.lexeme}' cannot have type void", ttok,
                            "void means 'no value', so it can only be a function's return type, "
                            "not the type of a variable.",
                            steps=["Variables must hold a value; void has none."])
            elif not self.compatible(declared, got):
                fix = None
                if got in self.lang.canonical.values() and got != "void":
                    fix = Fix(f"change the type of '{name_tok.lexeme}' to {self.tn(got)}",
                              [(ttok.start, ttok.end, self.tn(got))])
                first = e.first_token()
                self.report(
                    "E303", f"'{name_tok.lexeme}' is declared {self.tn(declared)} but is given "
                            f"{self.a_tn(got)}", first,
                    f"The declaration says '{name_tok.lexeme}' holds {self.a_tn(declared)}, but "
                    f"the value on the right is {self.a_tn(got)}. {self.lang.name} does not "
                    f"convert between them automatically.",
                    [Label(ttok.line, ttok.col, len(ttok.lexeme), f"declared {self.tn(declared)}", False),
                     Label(first.line, first.col, max(last_token(e).end - first.start, 1),
                           f"this is {self.a_tn(got)}")],
                    fix, "medium",
                    [f"Declared type of '{name_tok.lexeme}': {self.tn(declared)}.",
                     f"Type of the initial value, worked out bottom-up from the parse tree: "
                     f"{self.tn(got)}.",
                     "The two do not match. The suggested fix changes the declared type; if the "
                     "value is what is wrong, change the value instead (hence medium confidence)."])
        self.declare(name_tok, declared)

    def condition(self, node, what):
        e = child(node, "expr")
        got = self.expr(e)
        if got is None or got == "bool" or (self.lang.int_as_bool and got == "int"):
            return
        first, last = e.first_token(), last_token(e)
        fix = None
        if got == "int":
            fix = Fix(f"compare it with 0: ({self.source[first.start:last.end]} != 0)",
                      [(last.end, last.end, " != 0")])
        self.report(
            "E305", f"the condition of this {what} is {self.a_tn(got)}, not {self.a_tn('bool')}",
            first,
            f"A condition must be true or false, so it has to be {self.a_tn('bool')}. "
            f"`{self.source[first.start:last.end]}` is {self.a_tn(got)}."
            + (" To test whether a number is non-zero, compare it explicitly." if got == "int" else ""),
            [Label(first.line, first.col, max(last.end - first.start, 1), f"this is {self.a_tn(got)}")],
            fix, "medium",
            [f"Conditions of an {what} must have type {self.tn('bool')}.",
             f"The condition's type, worked out from the parse tree, is {self.tn(got)}."])

    def return_stmt(self, node):
        rv = child(node, "ret_val")
        e = child(rv, "expr") if rv is not None else child(node, "expr")
        ret_tok = node.children[0].token
        got = self.expr(e) if e is not None and not e.is_empty() else "void"
        if self.current is None:
            return
        want = self.current.ret
        fname = self.current.name
        if want == "void" and got != "void":
            self.report("E307", f"'{fname}' returns void, so it cannot return a value", ret_tok,
                        f"Function '{fname}' is declared void (no result), but this return statement "
                        f"gives back {self.a_tn(got)}.",
                        steps=[f"Declared return type of '{fname}': void.",
                               f"Type of the returned value: {self.tn(got)}."])
        elif want not in (None, "void") and got == "void":
            self.report("E307", f"'{fname}' must return {self.a_tn(want)}", ret_tok,
                        f"Function '{fname}' is declared to return {self.a_tn(want)}, but this "
                        f"return statement gives back nothing.",
                        steps=[f"Declared return type of '{fname}': {self.tn(want)}.",
                               "This return statement has no value."])
        elif not self.compatible(want, got):
            first = e.first_token()
            self.report("E307", f"'{fname}' returns {self.tn(want)}, but this returns "
                                f"{self.a_tn(got)}", first,
                        f"Function '{fname}' is declared to return {self.a_tn(want)}, so every "
                        f"return statement must give back {self.a_tn(want)}. This one returns "
                        f"{self.a_tn(got)}.",
                        [Label(first.line, first.col, max(last_token(e).end - first.start, 1),
                               f"this is {self.a_tn(got)}"),
                         Label(self.current.token.line, self.current.token.col,
                               len(fname), f"declared to return {self.tn(want)}", False)],
                        steps=[f"Declared return type of '{fname}': {self.tn(want)}.",
                               f"Type of the returned value: {self.tn(got)}."])

    # ------------------------------------------------------------ expressions
    def expr(self, node):
        if node is None or node.is_empty():
            return None
        sym = node.symbol
        if sym == "expr":
            lhs, tail = node.children[0], node.children[1]
            if tail.is_empty():
                return self.expr(lhs)
            return self.assign(lhs, tail)
        if sym in BIN_CHAINS:
            left = self.expr(node.children[0])
            t = node.children[1]
            while t is not None and not t.is_empty():
                op = t.children[0].token
                right = self.expr(t.children[1])
                left = self.binop(op, left, right, node, t.children[1])
                t = t.children[2] if len(t.children) > 2 else None
            return left
        if sym == "unary":
            first = node.children[0]
            if first.token is not None:
                inner = self.expr(node.children[1])
                op = first.token
                if op.kind == "!" and inner not in (None, "bool") and not (
                        self.lang.int_as_bool and inner == "int"):
                    self.op_error(op, f"'!' needs {self.a_tn('bool')}, but its operand is "
                                      f"{self.a_tn(inner)}")
                if op.kind == "-" and inner not in (None, "int"):
                    self.op_error(op, f"'-' needs a number, but its operand is {self.a_tn(inner)}")
                return "bool" if op.kind == "!" else "int"
            return self.expr(first)
        if sym == "primary":
            first = node.children[0]
            if first.token is not None:
                k = first.token.kind
                if k == "INT":
                    return "int"
                if k == "STRING":
                    return "str"
                if k in ("true", "false"):
                    return "bool"
                if k == "(":
                    return self.expr(node.children[1])
                if k == "ID":
                    return self.name_use(first.token, node.children[1])
            return None
        # any other wrapper
        ks = kids(node)
        return self.expr(ks[0]) if len(ks) == 1 else None

    def op_error(self, op, title, why=None, steps=None):
        self.report("E306", title, op,
                    labels=[Label(op.line, op.col, len(op.lexeme), "not defined for these types")],
                    why=why or f"{title[0].upper() + title[1:]}. {self.lang.name} only applies "
                               f"operators to the types they are defined for.",
                    steps=steps or [f"Operand types are worked out bottom-up from the parse tree.",
                                    f"Operator '{op.lexeme}' is not defined for these types."])

    def binop(self, op, left, right, chain, right_node):
        k = op.kind
        L, R = self.tn(left), self.tn(right)
        if k == "+":
            if left == "str" or right == "str":
                mode = self.lang.string_concat
                if mode == "any" or (mode == "same" and left == right == "str") or None in (left, right):
                    return "str"
                self.op_error(op, f"'+' cannot combine {self.a_tn(left)} and {self.a_tn(right)}",
                              f"In {self.lang.name}, '+' adds two numbers"
                              + (" or joins two strings" if mode == "same" else "")
                              + f". Here the left side is {self.a_tn(left)} and the right side is "
                                f"{self.a_tn(right)}.")
                return None
            if left not in (None, "int") or right not in (None, "int"):
                self.op_error(op, f"'+' needs numbers, but got {L} + {R}")
            return "int"
        if k in ARITH:
            if left not in (None, "int") or right not in (None, "int"):
                self.op_error(op, f"'{k}' needs two numbers, but got {L} {k} {R}",
                              f"Arithmetic operators work on {self.a_tn('int')} only. Here the "
                              f"left side is {self.a_tn(left)} and the right side is {self.a_tn(right)}.")
            return "int"
        if k in COMPARE:
            if left not in (None, "int") or right not in (None, "int"):
                self.op_error(op, f"'{k}' compares numbers, but got {L} {k} {R}",
                              f"'{k}' only compares numbers. Here the left side is "
                              f"{self.a_tn(left)} and the right side is {self.a_tn(right)}.")
            return "bool"
        if k in EQUALITY:
            if not self.compatible(left, right):
                self.op_error(op, f"'{k}' compares {self.a_tn(left)} with {self.a_tn(right)}",
                              f"Both sides of '{k}' must have the same type, otherwise the "
                              f"comparison can never be true. Here they are {L} and {R}.")
            return "bool"
        if k in LOGIC:
            ok = lambda t: t in (None, "bool") or (self.lang.int_as_bool and t == "int")
            if not ok(left) or not ok(right):
                self.op_error(op, f"'{k}' needs {self.a_tn('bool')} on both sides, but got {L} {k} {R}",
                              f"'{k}' combines true/false values. Here the left side is "
                              f"{self.a_tn(left)} and the right side is {self.a_tn(right)}.")
            return "bool"
        return None

    def lvalue(self, node):
        """The identifier token if `node` is a plain variable, else None."""
        n = node
        while n.token is None:
            ks = kids(n)
            if n.symbol == "primary":
                first = n.children[0]
                if first.token is not None and first.token.kind == "ID" and n.children[1].is_empty():
                    return first.token
                return None
            if len(ks) != 1:
                return None
            n = ks[0]
        return None

    def assign(self, lhs, tail):
        target = self.lvalue(lhs)
        right = self.expr(tail.children[1])
        if target is None:
            first = lhs.first_token()
            self.report("E311", "only a variable can be assigned to", first,
                        f"The left side of '=' must be a variable name, but "
                        f"`{self.source[first.start:last_token(lhs).end]}` is an expression.",
                        steps=["Assignment stores a value in a variable.",
                               "The left side here is not a single variable name."])
            return right
        left = self.name_use(target, None)
        if left is not None and not self.compatible(left, right):
            eq = tail.children[0].token
            self.report("E304", f"cannot assign {self.a_tn(right)} to '{target.lexeme}', which is "
                                f"{self.a_tn(left)}", eq,
                        f"'{target.lexeme}' was declared as {self.a_tn(left)}, so it can only hold "
                        f"{self.a_tn(left)} values. The value on the right is {self.a_tn(right)}.",
                        [Label(eq.line, eq.col, 1, f"assigning {self.a_tn(right)}"),
                         Label(target.line, target.col, len(target.lexeme), f"{self.a_tn(left)}", False)],
                        steps=[f"Symbol table: '{target.lexeme}' has type {self.tn(left)}.",
                               f"Type of the right-hand side: {self.tn(right)}."])
        return left

    def name_use(self, name_tok, call_tail):
        if call_tail is not None and not call_tail.is_empty():
            first = call_tail.children[0].token
            if first.kind == ".":
                self.member_args(call_tail)     # e.g. System.out.println(...): library call
                return None
            return self.call(name_tok, call_tail)
        entry = self.lookup(name_tok.lexeme)
        if entry is not None:
            return entry[0]
        if name_tok.lexeme in self.funcs:
            self.report("E302", f"'{name_tok.lexeme}' is a function, not a variable", name_tok,
                        f"'{name_tok.lexeme}' names a function. To use its result, call it: "
                        f"{name_tok.lexeme}(...).",
                        fix=Fix(f"call it: {name_tok.lexeme}()", [(name_tok.end, name_tok.end, "()")]),
                        confidence="medium",
                        steps=[f"'{name_tok.lexeme}' is in the function table, not among variables."])
            return None
        sugg = self.closest(name_tok.lexeme, self.visible_names())
        scopes = " → ".join(reversed(self.scope_names))
        decl = self.lang.decl_example.format(name=name_tok.lexeme)
        if sugg:
            fix = Fix(f"replace '{name_tok.lexeme}' with '{sugg[0]}'",
                      [(name_tok.start, name_tok.end, sugg[0])])
            why = (f"'{name_tok.lexeme}' is used here but never declared in this scope or any "
                   f"enclosing one. It is {sugg[1]} edit{'s' if sugg[1] > 1 else ''} away from "
                   f"'{sugg[0]}', which is declared, so it is probably a typo. If you meant a new "
                   f"variable, declare it first: {decl}")
        else:
            fix = None
            why = (f"'{name_tok.lexeme}' is used here but never declared in this scope or any "
                   f"enclosing one. Declare it before using it: {decl}")
        self.report("E301", f"'{name_tok.lexeme}' is not declared"
                            + (f". Did you mean '{sugg[0]}'?" if sugg else ""), name_tok, why,
                    [Label(name_tok.line, name_tok.col, len(name_tok.lexeme),
                           f"did you mean '{sugg[0]}'?" if sugg else "not declared")],
                    fix, "medium" if sugg else "high",
                    [f"Looked up '{name_tok.lexeme}' in the symbol table, innermost scope first: "
                     f"{scopes}. Not found.",
                     (f"Closest declared name: '{sugg[0]}' ({sugg[1]} edit"
                      f"{'s' if sugg[1] > 1 else ''} away, using Damerau–Levenshtein distance)."
                      if sugg else "No declared name is within 2 edits, so no replacement is suggested.")])
        # declare it as unknown so later uses do not repeat the same error
        self.scopes[-1][name_tok.lexeme] = (None, name_tok)
        return None

    def args_of(self, call_tail):
        args, a = [], child(call_tail, "args")
        if a is None or a.is_empty():
            return args
        args.append(a.children[0])
        t = a.children[1] if len(a.children) > 1 else None
        while t is not None and not t.is_empty():
            args.append(t.children[1])
            t = t.children[2] if len(t.children) > 2 else None
        return args

    def member_args(self, call_tail):
        n = call_tail
        while n is not None and not n.is_empty():
            if n.children[0].token.kind == "(":
                for a in self.args_of(n):
                    self.expr(a)
                return
            n = child(n, "call_tail")

    def call(self, name_tok, call_tail):
        args = self.args_of(call_tail)
        types = [self.expr(a) for a in args]
        fn = self.funcs.get(name_tok.lexeme)
        if fn is None:
            if self.lookup(name_tok.lexeme) is not None:
                self.report("E302", f"'{name_tok.lexeme}' is a variable, not a function", name_tok,
                            f"'{name_tok.lexeme}' is declared as a variable, so it cannot be called "
                            f"with (...).",
                            steps=[f"'{name_tok.lexeme}' is in the variable table, not the function table."])
                return None
            sugg = self.closest(name_tok.lexeme, [f for f in self.funcs])
            self.report("E302", f"function '{name_tok.lexeme}' is not defined"
                                + (f". Did you mean '{sugg[0]}'?" if sugg else ""), name_tok,
                        f"There is no function called '{name_tok.lexeme}' in this program"
                        + (f". '{sugg[0]}' is {sugg[1]} edit{'s' if sugg[1] > 1 else ''} away, so "
                           f"it is probably a typo." if sugg else
                           ". Define it, or check the spelling of the name."),
                        [Label(name_tok.line, name_tok.col, len(name_tok.lexeme),
                               f"did you mean '{sugg[0]}'?" if sugg else "not defined")],
                        Fix(f"replace '{name_tok.lexeme}' with '{sugg[0]}'",
                            [(name_tok.start, name_tok.end, sugg[0])]) if sugg else None,
                        "medium" if sugg else "high",
                        [f"Looked up '{name_tok.lexeme}' among the program's functions"
                         + (f" and built-ins ({', '.join(self.lang.builtins)})" if self.lang.builtins else "")
                         + ". Not found."])
            return None
        if fn.params is None:          # built-in with any arguments
            return fn.ret
        open_tok = call_tail.children[0].token
        if len(args) != len(fn.params):
            n, m = len(fn.params), len(args)
            self.report("E308", f"'{fn.name}' takes {n} argument{'s' if n != 1 else ''}, but "
                                f"{m} {'were' if m != 1 else 'was'} given", name_tok,
                        f"'{fn.name}' is defined on line {fn.token.line} with {n} parameter"
                        f"{'s' if n != 1 else ''}"
                        + (f" ({', '.join(p.lexeme + ': ' + self.tn(t) for p, t in fn.params)})" if n else "")
                        + f", so every call must pass exactly {n} argument{'s' if n != 1 else ''}.",
                        [Label(name_tok.line, name_tok.col, len(fn.name), f"{m} given"),
                         Label(fn.token.line, fn.token.col, len(fn.name), f"takes {n}", False)],
                        steps=[f"Definition of '{fn.name}': {n} parameter{'s' if n != 1 else ''}.",
                               f"This call passes {m}."])
            return fn.ret
        for (ptok, ptype), a, got in zip(fn.params, args, types):
            if not self.compatible(ptype, got):
                first = a.first_token()
                self.report("E309", f"argument '{ptok.lexeme}' of '{fn.name}' must be "
                                    f"{self.a_tn(ptype)}, not {self.a_tn(got)}", first,
                            f"Parameter '{ptok.lexeme}' of '{fn.name}' (line {fn.token.line}) is "
                            f"{self.a_tn(ptype)}, but the value passed here is {self.a_tn(got)}.",
                            [Label(first.line, first.col, max(last_token(a).end - first.start, 1),
                                   f"this is {self.a_tn(got)}")],
                            steps=[f"Parameter '{ptok.lexeme}' has type {self.tn(ptype)}.",
                                   f"The argument's type is {self.tn(got)}."])
        return fn.ret


def check(tree, lang, source) -> List[Diagnostic]:
    return Checker(lang, source).run(tree)
