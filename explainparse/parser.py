"""Table-driven LL(1) parser: a fast symbol-only runner (used by the repair search),
a tree-building parser, and a classic panic-mode baseline for comparison."""
from .grammar import Grammar, GrammarError


class Node:
    __slots__ = ("symbol", "children", "parent", "token")

    def __init__(self, symbol, parent=None, token=None):
        self.symbol = symbol
        self.children = []
        self.parent = parent
        self.token = token

    def first_token(self):
        if self.token is not None:
            return self.token
        for c in self.children:
            t = c.first_token()
            if t is not None:
                return t
        return None

    def is_empty(self):
        return self.token is None and all(c.is_empty() for c in self.children)

    def to_dict(self):
        if self.token is not None:
            return {"symbol": self.symbol, "lexeme": self.token.lexeme,
                    "line": self.token.line, "synthetic": self.token.synthetic}
        return {"symbol": self.symbol,
                "children": [c.to_dict() for c in self.children if not c.is_empty()]}


class LL1Parser:
    def __init__(self, grammar: Grammar = None):
        self.grammar = grammar or Grammar()
        if not self.grammar.is_ll1():
            raise GrammarError(f"grammar is not LL(1): {self.grammar.conflicts}")
        self.table = self.grammar.table
        self.rtable = {nt: {t: tuple(reversed(p)) for t, p in row.items()}
                       for nt, row in self.table.items()}
        self.terminals = self.grammar.terminals
        self.start = self.grammar.start

    def run(self, kinds, pos=0, stack=None, stop=None, snaps=None):
        """Parse token kinds from `pos` with a symbol stack. Returns (status, pos, stack)
        where status is 'ok', 'error' or 'limit' (stopped at `stop`). If `snaps` is a dict,
        the stack is recorded before each token index is consumed (for resuming repairs)."""
        stack = list(stack) if stack is not None else [self.start]
        terms, rtable = self.terminals, self.rtable
        if snaps is not None:
            snaps[pos] = tuple(stack)
        while stack:
            if stop is not None and pos >= stop:
                return "limit", pos, stack
            top, la = stack[-1], kinds[pos]
            if top in terms:
                if top != la:
                    return "error", pos, stack
                stack.pop()
                pos += 1
                if snaps is not None:
                    snaps[pos] = tuple(stack)
                continue
            prod = rtable[top].get(la)
            if prod is None:
                return "error", pos, stack
            stack.pop()
            stack.extend(prod)
        return "ok", pos, stack

    def expected(self, stack):
        """Exact set of terminals acceptable next, from the pending stack contents."""
        exp = set()
        for sym in reversed(stack):
            if sym in self.terminals:
                exp.add(sym)
                return exp
            exp |= self.grammar.first[sym]
            if sym not in self.grammar.nullable:
                return exp
        return exp

    def parse_tree(self, tokens):
        """Build a parse tree. Returns (status, pos, stack_entries, root, tokmap)."""
        root = Node(self.start)
        stack = [(self.start, root)]
        pos, tokmap = 0, {}
        while stack:
            sym, node = stack[-1]
            tok = tokens[pos]
            if sym in self.terminals:
                if sym != tok.kind:
                    return "error", pos, stack, root, tokmap
                stack.pop()
                node.token = tok
                tokmap[pos] = node
                pos += 1
                continue
            prod = self.table[sym].get(tok.kind)
            if prod is None:
                return "error", pos, stack, root, tokmap
            stack.pop()
            node.children = [Node(s, node) for s in prod]
            for s, c in zip(reversed(prod), reversed(node.children)):
                stack.append((s, c))
        return "ok", pos, stack, root, tokmap

    def panic_mode_errors(self, kinds):
        """Classic panic-mode recovery (Dragon Book style) as a baseline.
        Returns the token indices where errors are reported. A new error is reported only
        after at least one token was matched since the previous one."""
        stack, pos, errors = [self.start], 0, []
        matched_since = None
        follow = self.grammar.follow
        for _ in range(100000):
            if not stack:
                break
            top, la = stack[-1], kinds[pos]
            if top in self.terminals:
                if top == la:
                    stack.pop()
                    pos += 1
                    matched_since = (matched_since or 0) + 1 if matched_since is not None else None
                    continue
                if matched_since is None or matched_since >= 1:
                    errors.append(pos)
                matched_since = 0
                stack.pop()
                continue
            prod = self.rtable[top].get(la)
            if prod is not None:
                stack.pop()
                stack.extend(prod)
                continue
            if matched_since is None or matched_since >= 1:
                errors.append(pos)
            matched_since = 0
            if la in follow[top] or la == "EOF":
                stack.pop()
            else:
                pos += 1
        return errors


def format_tree(node, indent=""):
    """Readable tree: drops empty subtrees and collapses single-child chains."""
    if node.token is not None:
        tag = " (inserted)" if node.token.synthetic else ""
        return f"{indent}{node.token.lexeme or node.symbol!s}{tag}\n"
    label = [node.symbol]
    kids = [c for c in node.children if not c.is_empty()]
    while len(kids) == 1 and kids[0].token is None:
        node = kids[0]
        label.append(node.symbol)
        kids = [c for c in node.children if not c.is_empty()]
    if len(label) > 3:
        label = [label[0], "…", label[-1]]
    out = f"{indent}{' › '.join(label)}\n"
    for k in kids:
        out += format_tree(k, indent + "  ")
    return out
