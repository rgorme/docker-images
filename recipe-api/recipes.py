"""Load Cookbook Manager YAML exports and map ingredients to canonical names.

A recipes folder holds ingredients.yaml and the *.yml exports, either beside it or
in a recipes/ subfolder. Run as a script to check a folder against its glossary:
    python recipes.py check /recipes      # exits 1 if any ingredient is unmapped
    python recipes.py unmapped /recipes   # unmapped names with counts, as YAML
"""

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GLOSSARY = "ingredients.yaml"

UNITS = {
    "g", "gram", "gr", "grams", "kg", "mg", "ml", "milliliter", "cl", "dl", "deciliter",
    "l", "liter", "oz", "cup", "cups", "tsp", "teaspoon", "teaspoons", "tbsp",
    "tsk", "spsk", "knsp", "knivspids", "knivspidser", "stk", "fed", "cm",
    "bundt", "bundter", "bdt", "dusk", "kvist", "kviste", "stilk", "stilke",
    "stængel", "stængler", "blad", "blade", "terning", "terninger",
    "dåse", "dåser", "ds", "pose", "poser", "pakke", "pakker", "pk", "brev",
    "flaske", "bakke", "potte", "glas", "skive", "skiver", "dråber",
    "håndfuld", "håndfulde", "drys", "nip",
}
SIZES = {"lille", "små", "stor", "store", "stort"}
# Plurals and spellings that mean the same unit, so amounts add up.
UNIT_ALIASES = {
    "gram": "g", "gr": "g", "grams": "g", "milliliter": "ml", "deciliter": "dl", "liter": "l",
    "knsp": "knivspids", "knivspidser": "knivspids", "dåser": "dåse", "ds": "dåse", "poser": "pose",
    "pakker": "pakke", "pk": "pakke", "bdt": "bundt", "bundter": "bundt", "kviste": "kvist",
    "stilke": "stilk", "stængler": "stængel", "blade": "blad", "terninger": "terning",
    "skiver": "skive", "håndfulde": "håndfuld", "teaspoon": "tsp", "teaspoons": "tsp", "cups": "cup",
}

QTY = r"(?P<qty>\d+/\d+|\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?|[½¼¾])"
LINE = re.compile(rf"^[\s•·×\-]*(?:(?:ca\.?|evt\.?)\s+)?(?:{QTY}\s*)?(?P<rest>.+?)\s*$", re.I)
FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75}


@dataclass
class Ingredient:
    line: str
    qty: float | None  # None = no amount given; 0 = to taste
    unit: str | None
    name: str  # as written, lowercased, without the note
    note: str | None
    canonical: str | None = None


@dataclass
class Recipe:
    id: str
    name: str
    tags: list[str]
    favorite: bool
    cook_count: int
    minutes: int | None
    servings: str | None
    source: str | None
    ingredients: list[Ingredient] = field(default_factory=list)
    sections: list[tuple[str | None, list[Ingredient]]] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)


def _qty(text: str | None) -> float | None:
    if text is None:
        return None
    if text in FRACTIONS:
        return FRACTIONS[text]
    if "/" in text:
        a, b = text.split("/")
        return int(a) / int(b)
    first = re.split(r"\s*[-–]\s*", text)[-1]  # a range counts as its top
    return float(first.replace(",", "."))


def _minutes(*durations: str | None) -> int | None:
    total = 0
    for d in durations:
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", d or "")
        if m:
            total += int(m[1] or 0) * 60 + int(m[2] or 0)
    return total or None


def parse_line(line: str) -> Ingredient:
    m = LINE.match(line)
    qty, rest = m["qty"], m["rest"]
    if re.match(r"[•·×\-]|\d+\s|ca\.?\s|evt\.?\s", rest, re.I):  # "0 · 1 dåse ..." from the export
        inner = parse_line(rest)
        inner.line = line
        return inner
    unit = None
    words = rest.split(" ")
    for _ in range(2):  # "lille dåse ...", "dåse lille ..."
        while len(words) > 1 and words[0].lower() in SIZES:
            words.pop(0)
        if unit is None and len(words) > 1 and words[0].lower().rstrip(".") in UNITS:
            unit = words.pop(0).lower().rstrip(".")
            unit = UNIT_ALIASES.get(unit, unit)
    rest = re.sub(r"\s+(efter (smag|behov))\s*$", r", \1", " ".join(words), flags=re.I)
    # The name ends at a comma, a dash or a parenthesis; the rest is a note.
    name, *note = re.split(r",|\s[–-]\s|\(", rest, maxsplit=1)
    name = re.sub(r"\s+", " ", name).strip(" *.").lower()
    note = note[0].strip(" ()") if note and note[0].strip(" ()") else None
    return Ingredient(line, _qty(qty), unit, name, note)


def _is_header(item) -> str | None:
    if isinstance(item, dict) and len(item) == 1:
        return str(next(iter(item)))
    if isinstance(item, str) and item.rstrip().endswith(":"):
        return item.rstrip()[:-1].strip()
    return None


def load_recipe(path: Path) -> Recipe:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rid = path.stem.rsplit("_", 1)[-1]
    recipe = Recipe(
        id=rid,
        name=str(data.get("name", path.stem)),
        tags=[str(t) for t in data.get("tags") or []],
        favorite=data.get("favorite") in (True, "yes") or data.get("on_favorites") in (True, "yes"),
        cook_count=int(data.get("cook_count") or 0),
        minutes=_minutes(data.get("prep_time"), data.get("cook_time"), data.get("other_time")),
        servings=data.get("servings"),
        source=data.get("source"),
        directions=[str(d) for d in data.get("directions") or [] if str(d).strip()],
    )
    section: tuple[str | None, list[Ingredient]] = (None, [])
    for item in data.get("ingredients") or []:
        header = _is_header(item)
        if header is not None:
            if section[1] or section[0]:
                recipe.sections.append(section)
            section = (header, [])
            continue
        if not str(item).strip():
            continue
        ing = parse_line(str(item))
        section[1].append(ing)
        recipe.ingredients.append(ing)
    if section[1] or section[0]:
        recipe.sections.append(section)
    return recipe


def read_glossary(folder: Path) -> dict:
    path = folder / GLOSSARY
    return (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}


def load_glossary(folder: Path) -> dict[str, str]:
    """Map every canonical name and alias (lowercased) to its canonical name.

    Names under `ignore` (not ingredients: tools, headings, notes) map to "".
    """
    glossary = read_glossary(folder)
    lookup = {str(name).lower(): "" for name in glossary.get("ignore") or []}
    for canonical, info in (glossary.get("ingredients") or {}).items():
        lookup[canonical.lower()] = canonical
        for alias in (info or {}).get("aliases", []):
            lookup[str(alias).lower()] = canonical
    return lookup


def load_all(folder: Path) -> list[Recipe]:
    lookup = load_glossary(folder)
    src = folder / "recipes" if (folder / "recipes").is_dir() else folder
    recipes = [load_recipe(p) for p in sorted(src.glob("*.yml"))]
    for r in recipes:
        for ing in r.ingredients:
            ing.canonical = lookup.get(ing.name)
    return recipes


def unmapped(recipes: list[Recipe]) -> Counter:
    return Counter(i.name for r in recipes for i in r.ingredients if i.canonical is None)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("check", "unmapped"):
        print(__doc__)
        return 2
    folder = Path(argv[2])
    recipes = load_all(folder)
    missing = unmapped(recipes)
    if argv[1] == "unmapped":
        yaml.safe_dump(dict(missing.most_common()), sys.stdout, allow_unicode=True, sort_keys=False)
        return 0
    lines = sum(len(r.ingredients) for r in recipes)
    print(f"{len(recipes)} recipes, {lines} ingredient lines, {len(missing)} unmapped names")
    for name, n in missing.most_common():
        where = [r.name for r in recipes if any(i.name == name for i in r.ingredients)]
        print(f"  {name!r} ({n}x): {', '.join(where[:3])}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
