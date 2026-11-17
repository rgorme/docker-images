"""MCP server that lets Claude in Home Assistant Assist search the recipe collection.

Recipes (Cookbook Manager YAML exports) and ingredients.yaml are read from
RECIPES_DIR at startup. Serves MCP over streamable HTTP on /mcp and a health
check on /healthz.
"""

import logging
import os
from collections import defaultdict
from pathlib import Path

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from recipes import Recipe, load_all, load_glossary, read_glossary

RECIPES_DIR = Path(os.environ.get("RECIPES_DIR", "/recipes"))
# Cupboard basics (spices, stock, oil, salt) that never decide whether a recipe
# can be made. Listed under `basics` in ingredients.yaml; this is the fallback.
BASICS = set(read_glossary(RECIPES_DIR).get("basics") or
             {"Salt", "Peber", "Salt og peber", "Olivenolie", "Neutral olie", "Smør", "Sukker"})

RECIPES: dict[str, Recipe] = {r.id: r for r in load_all(RECIPES_DIR)}
LOOKUP = load_glossary(RECIPES_DIR)
# Per ingredient: supplier (bilka|nemlig, default nemlig) and price ({kr, per, source})
# from the price job. Pantry targets live in Home Assistant (the counters' `maal`).
INFO: dict[str, dict] = {k: v or {} for k, v in (read_glossary(RECIPES_DIR).get("ingredients") or {}).items()}

server = MCPServer(
    name="recipes",
    instructions=(
        "The household's recipe collection (Danish). Ingredient names are canonical Danish names "
        "that match the pantry and freezer counters. Use match_recipes with what is in stock to plan "
        "meals, and shopping_list to turn chosen recipes into one list. For what the next shop costs, "
        "use estimate_cost: for the monthly Bilka run with the gaps from Home Assistant's "
        "inventar_mangler script as packages, for the Nemlig order with the shopping list."
    ),
)


def _canonical(name: str) -> str:
    return LOOKUP.get(name.strip().lower()) or name.strip()


def _ingredients(r: Recipe) -> list[str]:
    return sorted({i.canonical for i in r.ingredients if i.canonical})


def _summary(r: Recipe) -> dict:
    return {
        "id": r.id, "name": r.name, "minutes": r.minutes, "tags": r.tags,
        "favorite": r.favorite, "cook_count": r.cook_count, "ingredients": _ingredients(r),
    }


@server.tool()
def search_recipes(
    query: str | None = None,
    ingredient: str | None = None,
    tag: str | None = None,
    max_minutes: int | None = None,
    favorites_only: bool = False,
    limit: int = 10,
) -> list[dict]:
    """Find recipes by words in the name, an ingredient, a tag, total time or favourite status.
    Favourites and the most cooked come first."""
    want = _canonical(ingredient) if ingredient else None
    hits = [
        r for r in RECIPES.values()
        if (not query or query.lower() in r.name.lower())
        and (not want or want in _ingredients(r))
        and (not tag or tag.lower() in (t.lower() for t in r.tags))
        and (not max_minutes or (r.minutes and r.minutes <= max_minutes))
        and (not favorites_only or r.favorite)
    ]
    hits.sort(key=lambda r: (not r.favorite, -r.cook_count, r.name))
    return [_summary(r) for r in hits[:limit]]


@server.tool()
def match_recipes(have: list[str], max_missing: int = 10, limit: int = 10) -> list[dict]:
    """Rank recipes by how many of the ingredients in `have` (e.g. pantry and freezer contents)
    they use, then by fewest missing. Cupboard basics (spices, stock, oil, salt and pepper)
    count as always in stock and are left out of both lists."""
    stock = {_canonical(h) for h in have} | BASICS
    out = []
    for r in RECIPES.values():
        needed = _ingredients(r)
        missing = [i for i in needed if i not in stock]
        used = [i for i in needed if i in stock and i not in BASICS]
        if used and len(missing) <= max_missing:
            out.append({**_summary(r), "uses": used, "missing": missing})
    out.sort(key=lambda x: (-len(x["uses"]), len(x["missing"]), not x["favorite"]))
    return out[:limit]


@server.tool()
def get_recipe(id: str) -> dict:
    """Full recipe: ingredients grouped by section (with canonical names) and the method."""
    r = RECIPES.get(id)
    if r is None:
        raise ValueError(f"No recipe with id {id}")
    return {
        **_summary(r),
        "servings": r.servings,
        "source": r.source,
        "sections": [
            {"title": title, "ingredients": [
                {"line": i.line, "ingredient": i.canonical, "qty": i.qty, "unit": i.unit, "note": i.note}
                for i in items if i.canonical != ""
            ]}
            for title, items in r.sections
        ],
        "directions": r.directions,
    }


@server.tool()
def shopping_list(recipe_ids: list[str]) -> list[dict]:
    """Combine the ingredients of several recipes into one list, summing amounts per unit.
    A qty of 0 or null means 'to taste' / unspecified."""
    totals: dict[str, dict[str | None, float]] = defaultdict(lambda: defaultdict(float))
    used_in: dict[str, set[str]] = defaultdict(set)
    for rid in recipe_ids:
        r = RECIPES.get(rid)
        if r is None:
            raise ValueError(f"No recipe with id {rid}")
        for i in r.ingredients:
            if i.canonical:
                totals[i.canonical][i.unit] += i.qty or 0
                used_in[i.canonical].add(r.name)
    return [
        {"ingredient": name, "amounts": [{"qty": q or None, "unit": u} for u, q in units.items()],
         "recipes": sorted(used_in[name])}
        for name, units in sorted(totals.items())
    ]


def _priced(name: str, packages: float) -> dict:
    info = INFO.get(name, {})
    price = info.get("price") or {}
    kr = price.get("kr")
    return {
        "ingredient": name, "supplier": info.get("supplier", "nemlig"), "packages": packages,
        "price_per_package": kr, "per": price.get("per"), "price_source": price.get("source"),
        "subtotal": round(kr * packages, 2) if kr is not None else None,
    }


def _totals(lines: list[dict]) -> dict:
    totals: dict[str, float] = defaultdict(float)
    for line in lines:
        if line["subtotal"] is not None:
            totals[line["supplier"]] += line["subtotal"]
    return {
        "lines": lines,
        "total_kr": {s: round(t, 2) for s, t in totals.items()},
        "no_price": [line["ingredient"] for line in lines if line["subtotal"] is None],
    }


@server.tool()
def estimate_cost(items: list[str], packages: dict[str, float] | None = None) -> dict:
    """Estimate what a shopping list costs, per supplier (nemlig or bilka), from last paid
    prices. Each item counts as one package unless `packages` gives {name: count}: for the
    monthly Bilka run, pass the pantry gaps from Home Assistant's inventar_mangler script
    (vare -> mangler). Items with no known price are listed under no_price; say so rather
    than guess."""
    counts = {_canonical(k): v for k, v in (packages or {}).items()}
    lines = [_priced(n, counts.get(n, 1)) for n in dict.fromkeys(_canonical(i) for i in items)]
    return _totals(lines)


async def healthz(_request):
    return PlainTextResponse(f"ok: {len(RECIPES)} recipes")


app = server.streamable_http_app(
    stateless_http=True,
    json_response=True,
    # Reached only inside the cluster via its Service name, so the Host header varies.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
app.router.routes.append(Route("/healthz", healthz))

class QuietHealthz(logging.Filter):
    """Drop access-log lines for successful health checks; failures still log."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access args: (client, method, path, http_version, status_code)
        args = record.args if isinstance(record.args, tuple) else ()
        return not (len(args) == 5 and args[2] == "/healthz" and args[4] < 400)


logging.getLogger("uvicorn.access").addFilter(QuietHealthz())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
