"""Tests for Recipe Calc's block icon rendering (recipe_calc.block_render)."""

from __future__ import annotations

import io
import json

from PIL import Image

from recipe_calc.block_render import BlockRenderer, item_model_ref, model_texture_ref, resolve_model

MODELS = "assets/minecraft/models/"
TEXTURES = "assets/minecraft/textures/"
STONE_RGB = (100, 150, 200)


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
        return self.files[name]


def png(color: tuple[int, int, int], size: tuple[int, int] = (16, 16)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(buffer, "PNG")
    return buffer.getvalue()


FULL_FACE = {"texture": "#all"}
CUBE_ALL = {
    "textures": {"particle": "#all"},
    "elements": [
        {
            "from": [0, 0, 0],
            "to": [16, 16, 16],
            "faces": dict.fromkeys(("up", "down", "north", "south", "east", "west"), FULL_FACE),
        }
    ],
}


def stone_jar(**extra: object) -> FakeJar:
    return FakeJar(
        {
            MODELS + "block/cube_all.json": CUBE_ALL,
            MODELS + "block/stone.json": {
                "parent": "minecraft:block/cube_all",
                "textures": {"all": "minecraft:block/stone"},
            },
            MODELS + "item/stone.json": {"parent": "minecraft:block/stone"},
            TEXTURES + "block/stone.png": png(STONE_RGB),
            **extra,
        }
    )


# ── Model resolution ─────────────────────────────────────────────────────────


def test_model_texture_ref_follows_the_parent_chain() -> None:
    jar = stone_jar(**{MODELS + "item/stone_slab.json": {"parent": "block/stone"}})
    assert model_texture_ref(jar, "item/stone_slab") == "block/stone"


def test_model_texture_ref_falls_back_to_any_concrete_texture() -> None:
    jar = FakeJar({MODELS + "block/odd.json": {"textures": {"glow": "minecraft:block/odd"}}})
    assert model_texture_ref(jar, "block/odd") == "block/odd"


def test_model_texture_ref_stops_on_a_parent_loop() -> None:
    jar = FakeJar({MODELS + "block/loop.json": {"parent": "block/loop"}})
    assert model_texture_ref(jar, "block/loop") is None
    assert resolve_model(jar, "block/loop") == ({}, None, {})


def test_missing_or_broken_models_resolve_to_nothing() -> None:
    jar = FakeJar({MODELS + "block/broken.json": b"{", MODELS + "block/list.json": []})
    for ref in ("block/missing", "block/broken", "block/list"):
        assert model_texture_ref(jar, ref) is None
        assert resolve_model(jar, ref) == ({}, None, {})


def test_resolve_model_merges_over_the_parent() -> None:
    textures, elements, _display = resolve_model(stone_jar(), "item/stone")

    assert textures == {"particle": "#all", "all": "minecraft:block/stone"}
    assert elements == CUBE_ALL["elements"]


def test_item_model_ref_prefers_models_item() -> None:
    assert item_model_ref(stone_jar(), "stone") == "item/stone"


def test_item_model_ref_reads_1_21_4_item_definitions() -> None:
    definition = {
        "model": {
            "type": "minecraft:condition",
            "on_true": {"type": "minecraft:model", "model": "minecraft:block/stone"},
            "on_false": [{"model": "minecraft:block/other"}],
        }
    }
    jar = FakeJar({"assets/minecraft/items/polished.json": definition})
    assert item_model_ref(jar, "polished") == "block/stone"


def test_item_model_ref_without_any_model() -> None:
    jar = FakeJar({"assets/minecraft/items/air.json": {"model": {"type": "empty"}}})
    assert item_model_ref(jar, "air") is None
    assert item_model_ref(jar, "unknown") is None


# ── Rendering ────────────────────────────────────────────────────────────────


def _colors(image: Image.Image) -> set[tuple[int, int, int]]:
    counted = image.getcolors(maxcolors=image.width * image.height) or []
    opaque: set[tuple[int, int, int]] = set()
    for _count, color in counted:
        assert isinstance(color, tuple)  # RGBA images count colours as tuples
        if color[3] == 255:
            opaque.add((color[0], color[1], color[2]))
    return opaque


def test_a_cube_renders_three_shaded_faces() -> None:
    rendered = BlockRenderer(stone_jar()).render("stone")

    assert rendered is not None
    assert rendered.width == rendered.height
    r, g, b = STONE_RGB
    shaded = {(int(r * f), int(g * f), int(b * f)) for f in (1.0, 0.8, 0.6)}
    assert shaded <= _colors(rendered)


def test_render_png_is_the_requested_size() -> None:
    data = BlockRenderer(stone_jar()).render_png("stone", 32)

    assert data is not None
    assert Image.open(io.BytesIO(data)).size == (32, 32)


def test_renders_are_cached() -> None:
    renderer = BlockRenderer(stone_jar())
    assert renderer.render("stone") is renderer.render("stone")


def test_tinted_faces_take_the_grass_colour() -> None:
    jar = stone_jar(
        **{
            MODELS + "block/grass.json": {
                "textures": {"all": "block/white"},
                "elements": [
                    {
                        "from": [0, 0, 0],
                        "to": [16, 16, 16],
                        "faces": {"up": {"texture": "#all", "tintindex": 0}},
                    }
                ],
            },
            MODELS + "item/grass.json": {"parent": "block/grass"},
            TEXTURES + "block/white.png": png((255, 255, 255)),
        }
    )
    rendered = BlockRenderer(jar).render("grass")

    assert rendered is not None
    assert _colors(rendered) == {(124, 189, 107)}


def test_animated_and_oversized_textures_use_one_16px_frame() -> None:
    jar = stone_jar(**{TEXTURES + "block/stone.png": png(STONE_RGB, (32, 96))})
    rendered = BlockRenderer(jar).render("stone")

    assert rendered is not None
    assert (100, 150, 200) in _colors(rendered)


def test_stairs_render_from_the_other_side() -> None:
    jar = stone_jar(
        **{
            MODELS + "block/stairs.json": {
                "parent": "block/stone",
                "display": {"gui": {"rotation": [30, 135, 0]}},
                "elements": [
                    {
                        "from": [0, 0, 0],
                        "to": [16, 8, 16],
                        "faces": {"up": FULL_FACE, "north": FULL_FACE},
                    },
                    {"from": [8, 8, 0], "to": [16, 16, 16], "faces": {"east": FULL_FACE}},
                ],
            },
            MODELS + "item/stone_stairs.json": {"parent": "block/stairs"},
        }
    )
    rendered = BlockRenderer(jar).render("stone_stairs")

    assert rendered is not None
    assert rendered.width == rendered.height


def test_models_that_cannot_be_rendered() -> None:
    jar = stone_jar(
        **{
            # angled planes belong flat
            MODELS + "item/torch.json": {
                "textures": {"all": "block/stone"},
                "elements": [{"from": [0, 0, 8], "to": [16, 16, 8], "rotation": {"angle": 45}}],
            },
            # a flat item sprite has no elements
            MODELS + "item/stick.json": {"parent": "item/generated"},
            # faces whose texture is missing draw nothing
            MODELS + "item/ghost.json": {
                "textures": {"all": "block/missing"},
                "elements": CUBE_ALL["elements"],
            },
        }
    )
    renderer = BlockRenderer(jar)

    for item in ("torch", "stick", "ghost", "not_in_the_jar"):
        assert renderer.render(item) is None
        assert renderer.render_png(item, 32) is None
