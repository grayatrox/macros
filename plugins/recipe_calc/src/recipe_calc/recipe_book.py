"""Recipe Calc's calculation logic, with no GUI and no file access of its own.

Reads recipes and item tags out of a Minecraft client jar, picks one recipe per
item, works out how much of every ingredient a quantity needs, and lays out how
a recipe sits in its workstation's GUI. recipe_calc.app draws the results.

A jar is anything with ``namelist()`` and ``read(name)`` (see ``Archive``), so a
``zipfile.ZipFile`` works and tests pass an in-memory stand-in.
"""

from __future__ import annotations

import json
import math
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

# Recipe/tag folders were renamed in 1.21 (recipes -> recipe, tags/items -> tags/item)
RECIPE_DIRS = ("data/minecraft/recipe/", "data/minecraft/recipes/")
TAG_DIRS = ("data/minecraft/tags/item/", "data/minecraft/tags/items/")

STACK_SIZE = 64

# Brewing is hardcoded in the game rather than data-driven, so the jar has no
# potion recipes to extract - they are defined here instead. One brewing-stand
# batch turns 3 bottles + 1 ingredient into 3 potions. Blaze powder fuel is
# not counted (one powder fuels 20 batches).
BREWING_RECIPES: dict[str, tuple[dict[str, int], int]] = {
    "water_bottle": ({"glass_bottle": 1}, 1),
    "awkward_potion": ({"water_bottle": 3, "nether_wart": 1}, 3),
    "potion_of_night_vision": ({"awkward_potion": 3, "golden_carrot": 1}, 3),
    "potion_of_invisibility": ({"potion_of_night_vision": 3, "fermented_spider_eye": 1}, 3),
    "potion_of_leaping": ({"awkward_potion": 3, "rabbit_foot": 1}, 3),
    "potion_of_fire_resistance": ({"awkward_potion": 3, "magma_cream": 1}, 3),
    "potion_of_swiftness": ({"awkward_potion": 3, "sugar": 1}, 3),
    "potion_of_slowness": ({"potion_of_swiftness": 3, "fermented_spider_eye": 1}, 3),
    "potion_of_water_breathing": ({"awkward_potion": 3, "pufferfish": 1}, 3),
    "potion_of_healing": ({"awkward_potion": 3, "glistering_melon_slice": 1}, 3),
    "potion_of_harming": ({"potion_of_healing": 3, "fermented_spider_eye": 1}, 3),
    "potion_of_poison": ({"awkward_potion": 3, "spider_eye": 1}, 3),
    "potion_of_regeneration": ({"awkward_potion": 3, "ghast_tear": 1}, 3),
    "potion_of_strength": ({"awkward_potion": 3, "blaze_powder": 1}, 3),
    "potion_of_weakness": ({"water_bottle": 3, "fermented_spider_eye": 1}, 3),
    "potion_of_slow_falling": ({"awkward_potion": 3, "phantom_membrane": 1}, 3),
    "potion_of_the_turtle_master": ({"awkward_potion": 3, "turtle_helmet": 1}, 3),
    "potion_of_wind_charging": ({"awkward_potion": 3, "breeze_rod": 1}, 3),
    "potion_of_weaving": ({"awkward_potion": 3, "cobweb": 1}, 3),
    "potion_of_oozing": ({"awkward_potion": 3, "slime_block": 1}, 3),
    "potion_of_infestation": ({"awkward_potion": 3, "stone": 1}, 3),
}

# Every potion has splash and lingering variants: gunpowder makes it splash,
# dragon's breath makes the splash version linger.
for _name in [n for n in BREWING_RECIPES if n.startswith("potion_of_")]:
    BREWING_RECIPES["splash_" + _name] = ({_name: 3, "gunpowder": 1}, 3)
    BREWING_RECIPES["lingering_" + _name] = ({"splash_" + _name: 3, "dragon_breath": 1}, 3)

# A brewing ingredient used this many times is the bottles, not the catalyst
BOTTLES_PER_BATCH = 3

# Liquid tints. The potion bottle texture is an empty bottle; the game tints
# a separate liquid overlay at runtime, so the icons do the same.
POTION_COLORS = {
    "water_bottle": "#385dc6",
    "awkward_potion": "#385dc6",
    "night_vision": "#c2ff66",
    "invisibility": "#f6f6f6",
    "leaping": "#fdff84",
    "fire_resistance": "#ff9900",
    "swiftness": "#33ebff",
    "slowness": "#8bafe0",
    "water_breathing": "#98dac0",
    "healing": "#f82423",
    "harming": "#a9656a",
    "poison": "#87a363",
    "regeneration": "#cd5cab",
    "strength": "#ffc700",
    "weakness": "#484d48",
    "slow_falling": "#f3cfb9",
    "the_turtle_master": "#7699c6",
    "wind_charging": "#bdc9ff",
    "weaving": "#78695a",
    "oozing": "#99ffa3",
    "infestation": "#8c9b8c",
}

# Words minecraft.wiki keeps lowercase in page titles
WIKI_SMALL_WORDS = {"of", "the", "a", "an", "and", "with", "on"}

COOKING = ("smelting", "blasting", "smoking", "campfire_cooking")
SINGLE_INPUT = (*COOKING, "stonecutting")

# Recipe method -> the workstation shown in the placement panel
STATIONS = {
    "crafting_shaped": "Crafting Table",
    "crafting_shapeless": "Crafting Table (any arrangement)",
    "smelting": "Furnace",
    "blasting": "Blast Furnace",
    "smoking": "Smoker",
    "campfire_cooking": "Campfire",
    "stonecutting": "Stonecutter",
    "brewing": "Brewing Stand",
    "filling": "Fill Glass Bottle with Water",
}

# Recipe method -> its GUI texture in assets/minecraft/textures/gui/container/
STATION_TEXTURES = {
    "crafting_shaped": "crafting_table",
    "crafting_shapeless": "crafting_table",
    "smelting": "furnace",
    "blasting": "blast_furnace",
    "smoking": "smoker",
    "brewing": "brewing_stand",
    "stonecutting": "stonecutter",
}

# The plain brewing stand GUI at 2x; wider means the result slot was spliced in
SPLICED_BREWING_MIN_WIDTH = 353

Ref = tuple[str, str]  # ('item' | 'tag', name)
Kind = Literal["root", "craft", "base", "brew"]


class Archive(Protocol):
    """The part of ``zipfile.ZipFile`` this module reads a jar through."""

    def namelist(self) -> list[str]: ...

    def read(self, name: str) -> bytes: ...


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def pretty_name(item: str) -> str:
    """oak_log -> Oak Log"""
    return item.replace("_", " ").title()


def format_stacks(amount: int) -> str:
    """133 -> '2 stacks + 5' (Minecraft stacks of 64); under one stack is '—'."""
    stacks, rem = divmod(amount, STACK_SIZE)
    if stacks == 0:
        return "—"
    label = f"{stacks} stack" + ("s" if stacks > 1 else "")
    return label if rem == 0 else f"{label} + {rem}"


def wiki_url(item: str) -> str:
    """minecraft.wiki page for an item, e.g. potion_of_weakness ->
    https://minecraft.wiki/w/Potion_of_Weakness. Splash/lingering potions
    have no page of their own - they are sections on the base potion's."""
    anchor = ""
    if item.startswith("splash_potion_of_"):
        item, anchor = item[len("splash_") :], "#Splash_Potion"
    elif item.startswith("lingering_potion_of_"):
        item, anchor = item[len("lingering_") :], "#Lingering_Potion"
    title = "_".join(
        word if (i > 0 and word in WIKI_SMALL_WORDS) else word.capitalize()
        for i, word in enumerate(item.split("_"))
    )
    return f"https://minecraft.wiki/w/{title}{anchor}"


def texture_names(item: str) -> tuple[str, ...]:
    """Candidate texture paths in the jar for an item, most specific first."""
    if item.startswith("splash_potion_of_"):
        item = "splash_potion"  # potion bottle textures are tinted in-game
    elif item.startswith("lingering_potion_of_"):
        item = "lingering_potion"
    elif item.startswith("potion_of_") or item in ("awkward_potion", "water_bottle"):
        item = "potion"
    base = "assets/minecraft/textures/"
    return (
        f"{base}item/{item}.png",
        f"{base}block/{item}.png",
        f"{base}block/{item}_top.png",
        f"{base}block/{item}_front.png",
        f"{base}block/{item}_side.png",
    )


def potion_look(item: str) -> tuple[str, str] | None:
    """(bottle texture name, liquid colour) for a potion item, else None."""
    if item.startswith("splash_potion_of_"):
        bottle, effect = "splash_potion", item[len("splash_potion_of_") :]
    elif item.startswith("lingering_potion_of_"):
        bottle, effect = "lingering_potion", item[len("lingering_potion_of_") :]
    elif item.startswith("potion_of_"):
        bottle, effect = "potion", item[len("potion_of_") :]
    elif item in ("awkward_potion", "water_bottle"):
        bottle, effect = "potion", item
    else:
        return None
    return bottle, POTION_COLORS.get(effect, POTION_COLORS["water_bottle"])


# ---------------------------------------------------------------------------
# Recipe parsing
# ---------------------------------------------------------------------------


def ingredient_ref(entry: object) -> Ref | None:
    """Normalize one ingredient entry to ('item'|'tag', name) or None.

    Handles {'item': ...}, {'tag': ...}, plain strings ('#tag' or 'item',
    used by 1.21.2+), and lists of alternatives (first one is used).
    """
    if isinstance(entry, list):
        return ingredient_ref(entry[0]) if entry else None
    if isinstance(entry, str):
        if entry.startswith("#"):
            return ("tag", entry.lstrip("#").split(":")[-1])
        return ("item", entry.split(":")[-1])
    if isinstance(entry, dict):
        for key, kind in (("item", "item"), ("tag", "tag"), ("id", "item")):
            value = entry.get(key)
            if isinstance(value, str) and value:
                return (kind, value.split(":")[-1])
    return None


@dataclass(frozen=True)
class ParsedRecipe:
    """One recipe file, before tags are resolved to items."""

    result: str
    output: int
    method: str
    ingredients: dict[Ref, int]
    grid: tuple[Ref | None, ...]  # 9 crafting slots, row by row; None is empty


def _parse_result(recipe_json: dict[str, Any]) -> tuple[str, int] | None:
    """(result item, items per craft), or None if the result is unusable."""
    result = recipe_json.get("result")
    if isinstance(result, dict):
        result_item = result.get("id") or result.get("item") or ""
        output_count = result.get("count", 1)
    elif isinstance(result, str):
        result_item = result
        output_count = recipe_json.get("count", 1)
    else:
        return None
    if not isinstance(result_item, str) or not result_item.split(":")[-1]:
        return None
    try:
        return result_item.split(":")[-1], max(int(output_count), 1)
    except (TypeError, ValueError):
        return None


def _parse_inputs(
    method: str, recipe_json: dict[str, Any]
) -> tuple[list[Ref | None], list[Ref | None]] | None:
    """(every ingredient use, the 9-slot grid), or None for a type not handled."""
    refs: list[Ref | None] = []
    grid: list[Ref | None] = [None] * 9
    if method == "crafting_shaped":
        key = recipe_json.get("key", {})
        for r, row in enumerate(recipe_json.get("pattern", [])[:3]):
            for c, symbol in enumerate(row[:3]):
                if symbol != " " and symbol in key:
                    ref = ingredient_ref(key[symbol])
                    refs.append(ref)
                    grid[r * 3 + c] = ref
    elif method == "crafting_shapeless":
        refs = [ingredient_ref(e) for e in recipe_json.get("ingredients", [])]
        for i, present in enumerate([r for r in refs if r][:9]):
            grid[i] = present
    elif method in SINGLE_INPUT:
        ref = ingredient_ref(recipe_json.get("ingredient"))
        refs = [ref]
        grid[4] = ref
    else:
        return None
    return refs, grid


def parse_recipe(recipe_json: object) -> ParsedRecipe | None:
    """Read one recipe file's JSON; None for recipe types the calculator skips."""
    if not isinstance(recipe_json, dict):
        return None
    method = str(recipe_json.get("type", "")).split(":")[-1]
    result = _parse_result(recipe_json)
    inputs = _parse_inputs(method, recipe_json)
    if result is None or inputs is None:
        return None
    refs, grid = inputs
    ingredients: dict[Ref, int] = {}
    for ref in refs:
        if ref:
            ingredients[ref] = ingredients.get(ref, 0) + 1
    if not ingredients:
        return None
    return ParsedRecipe(result[0], result[1], method, ingredients, tuple(grid))


def resolve_tag(
    tags: Mapping[str, list[Any]], tag_name: str, visited: set[str] | None = None
) -> str | None:
    """Pick a representative item for a tag (e.g. planks -> oak_planks)."""
    if visited is None:
        visited = set()
    if tag_name in visited:
        return None
    visited.add(tag_name)
    for entry in tags.get(tag_name, []):
        value = entry.get("id", "") if isinstance(entry, dict) else entry
        if not isinstance(value, str) or not value:
            continue
        if value.startswith("#"):
            nested = resolve_tag(tags, value.lstrip("#").split(":")[-1], visited)
            if nested:
                return nested
        else:
            return value.split(":")[-1]
    return None


def recipe_priority(recipe_name: str, parsed: ParsedRecipe) -> int:
    """Lower wins when an item has several recipes.

    The canonical recipe is preferred (e.g. gold_ingot: smelting ore beats
    unpacking gold_block).
    """
    if recipe_name == parsed.result:
        return 0
    if parsed.method in SINGLE_INPUT:
        return 2
    if "_from_" in recipe_name:
        return 3
    return 1


# ---------------------------------------------------------------------------
# The recipe book
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    """How one item is made, with tags already resolved to concrete items."""

    ingredients: dict[str, int]
    output: int  # items made per craft
    method: str  # a key of STATIONS
    grid: tuple[str | None, ...]  # 9 slots; see ParsedRecipe.grid
    brewing: bool = False


@dataclass(frozen=True)
class BreakdownNode:
    """One row of a quantity breakdown: ``amount`` of ``item``, and what makes it."""

    item: str
    amount: int
    kind: Kind
    children: list[BreakdownNode] = field(default_factory=list)

    def walk(self) -> Iterator[BreakdownNode]:
        """This node and every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class RecipeBook:
    """One recipe per craftable item, plus every item any recipe mentions."""

    recipes: dict[str, Recipe]
    all_items: frozenset[str]

    @property
    def base_items(self) -> frozenset[str]:
        """Items no recipe makes: they are gathered in the world."""
        return self.all_items - self.recipes.keys()

    def kind(self, item: str) -> Kind:
        recipe = self.recipes.get(item)
        if recipe is None:
            return "base"
        return "brew" if recipe.brewing else "craft"

    def search(self, term: str) -> list[str]:
        """Items matching ``term``, sorted. Spaces match underscores ("oak planks")."""
        term = term.lower().strip()
        return [i for i in sorted(self.all_items) if term in i or term in i.replace("_", " ")]

    def breakdown(self, item: str, quantity: int) -> BreakdownNode:
        """Everything needed for ``quantity`` of ``item``, as a tree.

        Each ingredient's amount is rounded up to whole crafts of its parent.
        Recursion stops at base items and at any item already being made
        further up the same branch, so a recipe cycle cannot loop forever.
        """
        root = BreakdownNode(item, quantity, "root")
        self._add_children(root, item, quantity, frozenset())
        return root

    def _add_children(
        self, parent: BreakdownNode, item: str, needed: int, ancestors: frozenset[str]
    ) -> None:
        recipe = self.recipes.get(item)
        if recipe is None or item in ancestors:
            return
        crafts = math.ceil(needed / recipe.output)
        for ingredient, amount in sorted(recipe.ingredients.items()):
            node = BreakdownNode(ingredient, amount * crafts, self.kind(ingredient))
            parent.children.append(node)
            self._add_children(node, ingredient, node.amount, ancestors | {item})


def _read_json(archive: Archive, name: str) -> object:
    """A JSON file from the jar, or None if it is not valid JSON."""
    try:
        parsed: object = json.loads(archive.read(name).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed


def _read_tags(archive: Archive, names: list[str]) -> dict[str, list[Any]]:
    tags: dict[str, list[Any]] = {}
    for name in names:
        if name.startswith(TAG_DIRS) and name.endswith(".json"):
            tag_json = _read_json(archive, name)
            if isinstance(tag_json, dict) and isinstance(tag_json.get("values"), list):
                tags[PurePosixPath(name).stem] = tag_json["values"]
    return tags


def _resolve(parsed: ParsedRecipe, tags: Mapping[str, list[Any]]) -> Recipe | None:
    """``parsed`` with tags swapped for items; None if no ingredient resolves."""

    def item_of(ref: Ref) -> str | None:
        kind, name = ref
        return resolve_tag(tags, name) if kind == "tag" else name

    ingredients: dict[str, int] = {}
    for ref, count in parsed.ingredients.items():
        item = item_of(ref)
        if item:
            ingredients[item] = ingredients.get(item, 0) + count
    if not ingredients:
        return None
    grid = tuple(item_of(ref) if ref else None for ref in parsed.grid)
    return Recipe(ingredients, parsed.output, parsed.method, grid)


def _candidate_recipes(archive: Archive) -> dict[str, list[Recipe]]:
    """Every usable recipe per item, best first."""
    names = archive.namelist()
    tags = _read_tags(archive, names)  # first, so tag ingredients can be resolved
    candidates: dict[str, list[tuple[int, int, ParsedRecipe]]] = {}
    for name in names:
        if not (name.startswith(RECIPE_DIRS) and name.endswith(".json")):
            continue
        parsed = parse_recipe(_read_json(archive, name))
        if parsed is None:
            continue
        recipe_name = PurePosixPath(name).stem
        # Shorter names break priority ties (raw_gold beats deepslate_gold_ore)
        rank = (recipe_priority(recipe_name, parsed), len(recipe_name), parsed)
        candidates.setdefault(parsed.result, []).append(rank)

    options: dict[str, list[Recipe]] = {}
    for item, ranked in candidates.items():
        ranked.sort(key=lambda c: (c[0], c[1]))
        resolved = [r for _, _, parsed in ranked if (r := _resolve(parsed, tags))]
        if resolved:
            options[item] = resolved
    return options


def _brewing_recipe(name: str, ingredients: dict[str, int], output: int) -> Recipe:
    grid: list[str | None] = [None] * 9
    if name == "water_bottle":
        grid[4] = "glass_bottle"
        return Recipe(dict(ingredients), output, "filling", tuple(grid), brewing=True)
    for ingredient, count in ingredients.items():
        if count >= BOTTLES_PER_BATCH:  # the three bottles in the stand
            grid[6] = grid[7] = grid[8] = ingredient
        else:  # the catalyst in the top slot
            grid[1] = ingredient
    return Recipe(dict(ingredients), output, "brewing", tuple(grid), brewing=True)


def load_recipe_book(archive: Archive) -> RecipeBook:
    """Build the recipe book from a jar's recipe and tag files.

    Unreadable or unsupported recipe files are skipped. Errors reading the
    archive itself (``OSError``, ``zipfile.BadZipFile``) propagate.
    """
    options = _candidate_recipes(archive)
    recipes = {item: alternatives[0] for item, alternatives in options.items()}

    def is_cyclic(item: str, recipe: Recipe) -> bool:
        return any(
            item in (sub.ingredients if (sub := recipes.get(ing)) else {})
            for ing in recipe.ingredients
        )

    # Avoid pack/unpack cycles: e.g. redstone.json crafts 9 redstone
    # from redstone_block while redstone_block packs 9 redstone.
    # Swap to an alternative recipe (usually smelting) when detected.
    for _ in range(2):
        changed = False
        for item, alternatives in options.items():
            if is_cyclic(item, recipes[item]):
                for alt in alternatives[1:]:
                    if not is_cyclic(item, alt):
                        recipes[item] = alt
                        changed = True
                        break
        if not changed:
            break

    # When an item's only recipe unpacks a block that packs it back
    # (slime_ball <-> slime_block), the item is really gathered, not
    # crafted - drop the recipe so it counts as a base material
    for item in list(recipes):
        recipe = recipes[item]
        if len(recipe.ingredients) == 1 and recipe.output > 1 and is_cyclic(item, recipe):
            del recipes[item]

    # Brewing recipes are defined in code (see BREWING_RECIPES)
    for name, (ingredients, output) in BREWING_RECIPES.items():
        if name not in recipes:
            recipes[name] = _brewing_recipe(name, ingredients, output)

    all_items: set[str] = set()
    for item, recipe in recipes.items():
        all_items.add(item)
        all_items.update(recipe.ingredients)
    return RecipeBook(recipes, frozenset(all_items))


# ---------------------------------------------------------------------------
# Placement: where a recipe's items sit in its workstation GUI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """An item drawn at 1x GUI-texture coordinates (the view draws at 2x)."""

    x: int
    y: int
    item: str
    note: str = ""  # appended to the item's name in the hover caption
    count: int = 1  # drawn in the slot corner when more than 1
    plain: bool = False  # draws its own slot background (no GUI texture)


@dataclass(frozen=True)
class Placement:
    """Everything the placement panel draws for one item."""

    heading: str
    caption: str  # shown under the panel while no slot is hovered
    texture: str | None = None  # GUI texture name drawn behind the slots
    title: str = ""  # drawn on the texture, or on the plain panel
    title_y: int = 16  # centre of the title, in 2x view pixels
    slots: tuple[Slot, ...] = ()
    result: Slot | None = None  # drawn in a box to the right of the panel

    @property
    def panel(self) -> bool:
        """Whether a plain panel stands in for a missing GUI texture."""
        return self.texture is None


RESULT_NOTE = "  (result)"


def station_texture(recipe: Recipe | None) -> str | None:
    """The GUI texture a recipe is shown in, or None for a plain panel."""
    return STATION_TEXTURES.get(recipe.method) if recipe else None


def placement(item: str, recipe: Recipe | None, texture_width: int | None) -> Placement:
    """Lay out ``recipe`` for ``item`` in its workstation's GUI.

    ``texture_width`` is the width at 2x of the texture ``station_texture``
    named, or None when there is none or the jar lacks it; the layout then
    falls back to plain slots with the result in a box beside them.
    """
    if recipe is None:
        return Placement(
            heading="Placement  ·  base item, not craftable",
            caption=f"{pretty_name(item)} is gathered in the world, not crafted",
            title="Base Item",
            title_y=36,
            slots=(Slot(80, 34, item, plain=True),),
        )

    station = STATIONS.get(recipe.method, recipe.method)
    output = recipe.output
    result_text = pretty_name(item) + (f" \N{MULTIPLICATION SIGN} {output}" if output > 1 else "")
    if recipe.method == "brewing":
        caption = f"The 3 bottles brew into {result_text} in place"
    else:
        caption = f"Makes {result_text} per craft  ·  hover slots for names"
    heading = f"Placement  ·  {station}"

    texture = station_texture(recipe)
    if texture is None or texture_width is None:
        x = 40
        slots: list[Slot] = []
        for ingredient, count in recipe.ingredients.items():
            slots.append(Slot(x, 34, ingredient, count=count, plain=True))
            x += 30
        return Placement(
            heading=heading,
            caption=caption,
            title=station,
            title_y=22,
            slots=tuple(slots),
            result=Slot(0, 0, item, RESULT_NOTE, output),
        )

    slots = list(_station_slots(item, recipe, texture_width))
    return Placement(
        heading=heading,
        caption=caption,
        texture=texture,
        title=station.split(" (")[0],
        slots=tuple(s for s in slots if s.item),
    )


def _station_slots(item: str, recipe: Recipe, texture_width: int) -> Iterator[Slot]:
    """Slots on the real GUI texture, at the game's own coordinates."""
    output = recipe.output
    source = next(iter(recipe.ingredients), "")
    if recipe.method in ("crafting_shaped", "crafting_shapeless"):
        for i, cell in enumerate(recipe.grid[:9]):
            yield Slot(30 + (i % 3) * 18, 17 + (i // 3) * 18, cell or "")
        yield Slot(124, 35, item, RESULT_NOTE, output)
    elif recipe.method in COOKING:
        yield Slot(56, 17, source)
        yield Slot(56, 53, "coal", "  (or any other fuel)")
        yield Slot(116, 35, item, RESULT_NOTE, output)
    elif recipe.method == "stonecutting":
        yield Slot(20, 33, source)
        yield Slot(143, 33, item, RESULT_NOTE, output)
    elif recipe.method == "brewing":
        ingredients = recipe.ingredients.items()
        catalyst = next((i for i, n in ingredients if n == 1), "")
        bottle = next((i for i, n in ingredients if n >= BOTTLES_PER_BATCH), "")
        yield Slot(17, 17, "blaze_powder", "  (fuel, lasts 20 batches)")
        yield Slot(79, 17, catalyst)
        for x, y in ((56, 51), (79, 58), (102, 51)):
            yield Slot(x, y, bottle)
        # The real GUI has no result slot; a spliced-in one makes it wider
        if texture_width >= SPLICED_BREWING_MIN_WIDTH:
            yield Slot(181, 35, item, RESULT_NOTE, output)


# ---------------------------------------------------------------------------
# Finding installed jars
# ---------------------------------------------------------------------------

LAST_USED = " (last used)"


@dataclass(frozen=True)
class JarCandidate:
    """A Minecraft client jar the user could load recipes from."""

    path: Path
    version: str
    source: str  # which launcher installed it


def archive_has_recipes(archive: Archive) -> bool:
    return any(name.startswith(RECIPE_DIRS) for name in archive.namelist())


def jar_has_recipes(jar_path: Path) -> bool:
    """Check the jar actually contains recipe data before offering it."""
    try:
        with zipfile.ZipFile(jar_path, "r") as jar:
            return archive_has_recipes(jar)
    except (OSError, zipfile.BadZipFile):
        return False


def find_minecraft_jars(
    appdata: Path, has_recipes: Callable[[Path], bool] = jar_has_recipes
) -> list[JarCandidate]:
    """Every Minecraft client jar with recipe data under ``appdata``
    (``%APPDATA%``), default launcher first."""
    found: list[JarCandidate] = []
    seen: set[Path] = set()

    def scan_versions_dir(versions_dir: Path, source: str) -> None:
        if not versions_dir.exists():
            return
        for folder in sorted(versions_dir.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            jar = folder / f"{folder.name}.jar"
            if not jar.exists():
                jars = sorted(folder.glob("*.jar"))
                if not jars:
                    continue
                jar = jars[0]
            if jar in seen or not has_recipes(jar):
                continue
            seen.add(jar)
            found.append(JarCandidate(jar, folder.name, source))

    scan_versions_dir(appdata / ".minecraft" / "versions", ".minecraft (default)")
    scan_versions_dir(appdata / "ModrinthApp" / "meta" / "versions", "Modrinth App")
    for base in (appdata / "Modrinth" / "profiles", appdata / "Modrinth" / "instances"):
        if base.exists():
            for profile in base.iterdir():
                if profile.is_dir():
                    source = f"Modrinth ({profile.name})"
                    scan_versions_dir(profile / ".minecraft" / "versions", source)
                    scan_versions_dir(profile / "versions", source)
    return found


def prefer_saved(
    candidates: list[JarCandidate],
    saved: Mapping[str, object],
    exists: Callable[[Path], bool] = Path.exists,
) -> list[JarCandidate]:
    """``candidates`` with the jar remembered from the last session first.

    ``saved`` is the remembered {'path', 'version', 'source'}. A remembered jar
    the scan did not find (it came from Browse) is added; one that no longer
    exists is ignored. The first entry's source is marked as last used.
    """
    ordered = list(candidates)
    saved_path = saved.get("path")
    if not isinstance(saved_path, str) or not saved_path or not exists(Path(saved_path)):
        return ordered
    for i, cand in enumerate(ordered):
        if str(cand.path) == saved_path:
            ordered.insert(0, ordered.pop(i))
            break
    else:
        path = Path(saved_path)
        version, source = saved.get("version"), saved.get("source")
        ordered.insert(
            0,
            JarCandidate(
                path,
                version if isinstance(version, str) else path.stem,
                source if isinstance(source, str) else "remembered",
            ),
        )
    ordered[0] = replace(ordered[0], source=ordered[0].source + LAST_USED)
    return ordered


def remembered(candidate: JarCandidate) -> dict[str, str]:
    """What the config file keeps about the chosen jar."""
    return {
        "path": str(candidate.path),
        "version": candidate.version,
        "source": candidate.source.replace(LAST_USED, ""),
    }
