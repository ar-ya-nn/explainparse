"""The languages ExplainParse understands. Each one is plain data: a grammar text, its
keywords and operators, and a few language-specific phrases used in explanations.
The lexer, LL(1) parser, repair search, root-cause analysis and explanations are shared."""
from dataclasses import dataclass, field
from typing import Dict, Tuple

from .grammar import LUMEN_GRAMMAR

# Expression grammar shared by all three languages (same nonterminal names, so the
# explanation rules and the semantic checker work for every language).
_EXPR = r"""
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
args         -> expr args_tail | ε
args_tail    -> ',' expr args_tail | ε
"""

_STATEMENTS = r"""
block        -> '{' stmt_list '}'
stmt_list    -> stmt stmt_list | ε
stmt         -> var_decl | if_stmt | while_stmt | return_stmt | expr_stmt | block
if_stmt      -> 'if' '(' expr ')' block else_part
else_part    -> 'else' else_body | ε
else_body    -> block | if_stmt
while_stmt   -> 'while' '(' expr ')' block
return_stmt  -> 'return' ret_val ';'
ret_val      -> expr | ε
expr_stmt    -> expr ';'
params       -> param param_tail | ε
param_tail   -> ',' param param_tail | ε
"""

MINIC_GRAMMAR = r"""
program      -> func_list EOF
func_list    -> func_decl func_list | ε
func_decl    -> type ID '(' params ')' block
param        -> type ID
type         -> 'int' | 'bool' | 'void'
var_decl     -> type ID init ';'
init         -> '=' expr | ε
primary      -> INT | STRING | 'true' | 'false' | ID call_tail | '(' expr ')'
call_tail    -> '(' args ')' | ε
""" + _STATEMENTS + _EXPR

MINIJAVA_GRAMMAR = r"""
program      -> 'class' ID '{' member_list '}' EOF
member_list  -> func_decl member_list | ε
func_decl    -> modifiers type dims ID '(' params ')' block
modifiers    -> modifier modifiers | ε
modifier     -> 'public' | 'private' | 'static'
param        -> type dims ID
type         -> 'int' | 'boolean' | 'String' | 'void'
dims         -> '[' ']' | ε
var_decl     -> type dims ID init ';'
init         -> '=' expr | ε
primary      -> INT | STRING | 'true' | 'false' | ID call_tail | '(' expr ')'
call_tail    -> '(' args ')' | '.' ID call_tail | ε
""" + _STATEMENTS + _EXPR


@dataclass
class Language:
    id: str
    name: str
    extension: str
    grammar: str
    keywords: frozenset
    types: Tuple[str, ...]                 # type keywords as written in this language
    canonical: Dict[str, str]              # type keyword -> internal type (int/bool/str/void)
    two_char_ops: Tuple[str, ...]
    one_char_ops: frozenset
    builtins: Dict[str, str] = field(default_factory=dict)   # function -> return type
    rules: Dict[tuple, str] = field(default_factory=dict)    # explanation overrides
    decl_example: str = ""                 # how to declare a variable, {name} placeholder
    string_concat: str = "same"            # '+' on strings: 'same' (str+str), 'any', 'none'
    int_as_bool: bool = False              # C: an int may be used as a condition
    placeholder: str = ""

    def type_name(self, canon):
        """How an internal type is written in this language (bool -> boolean in Java)."""
        if canon is None:
            return "unknown"
        base, arr = (canon[:-2], "[]") if canon.endswith("[]") else (canon, "")
        for kw, c in self.canonical.items():
            if c == base:
                return kw + arr
        return {"str": "string"}.get(base, base) + arr

    @property
    def types_text(self):
        t = list(self.types)
        return ", ".join(t[:-1]) + " and " + t[-1] if len(t) > 1 else t[0]

    def meta(self):
        return {"id": self.id, "name": self.name, "extension": self.extension,
                "keywords": sorted(self.keywords - set(self.types)), "types": list(self.types),
                "placeholder": self.placeholder}


_COMMON_OPS = frozenset("(){},;=+-*/%<>!")
_TWO = ("==", "!=", "<=", ">=", "&&", "||")

LUMEN = Language(
    id="lumen", name="Lumen", extension=".lm", grammar=LUMEN_GRAMMAR,
    keywords=frozenset({"fn", "let", "if", "else", "while", "return", "int", "bool", "str",
                        "true", "false"}),
    types=("int", "bool", "str"), canonical={"int": "int", "bool": "bool", "str": "str"},
    two_char_ops=("->",) + _TWO, one_char_ops=_COMMON_OPS | {":"},
    builtins={"print": "void"},
    decl_example="let {name}: int = 0;",
    placeholder="fn main() -> int {\n    let x: int = 5;\n    return x;\n}",
)

MINIC = Language(
    id="minic", name="Mini-C", extension=".c", grammar=MINIC_GRAMMAR,
    keywords=frozenset({"int", "bool", "void", "if", "else", "while", "return", "true", "false"}),
    types=("int", "bool", "void"), canonical={"int": "int", "bool": "bool", "void": "void"},
    two_char_ops=_TWO, one_char_ops=_COMMON_OPS,
    builtins={"printf": "int", "puts": "int"},
    rules={
        ("func_decl", "("): "A function name must be followed by its parameter list in "
                            "parentheses, even when empty: int name() { ... }",
        ("func_decl", "ID"): "A function's return type must be followed by its name: "
                             "int name() { ... }",
        ("var_decl", "ID"): "A type in a declaration must be followed by the variable's name: "
                            "int x = 0;",
        ("param", "ID"): "Each parameter needs a type and a name: int x",
    },
    decl_example="int {name} = 0;",
    string_concat="none", int_as_bool=True,
    placeholder="int main() {\n    int x = 5;\n    return x;\n}",
)

MINIJAVA = Language(
    id="minijava", name="Mini-Java", extension=".java", grammar=MINIJAVA_GRAMMAR,
    keywords=frozenset({"class", "public", "private", "static", "int", "boolean", "String", "void",
                        "if", "else", "while", "return", "true", "false"}),
    types=("int", "boolean", "String", "void"),
    canonical={"int": "int", "boolean": "bool", "String": "str", "void": "void"},
    two_char_ops=_TWO, one_char_ops=_COMMON_OPS | {".", "[", "]"},
    rules={
        ("func_decl", "("): "A method name must be followed by its parameter list in "
                            "parentheses, even when empty: static int name() { ... }",
        ("func_decl", "ID"): "A method's return type must be followed by its name: "
                             "static int name() { ... }",
        ("var_decl", "ID"): "A type in a declaration must be followed by the variable's name: "
                            "int x = 0;",
        ("param", "ID"): "Each parameter needs a type and a name: int x",
        ("program", "class"): "A Mini-Java program is one class: class Main { ... }",
        ("program", "{"): "The class name must be followed by the class body in braces.",
    },
    decl_example="int {name} = 0;",
    string_concat="any",
    placeholder="class Main {\n    static int main() {\n        int x = 5;\n        return x;\n    }\n}",
)

LANGUAGES = {l.id: l for l in (LUMEN, MINIC, MINIJAVA)}


def get_language(lang="lumen") -> Language:
    if isinstance(lang, Language):
        return lang
    key = (lang or "lumen").lower().replace("-", "").replace("_", "")
    aliases = {"lm": "lumen", "c": "minic", "java": "minijava"}
    key = aliases.get(key, key)
    if key not in LANGUAGES:
        raise ValueError(f"unknown language '{lang}' (choose from {', '.join(LANGUAGES)})")
    return LANGUAGES[key]


def language_for_file(path) -> Language:
    for l in LANGUAGES.values():
        if str(path).endswith(l.extension):
            return l
    return LUMEN
