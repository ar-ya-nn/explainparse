"""Build the static GitHub Pages site (no server needed; Python runs in the browser via Pyodide).

    python -m explainparse.build_site            # writes ./site
Then publish the contents of ./site with GitHub Pages.
"""
import glob
import json
import os
import shutil
from .server import meta, ROOT

MODULES = ["__init__.py", "tokens.py", "diagnostics.py", "languages.py", "lexer.py", "grammar.py",
           "parser.py", "explain.py", "semantic.py", "engine.py", "evaluate.py", "extras.py"]


def build(out="site"):
    out = os.path.join(ROOT, out)
    os.makedirs(out, exist_ok=True)
    pkg = os.path.join(ROOT, "explainparse")
    files = {m: open(os.path.join(pkg, m), encoding="utf-8").read() for m in MODULES}
    with open(os.path.join(out, "bundle.json"), "w", encoding="utf-8") as f:
        json.dump({"files": files, "meta": meta()}, f)
    shutil.copy(os.path.join(ROOT, "web", "index.html"), os.path.join(out, "index.html"))
    open(os.path.join(out, ".nojekyll"), "w").close()
    return out


if __name__ == "__main__":
    print(f"site written to {build()}")
