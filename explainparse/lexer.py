"""Hand-written lexer (Lumen, Mini-C, Mini-Java) with explainable, recoverable lexical errors."""
from typing import List, Tuple
from .tokens import Token
from .diagnostics import Diagnostic, Label, Fix
from .languages import get_language


def lex(src: str, lang="lumen") -> Tuple[List[Token], List[Diagnostic]]:
    lang = get_language(lang)
    KEYWORDS, TWO_CHAR_OPS, ONE_CHAR_OPS = lang.keywords, lang.two_char_ops, lang.one_char_ops
    NAME = lang.name
    toks: List[Token] = []
    diags: List[Diagnostic] = []
    i, n = 0, len(src)
    line, line_start = 1, 0

    def col(pos):
        return pos - line_start + 1

    def emit(kind, a, b):
        toks.append(Token(kind, src[a:b], line, col(a), a, b))

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            line_start = i
            continue
        if c in " \t\r":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j == -1:
                diags.append(Diagnostic(
                    "E103", "unterminated block comment", line, col(i),
                    "A block comment starting with '/*' must be closed with '*/'. Because it never is, "
                    "everything after it until the end of the file was treated as a comment.",
                    [Label(line, col(i), 2, "comment starts here and never ends")],
                    Fix("close the comment with '*/'", [(n, n, " */")]), phase="lexer"))
                i = n
                continue
            seg = src[i:j + 2]
            if "\n" in seg:
                line += seg.count("\n")
                line_start = i + seg.rfind("\n") + 1
            i = j + 2
            continue
        if c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            if j < n and (src[j].isalpha() or src[j] == "_"):
                k = j
                while k < n and (src[k].isalnum() or src[k] == "_"):
                    k += 1
                word = src[i:k]
                diags.append(Diagnostic(
                    "E102", f"malformed number '{word}'", line, col(i),
                    f"'{word}' starts with digits but continues with letters. Numbers may only contain "
                    f"digits, and identifiers cannot start with a digit.",
                    [Label(line, col(i), k - i, "not a valid number or identifier")],
                    Fix(f"rename it so it starts with a letter, e.g. 'v{word}'", [(i, i, "v")]),
                    confidence="medium", phase="lexer"))
                emit("ID", i, k)   # recover as an identifier so parsing continues
                i = k
                continue
            emit("INT", i, j)
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            emit(word if word in KEYWORDS else "ID", i, j)
            i = j
            continue
        if c == '"' or c == "'":
            j = i + 1
            closed = False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == c:
                    closed = True
                    break
                j += 1
            j = min(j, n)
            if c == "'":
                end = j + 1 if closed else j
                diags.append(Diagnostic(
                    "E104", "strings use double quotes", line, col(i),
                    f"{NAME} strings are written with double quotes (\"...\"). Single quotes are not "
                    "valid string delimiters.",
                    [Label(line, col(i), end - i, "single-quoted string")],
                    Fix("replace the single quotes with double quotes",
                        [(i, i + 1, '"')] + ([(j, j + 1, '"')] if closed else [(j, j, '"')])),
                    phase="lexer"))
                emit("STRING", i, end)
                i = end
                continue
            if closed:
                emit("STRING", i, j + 1)
                i = j + 1
            else:
                # Root-cause guess: `"text;` usually means the quote belongs before the ';'
                body = src[i:j].rstrip()
                if body.endswith(";") and len(body) > 2:
                    k = i + len(body) - 1
                    diags.append(Diagnostic(
                        "E101", "unterminated string", line, col(i),
                        "This string literal opens with '\"' but is never closed. The ';' at the "
                        "end of the line suggests the closing quote belongs just before it, so the "
                        "';' is treated as the end of the statement rather than part of the text.",
                        [Label(line, col(i), k - i, "string starts here"),
                         Label(line, col(k), 1, "expected '\"' before this ';'", False)],
                        Fix("close the string before the ';'", [(k, k, '"')]), phase="lexer"))
                    emit("STRING", i, k)
                    i = k
                    continue
                diags.append(Diagnostic(
                    "E101", "unterminated string", line, col(i),
                    "This string literal opens with '\"' but the line ends before a closing '\"'. "
                    "Strings cannot span multiple lines.",
                    [Label(line, col(i), j - i, "string starts here"),
                     Label(line, col(j), 1, "expected '\"' before the end of the line", False)],
                    Fix("close the string with '\"' at the end of the line", [(j, j, '"')]),
                    phase="lexer"))
                emit("STRING", i, j)
                i = j
            continue
        two = src[i:i + 2]
        if two in TWO_CHAR_OPS:
            emit(two, i, i + 2)
            i += 2
            continue
        if c in ONE_CHAR_OPS:
            emit(c, i, i + 1)
            i += 1
            continue
        if c in "&|":
            op = c * 2
            word = "and" if c == "&" else "or"
            diags.append(Diagnostic(
                "E100", f"unknown operator '{c}'", line, col(i),
                f"{NAME} has no single '{c}' operator. Logical {word} is written '{op}'.",
                [Label(line, col(i), 1, f"did you mean '{op}'?")],
                Fix(f"replace '{c}' with '{op}'", [(i, i + 1, op)]), confidence="medium", phase="lexer"))
            toks.append(Token(op, c, line, col(i), i, i + 1))   # recover as the intended operator
            i += 1
            continue
        diags.append(Diagnostic(
            "E100", f"invalid character {c!r}", line, col(i),
            f"The character {c!r} is not part of the {NAME} language and cannot start any token.",
            [Label(line, col(i), 1, "not allowed here")],
            Fix("remove it", [(i - 1, i + 1, "") if i > 0 and src[i - 1] == " " and
                              src[i + 1:i + 2] in (";", ")", ",", "") else (i, i + 1, "")]),
            phase="lexer"))
        i += 1

    toks.append(Token("EOF", "", line, col(i), n, n))
    return toks, diags
