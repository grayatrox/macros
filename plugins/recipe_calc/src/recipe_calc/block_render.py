"""Inventory-style icons for blocks, drawn from their models in the jar.

Blocks with no flat item texture (fences, slabs, stairs, walls...) get an
isometric render of their block model, the way the game draws inventory icons.
Needs Pillow; without it ``BlockRenderer.render`` returns None and recipe_calc.app
falls back to flat textures. Nothing here touches the GUI.
"""

from __future__ import annotations

import io
import json
from typing import Any, NamedTuple

from recipe_calc.recipe_book import Archive

try:
    from PIL import Image
except ImportError:  # optional: the app still runs, with flat icons
    HAVE_PILLOW = False
else:
    HAVE_PILLOW = True

MODELS = "assets/minecraft/models/"
TEXTURE_SIZE = 16
GRASS_TINT = (124, 189, 107)  # grass/foliage tintindex colour
WHITE = (255, 255, 255)
STAIRS_GUI_YAW = 135  # stairs ask the icon camera to look from the other side
# Parent chains are followed this deep at most: vanilla's are shorter, and the
# limit stops a model that names itself (or a loop) from recursing forever
MAX_TEXTURE_DEPTH = 4
MAX_MODEL_DEPTH = 6

TEXTURE_KEYS = ("side", "all", "top", "end", "bottom", "texture", "particle", "layer0")


def _read_json(archive: Archive, name: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def model_texture_ref(archive: Archive, model_ref: str, depth: int = 0) -> str | None:
    """Follow a model ('item/oak_slab') to a concrete texture ('block/oak_planks').

    Slabs, stairs, carpets and the like have no texture of their own - their
    models borrow the parent block's.
    """
    if depth > MAX_TEXTURE_DEPTH:
        return None
    model = _read_json(archive, f"{MODELS}{model_ref}.json")
    if model is None:
        return None
    textures = model.get("textures", {})
    for key in TEXTURE_KEYS:
        ref = textures.get(key, "")
        if isinstance(ref, str) and ref and not ref.startswith("#"):
            return ref.split(":")[-1]
    for ref in textures.values():
        if isinstance(ref, str) and ref and not ref.startswith("#"):
            return ref.split(":")[-1]
    parent = model.get("parent", "")
    if isinstance(parent, str) and parent:
        return model_texture_ref(archive, parent.split(":")[-1], depth + 1)
    return None


def _first_model(node: object) -> str | None:
    """Dig through an item definition's condition/select wrappers to a model name."""
    if isinstance(node, dict):
        model = node.get("model")
        if isinstance(model, str):
            return model
        values: list[object] = list(node.values())
    elif isinstance(node, list):
        values = node
    else:
        return None
    for value in values:
        if found := _first_model(value):
            return found
    return None


def item_model_ref(archive: Archive, item: str) -> str | None:
    """Model reference for an item. Older jars keep it at models/item/;
    1.21.4+ jars point there from a definition in assets/minecraft/items/."""
    try:
        archive.read(f"{MODELS}item/{item}.json")
    except KeyError:
        pass
    else:
        return f"item/{item}"
    definition = _read_json(archive, f"assets/minecraft/items/{item}.json")
    ref = _first_model(definition)
    return ref.split(":")[-1] if ref else None


Point = tuple[float, float]


class Face(NamedTuple):
    """One cuboid face: its model spec, default uv, and where it lands on screen."""

    spec: dict[str, Any]
    uv: list[float]
    corners: tuple[Point, Point, Point]  # texture origin, its u end, its v end
    shade: float


Model = tuple[dict[str, Any], list[dict[str, Any]] | None, dict[str, Any]]


def resolve_model(archive: Archive, model_ref: str, depth: int = 0) -> Model:
    """(textures, elements, display) of a model merged over its parent chain."""
    if depth > MAX_MODEL_DEPTH:
        return {}, None, {}
    model = _read_json(archive, f"{MODELS}{model_ref}.json")
    if model is None:
        return {}, None, {}
    textures: dict[str, Any] = {}
    elements: list[dict[str, Any]] | None = None
    display: dict[str, Any] = {}
    parent = model.get("parent", "")
    if isinstance(parent, str) and parent and not parent.startswith("builtin/"):
        textures, elements, display = resolve_model(archive, parent.split(":")[-1], depth + 1)
    textures.update(model.get("textures", {}))
    if model.get("elements"):
        elements = model["elements"]
    display.update(model.get("display", {}))
    return textures, elements, display


def _rotate_for_stairs(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn the model so the icon camera sees the side a 135 gui yaw asks for."""
    remap = {
        "up": "up",
        "down": "down",
        "west": "north",
        "north": "east",
        "east": "south",
        "south": "west",
    }
    rotated = []
    for el in elements:
        x1, y1, z1 = el.get("from", [0, 0, 0])
        x2, y2, z2 = el.get("to", [16, 16, 16])
        rotated.append(
            {
                "from": [16 - max(z1, z2), y1, min(x1, x2)],
                "to": [16 - min(z1, z2), y2, max(x1, x2)],
                "faces": {remap[k]: v for k, v in el.get("faces", {}).items() if k in remap},
            }
        )
    return rotated


class BlockRenderer:
    """Renders block models from one jar, caching textures and results."""

    S = 4  # screen pixels per texture pixel
    SIZE = 34 * S
    ORIGIN = (SIZE // 2, 25 * S)  # near vertical edge is at (16, y, 0)

    def __init__(self, archive: Archive) -> None:
        self.archive = archive
        self._textures: dict[str, Image.Image | None] = {}
        self._renders: dict[str, Image.Image | None] = {}

    def render(self, item: str) -> Image.Image | None:
        """Isometric render of ``item``'s block model, square-cropped; None if
        it has no cuboid model (flat items, flowers, torches) or no Pillow."""
        if not HAVE_PILLOW:
            return None
        if item not in self._renders:
            self._renders[item] = self._render(item)
        return self._renders[item]

    def render_png(self, item: str, px: int) -> bytes | None:
        """``render`` scaled to ``px`` square, as PNG bytes."""
        rendered = self.render(item)
        if rendered is None:
            return None
        buffer = io.BytesIO()
        rendered.resize((px, px), Image.Resampling.NEAREST).save(buffer, "PNG")
        return buffer.getvalue()

    def _texture(self, textures: dict[str, Any], ref: object) -> Image.Image | None:
        """Texture variable ('#texture' or 'minecraft:block/x') as a 16px image."""
        for _ in range(8):
            if isinstance(ref, str) and ref.startswith("#"):
                ref = textures.get(ref[1:])
            else:
                break
        if not isinstance(ref, str) or not ref or ref.startswith("#"):
            return None
        path = f"assets/minecraft/textures/{ref.split(':')[-1]}.png"
        if path not in self._textures:
            self._textures[path] = self._load_texture(path)
        return self._textures[path]

    def _load_texture(self, path: str) -> Image.Image | None:
        try:
            img = Image.open(io.BytesIO(self.archive.read(path))).convert("RGBA")
        except (KeyError, OSError):
            return None
        if img.height > img.width:  # animated strip: keep the first frame
            img = img.crop((0, 0, img.width, img.width))
        if img.width != TEXTURE_SIZE:
            img = img.resize((TEXTURE_SIZE, TEXTURE_SIZE))
        return img

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        ox, oy = self.ORIGIN
        return (ox + (x + z - 16) * self.S, oy + (x - z) * self.S / 2 - y * self.S)

    def _face(self, out: Image.Image, textures: dict[str, Any], face: Face) -> None:
        """Draw one textured face of a cuboid onto ``out``."""
        spec, uv, (p0, pu, pv), shade = face
        tex = self._texture(textures, spec.get("texture", "#all"))
        if tex is None:
            return
        u1, v1, u2, v2 = (round(v) for v in spec.get("uv", uv))
        box = (min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2))
        if box[2] <= box[0] or box[3] <= box[1]:
            return
        crop = tex.crop(box)
        tint = GRASS_TINT if spec.get("tintindex") is not None else WHITE
        if shade < 1.0 or tint != WHITE:
            channels = list(crop.split())
            for i in range(3):
                factor = shade * tint[i] / 255
                channels[i] = channels[i].point([int(v * factor) for v in range(256)])
            crop = Image.merge("RGBA", channels)
        ux, uy = pu[0] - p0[0], pu[1] - p0[1]
        vx, vy = pv[0] - p0[0], pv[1] - p0[1]
        det = ux * vy - uy * vx
        if det == 0:
            return
        w, h = crop.width, crop.height
        # Affine coefficients mapping output pixels back into the crop
        a = vy / det * w
        b = -vx / det * w
        c = -(a * p0[0] + b * p0[1])
        d = -uy / det * h
        e = ux / det * h
        f = -(d * p0[0] + e * p0[1])
        layer = crop.transform(
            (self.SIZE, self.SIZE),
            Image.Transform.AFFINE,
            (a, b, c, d, e, f),
            resample=Image.Resampling.NEAREST,
        )
        out.alpha_composite(layer)

    def _render(self, item: str) -> Image.Image | None:
        """Top / north / east faces of the model's cuboids, with the game's shading."""
        model_ref = item_model_ref(self.archive, item)
        if model_ref is None:
            return None
        textures, elements, display = resolve_model(self.archive, model_ref)
        if not elements:
            return None
        if any("rotation" in el for el in elements):
            return None  # angled planes (flowers, torches) belong flat

        # The game's icon camera shows the top, north (lower-left) and east
        # (lower-right) faces. Models that declare a 135 gui yaw (stairs)
        # want their other side shown - rotate the model instead.
        rotation = display.get("gui", {}).get("rotation", [30, 225, 0])
        if len(rotation) > 1 and rotation[1] % 360 == STAIRS_GUI_YAW:
            elements = _rotate_for_stairs(elements)

        out = Image.new("RGBA", (self.SIZE, self.SIZE))
        project = self._project

        # Painter's algorithm: nearest cuboids (high x, low z, high y) last
        def nearness(el: dict[str, Any]) -> float:
            x, y, z = el.get("from", [0, 0, 0])
            return float(x - z + y)

        for el in sorted(elements, key=nearness):
            x1, y1, z1 = el.get("from", [0, 0, 0])
            x2, y2, z2 = el.get("to", [16, 16, 16])
            faces = el.get("faces", {})
            if spec := faces.get("up"):
                corners = (project(x1, y2, z1), project(x2, y2, z1), project(x1, y2, z2))
                self._face(out, textures, Face(spec, [x1, z1, x2, z2], corners, 1.0))
            if spec := faces.get("north"):
                corners = (project(x2, y2, z1), project(x1, y2, z1), project(x2, y1, z1))
                uv = [16 - x2, 16 - y2, 16 - x1, 16 - y1]
                self._face(out, textures, Face(spec, uv, corners, 0.8))
            if spec := faces.get("east"):
                corners = (project(x2, y2, z2), project(x2, y2, z1), project(x2, y1, z2))
                uv = [16 - z2, 16 - y2, 16 - z1, 16 - y1]
                self._face(out, textures, Face(spec, uv, corners, 0.6))

        bbox = out.getbbox()
        if bbox is None:
            return None
        # Square-crop around the content so the icon stays centred
        left, top, right, bottom = bbox
        side = max(right - left, bottom - top)
        cx, cy = (left + right) // 2, (top + bottom) // 2
        x0, y0 = cx - side // 2, cy - side // 2
        return out.crop((x0, y0, x0 + side, y0 + side))
