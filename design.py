import ctypes
import os
from tkinter import font as tkfont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")
APP_FONT_FAMILY = "Vazirmatn"
FALLBACK_FONT_FAMILY = "Segoe UI"
BUNDLED_FONT_FILES = (
    os.path.join(FONT_DIR, "Vazirmatn-Variable.ttf"),
)
FR_PRIVATE = 0x10
HWND_BROADCAST = 0xFFFF
WM_FONTCHANGE = 0x001D

RTL_BIDI_CLASSES = {"R", "AL", "AN"}
LTR_BIDI_CLASSES = {"L"}
USER_COLOR_PALETTE = [
    "#229ed9",
    "#31a24c",
    "#e17055",
    "#9b59b6",
    "#f39c12",
    "#00a8a8",
    "#d8436e",
    "#607d8b",
    "#5e72e4",
    "#26a69a",
]

APP_BG = ("#dfeaf3", "#0e1621")
SURFACE_BG = ("#ffffff", "#17212b")
SURFACE_BORDER = ("#cfdce8", "#243447")
HEADER_TEXT_COLOR = ("#17212b", "#eef5fb")
STATUS_TEXT_COLOR = ("#6d7f8f", "#8ea2b4")
TELEGRAM_BLUE = ("#229ed9", "#2aabee")
TELEGRAM_BLUE_HOVER = ("#168ac0", "#229ed9")
TELEGRAM_BLUE_SOFT = ("#e5f5fd", "#20384a")
INPUT_BG = ("#f8fbfd", "#1b2836")
INPUT_BORDER = ("#d6e3ee", "#2b3e50")
INPUT_TEXT_COLOR = ("#17212b", "#edf3f8")
INPUT_SELECTION_BG = ("#c7e8f8", "#2f6f9f")
SECONDARY_BUTTON_BG = ("#edf7fc", "#203040")
SECONDARY_BUTTON_HOVER = ("#dff0fa", "#284153")

MESSAGE_BG = ("#e7f0f7", "#0e1621")
MESSAGE_INCOMING_BG = ("#ffffff", "#182533")
MESSAGE_INCOMING_BORDER = ("#d8e4ef", "#263849")
MESSAGE_OUTGOING_BG = ("#dff1ff", "#2b5278")
MESSAGE_OUTGOING_BORDER = ("#bfdff2", "#396a93")
MENU_BG = ("#ffffff", "#17212b")
MENU_FG = ("#17212b", "#edf3f8")
MENU_ACTIVE_BG = ("#e6f4fb", "#20384a")
MENU_ACTIVE_FG = ("#17212b", "#ffffff")
MESSAGE_META_COLOR = ("#229ed9", "#6ab7e8")
MESSAGE_TEXT_COLOR = ("#17212b", "#edf3f8")
MESSAGE_TIME_COLOR = ("#6f8294", "#93a4b1")
MESSAGE_SELECTED_BORDER = ("#229ed9", "#6ab7e8")
MESSAGE_UNREAD_BORDER = ("#f2b84b", "#f6c15b")
MESSAGE_UNREAD_COLOR = ("#d99113", "#ffd36a")
REPLY_PREVIEW_BG = ("#f2f8fc", "#203243")
REPLY_PREVIEW_BORDER = ("#cfe2ef", "#31485b")
REPLY_ACCENT_COLOR = ("#229ed9", "#6ab7e8")
REPLY_TEXT_COLOR = ("#2f5f7d", "#b8def4")
REPLY_MUTED_COLOR = ("#6d7f8f", "#9eb0bf")
SCROLLBAR_BUTTON = ("#bfd2df", "#2c4054")
SCROLLBAR_BUTTON_HOVER = ("#9eb8cb", "#3a5268")
SIDEBAR_BG = ("#ffffff", "#17212b")
SIDEBAR_WIDTH = 248
ROOM_ACTIVE_BG = ("#e5f5fd", "#20384a")
ROOM_HOVER_BG = ("#f2f8fc", "#1d2b3a")
ROOM_TEXT_COLOR = ("#17212b", "#edf3f8")
ROOM_MUTED_COLOR = ("#6d7f8f", "#8ea2b4")
ROOM_ACTIVE_TEXT_COLOR = ("#229ed9", "#6ab7e8")
MESSAGE_X_MARGIN = 18
MESSAGE_TOP_MARGIN = 12
MESSAGE_GAP = 8
MESSAGE_X_PADDING = 13
MESSAGE_TOP_PADDING = 9
MESSAGE_BOTTOM_PADDING = 9
MESSAGE_META_GAP = 5
MESSAGE_LINE_GAP = 5
MESSAGE_TIME_GAP = 8
MESSAGE_REPLY_GAP = 6
MESSAGE_MIN_HEIGHT = 36
MESSAGE_CARD_RADIUS = 14
MESSAGE_MAX_WIDTH = 560
MESSAGE_MAX_WIDTH_RATIO = 0.78
MESSAGE_SCROLL_PIXELS = 520
INPUT_MIN_HEIGHT = 44
INPUT_MAX_SCREEN_RATIO = 0.34
INPUT_VERTICAL_PADDING = 20
INPUT_LINE_PADDING = 14
SEND_BUTTON_MIN_HEIGHT = 44
IMAGE_BUTTON_MIN_HEIGHT = 44
IMAGE_RENDER_MAX_WIDTH = 420
IMAGE_RENDER_MAX_HEIGHT = 280
IMAGE_PLACEHOLDER_HEIGHT = 96
IMAGE_BLOCK_GAP = 7


def load_bundled_fonts():
    loaded_font_paths = []
    if os.name != "nt":
        return loaded_font_paths

    for font_path in BUNDLED_FONT_FILES:
        absolute_path = os.path.abspath(font_path)
        if not os.path.exists(absolute_path):
            continue

        try:
            added = ctypes.windll.gdi32.AddFontResourceExW(
                absolute_path,
                FR_PRIVATE,
                0,
            )
        except (AttributeError, OSError):
            continue

        if added:
            loaded_font_paths.append(absolute_path)

    if loaded_font_paths:
        try:
            ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0)
        except (AttributeError, OSError):
            pass

    return loaded_font_paths


def unload_bundled_fonts(loaded_font_paths):
    if os.name != "nt":
        return

    for font_path in loaded_font_paths:
        try:
            ctypes.windll.gdi32.RemoveFontResourceExW(font_path, FR_PRIVATE, 0)
        except (AttributeError, OSError):
            pass


def resolve_font_family(loaded_font_paths):
    if loaded_font_paths:
        return APP_FONT_FAMILY

    available_families = {
        family.casefold()
        for family in tkfont.families()
    }
    if APP_FONT_FAMILY.casefold() in available_families:
        return APP_FONT_FAMILY
    if FALLBACK_FONT_FAMILY.casefold() in available_families:
        return FALLBACK_FONT_FAMILY
    return "Arial"
