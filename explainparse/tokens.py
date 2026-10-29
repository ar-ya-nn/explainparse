"""Token definitions and human-readable descriptions of terminals."""
from dataclasses import dataclass

KEYWORDS = frozenset({"fn", "let", "if", "else", "while", "return",
                      "int", "bool", "str", "true", "false"})
TYPE_KEYWORDS = ("int", "bool", "str")
TWO_CHAR_OPS = ("->", "==", "!=", "<=", ">=", "&&", "||")
ONE_CHAR_OPS = frozenset("(){},;:=+-*/%<>!")


@dataclass
class Token:
    kind: str        # terminal name: keyword/operator itself, or ID / INT / STRING / EOF
    lexeme: str
    line: int        # 1-based
    col: int         # 1-based
    start: int       # character offset in source
    end: int
    synthetic: bool = False   # inserted by the repair engine, not in the source

    @property
    def end_col(self) -> int:
        return self.col if self.synthetic else self.col + len(self.lexeme)

    def __repr__(self):
        flag = "*" if self.synthetic else ""
        return f"{self.kind}{flag}({self.lexeme!r}@{self.line}:{self.col})"


_NAMES = {"ID": "an identifier", "INT": "a number", "STRING": "a string",
          "EOF": "the end of the file"}


def describe_terminal(t: str) -> str:
    """How a grammar terminal is named in messages: ';' -> "';'", ID -> 'an identifier'."""
    return _NAMES.get(t, f"'{t}'")


def describe_token(tok: Token) -> str:
    """How an actual token is named in messages."""
    if tok.kind == "EOF":
        return "the end of the file"
    if tok.kind == "ID":
        return f"identifier '{tok.lexeme}'"
    if tok.kind == "INT":
        return f"number {tok.lexeme}"
    if tok.kind == "STRING":
        return f"string {tok.lexeme}"
    if tok.kind[:1].isalpha():          # keywords of any language are named after themselves
        return f"keyword '{tok.lexeme}'"
    return f"'{tok.lexeme}'"
