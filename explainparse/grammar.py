"""Grammar-as-data: parses a BNF-style spec and builds FIRST/FOLLOW sets and the LL(1) table."""
import re
from collections import OrderedDict

EPSILON = "ε"

LUMEN_GRAMMAR = r"""
program      -> item_list EOF
item_list    -> item item_list | ε
item         -> func_decl | stmt
func_decl    -> 'fn' ID '(' params ')' '->' type block
params       -> param param_tail | ε
param_tail   -> ',' param param_tail | ε
param        -> ID ':' type
type         -> 'int' | 'bool' | 'str'
block        -> '{' stmt_list '}'
stmt_list    -> stmt stmt_list | ε
stmt         -> var_decl | if_stmt | while_stmt | return_stmt | expr_stmt | block
var_decl     -> 'let' ID ':' type '=' expr ';'
if_stmt      -> 'if' '(' expr ')' block else_part
else_part    -> 'else' else_body | ε
else_body    -> block | if_stmt
while_stmt   -> 'while' '(' expr ')' block
return_stmt  -> 'return' expr ';'
expr_stmt    -> expr ';'
expr         -> or_expr assign_tail
assign_tail  -> '=' expr | ε
or_expr      -> and_expr or_tail
or_tail      -> '||' and_expr or_tail | ε
and_expr     -> eq_expr and_tail
and_tail     -> '&&' eq_expr and_tail | ε
eq_expr      -> rel_expr eq_tail
eq_tail      -> '==' rel_expr eq_tail | '!=' rel_expr eq_tail | ε
rel_expr     -> add_expr rel_tail
rel_tail     -> '<' add_expr | '>' add_expr | '<=' add_expr | '>=' add_expr | ε
add_expr     -> mul_expr add_tail
add_tail     -> '+' mul_expr add_tail | '-' mul_expr add_tail | ε
mul_expr     -> unary mul_tail
mul_tail     -> '*' unary mul_tail | '/' unary mul_tail | '%' unary mul_tail | ε
unary        -> '!' unary | '-' unary | primary
primary      -> INT | STRING | 'true' | 'false' | ID call_tail | '(' expr ')'
call_tail    -> '(' args ')' | ε
args         -> expr args_tail | ε
args_tail    -> ',' expr args_tail | ε
"""


class GrammarError(Exception):
    pass


class Grammar:
    def __init__(self, text: str = LUMEN_GRAMMAR, start: str = "program"):
        self.start = start
        self.productions = OrderedDict()
        self.terminals = set()
        for raw in text.strip().splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            lhs, arrow, rhs = line.partition("->")
            if not arrow:
                raise GrammarError(f"missing '->' in: {raw}")
            lhs = lhs.strip()
            alts = [[]]
            for sym in re.findall(r"'[^']*'|\S+", rhs):
                if sym == "|":
                    alts.append([])
                elif sym == EPSILON:
                    continue
                elif sym.startswith("'"):
                    self.terminals.add(sym[1:-1])
                    alts[-1].append(sym[1:-1])
                elif sym.isupper():
                    self.terminals.add(sym)
                    alts[-1].append(sym)
                else:
                    alts[-1].append(sym)
            self.productions.setdefault(lhs, []).extend(tuple(a) for a in alts)
        self.nonterminals = set(self.productions)
        clash = self.nonterminals & self.terminals
        if clash:
            raise GrammarError(f"symbols used as both terminal and nonterminal: {clash}")
        for nt, prods in self.productions.items():
            for p in prods:
                for s in p:
                    if s not in self.terminals and s not in self.nonterminals:
                        raise GrammarError(f"undefined nonterminal '{s}' in rule for '{nt}'")
        self._compute_first()
        self._compute_follow()
        self._build_table()

    # ---- FIRST / FOLLOW -------------------------------------------------
    def first_of(self, seq):
        """FIRST set of a symbol sequence, and whether the whole sequence is nullable."""
        out = set()
        for s in seq:
            if s in self.terminals:
                out.add(s)
                return out, False
            out |= self.first[s]
            if s not in self.nullable:
                return out, False
        return out, True

    def _compute_first(self):
        self.first = {nt: set() for nt in self.nonterminals}
        self.nullable = set()
        changed = True
        while changed:
            changed = False
            for nt, prods in self.productions.items():
                for p in prods:
                    f, nul = self.first_of(p)
                    if not f <= self.first[nt]:
                        self.first[nt] |= f
                        changed = True
                    if nul and nt not in self.nullable:
                        self.nullable.add(nt)
                        changed = True

    def _compute_follow(self):
        self.follow = {nt: set() for nt in self.nonterminals}
        changed = True
        while changed:
            changed = False
            for nt, prods in self.productions.items():
                for p in prods:
                    for i, s in enumerate(p):
                        if s not in self.nonterminals:
                            continue
                        f, nul = self.first_of(p[i + 1:])
                        new = f | (self.follow[nt] if nul else set())
                        if not new <= self.follow[s]:
                            self.follow[s] |= new
                            changed = True

    # ---- LL(1) table ----------------------------------------------------
    def _build_table(self):
        self.table = {nt: {} for nt in self.nonterminals}
        self.conflicts = []
        for nt, prods in self.productions.items():
            for p in prods:
                f, nul = self.first_of(p)
                lookaheads = f | (self.follow[nt] if nul else set())
                for t in lookaheads:
                    existing = self.table[nt].get(t)
                    if existing is not None and existing != p:
                        self.conflicts.append((nt, t, existing, p))
                    else:
                        self.table[nt][t] = p

    def is_ll1(self):
        return not self.conflicts

    def describe(self):
        """Pretty report of FIRST, FOLLOW and table size (handy for the course report)."""
        lines = []
        for nt in self.productions:
            f = ", ".join(sorted(self.first[nt]))
            fo = ", ".join(sorted(self.follow[nt]))
            eps = "  (nullable)" if nt in self.nullable else ""
            lines.append(f"{nt:<12} FIRST = {{ {f} }}{eps}\n{'':<12} FOLLOW = {{ {fo} }}")
        entries = sum(len(r) for r in self.table.values())
        lines.append(f"\nLL(1) table: {len(self.nonterminals)} nonterminals x "
                     f"{len(self.terminals)} terminals, {entries} entries, "
                     f"{len(self.conflicts)} conflicts")
        return "\n".join(lines)
