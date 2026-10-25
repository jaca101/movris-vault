"""Idiomas de la interfaz.

Los textos se escriben en inglés en el código, dentro de `_()`, y cada idioma tiene su catálogo
en `locale/<código>.po` (el formato de gettext, que se edita con Poedit o cualquier editor).
Los .po se leen directamente, sin compilarlos a .mo, para no depender de las herramientas de gettext.
Para añadir un idioma: copiar es.po, traducirlo y añadir su código a LANGUAGES.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Optional

LOCALE_DIR = Path(__file__).parent / "locale"
# Cada idioma con su nombre en ese idioma, que es como lo busca quien lo habla
LANGUAGES = {"en": "English", "es": "Español"}
AUTO = "auto"

_catalog: dict[str, list[str]] = {}
_language = "en"


def parse_po(text: str) -> dict[str, list[str]]:
    """msgid → traducciones (una, o varias si tiene plurales). Se saltan las vacías y las «fuzzy»."""
    catalog: dict[str, list[str]] = {}
    entry: dict[str, str] = {}
    key: Optional[str] = None
    fuzzy = False

    def flush() -> None:
        msgid = entry.get("msgid")
        forms = [entry[k] for k in sorted(k for k in entry if k.startswith("msgstr"))]
        if msgid and not fuzzy and forms and all(forms):
            catalog[msgid] = forms

    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if not line:
            flush()
            entry, key, fuzzy = {}, None, False
        elif line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        elif line.startswith("#"):
            continue
        elif line.startswith('"') and key:
            entry[key] += ast.literal_eval(line)
        else:
            key, _sep, value = line.partition(" ")
            entry[key] = ast.literal_eval(value)
    return catalog


def system_language() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        for part in os.environ.get(var, "").split(":"):
            code = part.split(".")[0].split("_")[0].lower()
            if code in LANGUAGES:
                return code
    return "en"


def set_language(choice: str = AUTO) -> str:
    """`choice` es "auto" (el del sistema) o un código de LANGUAGES. Devuelve el idioma que queda."""
    global _catalog, _language
    language = system_language() if choice == AUTO else choice
    if language not in LANGUAGES:
        language = "en"
    po = LOCALE_DIR / f"{language}.po"
    _catalog = parse_po(po.read_text(encoding="utf-8")) if po.exists() else {}
    _language = language
    return language


def current_language() -> str:
    return _language


def _(message: str) -> str:
    forms = _catalog.get(message)
    return forms[0] if forms else message


def ngettext(singular: str, plural: str, n: int) -> str:
    # Español e inglés: singular solo para 1. Otros idiomas necesitarán leer Plural-Forms
    index = 0 if n == 1 else 1
    forms = _catalog.get(singular)
    if forms and len(forms) > index:
        return forms[index]
    return singular if n == 1 else plural
