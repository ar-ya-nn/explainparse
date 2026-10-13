"""Diagnostic data model and a Rust/Elm-style terminal renderer."""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple
import textwrap


@dataclass
class Label:
    line: int
    col: int
    length: int
    message: str
    primary: bool = True


@dataclass
class Fix:
    description: str
    edits: List[Tuple[int, int, str]] = field(default_factory=list)  # (start, end, replacement)


@dataclass
class Diagnostic:
    code: str
    title: str
    line: int
    col: int
    why: str
    labels: List[Label] = field(default_factory=list)
    fix: Optional[Fix] = None
    confidence: str = "high"
    phase: str = "parser"            # 'lexer' or 'parser'
    root_cause: Optional[dict] = None  # {'line','col','note'} when the cause is elsewhere
    alternative: Optional[str] = None  # runner-up fix when we are not confident

    def to_dict(self):
        d = asdict(self)
        if self.fix:
            d["fix"]["edits"] = [{"start": s, "end": e, "text": t} for s, e, t in self.fix.edits]
        return d


class _Style:
    def __init__(self, color):
        on = color
        self.red = "\033[1;31m" if on else ""
        self.blue = "\033[1;34m" if on else ""
        self.cyan = "\033[36m" if on else ""
        self.green = "\033[32m" if on else ""
        self.yellow = "\033[33m" if on else ""
        self.bold = "\033[1m" if on else ""
        self.reset = "\033[0m" if on else ""


def render(d: Diagnostic, source: str, filename: str = "<input>", color: bool = False) -> str:
    s = _Style(color)
    lines = source.split("\n")
    labels = sorted(d.labels, key=lambda l: (l.line, l.col))
    shown = sorted({l.line for l in labels} | {d.line})
    width = len(str(max(shown))) if shown else 1
    gut = " " * width
    out = [f"{s.red}error[{d.code}]{s.reset}{s.bold}: {d.title}{s.reset}",
           f"{gut}{s.blue}-->{s.reset} {filename}:{d.line}:{d.col}",
           f"{gut} {s.blue}|{s.reset}"]
    prev = None
    for ln in shown:
        if prev is not None and ln > prev + 1:
            out.append(f"{s.blue}{'.' * max(width, 3)}{s.reset}")
        text = lines[ln - 1] if 0 < ln <= len(lines) else ""
        out.append(f"{s.blue}{ln:>{width}} |{s.reset} {text}")
        for lab in [l for l in labels if l.line == ln]:
            mark = "^" if lab.primary else "-"
            col_ = s.red if lab.primary else s.cyan
            pad = " " * max(lab.col - 1, 0)
            out.append(f"{gut} {s.blue}|{s.reset} {pad}{col_}{mark * max(lab.length, 1)} {lab.message}{s.reset}")
        prev = ln
    out.append(f"{gut} {s.blue}|{s.reset}")
    wrap = lambda prefix, body: textwrap.fill(body, width=88, initial_indent=prefix,
                                              subsequent_indent=" " * len(prefix))
    out.append(wrap(f"{gut} {s.blue}={s.reset} why: ", d.why))
    if d.root_cause:
        out.append(wrap(f"{gut} {s.blue}={s.reset} root cause: ",
                        f"line {d.root_cause['line']}: {d.root_cause['note']}"))
    if d.fix:
        out.append(f"{gut} {s.blue}={s.reset} {s.green}fix:{s.reset} {d.fix.description} "
                   f"{s.yellow}(confidence: {d.confidence}){s.reset}")
    if d.alternative:
        out.append(f"{gut} {s.blue}={s.reset} alternative: {d.alternative}")
    return "\n".join(out)


def apply_edits(source: str, edits) -> str:
    """Apply (start, end, text) edits given in original-source offsets, in order of creation."""
    indexed = list(enumerate(edits))
    indexed.sort(key=lambda it: (it[1][0], it[0]), reverse=True)
    for _, (start, end, text) in indexed:
        source = source[:start] + text + source[end:]
    return source
