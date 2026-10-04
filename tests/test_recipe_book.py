"""Tests for Recipe Calc's calculation logic (recipe_calc.recipe_book)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from recipe_calc.recipe_book import (
    BREWING_RECIPES,
    BreakdownNode,
    JarCandidate,
    ParsedRecipe,
    Recipe,
    RecipeBook,
    Slot,
    find_minecraft_jars,
    format_stacks,
    ingredient_ref,
    jar_has_recipes,
    load_recipe_book,
    parse_recipe,
    placement,
    potion_look,
    prefer_saved,
    pretty_name,
    recipe_priority,
    remembered,
    resolve_tag,
    station_texture,
    texture_names,
    wiki_url,
)

RECIPES = "data/minecraft/recipe/"
TAGS = "data/minecraft/tags/item/"


class FakeJar:
    """An in-memory jar: JSON documents are serialised, bytes kept as they are."""

    def __init__(self, files: dict[str, object]) -> None:
        self.files = {
            name: data if isinstance(data, bytes) else json.dumps(data).encode()
            for name, data in files.items()
        }

    def namelist(self) -> list[str]:
        return list(self.files)

    def read(self, name: str) -> bytes:
        return self.files[name]  # KeyError when missing, like zipfile


def shaped(result: str, pattern: list[str], key: dict[str, object], count: int = 1) -> object:
    return {
        "type": "minecraft:crafting_shaped",
        "pattern": pattern,
        "key": key,
        "result": {"id": f"minecraft:{result}", "count": count},
    }


def shapeless(result: str, ingredients: list[object], count: int = 1) -> object:
    return {
        "type": "minecraft:crafting_shapeless",
        "ingredients": ingredients,
        "result": {"id": f"minecraft:{result}", "count": count},
    }


def smelting(result: str, ingredient: object) -> object:
    return {"type": "minecraft:smelting", "ingredient": ingredient, "result": {"id": result}}


def recipe(
    ingredients: dict[str, int], output: int = 1, method: str = "crafting_shapeless"
) -> Recipe:
    return Recipe(ingredients, output, method, (None,) * 9)


def book(recipes: dict[str, Recipe]) -> RecipeBook:
    items = set(recipes)
    for r in recipes.values():
        items.update(r.ingredients)
    return RecipeBook(recipes, frozenset(items))


# ── Names ────────────────────────────────────────────────────────────────────


def test_pretty_name_title_cases_words() -> None:
    assert pretty_name("oak_log") == "Oak Log"


@pytest.mark.parametrize(
    ("amount", "label"),
    [
        (0, "—"),
        (63, "—"),
        (64, "1 stack"),
        (65, "1 stack + 1"),
        (128, "2 stacks"),
        (133, "2 stacks + 5"),
    ],
)
def test_format_stacks(amount: int, label: str) -> None:
    assert format_stacks(amount) == label


@pytest.mark.parametrize(
    ("item", "url"),
    [
        ("oak_log", "https://minecraft.wiki/w/Oak_Log"),
        ("potion_of_weakness", "https://minecraft.wiki/w/Potion_of_Weakness"),
        ("potion_of_the_turtle_master", "https://minecraft.wiki/w/Potion_of_the_Turtle_Master"),
        ("splash_potion_of_healing", "https://minecraft.wiki/w/Potion_of_Healing#Splash_Potion"),
        (
            "lingering_potion_of_poison",
            "https://minecraft.wiki/w/Potion_of_Poison#Lingering_Potion",
        ),
    ],
)
def test_wiki_url(item: str, url: str) -> None:
    assert wiki_url(item) == url


@pytest.mark.parametrize(
    ("item", "texture"),
    [
        ("oak_log", "oak_log"),
        ("potion_of_healing", "potion"),
        ("water_bottle", "potion"),
        ("splash_potion_of_healing", "splash_potion"),
        ("lingering_potion_of_healing", "lingering_potion"),
    ],
)
def test_texture_names_try_item_then_block_textures(item: str, texture: str) -> None:
    names = texture_names(item)
    assert names[0] == f"assets/minecraft/textures/item/{texture}.png"
    assert names[1] == f"assets/minecraft/textures/block/{texture}.png"
    assert len(names) == 5


@pytest.mark.parametrize(
    ("item", "look"),
    [
        ("potion_of_healing", ("potion", "#f82423")),
        ("splash_potion_of_healing", ("splash_potion", "#f82423")),
        ("lingering_potion_of_swiftness", ("lingering_potion", "#33ebff")),
        ("awkward_potion", ("potion", "#385dc6")),
        ("potion_of_something_new", ("potion", "#385dc6")),  # unknown effect: water colour
        ("oak_log", None),
    ],
)
def test_potion_look(item: str, look: tuple[str, str] | None) -> None:
    assert potion_look(item) == look


# ── Parsing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entry", "ref"),
    [
        ({"item": "minecraft:stick"}, ("item", "stick")),
        ({"tag": "minecraft:planks"}, ("tag", "planks")),
        ({"id": "minecraft:stick"}, ("item", "stick")),
        ("minecraft:stick", ("item", "stick")),
        ("#minecraft:planks", ("tag", "planks")),
        ([{"item": "a"}, {"item": "b"}], ("item", "a")),
        ([], None),
        ({}, None),
        ({"item": ""}, None),
        (None, None),
        (7, None),
    ],
)
def test_ingredient_ref(entry: object, ref: tuple[str, str] | None) -> None:
    assert ingredient_ref(entry) == ref


def test_shaped_recipe_keeps_its_grid() -> None:
    parsed = parse_recipe(shaped("stick", ["#", "#"], {"#": "#minecraft:planks"}, count=4))

    assert parsed == ParsedRecipe(
        "stick",
        4,
        "crafting_shaped",
        {("tag", "planks"): 2},
        (("tag", "planks"), None, None, ("tag", "planks"), None, None, None, None, None),
    )


def test_shaped_pattern_spaces_and_unknown_symbols_are_empty() -> None:
    parsed = parse_recipe(shaped("x", ["A B", " ?A"], {"A": "a"}))

    assert parsed is not None
    assert parsed.ingredients == {("item", "a"): 2}
    assert parsed.grid[:6] == (("item", "a"), None, None, None, None, ("item", "a"))


def test_shapeless_recipe_fills_the_grid_in_order() -> None:
    parsed = parse_recipe(shapeless("mix", ["a", "b", "a"]))

    assert parsed is not None
    assert parsed.ingredients == {("item", "a"): 2, ("item", "b"): 1}
    assert parsed.grid[:4] == (("item", "a"), ("item", "b"), ("item", "a"), None)


@pytest.mark.parametrize(
    "method", ["smelting", "blasting", "smoking", "campfire_cooking", "stonecutting"]
)
def test_single_input_recipe_sits_in_the_middle_slot(method: str) -> None:
    parsed = parse_recipe(
        {"type": f"minecraft:{method}", "ingredient": "iron_ore", "result": {"id": "iron"}}
    )

    assert parsed is not None
    assert parsed.method == method
    assert parsed.grid[4] == ("item", "iron_ore")


def test_string_result_reads_count_from_the_recipe() -> None:
    parsed = parse_recipe(
        {"type": "stonecutting", "ingredient": "stone", "result": "stone_slab", "count": 2}
    )

    assert parsed is not None
    assert (parsed.result, parsed.output) == ("stone_slab", 2)


def test_zero_count_is_treated_as_one() -> None:
    parsed = parse_recipe(shapeless("a", ["b"], count=0))

    assert parsed is not None
    assert parsed.output == 1


@pytest.mark.parametrize(
    "recipe_json",
    [
        "not a recipe",
        {"type": "minecraft:smithing_transform", "result": {"id": "x"}, "base": "y"},
        {"type": "crafting_shapeless", "ingredients": ["a"]},  # no result
        {"type": "crafting_shapeless", "ingredients": ["a"], "result": 5},
        {"type": "crafting_shapeless", "ingredients": ["a"], "result": {"id": ""}},
        {"type": "crafting_shapeless", "ingredients": ["a"], "result": {"id": 3}},
        {"type": "crafting_shapeless", "ingredients": [], "result": {"id": "x"}},
        {"type": "crafting_shapeless", "ingredients": ["a"], "result": {"id": "x", "count": "?"}},
    ],
)
def test_unusable_recipes_are_skipped(recipe_json: object) -> None:
    assert parse_recipe(recipe_json) is None


def test_resolve_tag_follows_nested_tags_and_id_entries() -> None:
    tags: dict[str, list[object]] = {
        "planks": ["#minecraft:oak_family"],
        "oak_family": [{"id": "minecraft:oak_planks", "required": False}],
    }
    assert resolve_tag(tags, "planks") == "oak_planks"


def test_resolve_tag_survives_a_cycle_and_bad_entries() -> None:
    tags: dict[str, list[object]] = {"a": ["#b", 3, "", {"id": ""}], "b": ["#a"]}
    assert resolve_tag(tags, "a") is None
    assert resolve_tag(tags, "missing") is None


def test_recipe_priority_prefers_canonical_then_crafting() -> None:
    def ranked(name: str, method: str) -> int:
        parsed = ParsedRecipe("gold_ingot", 1, method, {("item", "x"): 1}, (None,) * 9)
        return recipe_priority(name, parsed)

    assert ranked("gold_ingot", "smelting") == 0
    assert ranked("gold_ingot_bundle", "crafting_shaped") == 1
    assert ranked("gold_ingot_from_smelting_raw_gold", "smelting") == 2
    assert ranked("gold_ingot_from_nuggets", "crafting_shaped") == 3


# ── Loading a recipe book ───────────────────────────────────────────────────


def test_tags_resolve_to_a_representative_item() -> None:
    jar = FakeJar(
        {
            TAGS + "planks.json": {"values": ["minecraft:oak_planks", "minecraft:birch_planks"]},
            RECIPES + "stick.json": shaped("stick", ["#", "#"], {"#": "#minecraft:planks"}, 4),
        }
    )
    stick = load_recipe_book(jar).recipes["stick"]

    assert stick.ingredients == {"oak_planks": 2}
    assert stick.output == 4
    assert stick.grid[0] == stick.grid[3] == "oak_planks"


def test_pre_1_21_folder_names_are_read() -> None:
    jar = FakeJar(
        {
            "data/minecraft/tags/items/planks.json": {"values": ["oak_planks"]},
            "data/minecraft/recipes/stick.json": shaped("stick", ["#", "#"], {"#": "#planks"}),
        }
    )
    assert load_recipe_book(jar).recipes["stick"].ingredients == {"oak_planks": 2}


def test_the_canonical_recipe_wins() -> None:
    jar = FakeJar(
        {
            RECIPES + "gold_ingot_from_nuggets.json": shapeless("gold_ingot", ["gold_nugget"] * 9),
            RECIPES + "gold_ingot_from_smelting_raw_gold.json": smelting("gold_ingot", "raw_gold"),
            RECIPES + "gold_ingot.json": shapeless("gold_ingot", ["gold_block"], 9),
        }
    )
    assert load_recipe_book(jar).recipes["gold_ingot"].ingredients == {"gold_block": 1}


def test_shorter_recipe_names_break_ties() -> None:
    jar = FakeJar(
        {
            RECIPES + "gold_ingot_from_smelting_deepslate_gold_ore.json": smelting(
                "gold_ingot", "deepslate_gold_ore"
            ),
            RECIPES + "gold_ingot_from_smelting_raw_gold.json": smelting("gold_ingot", "raw_gold"),
        }
    )
    assert load_recipe_book(jar).recipes["gold_ingot"].ingredients == {"raw_gold": 1}


def test_furnace_beats_blast_furnace_whatever_the_jar_order() -> None:
    blasting = {
        "type": "minecraft:blasting",
        "ingredient": "iron_ore",
        "result": {"id": "iron_ingot"},
    }
    for order in ((blasting, "iron_ore"), ("iron_ore", blasting)):
        files: dict[str, object] = {}
        for entry in order:
            if entry == "iron_ore":
                files[RECIPES + "iron_ingot_from_smelting_iron_ore.json"] = smelting(
                    "iron_ingot", "iron_ore"
                )
            else:
                files[RECIPES + "iron_ingot_from_blasting_iron_ore.json"] = blasting
        best = load_recipe_book(FakeJar(files)).recipes["iron_ingot"]
        assert best.method == "smelting"


def test_pack_unpack_cycle_switches_to_an_alternative() -> None:
    jar = FakeJar(
        {
            RECIPES + "redstone.json": shapeless("redstone", ["redstone_block"], 9),
            RECIPES + "redstone_from_smelting_redstone_ore.json": smelting(
                "redstone", "redstone_ore"
            ),
            RECIPES + "redstone_block.json": shaped(
                "redstone_block", ["###", "###", "###"], {"#": "redstone"}
            ),
        }
    )
    recipes = load_recipe_book(jar).recipes

    assert recipes["redstone"].ingredients == {"redstone_ore": 1}
    assert recipes["redstone_block"].ingredients == {"redstone": 9}


def test_an_item_only_unpacked_from_its_block_is_gathered() -> None:
    jar = FakeJar(
        {
            RECIPES + "slime_ball.json": shapeless("slime_ball", ["slime_block"], 9),
            RECIPES + "slime_block.json": shaped(
                "slime_block", ["###", "###", "###"], {"#": "slime_ball"}
            ),
        }
    )
    loaded = load_recipe_book(jar)

    assert "slime_ball" not in loaded.recipes
    assert "slime_ball" in loaded.base_items
    assert loaded.kind("slime_ball") == "base"


def test_brewing_recipes_are_added_with_their_stand_layout() -> None:
    loaded = load_recipe_book(FakeJar({}))

    assert set(BREWING_RECIPES) <= set(loaded.recipes)
    awkward = loaded.recipes["awkward_potion"]
    assert awkward.brewing
    assert awkward.method == "brewing"
    assert awkward.grid[1] == "nether_wart"
    assert awkward.grid[6:] == ("water_bottle",) * 3
    water = loaded.recipes["water_bottle"]
    assert (water.method, water.grid[4]) == ("filling", "glass_bottle")
    assert loaded.recipes["lingering_potion_of_healing"].ingredients == {
        "splash_potion_of_healing": 3,
        "dragon_breath": 1,
    }
    assert loaded.kind("awkward_potion") == "brew"


def test_a_jar_recipe_overrides_the_built_in_brewing_one() -> None:
    jar = FakeJar({RECIPES + "water_bottle.json": shapeless("water_bottle", ["glass_bottle"])})
    water = load_recipe_book(jar).recipes["water_bottle"]

    assert not water.brewing
    assert water.method == "crafting_shapeless"


def test_bad_files_are_skipped() -> None:
    jar = FakeJar(
        {
            RECIPES + "broken.json": b"{not json",
            RECIPES + "latin1.json": b"\xff\xfe",
            RECIPES + "notes.txt": b"ignored",
            TAGS + "broken.json": b"[",
            TAGS + "no_values.json": {"replace": False},
            RECIPES + "stick.json": shapeless("stick", ["oak_planks"]),
        }
    )
    assert load_recipe_book(jar).recipes["stick"].ingredients == {"oak_planks": 1}


@pytest.mark.parametrize(
    "bad_recipe",
    [
        {"type": "crafting_shaped", "pattern": [1], "key": {"#": "b"}, "result": {"id": "a"}},
        {"type": "crafting_shaped", "pattern": None, "key": {"#": "b"}, "result": {"id": "a"}},
        {"type": "crafting_shaped", "pattern": ["#"], "key": None, "result": {"id": "a"}},
        {"type": "crafting_shaped", "pattern": "###", "key": {"#": "b"}, "result": {"id": "a"}},
        {"type": "crafting_shapeless", "ingredients": 5, "result": {"id": "a"}},
        {"type": "crafting_shapeless", "ingredients": None, "result": {"id": "a"}},
    ],
)
def test_malformed_recipe_shapes_are_skipped_not_fatal(bad_recipe: object) -> None:
    jar = FakeJar(
        {
            RECIPES + "bad.json": bad_recipe,
            RECIPES + "stick.json": shapeless("stick", ["oak_planks"]),
        }
    )
    book = load_recipe_book(jar)

    assert book.recipes["stick"].ingredients == {"oak_planks": 1}
    assert "a" not in book.recipes


def test_unresolvable_tag_ingredients_drop_the_recipe() -> None:
    jar = FakeJar({RECIPES + "x.json": shapeless("x", ["#minecraft:nothing"])})
    assert "x" not in load_recipe_book(jar).recipes


def test_all_items_cover_results_and_ingredients() -> None:
    jar = FakeJar({RECIPES + "stick.json": shapeless("stick", ["oak_planks"])})
    loaded = load_recipe_book(jar)

    assert {"stick", "oak_planks", "nether_wart"} <= loaded.all_items
    assert "oak_planks" in loaded.base_items
    assert "stick" not in loaded.base_items


# ── Breakdown and search ────────────────────────────────────────────────────


def test_breakdown_rounds_up_to_whole_crafts() -> None:
    recipes = book(
        {
            "stick": recipe({"oak_planks": 2}, output=4),
            "oak_planks": recipe({"oak_log": 1}, output=4),
        }
    )
    tree = recipes.breakdown("stick", 5)

    assert tree == BreakdownNode(
        "stick",
        5,
        "root",
        [BreakdownNode("oak_planks", 4, "craft", [BreakdownNode("oak_log", 1, "base")])],
    )


def test_breakdown_sorts_ingredients_and_marks_brewing() -> None:
    recipes = book(
        {
            "potion": Recipe({"water": 3, "catalyst": 1}, 3, "brewing", (None,) * 9, brewing=True),
            "water": Recipe({"bottle": 1}, 1, "filling", (None,) * 9, brewing=True),
        }
    )
    tree = recipes.breakdown("potion", 3)

    assert [(n.item, n.amount, n.kind) for n in tree.children] == [
        ("catalyst", 1, "base"),
        ("water", 3, "brew"),
    ]


def test_breakdown_stops_at_a_recipe_cycle() -> None:
    recipes = book({"a": recipe({"b": 1}), "b": recipe({"a": 2})})
    tree = recipes.breakdown("a", 1)

    # "a" is already being made further up, so its second appearance is a leaf
    assert [(n.item, n.amount) for n in tree.walk()] == [("a", 1), ("b", 1), ("a", 2)]


def test_breakdown_of_a_base_item_is_just_the_item() -> None:
    assert book({}).breakdown("dirt", 7) == BreakdownNode("dirt", 7, "root")


def test_search_matches_underscores_or_spaces_case_insensitively() -> None:
    recipes = book({"oak_planks": recipe({"oak_log": 1}), "stick": recipe({"oak_planks": 2})})

    assert recipes.search("Oak Pl") == ["oak_planks"]
    assert recipes.search("oak") == ["oak_log", "oak_planks"]
    assert recipes.search("  ") == ["oak_log", "oak_planks", "stick"]
    assert recipes.search("zzz") == []


# ── Placement ────────────────────────────────────────────────────────────────


def test_base_item_gets_a_plain_slot() -> None:
    layout = placement("dirt", None, None)

    assert layout.heading == "Placement  ·  base item, not craftable"
    assert layout.caption == "Dirt is gathered in the world, not crafted"
    assert layout.panel
    assert layout.title == "Base Item"
    assert layout.slots == (Slot(80, 34, "dirt", plain=True),)
    assert layout.result is None


def test_crafting_table_places_the_grid_and_result() -> None:
    grid = ("a", None, None, None, "b", None, None, None, "c")
    layout = placement("x", Recipe({"a": 1, "b": 1, "c": 1}, 2, "crafting_shaped", grid), 352)

    assert layout.texture == "crafting_table"
    assert layout.title == "Crafting Table"
    assert layout.caption == "Makes X \N{MULTIPLICATION SIGN} 2 per craft  ·  hover slots for names"
    assert layout.slots == (
        Slot(30, 17, "a"),
        Slot(48, 35, "b"),
        Slot(66, 53, "c"),
        Slot(124, 35, "x", "  (result)", 2),
    )
    assert layout.result is None


def test_shapeless_title_drops_the_arrangement_note() -> None:
    layout = placement("x", recipe({"a": 1}), 352)

    assert layout.heading == "Placement  ·  Crafting Table (any arrangement)"
    assert layout.title == "Crafting Table"


def test_furnace_shows_fuel_and_result() -> None:
    layout = placement("iron_ingot", recipe({"raw_iron": 1}, method="smelting"), 352)

    assert layout.texture == "furnace"
    assert [s.item for s in layout.slots] == ["raw_iron", "coal", "iron_ingot"]
    assert layout.caption == "Makes Iron Ingot per craft  ·  hover slots for names"


def test_stonecutter_places_input_and_result() -> None:
    layout = placement("slab", recipe({"stone": 1}, 2, "stonecutting"), 352)

    assert layout.slots == (Slot(20, 33, "stone"), Slot(143, 33, "slab", "  (result)", 2))


@pytest.mark.parametrize(("width", "has_result"), [(352, False), (440, True)])
def test_brewing_stand_result_needs_the_spliced_slot(width: int, has_result: bool) -> None:
    brew = Recipe({"awkward_potion": 3, "sugar": 1}, 3, "brewing", (None,) * 9, brewing=True)
    layout = placement("potion_of_swiftness", brew, width)

    items = [s.item for s in layout.slots]
    assert items[:5] == [
        "blaze_powder",
        "sugar",
        "awkward_potion",
        "awkward_potion",
        "awkward_potion",
    ]
    assert ("potion_of_swiftness" in items) is has_result
    assert (
        layout.caption
        == "The 3 bottles brew into Potion Of Swiftness \N{MULTIPLICATION SIGN} 3 in place"
    )


@pytest.mark.parametrize(
    ("method", "width"),
    [("campfire_cooking", None), ("filling", None), ("smelting", None)],
)
def test_without_a_texture_slots_are_plain_and_the_result_sits_beside(
    method: str, width: int | None
) -> None:
    layout = placement("x", recipe({"a": 2, "b": 1}, 4, method), width)

    assert layout.panel
    assert layout.slots == (
        Slot(40, 34, "a", count=2, plain=True),
        Slot(70, 34, "b", count=1, plain=True),
    )
    assert layout.result is not None
    assert (layout.result.item, layout.result.count) == ("x", 4)


def test_station_texture() -> None:
    assert station_texture(None) is None
    assert station_texture(recipe({"a": 1}, method="campfire_cooking")) is None
    assert station_texture(recipe({"a": 1}, method="blasting")) == "blast_furnace"


# ── Finding jars ─────────────────────────────────────────────────────────────


def _jar(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_find_jars_in_every_launcher_default_first(tmp_path: Path) -> None:
    default = tmp_path / ".minecraft" / "versions"
    newer = _jar(default / "1.21.4" / "1.21.4.jar")
    older = _jar(default / "1.20.1" / "1.20.1.jar")
    renamed = _jar(default / "custom" / "client.jar")  # no <folder>.jar: first jar is used
    (default / "empty").mkdir()
    _jar(default / "loose.jar")  # a file, not a version folder
    modrinth_app = _jar(tmp_path / "ModrinthApp" / "meta" / "versions" / "1.21" / "1.21.jar")
    profile = _jar(tmp_path / "Modrinth" / "profiles" / "Pack" / "versions" / "1.19" / "1.19.jar")

    found = find_minecraft_jars(tmp_path, has_recipes=lambda _jar: True)

    assert found == [
        JarCandidate(renamed, "custom", ".minecraft (default)"),
        JarCandidate(newer, "1.21.4", ".minecraft (default)"),
        JarCandidate(older, "1.20.1", ".minecraft (default)"),
        JarCandidate(modrinth_app, "1.21", "Modrinth App"),
        JarCandidate(profile, "1.19", "Modrinth (Pack)"),
    ]


def test_find_jars_skips_jars_without_recipes(tmp_path: Path) -> None:
    _jar(tmp_path / ".minecraft" / "versions" / "optifine" / "optifine.jar")
    assert find_minecraft_jars(tmp_path, has_recipes=lambda _jar: False) == []


def test_find_jars_with_no_launchers_installed(tmp_path: Path) -> None:
    assert find_minecraft_jars(tmp_path) == []


def test_jar_has_recipes_reads_the_zip(tmp_path: Path) -> None:
    with_recipes, without = tmp_path / "a.jar", tmp_path / "b.jar"
    with zipfile.ZipFile(with_recipes, "w") as jar:
        jar.writestr(RECIPES + "stick.json", "{}")
    with zipfile.ZipFile(without, "w") as jar:
        jar.writestr("assets/minecraft/lang/en_us.json", "{}")
    not_a_zip = tmp_path / "c.jar"
    not_a_zip.write_text("nope")

    assert jar_has_recipes(with_recipes)
    assert not jar_has_recipes(without)
    assert not jar_has_recipes(not_a_zip)
    assert not jar_has_recipes(tmp_path / "missing.jar")


A = JarCandidate(Path("a.jar"), "1.21", ".minecraft (default)")
B = JarCandidate(Path("b.jar"), "1.20", "Modrinth App")


def test_prefer_saved_moves_the_remembered_jar_first() -> None:
    ordered = prefer_saved([A, B], remembered(B), exists=lambda _p: True)

    assert ordered == [JarCandidate(B.path, "1.20", "Modrinth App (last used)"), A]


def test_prefer_saved_adds_a_remembered_browsed_jar() -> None:
    saved = {"path": "c.jar", "version": "snapshot", "source": "manual selection"}
    ordered = prefer_saved([A], saved, exists=lambda _p: True)

    assert ordered == [
        JarCandidate(Path("c.jar"), "snapshot", "manual selection (last used)"),
        A,
    ]


def test_prefer_saved_fills_in_missing_details() -> None:
    ordered = prefer_saved([], {"path": "dir/c.jar"}, exists=lambda _p: True)

    assert ordered == [JarCandidate(Path("dir/c.jar"), "c", "remembered (last used)")]


@pytest.mark.parametrize("saved", [{}, {"path": ""}, {"path": 3}, {"path": "gone.jar"}])
def test_prefer_saved_ignores_unusable_memories(saved: dict[str, object]) -> None:
    def exists(path: Path) -> bool:
        return path != Path("gone.jar")

    assert prefer_saved([A, B], saved, exists=exists) == [A, B]


def test_remembered_drops_the_last_used_marker() -> None:
    marked = JarCandidate(Path("a.jar"), "1.21", ".minecraft (default) (last used)")

    assert remembered(marked) == {
        "path": "a.jar",
        "version": "1.21",
        "source": ".minecraft (default)",
    }
