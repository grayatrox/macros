"""Tests for Recipe Calc's PySide6 window (recipe_calc.app).

The calculation itself is covered by test_recipe_book.py; these drive the real
window, offscreen, against a small jar built in a temp folder.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PIL import Image
from PySide6 import QtCore, QtWidgets

from recipe_calc import app as calc_app
from recipe_calc import block_render
from recipe_calc.app import JarAssets, RecipeCalculator, format_geometry, parse_geometry
from recipe_calc.recipe_book import JarCandidate, Placement, Slot

RECIPES = "data/minecraft/recipe/"
TEXTURES = "assets/minecraft/textures/"


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    # No real windows during a test run. Set before the QApplication exists,
    # which is never destroyed: Qt allows only one per process.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert isinstance(app, QtWidgets.QApplication)
    return app


def png(color: tuple[int, int, int, int], size: tuple[int, int] = (16, 16)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def make_jar(path: Path) -> Path:
    files: dict[str, object] = {
        "data/minecraft/tags/item/planks.json": {"values": ["minecraft:oak_planks"]},
        RECIPES + "stick.json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["#", "#"],
            "key": {"#": "#minecraft:planks"},
            "result": {"id": "minecraft:stick", "count": 4},
        },
        RECIPES + "oak_planks.json": {
            "type": "minecraft:crafting_shapeless",
            "ingredients": ["minecraft:oak_log"],
            "result": {"id": "minecraft:oak_planks", "count": 4},
        },
        TEXTURES + "item/stick.png": png((150, 100, 50, 255)),
        TEXTURES + "block/oak_planks.png": png((180, 140, 90, 255)),
        TEXTURES + "item/potion_overlay.png": png((255, 255, 255, 255)),
        TEXTURES + "item/potion.png": png((0, 0, 0, 0)),
        TEXTURES + "gui/container/crafting_table.png": png((198, 198, 198, 255), (256, 256)),
        TEXTURES + "gui/container/brewing_stand.png": png((198, 198, 198, 255), (256, 256)),
    }
    with zipfile.ZipFile(path, "w") as jar:
        for name, data in files.items():
            jar.writestr(name, data if isinstance(data, bytes) else json.dumps(data))
    return path


def wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, "the window never got there"
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)


@pytest.fixture
def errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    shown: list[str] = []
    monkeypatch.setattr(calc_app, "show_error", lambda _parent, _title, msg: shown.append(msg))
    return shown


@pytest.fixture
def open_window(
    qapp: QtWidgets.QApplication, tmp_path: Path
) -> Iterator[Callable[[list[JarCandidate]], RecipeCalculator]]:
    windows: list[RecipeCalculator] = []

    def open_with(found: list[JarCandidate]) -> RecipeCalculator:
        window = RecipeCalculator(config_path=tmp_path / "config.json", find_jars=lambda: found)
        windows.append(window)
        return window

    yield open_with
    for window in windows:
        window.close()
        window.deleteLater()


def loaded(window: RecipeCalculator) -> RecipeCalculator:
    wait_for(lambda: window.status_label.text().startswith("Loaded"))
    return window


def select(window: RecipeCalculator, item: str) -> None:
    (row,) = [
        window.items_list.item(i)
        for i in range(window.items_list.count())
        if window.items_list.item(i).data(calc_app.ITEM_ROLE) == item
    ]
    row.setSelected(True)


def breakdown_rows(window: RecipeCalculator) -> list[tuple[str, str, str]]:
    """(item, amount, stacks) of every breakdown row, depth first."""
    rows: list[tuple[str, str, str]] = []

    def walk(row: QtWidgets.QTreeWidgetItem | None) -> None:
        assert row is not None
        rows.append((row.text(0), row.text(1), row.text(2)))
        for i in range(row.childCount()):
            walk(row.child(i))

    for i in range(window.breakdown_tree.topLevelItemCount()):
        walk(window.breakdown_tree.topLevelItem(i))
    return rows


# ── Loading and showing recipes ─────────────────────────────────────────────


def test_the_only_jar_found_is_loaded_and_remembered(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator], tmp_path: Path, errors: list[str]
) -> None:
    jar = make_jar(tmp_path / "1.21.jar")
    window = loaded(open_window([JarCandidate(jar, "1.21", ".minecraft (default)")]))

    assert window.windowTitle() == "Minecraft Recipe Calculator - 1.21"
    assert window.jar_path_entry.text() == str(jar)
    assert window.version_label.text() == "1.21  ·  .minecraft (default)"
    assert window.items_box.title() == f"Items  ({len(window.book.all_items)})"
    assert window.browse_button.isEnabled()
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["jar"] == {"path": str(jar), "version": "1.21", "source": ".minecraft (default)"}
    assert errors == []


def test_selecting_an_item_shows_its_breakdown_and_placement(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator], tmp_path: Path
) -> None:
    window = loaded(open_window([JarCandidate(make_jar(tmp_path / "a.jar"), "1.21", "test")]))

    select(window, "stick")

    assert breakdown_rows(window) == [
        (" Stick", "1", "—"),
        (" Oak Planks", "2", "—"),
        (" Oak Log", "1", "—"),
    ]
    assert window.grid_label.text() == "Placement  ·  Crafting Table"
    assert window.grid_caption.text().startswith("Makes Stick")
    assert "https://minecraft.wiki/w/Stick" in window.wiki_link.text()
    assert not window.placement_view.grab().isNull()

    window.qty_spin.setValue(130)
    assert breakdown_rows(window)[:2] == [
        (" Stick", "130", "2 stacks + 2"),
        (" Oak Planks", "66", "1 stack + 2"),
    ]


def test_clicking_a_breakdown_row_shows_that_items_placement(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator], tmp_path: Path
) -> None:
    window = loaded(open_window([JarCandidate(make_jar(tmp_path / "a.jar"), "1.21", "test")]))
    select(window, "stick")

    stick_row = window.breakdown_tree.topLevelItem(0)
    assert stick_row is not None
    planks_row = stick_row.child(0)
    assert planks_row is not None
    log_row = planks_row.child(0)
    assert log_row is not None
    log_row.setSelected(True)

    assert window.grid_label.text() == "Placement  ·  base item, not craftable"
    assert window.grid_caption.text() == "Oak Log is gathered in the world, not crafted"


def test_search_filters_the_item_list(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator], tmp_path: Path
) -> None:
    window = loaded(open_window([JarCandidate(make_jar(tmp_path / "a.jar"), "1.21", "test")]))

    window.search_entry.setText("oak pl")

    assert window.items_list.count() == 1
    assert window.items_list.item(0).text() == " Oak Planks"
    assert window.items_box.title() == "Items  (1)"


# ── Choosing a jar ───────────────────────────────────────────────────────────


def test_no_jar_found_asks_for_browse(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator],
) -> None:
    window = open_window([])

    wait_for(lambda: window.status_label.text().startswith("No jar"))
    assert window.jar_path_entry.text() == "(No jar found - use Browse button)"
    assert window.version_label.text() == "no version detected"


def test_several_jars_let_the_user_choose(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = JarCandidate(make_jar(tmp_path / "a.jar"), "1.21", "test")
    second = JarCandidate(make_jar(tmp_path / "b.jar"), "1.20", "test")
    offered: list[list[JarCandidate]] = []

    def choose(_parent: object, candidates: list[JarCandidate]) -> JarCandidate:
        offered.append(candidates)
        return candidates[1]

    monkeypatch.setattr(calc_app, "choose_jar", choose)
    window = loaded(open_window([first, second]))

    assert offered == [[first, second]]
    assert window.jar == second
    assert window.windowTitle().endswith("1.20")


def test_browse_loads_the_picked_jar(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jar = make_jar(tmp_path / "snapshot.jar")
    window = open_window([])
    wait_for(lambda: window.status_label.text().startswith("No jar"))
    monkeypatch.setattr(calc_app, "ask_jar_file", lambda _parent: str(jar))

    window.browse_button.click()

    loaded(window)
    assert window.version_label.text() == "snapshot  ·  manual selection"


def test_a_jar_that_is_not_a_zip_is_reported(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator], tmp_path: Path, errors: list[str]
) -> None:
    broken = tmp_path / "broken.jar"
    broken.write_text("not a zip")
    window = open_window([JarCandidate(broken, "1.21", "test")])

    wait_for(lambda: bool(errors))
    assert errors[0].startswith("Failed to load recipes: ")
    assert window.status_label.text() == "Failed to load recipes. Please select another jar file."
    assert window.browse_button.isEnabled()


def test_a_jar_deleted_before_loading_is_reported(
    open_window: Callable[[list[JarCandidate]], RecipeCalculator], tmp_path: Path, errors: list[str]
) -> None:
    window = open_window([JarCandidate(tmp_path / "gone.jar", "1.21", "test")])

    wait_for(lambda: bool(errors))
    assert errors == ["No valid jar file selected. Please use Browse to select a jar file."]
    assert not window.loading


def test_a_permission_error_while_scanning_for_jars_is_reported(
    qapp: QtWidgets.QApplication, tmp_path: Path, errors: list[str]
) -> None:
    def locked() -> list[JarCandidate]:
        raise PermissionError("[WinError 5] Access is denied: 'versions'")

    window = RecipeCalculator(config_path=tmp_path / "config.json", find_jars=locked)
    try:
        wait_for(lambda: bool(errors))
        assert errors[0].startswith("Could not scan for Minecraft jars: ")
        assert "Access is denied" in errors[0]
        assert (
            window.status_label.text()
            == "No jar detected. Please use Browse to select it manually."
        )
        assert window.jar_path_entry.text() == "(No jar found - use Browse button)"
        assert window.browse_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


# ── Window geometry ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "geometry"),
    [
        ("1000x740+10+20", (1000, 740, 10, 20)),
        ("800x600-8+0", (800, 600, -8, 0)),
        ("800x600", None),
        ("800x600+-8+0", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_parse_geometry(text: object, geometry: tuple[int, int, int, int] | None) -> None:
    assert parse_geometry(text) == geometry


def test_format_geometry_round_trips() -> None:
    assert parse_geometry(format_geometry(800, 600, -8, 30)) == (800, 600, -8, 30)


def test_saved_geometry_is_restored_and_saved_on_close(
    qapp: QtWidgets.QApplication, tmp_path: Path
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"geometry": "820x610+40+50"}), encoding="utf-8")
    window = RecipeCalculator(config_path=config, find_jars=list)
    assert (window.width(), window.height()) == (820, 610)

    window.resize(900, 700)
    window.close()

    saved = json.loads(config.read_text(encoding="utf-8"))
    width, height, _x, _y = parse_geometry(saved["geometry"]) or (0, 0, 0, 0)
    assert (width, height) == (900, 700)
    window.deleteLater()


def test_an_unreadable_config_starts_fresh(qapp: QtWidgets.QApplication, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text("[1, 2]", encoding="utf-8")
    window = RecipeCalculator(config_path=config, find_jars=list)

    assert (window.width(), window.height()) == calc_app.DEFAULT_SIZE
    window.deleteLater()


# ── Placement panel and textures ─────────────────────────────────────────────


def test_hovering_a_slot_names_its_item(qapp: QtWidgets.QApplication) -> None:
    view = calc_app.PlacementView(lambda _item, _big: None)
    layout = Placement(
        heading="h",
        caption="default",
        title="Base Item",
        slots=(Slot(80, 34, "oak_log", "  (note)"),),
        result=Slot(0, 0, "stick", "  (result)", 4),
    )
    view.show_placement(layout, None)

    assert view.caption_at(QtCore.QPoint(170, 75)) == "Oak Log  (note)"
    assert view.caption_at(QtCore.QPoint(420, 70)) == "Stick  (result)"
    assert view.caption_at(QtCore.QPoint(5, 5)) == "default"
    assert not view.grab().isNull()


def test_icons_come_from_the_jar_at_list_and_result_size(
    qapp: QtWidgets.QApplication, tmp_path: Path
) -> None:
    assets = JarAssets(make_jar(tmp_path / "a.jar"))
    try:
        small, big = assets.icon("stick"), assets.icon("stick", big=True)
        assert small is not None
        assert big is not None
        assert (small.width(), big.width()) == (32, 48)
        planks = assets.icon("oak_planks")  # no item texture: the block one is used
        assert planks is not None
        assert assets.icon("nothing_like_this") is None
    finally:
        assets.close()


def test_potions_are_tinted_bottles(qapp: QtWidgets.QApplication, tmp_path: Path) -> None:
    assets = JarAssets(make_jar(tmp_path / "a.jar"))
    try:
        icon = assets.icon("potion_of_healing")
        assert icon is not None
        assert icon.toImage().pixelColor(0, 0).name() == "#f82423"
    finally:
        assets.close()


def test_gui_textures_and_the_spliced_brewing_stand(
    qapp: QtWidgets.QApplication, tmp_path: Path
) -> None:
    assets = JarAssets(make_jar(tmp_path / "a.jar"))
    try:
        crafting = assets.gui_texture("crafting_table")
        brewing = assets.gui_texture("brewing_stand")
        assert crafting is not None
        assert brewing is not None
        assert (crafting.width(), crafting.height()) == (352, 166)
        assert brewing.width() == 440
        assert assets.gui_texture("furnace") is None
    finally:
        assets.close()


def test_without_pillow_a_slab_gets_its_flat_half_texture(
    qapp: QtWidgets.QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(block_render, "HAVE_PILLOW", False)
    jar = tmp_path / "slab.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr(
            "assets/minecraft/models/item/oak_slab.json",
            json.dumps({"parent": "block/oak_slab"}),
        )
        archive.writestr(
            "assets/minecraft/models/block/oak_slab.json",
            json.dumps({"textures": {"side": "minecraft:block/oak_planks"}, "elements": [{}]}),
        )
        archive.writestr(TEXTURES + "block/oak_planks.png", png((180, 140, 90, 255)))
    assets = JarAssets(jar)
    try:
        icon = assets.icon("oak_slab")
        assert icon is not None
        image = icon.toImage()
        assert image.pixelColor(0, 0).alpha() == 0  # top half left empty
        assert image.pixelColor(0, 31).alpha() == 255
    finally:
        assets.close()


def test_a_missing_jar_has_no_textures(qapp: QtWidgets.QApplication, tmp_path: Path) -> None:
    assets = JarAssets(tmp_path / "gone.jar")
    assert assets.icon("stick") is None
    assert assets.gui_texture("crafting_table") is None
    assert JarAssets(None).load_image("anything.png") is None
