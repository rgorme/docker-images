# recipe-api

MCP server that lets Claude in Home Assistant Assist search the Cookbook Manager
recipe collection: `search_recipes`, `match_recipes` (what can we make from what we
have), `get_recipe` and `shopping_list`. MCP is served on `/mcp` (streamable HTTP),
a health check on `/healthz`.

The image holds only code. Recipes and `ingredients.yaml` live in the private
`cookbook-manager-recipes` repo and are read from `RECIPES_DIR` (default `/recipes`).

Check a recipes folder against its glossary:

```bash
docker run --rm -v "$PWD:/recipes" ghcr.io/rgorme/recipe-api python recipes.py check /recipes
```
