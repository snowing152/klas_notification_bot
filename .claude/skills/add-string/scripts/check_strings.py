#!/usr/bin/env python3
"""Check app/strings.py for key parity and placeholder consistency.

Parses the file with `ast` rather than importing it: `app.config` raises at
import time without BOT_TOKEN/ADMIN_ID, and this check must run anywhere.

Exit 0 when clean, 1 when a problem is found.
"""

import ast
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"\{(\w+)")

# Keys that are deliberately English-only. "nonexistent_key" backs
# test_string_fallback, which asserts that a key absent from KO/RU falls back
# to the EN table - translating it would defeat the test.
EN_ONLY = {"nonexistent_key"}


def load_tables(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Return {"EN": [(key, template), ...], ...} preserving duplicates."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_strings":
            tables = {}
            for lang_node, table_node in zip(node.value.keys, node.value.values):
                lang = ast.unparse(lang_node).replace("Language.", "")
                tables[lang] = [
                    (ast.literal_eval(k), ast.literal_eval(v))
                    for k, v in zip(table_node.keys, table_node.values)
                ]
            return tables
    raise SystemExit("could not find the _strings table in " + str(path))


def main() -> int:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        # .claude/skills/add-string/scripts/ -> repo root
        target = Path(__file__).resolve().parents[4] / "app" / "strings.py"
    tables = load_tables(target)
    problems: list[str] = []

    for lang, pairs in tables.items():
        keys = [k for k, _ in pairs]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            problems.append(f"{lang}: duplicate keys (the earlier value is discarded): {dupes}")

    maps = {lang: dict(pairs) for lang, pairs in tables.items()}
    all_keys = sorted(set().union(*(m.keys() for m in maps.values())))

    for key in all_keys:
        if key in EN_ONLY:
            continue
        missing = sorted(lang for lang, m in maps.items() if key not in m)
        if missing:
            problems.append(f"{key!r}: missing from {', '.join(missing)}")
            continue
        found = {lang: set(PLACEHOLDER.findall(m[key])) for lang, m in maps.items()}
        if len({frozenset(v) for v in found.values()}) > 1:
            detail = "; ".join(f"{lang}={sorted(v) or '[]'}" for lang, v in found.items())
            problems.append(f"{key!r}: placeholders differ between languages: {detail}")

    if problems:
        print(f"{target}: {len(problems)} problem(s)")
        for p in problems:
            print("  -", p)
        return 1

    print(f"{target} OK: {len(all_keys)} keys x {len(maps)} languages ({', '.join(sorted(maps))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
