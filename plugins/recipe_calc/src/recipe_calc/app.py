"""Minecraft Recipe Calculator: a dark PySide6 (Qt) window.

Pick a Minecraft client jar (found automatically under %APPDATA%, or with
Browse) and an item, and see every ingredient a quantity of it needs and how
its recipe sits in the workstation's in-game GUI. The calculation lives in
recipe_book and block icons are rendered by block_render; this module is
the window and the jar's textures as Qt images.

PySide6 and Pillow come from the launcher's dependency install, or from the
first-start install in recipe_calc.__main__ when the app is run directly (see
this plugin's pyproject.toml). Without Pillow, blocks get flat icons.
"""

from __future__ import annotations

import contextlib
import html
import json
import logging
import os
import re
import sys
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

from PySide6 import QtCore, QtGui, QtWidgets

from recipe_calc.block_render import BlockRenderer, item_model_ref, model_texture_ref
from recipe_calc.recipe_book import (
    BreakdownNode,
    JarCandidate,
    Placement,
    RecipeBook,
    find_minecraft_jars,
    format_stacks,
    load_recipe_book,
    placement,
    potion_look,
    prefer_saved,
    pretty_name,
    remembered,
    station_texture,
    texture_names,
    wiki_url,
)

log = logging.getLogger("recipe_calc")

TITLE = "Minecraft Recipe Calculator"

# Remembered window geometry and jar selection
CONFIG_PATH = Path(os.getenv("APPDATA", str(Path.home()))) / "MinecraftRecipeCalc" / "config.json"
DEFAULT_SIZE = (1000, 740)
# "WxH+X+Y", the format the Tk version wrote, so existing config files still apply
GEOMETRY = re.compile(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)")

# Dark theme palette
BG = "#1b1d21"  # window background
PANEL = "#25272c"  # status bar / panels
FIELD = "#2e3138"  # entries, lists, trees
FIELD_ALT = "#33363b"  # alternate list rows
BUTTON = "#33363d"
BUTTON_HOVER = "#3e424b"
BUTTON_PRESSED = "#2a2c31"
DISABLED = "#26282c"
BORDER = "#3c4048"
FG = "#e8e8e8"
FG_DIM = "#9aa0a8"
ACCENT = "#54c556"  # creeper green
ACCENT_DARK = "#3d9e40"
ACCENT_HOVER = "#66d168"
ACCENT_TEXT = "#0f2410"
GOLD = "#f0b13e"  # base materials
SELECT = "#31512f"  # list/tree selection
LINK = "#6fb7ff"
BREW_COLOR = "#c58fff"  # brewing entries in the item list / breakdown
GUI_TITLE = "#404040"  # station name drawn on its GUI texture, as in-game
COUNT_SHADOW = "#3b3b3b"
FONT_FAMILY = "Segoe UI"

ICON_PX = 16  # vanilla texture size
VIEW_SIZE = (470, 166)  # the placement panel
RESULT_BOX = (406, 59)  # top-left of the result icon beside a textureless panel
ITEM_ROLE = QtCore.Qt.ItemDataRole.UserRole  # where list/tree rows keep the item id

KIND_COLORS = {"root": ACCENT, "base": GOLD, "brew": BREW_COLOR}


# --- Config -------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    """The saved config, or {} if there is none or it cannot be read."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_config(path: Path, config: dict[str, Any]) -> None:
    """Write the config; a failure is logged, since it only costs a preference."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("Could not save settings to %s: %s", path, e)


def parse_geometry(text: object) -> tuple[int, int, int, int] | None:
    """'1000x740+10+20' -> (width, height, x, y), or None if malformed."""
    match = GEOMETRY.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        return None
    w, h, x, y = (int(group) for group in match.groups())
    return w, h, x, y


def format_geometry(width: int, height: int, x: int, y: int) -> str:
    return f"{width}x{height}{x:+d}{y:+d}"


# --- Theme --------------------------------------------------------------------


def make_window_icon() -> QtGui.QIcon:
    """Draw a pixel-art pickaxe for the window icon. Needs a QApplication."""
    pixels = [
        "....WWWWWWWW....",
        "..WWGGGGGGGGWW..",
        ".WGGG......GGGW.",
        ".GGG........GGG.",
        "GGG....bB....GGG",
        "GG.....bB.....GG",
        *[".......bB......."] * 10,
    ]
    colors = {"W": "#c6ced8", "G": "#9aa3ad", "B": "#8a5a2b", "b": "#6e4620"}
    image = QtGui.QImage(16, 16, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    for y, row in enumerate(pixels):
        for x, char in enumerate(row):
            if char in colors:
                image.setPixelColor(x, y, QtGui.QColor(colors[char]))
    # The default (nearest-neighbour) scaling keeps the pixels crisp
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image.scaled(64, 64)))


def stylesheet() -> str:
    """The window's Qt style sheet, built from the dark palette."""
    return "\n".join(
        [
            f"QWidget {{ background: {BG}; color: {FG}; }}",
            f'QLabel[role="header"] {{ color: {ACCENT}; font-size: 16pt; font-weight: bold; }}',
            f'QLabel[role="sub"] {{ color: {FG_DIM}; }}',
            f'QLabel[role="section"] {{ color: {ACCENT}; font-size: 10pt; font-weight: bold; }}',
            f"QLabel#status {{ background: {PANEL}; color: {FG_DIM}; padding: 5px 10px; }}",
            f"QGroupBox {{ border: 1px solid {BORDER}; margin-top: 10px; padding: 8px 5px 5px; }}",
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px;"
            f" color: {FG_DIM}; font-weight: bold; }}",
            f"QPushButton {{ background: {BUTTON}; border: 1px solid {BORDER};"
            " padding: 5px 12px; }",
            f"QPushButton:hover {{ background: {BUTTON_HOVER}; }}",
            f"QPushButton:pressed {{ background: {BUTTON_PRESSED}; }}",
            f'QPushButton[role="accent"] {{ background: {ACCENT}; color: {ACCENT_TEXT};'
            f" border-color: {ACCENT_DARK}; font-weight: bold; }}",
            f'QPushButton[role="accent"]:hover {{ background: {ACCENT_HOVER}; }}',
            f'QPushButton[role="accent"]:pressed {{ background: {ACCENT_DARK}; }}',
            f'QPushButton:disabled, QPushButton[role="accent"]:disabled {{'
            f" background: {DISABLED}; color: {FG_DIM}; }}",
            # The spin box is left to Fusion and the palette: styling it here
            # would drop its arrows, which style sheets cannot draw back
            f"QLineEdit {{ background: {FIELD}; border: 1px solid {BORDER};"
            f" padding: 5px; selection-background-color: {SELECT}; }}",
            f"QLineEdit:focus {{ border-color: {ACCENT}; }}",
            f"QListWidget, QTreeWidget {{ background: {FIELD};"
            f" alternate-background-color: {FIELD_ALT}; border: 1px solid {BORDER};"
            " outline: none; }",
            f"QListWidget::item:selected, QTreeWidget::item:selected {{ background: {SELECT};"
            f" color: {FG}; }}",
            f"QHeaderView::section {{ background: {BUTTON}; color: {FG_DIM}; border: none;"
            f" border-right: 1px solid {BORDER}; padding: 5px 8px; font-weight: bold; }}",
            f"QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}",
            f"QScrollBar::handle:vertical {{ background: {BORDER}; min-height: 24px; }}",
            "QScrollBar::handle:vertical:hover { background: #4c515b; }",
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }",
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }",
        ]
    )


def apply_dark_theme(app: QtWidgets.QApplication) -> None:
    """Fusion with the dark palette, so dialogs match the window too."""
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    role = QtGui.QPalette.ColorRole
    for color_role, color in (
        (role.Window, BG),
        (role.WindowText, FG),
        (role.Base, FIELD),
        (role.AlternateBase, FIELD_ALT),
        (role.Text, FG),
        (role.Button, BUTTON),
        (role.ButtonText, FG),
        (role.Highlight, SELECT),
        (role.HighlightedText, FG),
        (role.Link, LINK),
    ):
        palette.setColor(color_role, QtGui.QColor(color))
    app.setPalette(palette)
    app.setFont(QtGui.QFont(FONT_FAMILY, 9))
    app.setStyleSheet(stylesheet())


def font(size: int, *, bold: bool = True) -> QtGui.QFont:
    result = QtGui.QFont(FONT_FAMILY, size)
    result.setBold(bold)
    return result


# --- Dialogs ------------------------------------------------------------------
# Every dialog goes through one of these, so tests can stand in for the user.


def show_error(parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
    QtWidgets.QMessageBox.critical(parent, title, message)


def ask_jar_file(parent: QtWidgets.QWidget | None) -> str:
    """Pick a jar; returns "" if cancelled."""
    path, _filter = QtWidgets.QFileDialog.getOpenFileName(
        parent, "Select a Minecraft jar file", "", "JAR files (*.jar);;All files (*.*)"
    )
    return path


def choose_jar(parent: QtWidgets.QWidget, candidates: list[JarCandidate]) -> JarCandidate:
    """Let the user pick between several detected installations."""
    dialog = JarChooser(parent, candidates)
    dialog.exec()
    # Dismissing the dialog falls back to the highlighted jar rather than
    # leaving the app with a jar chosen but no recipes and nothing to click.
    return dialog.chosen()


class JarChooser(QtWidgets.QDialog):
    """Lists every detected installation; the first is preselected."""

    def __init__(self, parent: QtWidgets.QWidget, candidates: list[JarCandidate]) -> None:
        super().__init__(parent)
        self.candidates = candidates
        self.setWindowTitle("Select Minecraft Version")
        self.resize(680, 300)
        self.move(parent.x() + 160, parent.y() + 180)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QtWidgets.QLabel("Multiple Minecraft installations found")
        heading.setProperty("role", "section")
        layout.addWidget(heading)
        sub = QtWidgets.QLabel("Choose the version to load recipes from:")
        sub.setProperty("role", "sub")
        layout.addWidget(sub)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Version", "Launcher", "Location"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 140)
        self.tree.setColumnWidth(1, 160)
        for cand in candidates:
            self.tree.addTopLevelItem(
                QtWidgets.QTreeWidgetItem([cand.version, cand.source, str(cand.path)])
            )
        if (first := self.tree.topLevelItem(0)) is not None:
            self.tree.setCurrentItem(first)
        self.tree.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.tree, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        use = QtWidgets.QPushButton("Use Selected")
        use.setProperty("role", "accent")
        use.clicked.connect(self.accept)
        buttons.addWidget(use)
        layout.addLayout(buttons)

    def chosen(self) -> JarCandidate:
        item = self.tree.currentItem()
        index = self.tree.indexOfTopLevelItem(item) if item is not None else 0
        return self.candidates[max(index, 0)]


# --- Jar textures as Qt images --------------------------------------------------


def zoom(image: QtGui.QImage, factor: int) -> QtGui.QPixmap:
    """Scale up by a whole factor with nearest-neighbour, keeping pixels crisp."""
    return QtGui.QPixmap.fromImage(image.scaled(image.width() * factor, image.height() * factor))


def blank(width: int, height: int) -> QtGui.QImage:
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    return image


class JarAssets:
    """Icons and GUI textures from one jar, cached. The jar opens on first use."""

    def __init__(self, jar_path: Path | None) -> None:
        self.jar_path = jar_path
        self._zip: zipfile.ZipFile | None = None
        self._renderer: BlockRenderer | None = None
        self._icons: dict[tuple[str, bool], QtGui.QPixmap | None] = {}
        self._gui: dict[str, QtGui.QPixmap | None] = {}
        self._potions: dict[tuple[str, str], QtGui.QImage | None] = {}

    def close(self) -> None:
        if self._zip is not None:
            with contextlib.suppress(OSError):
                self._zip.close()
        self._zip = None
        self._renderer = None

    def _archive(self) -> zipfile.ZipFile | None:
        if self._zip is None and self.jar_path is not None and self.jar_path.exists():
            try:
                self._zip = zipfile.ZipFile(self.jar_path, "r")
            except (OSError, zipfile.BadZipFile) as e:
                log.warning("Cannot read textures from %s: %s", self.jar_path, e)
        return self._zip

    def load_image(self, path: str) -> QtGui.QImage | None:
        """A PNG from the jar, first frame only if animated; None if missing."""
        archive = self._archive()
        if archive is None:
            return None
        try:
            data = archive.read(path)
        except KeyError:
            return None
        image = QtGui.QImage.fromData(data)
        if image.isNull():
            return None
        image = image.convertToFormat(QtGui.QImage.Format.Format_ARGB32)
        if image.height() > image.width():  # animated texture strip
            image = image.copy(0, 0, image.width(), image.width())
        return image

    def icon(self, item: str, *, big: bool = False) -> QtGui.QPixmap | None:
        """Item icon, 32px for lists and 48px for a result; None if the jar has none.

        Flat item textures are used as-is; blocks without one get an isometric
        render of their model, the way the game draws inventory icons.
        """
        key = (item, big)
        if key not in self._icons:
            self._icons[key] = self._make_icon(item, 3 if big else 2)
        return self._icons[key]

    def _make_icon(self, item: str, factor: int) -> QtGui.QPixmap | None:
        archive = self._archive()
        if archive is None:
            return None
        image = self._potion_image(item) if potion_look(item) else None
        if image is None:
            # True items keep their flat sprite, like the game
            image = self.load_image(f"assets/minecraft/textures/item/{item}.png")
        if image is not None:
            return zoom(image, factor)

        if self._renderer is None:
            self._renderer = BlockRenderer(archive)
        png = self._renderer.render_png(item, ICON_PX * factor)
        if png is not None:
            pixmap = QtGui.QPixmap()
            if pixmap.loadFromData(png):
                return pixmap

        for name in texture_names(item):
            if (image := self.load_image(name)) is not None:
                return zoom(image, factor)
        image = self._model_flat_image(item)
        return zoom(image, factor) if image is not None else None

    def _model_flat_image(self, item: str) -> QtGui.QImage | None:
        """Fallback when the model can't be rendered (no Pillow): flat parent
        texture, half-sliced for slabs / stepped for stairs."""
        archive = self._archive()
        if archive is None:
            return None
        model_ref = item_model_ref(archive, item)
        ref = model_texture_ref(archive, model_ref) if model_ref else None
        image = self.load_image(f"assets/minecraft/textures/{ref}.png") if ref else None
        if image is None or not item.endswith(("_slab", "_stairs")):
            return image
        w, h = image.width(), image.height()
        shaped = blank(w, h)
        painter = QtGui.QPainter(shaped)
        painter.drawImage(QtCore.QPoint(0, h // 2), image, QtCore.QRect(0, h // 2, w, h - h // 2))
        if item.endswith("_stairs"):
            painter.drawImage(QtCore.QPoint(0, 0), image, QtCore.QRect(0, 0, w // 2, h // 2))
        painter.end()
        return shaped

    def _potion_image(self, item: str) -> QtGui.QImage | None:
        """Potion bottle with its liquid tinted the effect's colour, composited
        the way the game renders potion items (tinted overlay under the glass)."""
        look = potion_look(item)
        if look is None:
            return None
        if look not in self._potions:
            self._potions[look] = self._tinted_bottle(*look)
        return self._potions[look]

    def _tinted_bottle(self, bottle: str, color: str) -> QtGui.QImage | None:
        base = "assets/minecraft/textures/item/"
        overlay = self.load_image(base + "potion_overlay.png")
        glass = self.load_image(f"{base}{bottle}.png")
        if overlay is None or glass is None:
            return None
        tint = QtGui.QColor(color)
        image = blank(overlay.width(), overlay.height())
        for y in range(overlay.height()):
            for x in range(overlay.width()):
                pixel = overlay.pixelColor(x, y)
                if pixel.alpha() == 0:
                    continue
                image.setPixelColor(
                    x,
                    y,
                    QtGui.QColor(
                        pixel.red() * tint.red() // 255,
                        pixel.green() * tint.green() // 255,
                        pixel.blue() * tint.blue() // 255,
                    ),
                )
        painter = QtGui.QPainter(image)  # glass on top
        painter.drawImage(0, 0, glass)
        painter.end()
        return image

    def _container_image(self, name: str) -> QtGui.QImage | None:
        """Top section (176x83, above the player inventory) of a container GUI."""
        image = self.load_image(f"assets/minecraft/textures/gui/container/{name}.png")
        return image.copy(0, 0, 176, 83) if image is not None else None

    def _brewing_stand_texture(self) -> QtGui.QPixmap | None:
        """Brewing stand GUI widened to 220px with the crafting table's arrow
        and output slot spliced into the extension (the real GUI has no
        result slot - the bottles transform in place)."""
        brew = self._container_image("brewing_stand")
        if brew is None:
            return None
        craft = self._container_image("crafting_table")
        if craft is None:
            return zoom(brew, 2)

        wide = blank(220, 83)
        painter = QtGui.QPainter(wide)
        point, rect = QtCore.QPoint, QtCore.QRect
        # Everything left of the original right border
        painter.drawImage(point(0, 0), brew, rect(0, 0, 168, 83))
        # Tile a clean background strip (with top/bottom borders) across the extension
        painter.setClipRect(rect(168, 0, 44, 83))
        for x in range(168, 212, 8):
            painter.drawImage(point(x, 0), brew, rect(160, 0, 8, 83))
        painter.setClipping(False)
        # Re-attach the right border and corners
        painter.drawImage(point(212, 0), brew, rect(168, 0, 8, 83))
        # Arrow and output slot borrowed from the crafting table GUI
        painter.drawImage(point(138, 30), craft, rect(85, 30, 30, 21))
        painter.drawImage(point(172, 26), craft, rect(115, 26, 36, 36))
        painter.end()
        return zoom(wide, 2)

    def gui_texture(self, name: str) -> QtGui.QPixmap | None:
        """Workstation GUI background at 2x, so its 16px slots fit 32px icons."""
        if name not in self._gui:
            if name == "brewing_stand":
                self._gui[name] = self._brewing_stand_texture()
            else:
                image = self._container_image(name)
                self._gui[name] = zoom(image, 2) if image is not None else None
        return self._gui[name]


# --- Placement panel ------------------------------------------------------------

IconSource = Callable[[str, bool], QtGui.QPixmap | None]


class PlacementView(QtWidgets.QWidget):
    """Draws a recipe inside its workstation's in-game GUI, with hover captions."""

    # The caption to show under the panel: the hovered slot's item, or the default
    caption_changed = QtCore.Signal(str)

    def __init__(self, icon: IconSource) -> None:
        super().__init__()
        self.icon = icon
        self.setFixedSize(*VIEW_SIZE)
        self.setMouseTracking(True)
        self.placement: Placement | None = None
        self.texture: QtGui.QPixmap | None = None
        # (region, caption) per slot, in view pixels
        self.regions: list[tuple[QtCore.QRect, str]] = []

    def show_placement(self, placement: Placement, texture: QtGui.QPixmap | None) -> None:
        self.placement, self.texture = placement, texture
        self.regions = [
            (QtCore.QRect(s.x * 2, s.y * 2, 33, 33), pretty_name(s.item) + s.note)
            for s in placement.slots
        ]
        if placement.result is not None:
            result = placement.result
            self.regions.append(
                (QtCore.QRect(*RESULT_BOX, 49, 49), pretty_name(result.item) + result.note)
            )
        self.update()

    def caption_at(self, pos: QtCore.QPoint) -> str:
        """The hovered slot's caption, or the placement's default."""
        for region, caption in self.regions:
            if region.contains(pos):
                return caption
        return self.placement.caption if self.placement else ""

    @override
    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self.caption_changed.emit(self.caption_at(event.position().toPoint()))

    @override
    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.caption_changed.emit(self.placement.caption if self.placement else "")

    @override
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        layout = self.placement
        if layout is None:
            return
        painter = QtGui.QPainter(self)
        if layout.texture is not None and self.texture is not None:
            painter.drawPixmap(0, 0, self.texture)
            _centered(
                painter,
                QtCore.QPoint(self.texture.width() // 2, 16),
                layout.title,
                GUI_TITLE,
                font(10),
            )
        else:
            painter.setPen(QtGui.QColor(BORDER))
            painter.setBrush(QtGui.QColor(PANEL))
            painter.drawRect(1, 1, 350, 164)
            _centered(painter, QtCore.QPoint(176, layout.title_y), layout.title, FG_DIM, font(10))

        for slot in layout.slots:
            x, y = slot.x * 2, slot.y * 2
            if slot.plain:
                painter.setPen(QtGui.QColor(BORDER))
                painter.setBrush(QtGui.QColor(FIELD))
                painter.drawRect(x - 3, y - 3, 38, 38)
            if (icon := self.icon(slot.item, False)) is not None:
                painter.drawPixmap(x, y, icon)
            if slot.count > 1:
                # Count in the slot corner with a shadow, like the game draws it
                _count(painter, x + 34, y + 35, slot.count, 10)

        if layout.result is not None:
            x, y = RESULT_BOX
            _centered(painter, QtCore.QPoint(378, 83), "→", FG_DIM, font(20))
            painter.setPen(QtGui.QColor(ACCENT_DARK))
            painter.setBrush(QtGui.QColor(FIELD))
            painter.drawRect(x - 5, y - 5, 58, 58)
            if (icon := self.icon(layout.result.item, True)) is not None:
                painter.drawPixmap(x, y, icon)
            if layout.result.count > 1:
                _count(painter, x + 51, y + 52, layout.result.count, 11)
        painter.end()


def _centered(
    painter: QtGui.QPainter, center: QtCore.QPoint, text: str, color: str, text_font: QtGui.QFont
) -> None:
    painter.setPen(QtGui.QColor(color))
    painter.setFont(text_font)
    cx, cy = center.x(), center.y()
    painter.drawText(
        QtCore.QRect(cx - 235, cy - 20, 470, 40), QtCore.Qt.AlignmentFlag.AlignCenter, text
    )


def _count(painter: QtGui.QPainter, right: int, bottom: int, count: int, size: int) -> None:
    """A stack count anchored at its bottom-right corner, drop shadow first."""
    painter.setFont(font(size))
    align = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom
    for dx, color in ((0, COUNT_SHADOW), (1, "#ffffff")):
        painter.setPen(QtGui.QColor(color))
        box = QtCore.QRect(right - dx - 60, bottom - dx - 30, 60, 30)
        painter.drawText(box, align, str(count))


# --- The window -----------------------------------------------------------------


def default_find_jars() -> list[JarCandidate]:
    return find_minecraft_jars(Path.home() / "AppData" / "Roaming")


class RecipeCalculator(QtWidgets.QWidget):
    # Background results carried back to the GUI thread
    jars_detected = QtCore.Signal(object)  # list[JarCandidate]
    recipes_loaded = QtCore.Signal(object)  # RecipeBook, or the error message

    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        find_jars: Callable[[], list[JarCandidate]] = default_find_jars,
    ) -> None:
        super().__init__()
        self.setWindowTitle(TITLE)
        self.setWindowIcon(make_window_icon())
        self.config_path = config_path
        self.config = load_config(config_path)
        geometry = parse_geometry(self.config.get("geometry"))
        if geometry is None:
            self.resize(*DEFAULT_SIZE)
        else:
            w, h, x, y = geometry
            self.resize(w, h)
            self.move(x, y)

        self.book = RecipeBook({}, frozenset())
        self.jar: JarCandidate | None = None
        self.assets = JarAssets(None)
        self.loading = False  # a background recipe load is in flight

        self.jars_detected.connect(self.on_jars_detected)
        self.recipes_loaded.connect(self.on_recipes_loaded)
        self.setup_ui()

        threading.Thread(target=lambda: self.jars_detected.emit(find_jars()), daemon=True).start()

    # --- UI ------------------------------------------------------------------

    @staticmethod
    def label(text: str, role: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", role)
        return label

    def setup_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QtWidgets.QVBoxLayout()
        main.setContentsMargins(12, 10, 12, 10)
        main.setSpacing(6)
        outer.addLayout(main, 1)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(self.label("⛏  Minecraft Recipe Calculator", "header"))
        header.addStretch(1)
        self.version_label = self.label("detecting version…", "sub")
        header.addWidget(self.version_label, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        main.addLayout(header)
        main.addSpacing(4)

        jar_box = QtWidgets.QGroupBox("Minecraft Jar")
        jar_row = QtWidgets.QHBoxLayout(jar_box)
        jar_row.addWidget(QtWidgets.QLabel("Path:"))
        self.jar_path_entry = QtWidgets.QLineEdit("Detecting...")
        jar_row.addWidget(self.jar_path_entry, 1)
        # Recipes load automatically whenever a jar is chosen, so Browse is the
        # only control here - and it is disabled while a load is in flight.
        self.browse_button = QtWidgets.QPushButton("Browse")
        self.browse_button.setProperty("role", "accent")
        self.browse_button.clicked.connect(self.select_jar_manually)
        jar_row.addWidget(self.browse_button)
        main.addWidget(jar_box)

        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(QtWidgets.QLabel("Search:"))
        self.search_entry = QtWidgets.QLineEdit()
        self.search_entry.textChanged.connect(self.update_list)
        search_row.addWidget(self.search_entry, 1)
        search_row.addWidget(QtWidgets.QLabel("Quantity:"))
        self.qty_spin = QtWidgets.QSpinBox()
        self.qty_spin.setRange(1, 500)
        self.qty_spin.setMinimumWidth(90)
        self.qty_spin.valueChanged.connect(self.on_qty_change)
        search_row.addWidget(self.qty_spin)
        main.addLayout(search_row)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(10)
        content.addWidget(self.items_section(), 1)
        content.addLayout(self.results_section(), 1)
        main.addLayout(content, 1)

        self.status_label = QtWidgets.QLabel("Loading recipes from Minecraft jar...")
        self.status_label.setObjectName("status")
        outer.addWidget(self.status_label)

    def items_section(self) -> QtWidgets.QGroupBox:
        self.items_box = QtWidgets.QGroupBox("Items")
        layout = QtWidgets.QVBoxLayout(self.items_box)
        self.items_list = QtWidgets.QListWidget()
        self.items_list.setIconSize(QtCore.QSize(32, 32))
        self.items_list.setAlternatingRowColors(True)
        self.items_list.setUniformItemSizes(True)
        self.items_list.setFont(font(10, bold=False))
        self.items_list.itemSelectionChanged.connect(self.on_select)
        layout.addWidget(self.items_list)
        return self.items_box

    def results_section(self) -> QtWidgets.QVBoxLayout:
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(4)

        # Placement panel: the workstation's real GUI texture with item icons
        # drawn onto its slots at the game's own coordinates
        bar = QtWidgets.QHBoxLayout()
        self.grid_label = self.label("Placement", "section")
        bar.addWidget(self.grid_label)
        bar.addStretch(1)
        self.wiki_link = QtWidgets.QLabel()
        self.wiki_link.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.wiki_link.linkActivated.connect(self.open_wiki)
        bar.addWidget(self.wiki_link)
        right.addLayout(bar)

        self.placement_view = PlacementView(lambda item, big: self.assets.icon(item, big=big))
        right.addWidget(self.placement_view)
        self.grid_caption = self.label("", "sub")
        self.placement_view.caption_changed.connect(self.grid_caption.setText)
        right.addWidget(self.grid_caption)
        right.addSpacing(6)

        right.addWidget(self.label("Recipe Breakdown", "section"))
        self.breakdown_tree = QtWidgets.QTreeWidget()
        self.breakdown_tree.setHeaderLabels(
            ["Item", "Amount", "Stacks (\N{MULTIPLICATION SIGN}64)"]
        )
        header = self.breakdown_tree.headerItem()
        for column in (1, 2):
            header.setTextAlignment(column, QtCore.Qt.AlignmentFlag.AlignRight)
        self.breakdown_tree.setColumnWidth(0, 260)
        self.breakdown_tree.setColumnWidth(1, 80)
        self.breakdown_tree.setColumnWidth(2, 130)
        self.breakdown_tree.setIconSize(QtCore.QSize(32, 32))
        self.breakdown_tree.setFont(font(10, bold=False))
        self.breakdown_tree.itemSelectionChanged.connect(self.on_breakdown_select)
        right.addWidget(self.breakdown_tree, 1)
        return right

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # --- Jar selection ---------------------------------------------------------

    def on_jars_detected(self, found: object) -> None:
        """Called with every jar found; offers a choice when there are several.
        The jar remembered from the last session sorts to the top."""
        saved = self.config.get("jar")
        candidates = prefer_saved(
            [c for c in found if isinstance(c, JarCandidate)] if isinstance(found, list) else [],
            saved if isinstance(saved, dict) else {},
        )
        if not candidates:
            self.jar_path_entry.setText("(No jar found - use Browse button)")
            self.version_label.setText("no version detected")
            self.set_status("No jar detected. Please use Browse to select it manually.")
        elif len(candidates) == 1:
            self.set_jar(candidates[0])
        else:
            # Shown while the chooser is open; loading it would be wasted work
            # if the user picks a different one.
            self.set_jar(candidates[0], load=False)
            self.set_status(f"{len(candidates)} Minecraft installations found - choose one.")
            self.set_jar(choose_jar(self, candidates))

    def set_jar(self, candidate: JarCandidate, *, load: bool = True) -> None:
        """Point the app at a jar, remember it, and start loading its recipes."""
        self.jar = candidate
        self.jar_path_entry.setText(str(candidate.path))
        self.setWindowTitle(f"{TITLE} - {candidate.version}")
        self.version_label.setText(f"{candidate.version}  ·  {candidate.source}")
        self.config["jar"] = remembered(candidate)
        save_config(self.config_path, self.config)
        if load:
            self.load_recipes_async()

    def select_jar_manually(self) -> None:
        if path := ask_jar_file(self):
            jar = Path(path)
            self.set_jar(JarCandidate(jar, jar.stem, "manual selection"))

    # --- Recipe loading ---------------------------------------------------------

    def load_recipes_async(self) -> None:
        """Load recipes from the selected jar on a worker thread."""
        if self.jar is None or not self.jar.path.exists():
            show_error(
                self, "Error", "No valid jar file selected. Please use Browse to select a jar file."
            )
            return
        if self.loading:  # Browse is disabled meanwhile; this is a backstop
            return
        self.loading = True
        self.browse_button.setEnabled(False)
        self.set_status(f"Loading recipes from {self.jar.path.name}...")
        # Icons belong to the previous jar - drop them
        self.assets.close()
        self.assets = JarAssets(self.jar.path)
        threading.Thread(target=self.load_worker, args=(self.jar.path,), daemon=True).start()

    def load_worker(self, jar_path: Path) -> None:
        """Run on a worker thread: reading thousands of recipe files takes a moment."""
        try:
            with zipfile.ZipFile(jar_path, "r") as jar:
                book = load_recipe_book(jar)
        except Exception as e:
            # Nothing else reports for this thread; a failure must not vanish
            log.exception("Failed to load recipes from %s", jar_path)
            self.recipes_loaded.emit(f"Failed to load recipes: {e}")
        else:
            self.recipes_loaded.emit(book)

    def on_recipes_loaded(self, result: object) -> None:
        """Back on the GUI thread: show the new recipes, or say why there are none."""
        self.loading = False
        self.browse_button.setEnabled(True)
        if isinstance(result, RecipeBook) and result.recipes:
            self.book = result
            self.update_list()
            self.set_status(f"Loaded {len(result.recipes)} recipes, {len(result.all_items)} items")
            return
        message = (
            result
            if isinstance(result, str)
            else "No recipes found in jar file. Make sure you selected the correct jar."
        )
        show_error(self, "Error", message)
        self.set_status("Failed to load recipes. Please select another jar file.")

    # --- Item list and breakdown ------------------------------------------------

    def update_list(self) -> None:
        """Repopulate the item list, applying the current search filter."""
        self.items_list.clear()
        items = self.book.search(self.search_entry.text())
        for item in items:
            row = QtWidgets.QListWidgetItem(" " + pretty_name(item))
            row.setData(ITEM_ROLE, item)
            if (icon := self.assets.icon(item)) is not None:
                row.setIcon(QtGui.QIcon(icon))
            if self.book.kind(item) == "brew":
                row.setForeground(QtGui.QColor(BREW_COLOR))
            self.items_list.addItem(row)
        self.items_box.setTitle(f"Items  ({len(items)})")

    def selected_item(self) -> str | None:
        rows = self.items_list.selectedItems()
        return str(rows[0].data(ITEM_ROLE)) if rows else None

    def on_select(self) -> None:
        if item := self.selected_item():
            self.display_recipe(item)

    def on_qty_change(self) -> None:
        if item := self.selected_item():
            self.display_recipe(item)

    def display_recipe(self, item: str) -> None:
        """Show the quantity breakdown and the placement of ``item``'s recipe."""
        self.update_grid(item)
        self.breakdown_tree.clear()
        self.breakdown_tree.addTopLevelItem(
            self.breakdown_row(self.book.breakdown(item, self.qty_spin.value()))
        )
        self.breakdown_tree.expandAll()

    def breakdown_row(self, node: BreakdownNode) -> QtWidgets.QTreeWidgetItem:
        row = QtWidgets.QTreeWidgetItem(
            [" " + pretty_name(node.item), str(node.amount), format_stacks(node.amount)]
        )
        row.setData(0, ITEM_ROLE, node.item)
        for column in (1, 2):
            row.setTextAlignment(
                column, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
            )
        if (icon := self.assets.icon(node.item)) is not None:
            row.setIcon(0, QtGui.QIcon(icon))
        if color := KIND_COLORS.get(node.kind):
            for column in range(3):
                row.setForeground(column, QtGui.QColor(color))
        if node.kind == "root":
            row.setFont(0, font(10))
        row.addChildren([self.breakdown_row(child) for child in node.children])
        return row

    def on_breakdown_select(self) -> None:
        """Show the placement for whichever breakdown row is clicked."""
        rows = self.breakdown_tree.selectedItems()
        if rows and (item := rows[0].data(0, ITEM_ROLE)):
            self.update_grid(str(item))

    # --- Placement panel ----------------------------------------------------------

    def update_grid(self, item: str) -> None:
        """Render the recipe inside the workstation's actual in-game GUI."""
        recipe = self.book.recipes.get(item)
        name = station_texture(recipe)
        texture = self.assets.gui_texture(name) if name else None
        layout = placement(item, recipe, texture.width() if texture else None)

        self.wiki_link.setText(
            f'<a href="{html.escape(wiki_url(item))}" style="color: {LINK};">'
            f"{html.escape(pretty_name(item))} on the wiki ↗</a>"
        )
        self.grid_label.setText(layout.heading)
        self.placement_view.show_placement(layout, texture)
        self.grid_caption.setText(layout.caption)

    def open_wiki(self, url: str) -> None:
        """Open the minecraft.wiki page for the item in the placement panel."""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    # --- Shutdown -------------------------------------------------------------------

    @override
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Remember window geometry and jar choice for the next session."""
        self.config["geometry"] = format_geometry(self.width(), self.height(), self.x(), self.y())
        save_config(self.config_path, self.config)
        self.assets.close()
        event.accept()


def main() -> int:
    """Open the window and run until it closes. Returns the exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    window = RecipeCalculator()
    window.show()
    return app.exec()
