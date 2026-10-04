import os
import re
import json
import math
import base64
import zipfile
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import threading

# Recipe/tag folders were renamed in 1.21 (recipes -> recipe, tags/items -> tags/item)
RECIPE_DIRS = ('data/minecraft/recipe/', 'data/minecraft/recipes/')
TAG_DIRS = ('data/minecraft/tags/item/', 'data/minecraft/tags/items/')

# Brewing is hardcoded in the game rather than data-driven, so the jar has no
# potion recipes to extract - they are defined here instead. One brewing-stand
# batch turns 3 bottles + 1 ingredient into 3 potions. Blaze powder fuel is
# not counted (one powder fuels 20 batches).
BREWING_RECIPES = {
    'water_bottle':                ({'glass_bottle': 1}, 1),
    'awkward_potion':              ({'water_bottle': 3, 'nether_wart': 1}, 3),
    'potion_of_night_vision':      ({'awkward_potion': 3, 'golden_carrot': 1}, 3),
    'potion_of_invisibility':      ({'potion_of_night_vision': 3, 'fermented_spider_eye': 1}, 3),
    'potion_of_leaping':           ({'awkward_potion': 3, 'rabbit_foot': 1}, 3),
    'potion_of_fire_resistance':   ({'awkward_potion': 3, 'magma_cream': 1}, 3),
    'potion_of_swiftness':         ({'awkward_potion': 3, 'sugar': 1}, 3),
    'potion_of_slowness':          ({'potion_of_swiftness': 3, 'fermented_spider_eye': 1}, 3),
    'potion_of_water_breathing':   ({'awkward_potion': 3, 'pufferfish': 1}, 3),
    'potion_of_healing':           ({'awkward_potion': 3, 'glistering_melon_slice': 1}, 3),
    'potion_of_harming':           ({'potion_of_healing': 3, 'fermented_spider_eye': 1}, 3),
    'potion_of_poison':            ({'awkward_potion': 3, 'spider_eye': 1}, 3),
    'potion_of_regeneration':      ({'awkward_potion': 3, 'ghast_tear': 1}, 3),
    'potion_of_strength':          ({'awkward_potion': 3, 'blaze_powder': 1}, 3),
    'potion_of_weakness':          ({'water_bottle': 3, 'fermented_spider_eye': 1}, 3),
    'potion_of_slow_falling':      ({'awkward_potion': 3, 'phantom_membrane': 1}, 3),
    'potion_of_the_turtle_master': ({'awkward_potion': 3, 'turtle_helmet': 1}, 3),
    'potion_of_wind_charging':     ({'awkward_potion': 3, 'breeze_rod': 1}, 3),
    'potion_of_weaving':           ({'awkward_potion': 3, 'cobweb': 1}, 3),
    'potion_of_oozing':            ({'awkward_potion': 3, 'slime_block': 1}, 3),
    'potion_of_infestation':       ({'awkward_potion': 3, 'stone': 1}, 3),
}

# Every potion has splash and lingering variants: gunpowder makes it splash,
# dragon's breath makes the splash version linger.
for _name in [n for n in BREWING_RECIPES if n.startswith('potion_of_')]:
    BREWING_RECIPES['splash_' + _name] = ({_name: 3, 'gunpowder': 1}, 3)
    BREWING_RECIPES['lingering_' + _name] = ({'splash_' + _name: 3, 'dragon_breath': 1}, 3)

BREW_COLOR = '#c58fff'  # brewing entries in the item list / breakdown

# Liquid tints. The potion bottle texture is an empty bottle; the game tints
# a separate liquid overlay at runtime, so the icons do the same.
POTION_COLORS = {
    'water_bottle':      '#385dc6',
    'awkward_potion':    '#385dc6',
    'night_vision':      '#c2ff66',
    'invisibility':      '#f6f6f6',
    'leaping':           '#fdff84',
    'fire_resistance':   '#ff9900',
    'swiftness':         '#33ebff',
    'slowness':          '#8bafe0',
    'water_breathing':   '#98dac0',
    'healing':           '#f82423',
    'harming':           '#a9656a',
    'poison':            '#87a363',
    'regeneration':      '#cd5cab',
    'strength':          '#ffc700',
    'weakness':          '#484d48',
    'slow_falling':      '#f3cfb9',
    'the_turtle_master': '#7699c6',
    'wind_charging':     '#bdc9ff',
    'weaving':           '#78695a',
    'oozing':            '#99ffa3',
    'infestation':       '#8c9b8c',
}

# Dark theme palette
BG          = "#1b1d21"   # window background
PANEL       = "#25272c"   # status bar / panels
FIELD       = "#2e3138"   # entries, listbox, text areas
BORDER      = "#3c4048"
FG          = "#e8e8e8"
FG_DIM      = "#9aa0a8"
ACCENT      = "#54c556"   # creeper green
ACCENT_DARK = "#3d9e40"
GOLD        = "#f0b13e"   # quantities
SELECT      = "#31512f"   # listbox/text selection
FONT_UI     = ("Segoe UI", 9)

def make_icon():
    """Draw a pixel-art pickaxe PhotoImage for the window icon"""
    pixels = [
        "....WWWWWWWW....",
        "..WWGGGGGGGGWW..",
        ".WGGG......GGGW.",
        ".GGG........GGG.",
        "GGG....bB....GGG",
        "GG.....bB.....GG",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
        ".......bB.......",
    ]
    colors = {'W': '#c6ced8', 'G': '#9aa3ad', 'B': '#8a5a2b', 'b': '#6e4620'}
    icon = tk.PhotoImage(width=16, height=16)
    for y, row in enumerate(pixels):
        for x, char in enumerate(row):
            if char in colors:
                icon.put(colors[char], (x, y))
    return icon.zoom(4)

def pretty_name(item):
    """oak_log -> Oak Log"""
    return item.replace('_', ' ').title()

def format_stacks(amount):
    """133 -> '2 stacks + 5' (Minecraft stacks of 64)"""
    stacks, rem = divmod(amount, 64)
    if stacks == 0:
        return "—"
    label = f"{stacks} stack" + ("s" if stacks > 1 else "")
    return label if rem == 0 else f"{label} + {rem}"

# Words minecraft.wiki keeps lowercase in page titles
WIKI_SMALL_WORDS = {'of', 'the', 'a', 'an', 'and', 'with', 'on'}

def wiki_url(item):
    """minecraft.wiki page for an item, e.g. potion_of_weakness ->
    https://minecraft.wiki/w/Potion_of_Weakness. Splash/lingering potions
    have no page of their own - they are sections on the base potion's."""
    anchor = ''
    if item.startswith('splash_potion_of_'):
        item, anchor = item[len('splash_'):], '#Splash_Potion'
    elif item.startswith('lingering_potion_of_'):
        item, anchor = item[len('lingering_'):], '#Lingering_Potion'
    title = '_'.join(word if (i > 0 and word in WIKI_SMALL_WORDS) else word.capitalize()
                     for i, word in enumerate(item.split('_')))
    return f'https://minecraft.wiki/w/{title}{anchor}'

# Remembered window geometry and jar selection
CONFIG_PATH = Path(os.getenv('APPDATA', str(Path.home()))) / 'MinecraftRecipeCalc' / 'config.json'

def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

def save_config(config):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding='utf-8')
    except OSError:
        pass

class MinecraftRecipeCalculator:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft Recipe Calculator")
        self.config_data = load_config()
        geometry = self.config_data.get('geometry', '')
        if not re.fullmatch(r'\d+x\d+[+-]\d+[+-]\d+', geometry):
            geometry = "1000x740"
        self.root.geometry(geometry)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.icon = make_icon()  # keep a reference so tk doesn't garbage-collect it
        self.root.iconphoto(True, self.icon)

        self.recipes = {}     # item -> {'ingredients': {item: count}, 'output': count per craft}
        self.tags = {}        # tag name -> list of member values
        self.all_items = set()
        self.base_items = set()
        self.jar_path = None
        self._node_items = {}  # breakdown tree node id -> item name
        self._icons = {}       # item -> 32px PhotoImage (item list / breakdown)
        self._icons_big = {}   # item -> 48px PhotoImage
        self._gui_cache = {}   # station name -> GUI background texture
        self._potion_cache = {}  # (bottle texture, tint) -> composited 16px image
        self._pil_cache = {}   # texture path -> PIL image (model rendering)
        self._render_cache = {}  # item -> isometric PIL render of its model
        self._assets_zip = None
        self._loading = False    # a background recipe load is in flight

        self.setup_style()
        self.setup_ui()
        self.detect_jar_path_threaded()

    def on_close(self):
        """Remember window geometry and jar choice for the next session"""
        self.config_data['geometry'] = self.root.geometry()
        save_config(self.config_data)
        self.root.destroy()

    def setup_style(self):
        """Apply the dark theme to all ttk widgets"""
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        style.theme_use('clam')

        style.configure('.', background=BG, foreground=FG, bordercolor=BORDER,
                        lightcolor=BG, darkcolor=BG, focuscolor=ACCENT, font=FONT_UI)
        style.configure('TLabelframe', background=BG, bordercolor=BORDER)
        style.configure('TLabelframe.Label', background=BG, foreground=FG_DIM,
                        font=("Segoe UI", 9, "bold"))
        style.configure('Header.TLabel', font=("Segoe UI", 16, "bold"), foreground=ACCENT)
        style.configure('Sub.TLabel', foreground=FG_DIM)
        style.configure('Section.TLabel', font=("Segoe UI", 10, "bold"), foreground=ACCENT)
        style.configure('Link.TLabel', foreground='#6fb7ff', font=("Segoe UI", 9, "underline"))
        style.configure('Status.TLabel', background=PANEL, foreground=FG_DIM, padding=(10, 5))

        style.configure('TButton', background='#33363d', foreground=FG,
                        bordercolor=BORDER, padding=(12, 5))
        style.map('TButton',
                  background=[('disabled', '#26282c'), ('pressed', '#2a2c31'), ('active', '#3e424b')],
                  foreground=[('disabled', FG_DIM)])
        style.configure('Accent.TButton', background=ACCENT, foreground='#0f2410',
                        bordercolor=ACCENT_DARK, font=("Segoe UI", 9, "bold"), padding=(12, 5))
        style.map('Accent.TButton',
                  background=[('disabled', '#26282c'), ('pressed', ACCENT_DARK), ('active', '#66d168')],
                  foreground=[('disabled', FG_DIM)])

        for widget in ('TEntry', 'TSpinbox'):
            style.configure(widget, fieldbackground=FIELD, background=FIELD,
                            foreground=FG, bordercolor=BORDER, insertcolor=FG,
                            lightcolor=FIELD, darkcolor=FIELD, arrowcolor=FG_DIM, padding=5)
            style.map(widget, bordercolor=[('focus', ACCENT)],
                      lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
        style.map('TSpinbox', arrowcolor=[('active', ACCENT)])

        style.configure('Vertical.TScrollbar', background='#3c4048', troughcolor=BG,
                        bordercolor=BG, arrowcolor=FG_DIM)
        style.map('Vertical.TScrollbar', background=[('active', '#4c515b')])

        style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=FG,
                        bordercolor=BORDER, rowheight=36, font=("Segoe UI", 10))
        style.configure('Treeview.Heading', background='#33363d', foreground=FG_DIM,
                        bordercolor=BORDER, font=("Segoe UI", 9, "bold"), padding=(8, 5))
        style.map('Treeview', background=[('selected', SELECT)], foreground=[('selected', FG)])
        style.map('Treeview.Heading', background=[('active', '#3e424b')])

    def setup_ui(self):
        # Status bar (packed first so it stays pinned to the bottom)
        self.status_var = tk.StringVar(value="Loading recipes from Minecraft jar...")
        status = ttk.Label(self.root, textvariable=self.status_var, style='Status.TLabel')
        status.pack(fill=tk.X, side=tk.BOTTOM)

        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # Header
        header = ttk.Frame(main_frame)
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="⛏  Minecraft Recipe Calculator", style='Header.TLabel').pack(side=tk.LEFT)
        self.version_label = ttk.Label(header, text="detecting version…", style='Sub.TLabel')
        self.version_label.pack(side=tk.RIGHT, anchor=tk.S, pady=(0, 4))

        # Jar selection frame
        jar_frame = ttk.LabelFrame(main_frame, text="Minecraft Jar", padding="10")
        jar_frame.pack(fill=tk.X, pady=5)

        jar_input_frame = ttk.Frame(jar_frame)
        jar_input_frame.pack(fill=tk.X, pady=5)

        ttk.Label(jar_input_frame, text="Path:").pack(side=tk.LEFT, padx=5)
        self.jar_path_var = tk.StringVar(value="Detecting...")
        self.jar_path_entry = ttk.Entry(jar_input_frame, textvariable=self.jar_path_var)
        self.jar_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # Recipes load automatically whenever a jar is chosen, so Browse is the
        # only control here - and it is disabled while a load is in flight.
        self.browse_button = ttk.Button(jar_input_frame, text="Browse", style='Accent.TButton',
                                        command=self.select_jar_manually)
        self.browse_button.pack(side=tk.LEFT, padx=5)

        # Search frame
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=5)

        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace('w', self.on_search)
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        ttk.Label(search_frame, text="Quantity:").pack(side=tk.LEFT, padx=5)
        self.qty_var = tk.IntVar(value=1)
        self.qty_var.trace('w', self.on_qty_change)
        qty_spin = ttk.Spinbox(search_frame, from_=1, to=500, textvariable=self.qty_var, width=10)
        qty_spin.pack(side=tk.LEFT, padx=5)

        # Content frame
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        # Left: item list
        self.items_frame = ttk.LabelFrame(content_frame, text="Items", padding="5")
        self.items_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        scrollbar = ttk.Scrollbar(self.items_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.items_tree = ttk.Treeview(self.items_frame, show='tree', yscrollcommand=scrollbar.set)
        self.items_tree.column('#0', anchor=tk.W, width=240)
        self.items_tree.tag_configure('odd', background='#33363b')
        self.items_tree.tag_configure('brew', foreground=BREW_COLOR)
        self.items_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.items_tree.yview)
        self.items_tree.bind('<<TreeviewSelect>>', self.on_select)

        # Right: Results
        right_frame = ttk.Frame(content_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        # Placement panel: the workstation's real GUI texture with item icons
        # drawn onto its slots at the game's own coordinates
        placement_bar = ttk.Frame(right_frame)
        placement_bar.pack(fill=tk.X)
        self.grid_label = ttk.Label(placement_bar, text="Placement", style='Section.TLabel')
        self.grid_label.pack(side=tk.LEFT)
        self.wiki_link = ttk.Label(placement_bar, text='', style='Link.TLabel', cursor='hand2')
        self.wiki_link.pack(side=tk.RIGHT, padx=(0, 8))
        self.wiki_link.bind('<Button-1>', self.open_wiki)
        self._wiki_item = None
        self.gui_canvas = tk.Canvas(right_frame, width=470, height=166, bg=BG, highlightthickness=0)
        self.gui_canvas.pack(anchor=tk.W, pady=(4, 0))
        self.gui_canvas.bind('<Motion>', self.on_canvas_hover)
        self.gui_canvas.bind('<Leave>',
                             lambda e: self.grid_caption.config(text=self._grid_default_caption))
        self.grid_caption = ttk.Label(right_frame, text='', style='Sub.TLabel')
        self.grid_caption.pack(anchor=tk.W, pady=(2, 6))
        self._canvas_slots = []        # (x1, y1, x2, y2, item, note) hover regions
        self._grid_default_caption = ''

        # Recipe breakdown
        ttk.Label(right_frame, text="Recipe Breakdown", style='Section.TLabel').pack(anchor=tk.W)
        tree_frame = ttk.Frame(right_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        tree_scroll = ttk.Scrollbar(tree_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.breakdown_tree = ttk.Treeview(tree_frame, columns=('amount', 'stacks'),
                                           show='tree headings', height=9, yscrollcommand=tree_scroll.set)
        self.breakdown_tree.heading('#0', text='Item', anchor=tk.W)
        self.breakdown_tree.heading('amount', text='Amount', anchor=tk.E)
        self.breakdown_tree.heading('stacks', text='Stacks (×64)', anchor=tk.E)
        self.breakdown_tree.column('#0', anchor=tk.W, width=260)
        self.breakdown_tree.column('amount', anchor=tk.E, width=80, stretch=False)
        self.breakdown_tree.column('stacks', anchor=tk.E, width=130, stretch=False)
        self.breakdown_tree.tag_configure('root', foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        self.breakdown_tree.tag_configure('base', foreground=GOLD)
        self.breakdown_tree.tag_configure('brew', foreground=BREW_COLOR)
        self.breakdown_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.breakdown_tree.yview)
        self.breakdown_tree.bind('<<TreeviewSelect>>', self.on_breakdown_select)

    # ------------------------------------------------------------------
    # Jar detection
    # ------------------------------------------------------------------
    def find_minecraft_jars(self):
        """Find every Minecraft client jar with recipe data, default launcher first.

        Returns a list of {'path': Path, 'version': str, 'source': str}.
        """
        appdata = Path.home() / "AppData" / "Roaming"
        found = []
        seen = set()

        def scan_versions_dir(versions_dir, source):
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
                if jar in seen or not self.jar_has_recipes(jar):
                    continue
                seen.add(jar)
                found.append({'path': jar, 'version': folder.name, 'source': source})

        scan_versions_dir(appdata / ".minecraft" / "versions", ".minecraft (default)")
        scan_versions_dir(appdata / "ModrinthApp" / "meta" / "versions", "Modrinth App")
        for base in (appdata / "Modrinth" / "profiles", appdata / "Modrinth" / "instances"):
            if base.exists():
                for profile in base.iterdir():
                    if profile.is_dir():
                        scan_versions_dir(profile / ".minecraft" / "versions", f"Modrinth ({profile.name})")
                        scan_versions_dir(profile / "versions", f"Modrinth ({profile.name})")

        return found

    def jar_has_recipes(self, jar_path):
        """Check the jar actually contains recipe data before offering it"""
        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                return any(name.startswith(RECIPE_DIRS) for name in jar.namelist())
        except (OSError, zipfile.BadZipFile):
            return False

    # ------------------------------------------------------------------
    # Item icons (textures straight from the jar)
    # ------------------------------------------------------------------
    def _open_assets(self):
        """Keep a jar handle open for reading textures on demand"""
        if self._assets_zip is None and self.jar_path and Path(self.jar_path).exists():
            try:
                self._assets_zip = zipfile.ZipFile(self.jar_path, 'r')
            except (OSError, zipfile.BadZipFile):
                self._assets_zip = None
        return self._assets_zip

    def _texture_names(self, item):
        """Candidate texture paths for an item, most specific first"""
        if item.startswith('splash_potion_of_'):
            item = 'splash_potion'   # potion bottle textures are tinted in-game
        elif item.startswith('lingering_potion_of_'):
            item = 'lingering_potion'
        elif item.startswith('potion_of_') or item in ('awkward_potion', 'water_bottle'):
            item = 'potion'
        base = 'assets/minecraft/textures/'
        return (f'{base}item/{item}.png',
                f'{base}block/{item}.png',
                f'{base}block/{item}_top.png',
                f'{base}block/{item}_front.png',
                f'{base}block/{item}_side.png')

    def get_icon(self, item, big=False):
        """Item icon as a PhotoImage (32px lists / 48px result), cached.

        Flat item textures are used as-is; blocks without one (fences,
        slabs, stairs, walls...) get an isometric render of their model,
        the way the game draws inventory icons."""
        cache = self._icons_big if big else self._icons
        if item in cache:
            return cache[item]

        icon = None
        if self._open_assets() is not None:
            img = None
            if item.startswith(('potion_of_', 'splash_potion_of_', 'lingering_potion_of_')) \
                    or item in ('awkward_potion', 'water_bottle'):
                img = self._potion_image(item)
            if img is None:
                # True items keep their flat sprite, like the game
                img = self._load_png(f'assets/minecraft/textures/item/{item}.png')
            if img is not None:
                icon = img.zoom(3 if big else 2)
            else:
                # Blocks get an isometric render of their model
                icon = self._render_block_icon(item, 48 if big else 32)
                if icon is None:
                    for name in self._texture_names(item):
                        img = self._load_png(name)
                        if img is not None:
                            break
                    if img is None:
                        img = self._model_flat_image(item)
                    if img is not None:
                        icon = img.zoom(3 if big else 2)
        cache[item] = icon
        return icon

    def _load_png(self, path):
        """PNG from the jar as a PhotoImage, or None"""
        jar = self._open_assets()
        try:
            data = jar.read(path)
        except KeyError:
            return None
        try:
            img = tk.PhotoImage(data=base64.b64encode(data))
            if img.height() > img.width():
                # Animated texture strip - keep only the first frame
                frame = tk.PhotoImage(width=img.width(), height=img.width())
                frame.tk.call(frame, 'copy', img, '-from', 0, 0, img.width(), img.width())
                img = frame
            return img
        except tk.TclError:
            return None

    def _model_texture_ref(self, model_ref, depth=0):
        """Follow a model JSON ('item/oak_slab') to a concrete texture path
        ('block/oak_planks'). Slabs, stairs, carpets and the like have no
        texture of their own - their models borrow the parent block's."""
        jar = self._open_assets()
        if depth > 4 or jar is None:
            return None
        try:
            model = json.loads(jar.read(f'assets/minecraft/models/{model_ref}.json').decode('utf-8'))
        except (KeyError, ValueError, UnicodeDecodeError):
            return None
        textures = model.get('textures', {})
        for key in ('side', 'all', 'top', 'end', 'bottom', 'texture', 'particle', 'layer0'):
            ref = textures.get(key, '')
            if ref and not ref.startswith('#'):
                return ref.split(':')[-1]
        for ref in textures.values():
            if isinstance(ref, str) and ref and not ref.startswith('#'):
                return ref.split(':')[-1]
        parent = model.get('parent', '')
        if parent:
            return self._model_texture_ref(parent.split(':')[-1], depth + 1)
        return None

    def _item_model_ref(self, item):
        """Model reference for an item. Older jars keep it at models/item/;
        1.21.4+ jars point there from a definition in assets/minecraft/items/."""
        jar = self._open_assets()
        if jar is None:
            return None
        try:
            jar.getinfo(f'assets/minecraft/models/item/{item}.json')
            return f'item/{item}'
        except KeyError:
            pass
        try:
            definition = json.loads(jar.read(f'assets/minecraft/items/{item}.json').decode('utf-8'))
        except (KeyError, ValueError, UnicodeDecodeError):
            return None

        def first_model(node):
            """Dig through condition/select wrappers to the first model name"""
            if isinstance(node, dict):
                model = node.get('model')
                if isinstance(model, str):
                    return model
                for value in node.values():
                    found = first_model(value)
                    if found:
                        return found
            elif isinstance(node, list):
                for value in node:
                    found = first_model(value)
                    if found:
                        return found
            return None

        ref = first_model(definition)
        return ref.split(':')[-1] if ref else None

    def _resolve_model(self, model_ref, depth=0):
        """Merged textures, cuboid elements and display info of a model
        plus its parent chain"""
        jar = self._open_assets()
        if depth > 6 or jar is None:
            return {}, None, {}
        try:
            model = json.loads(jar.read(f'assets/minecraft/models/{model_ref}.json').decode('utf-8'))
        except (KeyError, ValueError, UnicodeDecodeError):
            return {}, None, {}
        textures, elements, display = {}, None, {}
        parent = model.get('parent', '')
        if parent and not parent.startswith('builtin/'):
            textures, elements, display = self._resolve_model(parent.split(':')[-1], depth + 1)
        textures.update(model.get('textures', {}))
        if model.get('elements'):
            elements = model['elements']
        display.update(model.get('display', {}))
        return textures, elements, display

    def _pil_texture(self, textures, ref):
        """Texture variable ('#texture' or 'minecraft:block/x') as a 16px PIL image"""
        for _ in range(8):
            if isinstance(ref, str) and ref.startswith('#'):
                ref = textures.get(ref[1:])
            else:
                break
        if not isinstance(ref, str) or not ref or ref.startswith('#'):
            return None
        path = f"assets/minecraft/textures/{ref.split(':')[-1]}.png"
        if path in self._pil_cache:
            return self._pil_cache[path]
        img = None
        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(self._open_assets().read(path))).convert('RGBA')
            if img.height > img.width:
                img = img.crop((0, 0, img.width, img.width))
            if img.width != 16:
                img = img.resize((16, 16))
        except (KeyError, OSError, ImportError):
            img = None
        self._pil_cache[path] = img
        return img

    def _render_model(self, item):
        """Isometric render of the item's block model from its cuboids:
        top / south / east faces with the game's face shading"""
        try:
            from PIL import Image
        except ImportError:
            return None
        model_ref = self._item_model_ref(item)
        if model_ref is None:
            return None
        textures, elements, display = self._resolve_model(model_ref)
        if not elements:
            return None
        if any('rotation' in el for el in elements):
            return None  # angled planes (flowers, torches) belong flat

        # The game's icon camera shows the top, north (lower-left) and east
        # (lower-right) faces. Models that declare a 135 gui yaw (stairs)
        # want their other side shown - rotate the model instead.
        rotation = display.get('gui', {}).get('rotation', [30, 225, 0])
        if len(rotation) > 1 and rotation[1] % 360 == 135:
            remap = {'up': 'up', 'down': 'down', 'west': 'north',
                     'north': 'east', 'east': 'south', 'south': 'west'}
            rotated = []
            for el in elements:
                x1, y1, z1 = el.get('from', [0, 0, 0])
                x2, y2, z2 = el.get('to', [16, 16, 16])
                rotated.append({'from': [16 - max(z1, z2), y1, min(x1, x2)],
                                'to': [16 - min(z1, z2), y2, max(x1, x2)],
                                'faces': {remap[k]: v for k, v in el.get('faces', {}).items()
                                          if k in remap}})
            elements = rotated

        S = 4                          # screen pixels per texture pixel
        size = 34 * S
        ox, oy = size // 2, 25 * S     # near vertical edge is at (16, y, 0)
        out = Image.new('RGBA', (size, size))
        GRASS_TINT = (124, 189, 107)   # grass/foliage tintindex colour

        def project(x, y, z):
            return (ox + (x + z - 16) * S, oy + (x - z) * S / 2 - y * S)

        def face(spec, uv, p0, pu, pv, shade):
            tex = self._pil_texture(textures, spec.get('texture', '#all'))
            if tex is None:
                return
            u1, v1, u2, v2 = [round(v) for v in spec.get('uv', uv)]
            box = (min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2))
            if box[2] <= box[0] or box[3] <= box[1]:
                return
            crop = tex.crop(box)
            tint = GRASS_TINT if spec.get('tintindex') is not None else (255, 255, 255)
            if shade < 1.0 or tint != (255, 255, 255):
                channels = list(crop.split())
                for i in range(3):
                    factor = shade * tint[i] / 255
                    channels[i] = channels[i].point([int(v * factor) for v in range(256)])
                crop = Image.merge('RGBA', channels)
            ux, uy = pu[0] - p0[0], pu[1] - p0[1]
            vx, vy = pv[0] - p0[0], pv[1] - p0[1]
            det = ux * vy - uy * vx
            if det == 0:
                return
            w, h = crop.width, crop.height
            # Affine coefficients mapping output pixels back into the crop
            a_ = vy / det * w
            b_ = -vx / det * w
            c_ = -(a_ * p0[0] + b_ * p0[1])
            d_ = -uy / det * h
            e_ = ux / det * h
            f_ = -(d_ * p0[0] + e_ * p0[1])
            layer = crop.transform((size, size), Image.AFFINE, (a_, b_, c_, d_, e_, f_),
                                   resample=Image.NEAREST)
            out.alpha_composite(layer)

        # Painter's algorithm: nearest cuboids (high x, low z, high y) last
        for el in sorted(elements, key=lambda e: (lambda f: f[0] - f[2] + f[1])(e.get('from', [0, 0, 0]))):
            x1, y1, z1 = el.get('from', [0, 0, 0])
            x2, y2, z2 = el.get('to', [16, 16, 16])
            faces = el.get('faces', {})
            spec = faces.get('up')
            if spec:
                face(spec, [x1, z1, x2, z2],
                     project(x1, y2, z1), project(x2, y2, z1), project(x1, y2, z2), 1.0)
            spec = faces.get('north')
            if spec:
                face(spec, [16 - x2, 16 - y2, 16 - x1, 16 - y1],
                     project(x2, y2, z1), project(x1, y2, z1), project(x2, y1, z1), 0.8)
            spec = faces.get('east')
            if spec:
                face(spec, [16 - z2, 16 - y2, 16 - z1, 16 - y1],
                     project(x2, y2, z2), project(x2, y2, z1), project(x2, y1, z2), 0.6)

        bbox = out.getbbox()
        if bbox is None:
            return None
        # Square-crop around the content so the icon stays centred
        left, top, right, bottom = bbox
        side = max(right - left, bottom - top)
        cx, cy = (left + right) // 2, (top + bottom) // 2
        return out.crop((cx - side // 2, cy - side // 2,
                         cx - side // 2 + side, cy - side // 2 + side))

    def _render_block_icon(self, item, px):
        """Rendered block model as a PhotoImage of the requested size, or None"""
        if item not in self._render_cache:
            self._render_cache[item] = self._render_model(item)
        rendered = self._render_cache[item]
        if rendered is None:
            return None
        try:
            import io
            from PIL import Image
            buffer = io.BytesIO()
            rendered.resize((px, px), Image.NEAREST).save(buffer, 'PNG')
            return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))
        except (ImportError, OSError, tk.TclError):
            return None

    def _model_flat_image(self, item):
        """Fallback when the model can't be rendered (no Pillow): flat parent
        texture, half-sliced for slabs / stepped for stairs"""
        model_ref = self._item_model_ref(item)
        ref = self._model_texture_ref(model_ref) if model_ref else None
        if ref is None:
            return None
        img = self._load_png(f'assets/minecraft/textures/{ref}.png')
        if img is None:
            return None

        w, h = img.width(), img.height()
        if item.endswith('_slab'):
            shaped = tk.PhotoImage(width=w, height=h)
            shaped.tk.call(shaped, 'copy', img, '-from', 0, h // 2, w, h, '-to', 0, h // 2)
            img = shaped
        elif item.endswith('_stairs'):
            shaped = tk.PhotoImage(width=w, height=h)
            shaped.tk.call(shaped, 'copy', img, '-from', 0, h // 2, w, h, '-to', 0, h // 2)
            shaped.tk.call(shaped, 'copy', img, '-from', 0, 0, w // 2, h // 2)
            img = shaped
        return img

    def _potion_image(self, item):
        """Potion bottle with its liquid tinted the effect's colour, composited
        the way the game renders potion items (tinted overlay under the glass)"""
        if item.startswith('splash_potion_of_'):
            bottle, effect = 'splash_potion', item[len('splash_potion_of_'):]
        elif item.startswith('lingering_potion_of_'):
            bottle, effect = 'lingering_potion', item[len('lingering_potion_of_'):]
        elif item.startswith('potion_of_'):
            bottle, effect = 'potion', item[len('potion_of_'):]
        else:
            bottle, effect = 'potion', item  # water_bottle / awkward_potion
        color = POTION_COLORS.get(effect, POTION_COLORS['water_bottle'])

        key = (bottle, color)
        if key in self._potion_cache:
            return self._potion_cache[key]

        jar = self._open_assets()
        image = None
        try:
            base = 'assets/minecraft/textures/item/'
            overlay = tk.PhotoImage(data=base64.b64encode(jar.read(base + 'potion_overlay.png')))
            glass = tk.PhotoImage(data=base64.b64encode(jar.read(f'{base}{bottle}.png')))
            tint = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

            image = tk.PhotoImage(width=overlay.width(), height=overlay.height())
            for y in range(overlay.height()):
                for x in range(overlay.width()):
                    if overlay.tk.getboolean(
                            overlay.tk.call(overlay, 'transparency', 'get', x, y)):
                        continue
                    pixel = overlay.get(x, y)
                    if isinstance(pixel, str):
                        r, g, b = (int(v) for v in pixel.split())
                    else:
                        r, g, b = pixel
                    image.put(f'#{r * tint[0] // 255:02x}'
                              f'{g * tint[1] // 255:02x}'
                              f'{b * tint[2] // 255:02x}', (x, y))
            # Glass on top; default compositing keeps transparent pixels clear
            image.tk.call(image, 'copy', glass)
        except (KeyError, tk.TclError, ValueError):
            image = None
        self._potion_cache[key] = image
        return image

    # ------------------------------------------------------------------
    # Recipe loading
    # ------------------------------------------------------------------
    def _ingredient_ref(self, entry):
        """Normalize one ingredient entry to ('item'|'tag', name) or None.

        Handles {'item': ...}, {'tag': ...}, plain strings ('#tag' or 'item',
        used by 1.21.2+), and lists of alternatives (first one is used).
        """
        if isinstance(entry, list):
            return self._ingredient_ref(entry[0]) if entry else None
        if isinstance(entry, str):
            if entry.startswith('#'):
                return ('tag', entry.lstrip('#').split(':')[-1])
            return ('item', entry.split(':')[-1])
        if isinstance(entry, dict):
            if entry.get('item'):
                return ('item', entry['item'].split(':')[-1])
            if entry.get('tag'):
                return ('tag', entry['tag'].split(':')[-1])
            if entry.get('id'):
                return ('item', entry['id'].split(':')[-1])
        return None

    def _parse_recipe(self, recipe_json):
        """Return (result_item, output_count, type, {ref: count}, grid) or None.

        grid is a 9-slot crafting layout of ingredient refs (None = empty slot).
        """
        rtype = recipe_json.get('type', '').split(':')[-1]

        result = recipe_json.get('result')
        if isinstance(result, dict):
            result_item = result.get('id') or result.get('item') or ''
            output_count = result.get('count', 1)
        elif isinstance(result, str):
            result_item = result
            output_count = recipe_json.get('count', 1)
        else:
            return None
        result_item = result_item.split(':')[-1]
        if not result_item:
            return None

        refs = []
        grid = [None] * 9
        if rtype == 'crafting_shaped':
            key = recipe_json.get('key', {})
            for r, row in enumerate(recipe_json.get('pattern', [])[:3]):
                for c, symbol in enumerate(row[:3]):
                    if symbol != ' ' and symbol in key:
                        ref = self._ingredient_ref(key[symbol])
                        refs.append(ref)
                        grid[r * 3 + c] = ref
        elif rtype == 'crafting_shapeless':
            refs = [self._ingredient_ref(e) for e in recipe_json.get('ingredients', [])]
            for i, ref in enumerate([r for r in refs if r][:9]):
                grid[i] = ref
        elif rtype in ('smelting', 'blasting', 'smoking', 'campfire_cooking', 'stonecutting'):
            ref = self._ingredient_ref(recipe_json.get('ingredient'))
            refs = [ref]
            grid[4] = ref
        else:
            return None

        ingredients = {}
        for ref in refs:
            if ref:
                ingredients[ref] = ingredients.get(ref, 0) + 1
        if not ingredients:
            return None
        return result_item, max(int(output_count), 1), rtype, ingredients, grid

    def _resolve_tag(self, tag_name, visited=None):
        """Pick a representative item for a tag (e.g. planks -> oak_planks)"""
        if visited is None:
            visited = set()
        if tag_name in visited:
            return None
        visited.add(tag_name)
        for value in self.tags.get(tag_name, []):
            if isinstance(value, dict):
                value = value.get('id', '')
            if not isinstance(value, str) or not value:
                continue
            if value.startswith('#'):
                nested = self._resolve_tag(value.lstrip('#').split(':')[-1], visited)
                if nested:
                    return nested
            else:
                return value.split(':')[-1]
        return None

    def load_recipes(self):
        """Load recipes from Minecraft jar"""
        try:
            jar_path = self.jar_path

            if not jar_path or not Path(jar_path).exists():
                self.root.after(0, lambda: messagebox.showerror("Error",
                    "Jar file not found. Please select a valid jar file using Browse."))
                return False

            self.recipes = {}
            self.tags = {}
            self.all_items = set()
            self.base_items = set()
            # result_item -> list of (priority, output_count, {ref: count})
            candidates = {}

            with zipfile.ZipFile(jar_path, 'r') as jar:
                names = jar.namelist()

                # Item tags first, so tag ingredients can be resolved
                for name in names:
                    if name.startswith(TAG_DIRS) and name.endswith('.json'):
                        try:
                            tag_json = json.loads(jar.read(name).decode('utf-8'))
                            self.tags[Path(name).stem] = tag_json.get('values', [])
                        except (ValueError, UnicodeDecodeError):
                            pass

                for name in names:
                    if not (name.startswith(RECIPE_DIRS) and name.endswith('.json')):
                        continue
                    try:
                        parsed = self._parse_recipe(json.loads(jar.read(name).decode('utf-8')))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not parsed:
                        continue

                    result_item, output_count, rtype, ingredients, grid = parsed
                    recipe_name = Path(name).stem
                    # Prefer the canonical recipe when an item has several
                    # (e.g. gold_ingot: smelting ore beats unpacking gold_block)
                    if recipe_name == result_item:
                        priority = 0
                    elif rtype in ('smelting', 'blasting', 'smoking', 'campfire_cooking', 'stonecutting'):
                        priority = 2
                    elif '_from_' in recipe_name:
                        priority = 3
                    else:
                        priority = 1
                    # Shorter names break priority ties (raw_gold beats deepslate_gold_ore)
                    candidates.setdefault(result_item, []).append(
                        (priority, len(recipe_name), output_count, ingredients, rtype, grid))

            # Resolve tag references to concrete items, best candidates first
            resolved_candidates = {}
            for result_item, cands in candidates.items():
                cands.sort(key=lambda c: (c[0], c[1]))
                options = []
                for _, _, output_count, ingredients, rtype, grid in cands:
                    resolved = {}
                    for (kind, ref_name), count in ingredients.items():
                        item = self._resolve_tag(ref_name) if kind == 'tag' else ref_name
                        if item:
                            resolved[item] = resolved.get(item, 0) + count
                    resolved_grid = [
                        (self._resolve_tag(ref[1]) if ref[0] == 'tag' else ref[1]) if ref else None
                        for ref in grid
                    ]
                    if resolved:
                        options.append({'ingredients': resolved, 'output': output_count,
                                        'method': rtype, 'grid': resolved_grid})
                if options:
                    resolved_candidates[result_item] = options

            self.recipes = {item: options[0] for item, options in resolved_candidates.items()}

            # Avoid pack/unpack cycles: e.g. redstone.json crafts 9 redstone
            # from redstone_block while redstone_block packs 9 redstone.
            # Swap to an alternative recipe (usually smelting) when detected.
            def is_cyclic(item, recipe):
                return any(item in self.recipes.get(ing, {}).get('ingredients', {})
                           for ing in recipe['ingredients'])

            for _ in range(2):
                changed = False
                for item, options in resolved_candidates.items():
                    if is_cyclic(item, self.recipes[item]):
                        for alt in options[1:]:
                            if not is_cyclic(item, alt):
                                self.recipes[item] = alt
                                changed = True
                                break
                if not changed:
                    break

            # When an item's only recipe unpacks a block that packs it back
            # (slime_ball <-> slime_block), the item is really gathered, not
            # crafted - drop the recipe so it counts as a base material
            for item in list(self.recipes):
                recipe = self.recipes[item]
                if (len(recipe['ingredients']) == 1 and recipe['output'] > 1
                        and is_cyclic(item, recipe)):
                    del self.recipes[item]

            # Brewing recipes are defined in code (see BREWING_RECIPES)
            for name, (ingredients, output) in BREWING_RECIPES.items():
                if name in self.recipes:
                    continue
                grid = [None] * 9
                if name == 'water_bottle':
                    method = 'filling'
                    grid[4] = 'glass_bottle'
                else:
                    method = 'brewing'
                    for ingredient, count in ingredients.items():
                        if count >= 3:  # the three bottles in the stand
                            grid[6] = grid[7] = grid[8] = ingredient
                        else:           # the catalyst in the top slot
                            grid[1] = ingredient
                self.recipes[name] = {'ingredients': dict(ingredients), 'output': output,
                                      'method': method, 'grid': grid, 'brewing': True}

            for item, recipe in self.recipes.items():
                self.all_items.add(item)
                self.all_items.update(recipe['ingredients'])

            # Base items (no recipes for them)
            self.base_items = self.all_items - set(self.recipes.keys())

            return len(self.recipes) > 0
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to load recipes: {str(e)}"))
            return False

    def detect_jar_path_threaded(self):
        """Detect installed Minecraft jars in background thread"""
        def detect():
            candidates = self.find_minecraft_jars()
            self.root.after(0, self.on_jars_detected, candidates)

        thread = threading.Thread(target=detect, daemon=True)
        thread.start()

    def on_jars_detected(self, candidates):
        """Called with every jar found; offers a choice when there are several.
        The jar remembered from the last session sorts to the top."""
        saved = self.config_data.get('jar', {})
        saved_path = saved.get('path', '')
        if saved_path and Path(saved_path).exists():
            for i, cand in enumerate(candidates):
                if str(cand['path']) == saved_path:
                    candidates.insert(0, candidates.pop(i))
                    break
            else:
                # Remembered jar came from Browse - scanning won't find it
                candidates.insert(0, {'path': Path(saved_path),
                                      'version': saved.get('version', Path(saved_path).stem),
                                      'source': saved.get('source', 'remembered')})
            candidates[0]['source'] += ' (last used)'

        if not candidates:
            self.jar_path_var.set("(No jar found - use Browse button)")
            self.version_label.config(text="no version detected")
            self.status_var.set("No jar detected. Please use Browse to select it manually.")
        elif len(candidates) == 1:
            self.set_jar(candidates[0])
        else:
            self.set_jar(candidates[0], load=False)
            self.status_var.set(f"{len(candidates)} Minecraft installations found - choose one.")
            self.choose_jar_dialog(candidates)

    def set_jar(self, candidate, load=True):
        """Point the app at a jar and start loading its recipes.

        load=False is for the moment before the chooser dialog opens: the first
        candidate is shown so the UI is populated, but loading it would be wasted
        work if the user picks a different one.
        """
        self.jar_path = candidate['path']
        self.jar_path_var.set(str(candidate['path']))
        self.root.title(f"Minecraft Recipe Calculator - {candidate['version']}")
        self.version_label.config(text=f"{candidate['version']}  ·  {candidate['source']}")
        self.config_data['jar'] = {'path': str(candidate['path']),
                                   'version': candidate['version'],
                                   'source': candidate['source'].replace(' (last used)', '')}
        save_config(self.config_data)
        if load:
            self.load_recipes_async()

    def choose_jar_dialog(self, candidates):
        """Let the user pick between multiple detected installations"""
        win = tk.Toplevel(self.root)
        win.title("Select Minecraft Version")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        win.geometry(f"680x300+{self.root.winfo_rootx() + 160}+{self.root.winfo_rooty() + 180}")

        ttk.Label(win, text="Multiple Minecraft installations found", style='Section.TLabel').pack(
            anchor=tk.W, padx=12, pady=(12, 2))
        ttk.Label(win, text="Choose the version to load recipes from:", style='Sub.TLabel').pack(
            anchor=tk.W, padx=12)

        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tree = ttk.Treeview(frame, columns=('version', 'source', 'path'), show='headings',
                            height=6, yscrollcommand=scroll.set)
        tree.heading('version', text='Version', anchor=tk.W)
        tree.heading('source', text='Launcher', anchor=tk.W)
        tree.heading('path', text='Location', anchor=tk.W)
        tree.column('version', width=140, stretch=False)
        tree.column('source', width=160, stretch=False)
        tree.column('path', width=320)
        tree.tag_configure('odd', background='#33363b')
        for i, cand in enumerate(candidates):
            tree.insert('', tk.END, iid=str(i), tags=('odd',) if i % 2 else (),
                        values=(cand['version'], cand['source'], str(cand['path'])))
        tree.selection_set('0')
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.config(command=tree.yview)

        def use_selected(*_):
            selection = tree.selection()
            candidate = candidates[int(selection[0])] if selection else candidates[0]
            win.destroy()
            self.set_jar(candidate)

        tree.bind('<Double-1>', use_selected)
        buttons = ttk.Frame(win)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(buttons, text="Use Selected", style='Accent.TButton', command=use_selected).pack(side=tk.RIGHT)
        # Dismissing the dialog falls back to the highlighted jar rather than
        # leaving the app with a jar chosen but no recipes and nothing to click.
        win.protocol("WM_DELETE_WINDOW", use_selected)

    def select_jar_manually(self):
        """Open file dialog to manually select jar"""
        jar_path = filedialog.askopenfilename(
            title="Select a Minecraft jar file",
            filetypes=[("JAR files", "*.jar"), ("All files", "*.*")]
        )
        if jar_path:
            path = Path(jar_path)
            self.set_jar({'path': path, 'version': path.stem, 'source': 'manual selection'})

    def load_recipes_async(self):
        """Load recipes from the selected jar in a background thread"""
        if not self.jar_path or not Path(self.jar_path).exists():
            messagebox.showerror("Error", "No valid jar file selected. Please use Browse to select a jar file.")
            return
        # Browse stays live during a load, so guard against a second pass
        # stomping on self.recipes while the first is still building it.
        if self._loading:
            return

        self._loading = True
        self.browse_button.config(state=tk.DISABLED)
        self.status_var.set(f"Loading recipes from {Path(self.jar_path).name}...")

        # Icons belong to the previous jar - drop them
        if self._assets_zip is not None:
            try:
                self._assets_zip.close()
            except OSError:
                pass
        self._assets_zip = None
        self._icons = {}
        self._icons_big = {}
        self._gui_cache = {}
        self._potion_cache = {}
        self._pil_cache = {}
        self._render_cache = {}

        def load():
            success = self.load_recipes()
            self.root.after(0, self.on_recipes_loaded, success)

        thread = threading.Thread(target=load, daemon=True)
        thread.start()

    def on_recipes_loaded(self, success):
        """Called when recipes are loaded"""
        self._loading = False
        self.browse_button.config(state=tk.NORMAL)
        if success:
            self.update_listbox()
            self.status_var.set(f"Loaded {len(self.recipes)} recipes, {len(self.all_items)} items")
        elif len(self.recipes) == 0:
            messagebox.showerror("Error", "No recipes found in jar file. Make sure you selected the correct jar.")
            self.status_var.set("Failed to load recipes. Please select another jar file.")

    def update_listbox(self):
        """Repopulate the item list, applying the current search filter"""
        term = self.search_var.get().lower().strip()
        self.items_tree.delete(*self.items_tree.get_children())

        shown = 0
        for item in sorted(self.all_items):
            # Match with underscores or spaces ("oak planks" finds oak_planks)
            if term in item or term in item.replace('_', ' '):
                tags = []
                if self.recipes.get(item, {}).get('brewing'):
                    tags.append('brew')
                if shown % 2:
                    tags.append('odd')
                icon = self.get_icon(item)
                extra = {'image': icon} if icon else {}
                self.items_tree.insert('', tk.END, iid=item, text=' ' + pretty_name(item),
                                       tags=tuple(tags), **extra)
                shown += 1
        self.items_frame.config(text=f"Items  ({shown})")

    def on_search(self, *args):
        """Filter item list based on search"""
        self.update_listbox()

    def selected_item(self):
        selection = self.items_tree.selection()
        return selection[0] if selection else None

    def on_select(self, *args):
        """Handle item selection"""
        item = self.selected_item()
        if item:
            self.display_recipe(item)

    def on_qty_change(self, *args):
        """Recalculate when quantity changes"""
        item = self.selected_item()
        if item:
            self.display_recipe(item)

    def display_recipe(self, item):
        """Display recipe breakdown and placement grid"""
        try:
            quantity = max(self.qty_var.get(), 1)
        except tk.TclError:
            return

        self.update_grid(item)

        # Recipe tree
        self.breakdown_tree.delete(*self.breakdown_tree.get_children())
        self._node_items = {}
        root_icon = self.get_icon(item)
        root_id = self.breakdown_tree.insert('', tk.END, text=' ' + pretty_name(item), open=True,
                                             values=(quantity, format_stacks(quantity)), tags=('root',),
                                             **({'image': root_icon} if root_icon else {}))
        self._node_items[root_id] = item

        def add_children(parent_id, current_item, needed, ancestors=frozenset()):
            recipe = self.recipes.get(current_item)
            if recipe is None or current_item in ancestors:
                return
            crafts = math.ceil(needed / recipe['output'])
            for ingredient, amount in sorted(recipe['ingredients'].items()):
                total_needed = amount * crafts
                sub_recipe = self.recipes.get(ingredient)
                if sub_recipe is None:
                    tags = ('base',)
                elif sub_recipe.get('brewing'):
                    tags = ('brew',)
                else:
                    tags = ()
                icon = self.get_icon(ingredient)
                node = self.breakdown_tree.insert(parent_id, tk.END, text=' ' + pretty_name(ingredient),
                                                  open=True, tags=tags,
                                                  values=(total_needed, format_stacks(total_needed)),
                                                  **({'image': icon} if icon else {}))
                self._node_items[node] = ingredient
                add_children(node, ingredient, total_needed, ancestors | {current_item})

        add_children(root_id, item, quantity)

    def on_breakdown_select(self, *args):
        """Show the placement grid for whichever breakdown row is clicked"""
        selection = self.breakdown_tree.selection()
        if selection:
            item = self._node_items.get(selection[0])
            if item:
                self.update_grid(item)

    def _container_image(self, name):
        """Top section (176x83, above the player inventory) of a container
        GUI texture at 1x scale, or None"""
        jar = self._open_assets()
        if jar is None:
            return None
        try:
            data = jar.read(f'assets/minecraft/textures/gui/container/{name}.png')
            img = tk.PhotoImage(data=base64.b64encode(data))
            cropped = tk.PhotoImage(width=176, height=83)
            cropped.tk.call(cropped, 'copy', img, '-from', 0, 0, 176, 83)
            return cropped
        except (KeyError, tk.TclError):
            return None

    def _brewing_stand_texture(self):
        """Brewing stand GUI widened to 220px with the crafting table's arrow
        and output slot spliced into the extension (the real GUI has no
        result slot - the bottles transform in place)"""
        brew = self._container_image('brewing_stand')
        if brew is None:
            return None
        craft = self._container_image('crafting_table')
        if craft is None:
            return brew.zoom(2)

        wide = tk.PhotoImage(width=220, height=83)
        # Everything left of the original right border
        wide.tk.call(wide, 'copy', brew, '-from', 0, 0, 168, 83)
        # Tile a clean background strip (with top/bottom borders) across the extension
        wide.tk.call(wide, 'copy', brew, '-from', 160, 0, 168, 83, '-to', 168, 0, 212, 83)
        # Re-attach the right border and corners
        wide.tk.call(wide, 'copy', brew, '-from', 168, 0, 176, 83, '-to', 212, 0)
        # Arrow and output slot borrowed from the crafting table GUI
        wide.tk.call(wide, 'copy', craft, '-from', 85, 30, 115, 51, '-to', 138, 30)
        wide.tk.call(wide, 'copy', craft, '-from', 115, 26, 151, 62, '-to', 172, 26)
        return wide.zoom(2)

    def get_gui_texture(self, name):
        """Workstation GUI background scaled 2x so its 16px slots line up
        with the 32px item icons; the brewing stand gets a spliced result slot"""
        if name in self._gui_cache:
            return self._gui_cache[name]
        if name == 'brewing_stand':
            texture = self._brewing_stand_texture()
        else:
            img = self._container_image(name)
            texture = img.zoom(2) if img is not None else None
        self._gui_cache[name] = texture
        return texture

    def _place_item(self, gx, gy, item, note='', count=None):
        """Draw an item icon onto a GUI slot; gx/gy are 1x texture coordinates"""
        if not item:
            return
        x, y = gx * 2, gy * 2
        icon = self.get_icon(item)
        if icon:
            self.gui_canvas.create_image(x, y, image=icon, anchor=tk.NW)
        if count and count > 1:
            # Count in the slot corner with a shadow, like the game draws it
            self.gui_canvas.create_text(x + 34, y + 35, text=str(count), anchor=tk.SE,
                                        fill='#3b3b3b', font=("Segoe UI", 10, "bold"))
            self.gui_canvas.create_text(x + 33, y + 34, text=str(count), anchor=tk.SE,
                                        fill='white', font=("Segoe UI", 10, "bold"))
        self._canvas_slots.append((x, y, x + 32, y + 32, item, note))

    def _plain_slot(self, gx, gy, item, note='', count=None):
        """Slot with its own background, for stations without a GUI texture"""
        x, y = gx * 2, gy * 2
        self.gui_canvas.create_rectangle(x - 3, y - 3, x + 35, y + 35, fill=FIELD, outline=BORDER)
        self._place_item(gx, gy, item, note, count)

    def _draw_result(self, item, output):
        """Arrow and resulting item to the right of the workstation GUI"""
        canvas = self.gui_canvas
        canvas.create_text(378, 83, text='→', fill=FG_DIM, font=("Segoe UI", 20, "bold"))
        x, y = 406, 59
        canvas.create_rectangle(x - 5, y - 5, x + 53, y + 53, fill=FIELD, outline=ACCENT_DARK)
        icon = self.get_icon(item, big=True)
        if icon:
            canvas.create_image(x, y, image=icon, anchor=tk.NW)
        if output > 1:
            canvas.create_text(x + 51, y + 52, text=str(output), anchor=tk.SE,
                               fill='#3b3b3b', font=("Segoe UI", 11, "bold"))
            canvas.create_text(x + 50, y + 51, text=str(output), anchor=tk.SE,
                               fill='white', font=("Segoe UI", 11, "bold"))
        self._canvas_slots.append((x, y, x + 48, y + 48, item, '  (result)'))

    def open_wiki(self, *args):
        """Open the minecraft.wiki page for the item shown in the placement panel"""
        if self._wiki_item:
            webbrowser.open(wiki_url(self._wiki_item))

    def on_canvas_hover(self, event):
        """Show the hovered slot's item name under the GUI"""
        for x1, y1, x2, y2, item, note in self._canvas_slots:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.grid_caption.config(text=f"{pretty_name(item)}{note}")
                return
        self.grid_caption.config(text=self._grid_default_caption)

    def update_grid(self, item):
        """Render the recipe inside the workstation's actual in-game GUI"""
        recipe = self.recipes.get(item)
        canvas = self.gui_canvas
        canvas.delete('all')
        self._canvas_slots = []
        self._wiki_item = item
        self.wiki_link.config(text=f"{pretty_name(item)} on the wiki ↗")

        if recipe is None:
            self.grid_label.config(text="Placement  ·  base item, not craftable")
            canvas.create_rectangle(1, 1, 351, 165, fill=PANEL, outline=BORDER)
            canvas.create_text(176, 36, text='Base Item', fill=FG_DIM,
                               font=("Segoe UI", 10, "bold"))
            self._plain_slot(80, 34, item)
            self._grid_default_caption = f"{pretty_name(item)} is gathered in the world, not crafted"
            self.grid_caption.config(text=self._grid_default_caption)
            return

        methods = {
            'crafting_shaped': 'Crafting Table',
            'crafting_shapeless': 'Crafting Table (any arrangement)',
            'smelting': 'Furnace',
            'blasting': 'Blast Furnace',
            'smoking': 'Smoker',
            'campfire_cooking': 'Campfire',
            'stonecutting': 'Stonecutter',
            'brewing': 'Brewing Stand',
            'filling': 'Fill Glass Bottle with Water',
        }
        method = recipe.get('method', '')
        station = methods.get(method, method)
        self.grid_label.config(text=f"Placement  ·  {station}")

        output = recipe['output']
        result_text = pretty_name(item) + (f" × {output}" if output > 1 else "")

        textures = {'crafting_shaped': 'crafting_table', 'crafting_shapeless': 'crafting_table',
                    'smelting': 'furnace', 'blasting': 'blast_furnace', 'smoking': 'smoker',
                    'brewing': 'brewing_stand', 'stonecutting': 'stonecutter'}
        gui = self.get_gui_texture(textures[method]) if method in textures else None
        if gui:
            canvas.create_image(0, 0, image=gui, anchor=tk.NW)
            canvas.create_text(gui.width() // 2, 16, text=station.split(' (')[0],
                               fill='#404040', font=("Segoe UI", 10, "bold"))

        source = next(iter(recipe['ingredients']), None)

        if gui and method in ('crafting_shaped', 'crafting_shapeless'):
            cells = (recipe.get('grid') or [None] * 9)[:9]
            for i, cell in enumerate(cells):
                self._place_item(30 + (i % 3) * 18, 17 + (i // 3) * 18, cell)
            self._place_item(124, 35, item, note='  (result)', count=output)

        elif gui and method in ('smelting', 'blasting', 'smoking'):
            self._place_item(56, 17, source)
            self._place_item(56, 53, 'coal', note='  (or any other fuel)')
            self._place_item(116, 35, item, note='  (result)', count=output)

        elif gui and method == 'stonecutting':
            self._place_item(20, 33, source)
            self._place_item(143, 33, item, note='  (result)', count=output)

        elif gui and method == 'brewing':
            catalyst = next((i for i, n in recipe['ingredients'].items() if n == 1), None)
            bottle = next((i for i, n in recipe['ingredients'].items() if n >= 3), None)
            self._place_item(17, 17, 'blaze_powder', note='  (fuel, lasts 20 batches)')
            self._place_item(79, 17, catalyst)
            for gx, gy in ((56, 51), (79, 58), (102, 51)):
                self._place_item(gx, gy, bottle)
            if gui.width() > 352:  # result goes in the spliced output slot
                self._place_item(181, 35, item, note='  (result)', count=output)

        else:
            # No GUI texture (campfire, bottle filling, very old jars)
            canvas.create_rectangle(1, 1, 351, 165, fill=PANEL, outline=BORDER)
            canvas.create_text(176, 22, text=station, fill=FG_DIM, font=("Segoe UI", 10, "bold"))
            x = 40
            for ingredient, count in recipe['ingredients'].items():
                self._plain_slot(x, 34, ingredient, count=count)
                x += 30

        # Every GUI now displays the result in an output slot (the brewing
        # stand via the spliced-in one); only textureless layouts need the
        # external arrow
        if gui is None:
            self._draw_result(item, output)

        if method == 'brewing':
            self._grid_default_caption = f"The 3 bottles brew into {result_text} in place"
        else:
            self._grid_default_caption = f"Makes {result_text} per craft  ·  hover slots for names"
        self.grid_caption.config(text=self._grid_default_caption)

if __name__ == "__main__":
    root = tk.Tk()
    app = MinecraftRecipeCalculator(root)
    root.mainloop()
