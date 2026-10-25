"""Saca los textos traducibles del código: los `_("…")` y `ngettext("…", "…", n)`.

    python3 tools/extract_strings.py            lista los que faltan en cada .po
    python3 tools/extract_strings.py --pot      escribe src/movris_vault/locale/movris-vault.pot
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "movris_vault"
LOCALE = SRC / "locale"


def extract(src: Path = SRC) -> dict[str, tuple[str | None, list[str]]]:
    """msgid → (msgid_plural o None, lugares donde aparece)."""
    found: dict[str, tuple[str | None, list[str]]] = {}
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            plural = None
            if node.func.id == "_" and len(args) == 1:
                msgid = args[0]
            elif node.func.id == "ngettext" and len(args) >= 2:
                msgid, plural = args[0], args[1]
            else:
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            found.setdefault(msgid, (plural, []))[1].append(where)
    return found


def po_quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def write_pot(strings: dict[str, tuple[str | None, list[str]]], path: Path) -> None:
    out = ['msgid ""', 'msgstr ""', '"Content-Type: text/plain; charset=UTF-8\\n"', ""]
    for msgid, (plural, places) in strings.items():
        out.append("#: " + " ".join(places))
        out.append(f"msgid {po_quote(msgid)}")
        if plural:
            out += [f"msgid_plural {po_quote(plural)}", 'msgstr[0] ""', 'msgstr[1] ""']
        else:
            out.append('msgstr ""')
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from movris_vault.i18n import parse_po

    strings = extract()
    if "--pot" in sys.argv:
        write_pot(strings, LOCALE / "movris-vault.pot")
        print(f"{len(strings)} textos → {LOCALE / 'movris-vault.pot'}")
        return 0
    missing_any = False
    for po in sorted(LOCALE.glob("*.po")):
        catalog = parse_po(po.read_text(encoding="utf-8"))
        missing = [m for m in strings if m not in catalog]
        missing_any |= bool(missing)
        print(f"{po.name}: faltan {len(missing)} de {len(strings)}")
        for m in missing:
            print(f"   {m!r}")
    return 1 if missing_any else 0


if __name__ == "__main__":
    sys.exit(main())
