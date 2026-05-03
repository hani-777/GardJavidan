import colorsys
import io
import json
import os
import queue
import threading
import time
import unicodedata
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox

import customtkinter as ctk
import requests
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    ImageTk = None
    PIL_AVAILABLE = False

from config import *
from design import *
from github_api import GitHubApiError, GitHubIssueChat
from message_format import (
    APP_COMMENT_PATTERN,
    MESSAGE_ID_PATTERN,
    REPLY_BLOCK_PATTERN,
    format_image_markdown,
    image_preview_text,
    split_image_markdown,
)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("GitHub Chat")
        self.geometry("1040x700")
        self.minsize(860, 560)

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.theme_callback = self.on_appearance_mode_change

        self.loaded_font_paths = load_bundled_fonts()
        self.font_family = resolve_font_family(self.loaded_font_paths)

        self.config_data = self.load_or_create_config()
        self.chat = GitHubIssueChat(self.config_data)
        self.active_issue_number = positive_int(self.config_data.get("issue_number"), 1)
        self.issue_rooms = []
        self.room_buttons = {}
        self.room_states = {}
        self.rooms_loaded = False
        self.rooms_fetch_in_progress = False
        self.rooms_error = ""
        self.max_image_upload_bytes = (
            positive_int(
                self.config_data.get("max_image_upload_mb"),
                DEFAULT_MAX_IMAGE_UPLOAD_MB,
            )
            * 1024
            * 1024
        )
        self.active_poll_seconds = positive_int(
            self.config_data.get("active_poll_seconds"),
            DEFAULT_ACTIVE_POLL_SECONDS,
        )
        self.background_poll_seconds = positive_int(
            self.config_data.get("background_poll_seconds"),
            DEFAULT_BACKGROUND_POLL_SECONDS,
        )
        self.current_github_user = self.config_data.get("github_username", "").strip()
        self.last_seen_ids = set()
        self.message_groups = []
        self.message_hit_regions = []
        self.message_item_regions = []
        self.reply_hit_regions = []
        self.selected_message_group_index = None
        self.highlighted_message_index = None
        self.reply_target = None
        self.unread_by_user = {}
        self.unread_total = 0
        self.input_height = INPUT_MIN_HEIGHT
        self.input_resize_after_id = None
        self.message_total_height = 0
        self.message_canvas_width = 0
        self.fetch_in_progress = False
        self.initial_sync_done = False
        self.is_app_active = True
        self.issue_title = ""
        self.last_updated_at = None
        self.next_check_at = None
        self.rate_remaining = "?"
        self.rate_reset_at = ""
        self.status_error = ""
        self.error_backoff_seconds = 0
        self.poll_after_id = None
        self.ui_queue_after_id = None
        self.ui_queue = queue.Queue()
        self.worker_threads = []
        self.image_cache = {}
        self.image_downloads_in_progress = set()
        self.running = True

        self.build_ui()
        ctk.AppearanceModeTracker.add(self.theme_callback, self)
        self.apply_theme(redraw=False)
        self.bind_window_activity_events()
        self.start_ui_queue_pump()
        self.start_polling()

    def load_or_create_config(self):
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "w", encoding="utf-8") as file:
                json.dump(DEFAULT_CONFIG, file, indent=2, ensure_ascii=False)
            messagebox.showinfo(
                "Config created",
                "config.json created. Fill it first, then open the app again."
            )
            raise SystemExit

        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            config = json.load(file)

        config_changed = False
        for key, value in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = value
                config_changed = True

        if config_changed:
            with open(CONFIG_FILE, "w", encoding="utf-8") as file:
                json.dump(config, file, indent=2, ensure_ascii=False)

        return config

    def save_config(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as file:
            json.dump(self.config_data, file, indent=2, ensure_ascii=False)

    def chat_header_text(self):
        issue_number = self.active_issue_number
        issue_text = (
            f"{self.issue_title} (#{issue_number})"
            if self.issue_title
            else f"Issue #{issue_number}"
        )
        return (
            f"GitHub Chat - {self.config_data['owner']}/{self.config_data['repo']}"
            f" - {issue_text}"
        )

    def update_chat_header(self):
        if hasattr(self, "title_label"):
            self.title_label.configure(text=self.chat_header_text())

    @staticmethod
    def color(value):
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            if ctk.get_appearance_mode().casefold() == "dark":
                return value[1]
            return value[0]
        return value

    def on_appearance_mode_change(self, mode):
        if not getattr(self, "running", False):
            return

        self.apply_theme()

    def apply_theme(self, redraw=True):
        if not hasattr(self, "messages_canvas"):
            return

        self.configure(fg_color=APP_BG)
        self.sidebar.configure(fg_color=SIDEBAR_BG)
        self.sidebar_separator.configure(fg_color=SURFACE_BORDER)
        self.rooms_title_label.configure(text_color=HEADER_TEXT_COLOR)
        self.rooms_count_label.configure(text_color=STATUS_TEXT_COLOR)
        self.rooms_refresh_button.configure(
            fg_color=SECONDARY_BUTTON_BG,
            hover_color=SECONDARY_BUTTON_HOVER,
            text_color=TELEGRAM_BLUE,
        )
        self.rooms_list.configure(
            fg_color=SIDEBAR_BG,
            scrollbar_button_color=SCROLLBAR_BUTTON,
            scrollbar_button_hover_color=SCROLLBAR_BUTTON_HOVER,
        )
        self.header.configure(fg_color=SURFACE_BG)
        self.header_separator.configure(fg_color=SURFACE_BORDER)
        self.title_label.configure(text_color=HEADER_TEXT_COLOR)
        self.status_label.configure(text_color=STATUS_TEXT_COLOR)
        self.messages_panel.configure(fg_color=MESSAGE_BG)
        self.messages_canvas.configure(bg=self.color(MESSAGE_BG))
        self.messages_scrollbar.configure(
            fg_color=MESSAGE_BG,
            button_color=SCROLLBAR_BUTTON,
            button_hover_color=SCROLLBAR_BUTTON_HOVER,
        )
        self.composer_separator.configure(fg_color=SURFACE_BORDER)
        self.bottom.configure(fg_color=SURFACE_BG)
        self.reply_frame.configure(
            fg_color=REPLY_PREVIEW_BG,
            border_color=REPLY_PREVIEW_BORDER,
        )
        self.reply_title_label.configure(text_color=REPLY_TEXT_COLOR)
        self.reply_excerpt_label.configure(text_color=REPLY_MUTED_COLOR)
        self.reply_cancel_button.configure(
            fg_color=SECONDARY_BUTTON_BG,
            hover_color=SECONDARY_BUTTON_HOVER,
            text_color=TELEGRAM_BLUE,
        )
        self.input_box.configure(
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=INPUT_TEXT_COLOR,
        )
        self.image_button.configure(
            fg_color=SECONDARY_BUTTON_BG,
            hover_color=SECONDARY_BUTTON_HOVER,
            text_color=TELEGRAM_BLUE,
        )
        self.send_button.configure(
            fg_color=TELEGRAM_BLUE,
            hover_color=TELEGRAM_BLUE_HOVER,
            text_color="#ffffff",
        )
        self.hint.configure(text_color=STATUS_TEXT_COLOR)
        self.style_input_text_widget()
        self.refresh_message_group_styles()
        self.render_room_list()

        if redraw:
            self.render_messages(view_fraction=self.messages_canvas.yview()[0])

    def build_ui(self):
        self.configure(fg_color=APP_BG)
        self.grid_columnconfigure(0, minsize=SIDEBAR_WIDTH, weight=0)
        self.grid_columnconfigure(1, minsize=1, weight=0)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(2, weight=1)

        title_font = ctk.CTkFont(family=self.font_family, size=15, weight="bold")
        text_font = ctk.CTkFont(family=self.font_family, size=14)
        small_font = ctk.CTkFont(family=self.font_family, size=11)
        self.room_title_font = ctk.CTkFont(family=self.font_family, size=15, weight="bold")
        self.room_font = ctk.CTkFont(family=self.font_family, size=13, weight="bold")
        self.room_meta_font = ctk.CTkFont(family=self.font_family, size=11)
        self.message_font = tkfont.Font(family=self.font_family, size=11)
        self.message_meta_font = tkfont.Font(family=self.font_family, size=10, weight="bold")
        self.message_time_font = tkfont.Font(family=self.font_family, size=8)
        self.reply_font = tkfont.Font(family=self.font_family, size=8)
        self.reply_meta_font = tkfont.Font(family=self.font_family, size=8, weight="bold")

        self.sidebar = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=SIDEBAR_BG,
            border_width=0,
        )
        self.sidebar.grid(row=0, column=0, rowspan=5, padx=0, pady=0, sticky="nsew")
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(1, weight=1)

        self.rooms_header = ctk.CTkFrame(
            self.sidebar,
            corner_radius=0,
            fg_color="transparent",
        )
        self.rooms_header.grid(row=0, column=0, padx=14, pady=(13, 9), sticky="ew")
        self.rooms_header.grid_columnconfigure(0, weight=1)

        self.rooms_title_label = ctk.CTkLabel(
            self.rooms_header,
            text="Rooms",
            text_color=HEADER_TEXT_COLOR,
            font=self.room_title_font,
            anchor="w",
        )
        self.rooms_title_label.grid(row=0, column=0, sticky="ew")

        self.rooms_refresh_button = ctk.CTkButton(
            self.rooms_header,
            text="Refresh",
            width=74,
            height=30,
            corner_radius=8,
            font=ctk.CTkFont(family=self.font_family, size=12, weight="bold"),
            fg_color=SECONDARY_BUTTON_BG,
            hover_color=SECONDARY_BUTTON_HOVER,
            text_color=TELEGRAM_BLUE,
            command=self.fetch_issues_async,
        )
        self.rooms_refresh_button.grid(row=0, column=1, padx=(10, 0), sticky="e")

        self.rooms_count_label = ctk.CTkLabel(
            self.rooms_header,
            text="Loading issues...",
            text_color=STATUS_TEXT_COLOR,
            font=small_font,
            anchor="w",
        )
        self.rooms_count_label.grid(row=1, column=0, columnspan=2, sticky="ew")

        self.rooms_list = ctk.CTkScrollableFrame(
            self.sidebar,
            corner_radius=0,
            fg_color=SIDEBAR_BG,
            scrollbar_button_color=SCROLLBAR_BUTTON,
            scrollbar_button_hover_color=SCROLLBAR_BUTTON_HOVER,
        )
        self.rooms_list.grid(row=1, column=0, padx=8, pady=(0, 8), sticky="nsew")
        self.rooms_list.grid_columnconfigure(0, weight=1)

        self.sidebar_separator = ctk.CTkFrame(
            self,
            width=1,
            corner_radius=0,
            fg_color=SURFACE_BORDER,
        )
        self.sidebar_separator.grid(row=0, column=1, rowspan=5, padx=0, pady=0, sticky="ns")

        self.header = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=SURFACE_BG,
            border_width=0,
        )
        self.header.grid(row=0, column=2, padx=0, pady=0, sticky="ew")
        self.header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            self.header,
            text=self.chat_header_text(),
            font=title_font,
            text_color=HEADER_TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.title_label.grid(row=0, column=0, padx=20, pady=(13, 0), sticky="ew")

        self.status_label = ctk.CTkLabel(
            self.header,
            text="Connecting...",
            font=small_font,
            text_color=STATUS_TEXT_COLOR,
            anchor="w",
            justify="left",
        )
        self.status_label.grid(row=1, column=0, padx=20, pady=(1, 11), sticky="ew")

        self.header_separator = ctk.CTkFrame(
            self,
            height=1,
            corner_radius=0,
            fg_color=SURFACE_BORDER,
        )
        self.header_separator.grid(row=1, column=2, padx=0, pady=0, sticky="ew")

        self.messages_panel = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=MESSAGE_BG,
            border_width=0,
        )
        self.messages_panel.grid(row=2, column=2, padx=0, pady=0, sticky="nsew")
        self.messages_panel.grid_columnconfigure(0, weight=1)
        self.messages_panel.grid_rowconfigure(0, weight=1)

        self.messages_canvas = tk.Canvas(
            self.messages_panel,
            bg=self.color(MESSAGE_BG),
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=1,
        )
        self.messages_canvas.grid(row=0, column=0, padx=(12, 0), pady=(10, 10), sticky="nsew")
        self.messages_canvas.bind("<Configure>", self.on_messages_canvas_configure)
        self.bind_message_scroll(self.messages_canvas)
        self.bind_message_copy_events()

        self.messages_scrollbar = ctk.CTkScrollbar(
            self.messages_panel,
            orientation="vertical",
            command=self.messages_canvas.yview,
            fg_color=MESSAGE_BG,
            button_color=SCROLLBAR_BUTTON,
            button_hover_color=SCROLLBAR_BUTTON_HOVER,
        )
        self.messages_scrollbar.grid(row=0, column=1, padx=(6, 12), pady=14, sticky="ns")
        self.messages_canvas.configure(yscrollcommand=self.messages_scrollbar.set)

        self.composer_separator = ctk.CTkFrame(
            self,
            height=1,
            corner_radius=0,
            fg_color=SURFACE_BORDER,
        )
        self.composer_separator.grid(row=3, column=2, padx=0, pady=0, sticky="ew")

        self.bottom = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=SURFACE_BG,
            border_width=0,
        )
        self.bottom.grid(row=4, column=2, padx=0, pady=0, sticky="ew")
        self.bottom.grid_columnconfigure(0, weight=1)

        self.reply_frame = ctk.CTkFrame(
            self.bottom,
            corner_radius=10,
            fg_color=REPLY_PREVIEW_BG,
            border_color=REPLY_PREVIEW_BORDER,
            border_width=1,
        )
        self.reply_frame.grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 0), sticky="ew")
        self.reply_frame.grid_columnconfigure(0, weight=1)
        self.reply_title_label = ctk.CTkLabel(
            self.reply_frame,
            text="",
            text_color=REPLY_TEXT_COLOR,
            font=ctk.CTkFont(family=self.font_family, size=11, weight="bold"),
            anchor="w",
        )
        self.reply_title_label.grid(row=0, column=0, padx=(10, 8), pady=(6, 0), sticky="ew")
        self.reply_excerpt_label = ctk.CTkLabel(
            self.reply_frame,
            text="",
            text_color=REPLY_MUTED_COLOR,
            font=ctk.CTkFont(family=self.font_family, size=10),
            anchor="w",
        )
        self.reply_excerpt_label.grid(row=1, column=0, padx=(10, 8), pady=(0, 6), sticky="ew")
        self.reply_cancel_button = ctk.CTkButton(
            self.reply_frame,
            text="X",
            width=30,
            height=28,
            corner_radius=8,
            fg_color=SECONDARY_BUTTON_BG,
            hover_color=SECONDARY_BUTTON_HOVER,
            text_color=TELEGRAM_BLUE,
            command=self.clear_reply_target,
        )
        self.reply_cancel_button.grid(row=0, column=1, rowspan=2, padx=(0, 8), pady=6, sticky="e")
        self.reply_frame.grid_remove()

        self.input_box = ctk.CTkTextbox(
            self.bottom,
            height=INPUT_MIN_HEIGHT,
            wrap="word",
            corner_radius=12,
            font=text_font,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            border_width=1,
            text_color=INPUT_TEXT_COLOR,
        )
        self.input_box.grid(row=1, column=0, padx=(12, 8), pady=12, sticky="ew")
        self.input_box.bind("<Control-Return>", lambda event: self.send_current_message())
        self.input_box.bind("<KeyRelease>", self.schedule_input_refresh, add="+")
        self.bind_input_clipboard_events()
        self.style_input_text_widget()

        self.image_button = ctk.CTkButton(
            self.bottom,
            text="Attach",
            width=72,
            height=IMAGE_BUTTON_MIN_HEIGHT,
            corner_radius=12,
            font=ctk.CTkFont(family=self.font_family, size=13, weight="bold"),
            fg_color=SECONDARY_BUTTON_BG,
            hover_color=SECONDARY_BUTTON_HOVER,
            text_color=TELEGRAM_BLUE,
            command=self.choose_image,
        )
        self.image_button.grid(row=1, column=1, padx=(0, 8), pady=12, sticky="e")

        self.send_button = ctk.CTkButton(
            self.bottom,
            text="Send",
            width=100,
            height=SEND_BUTTON_MIN_HEIGHT,
            corner_radius=12,
            font=ctk.CTkFont(family=self.font_family, size=15, weight="bold"),
            fg_color=TELEGRAM_BLUE,
            hover_color=TELEGRAM_BLUE_HOVER,
            text_color="#ffffff",
            command=self.send_current_message,
        )
        self.send_button.grid(row=1, column=2, padx=(0, 12), pady=12, sticky="e")

        self.hint = ctk.CTkLabel(
            self,
            text="",
            text_color=STATUS_TEXT_COLOR,
            font=small_font,
            anchor="w",
            justify="left",
        )
        self.hint.grid_remove()

        self.configure_text_direction_tags()
        self.update_input_direction()
        self.resize_input_box()

    @staticmethod
    def text_direction(text, default="ltr"):
        for character in text:
            bidi_class = unicodedata.bidirectional(character)
            if bidi_class in RTL_BIDI_CLASSES:
                return "rtl"
            if bidi_class in LTR_BIDI_CLASSES:
                return "ltr"
        return default

    def configure_text_direction_tags(self):
        common_input_options = {
            "lmargin1": 8,
            "lmargin2": 8,
            "rmargin": 8,
        }

        self.input_box.tag_config("rtl", justify="right", **common_input_options)
        self.input_box.tag_config("ltr", justify="left", **common_input_options)

    def schedule_input_refresh(self, event=None):
        if self.input_resize_after_id:
            try:
                self.after_cancel(self.input_resize_after_id)
            except tk.TclError:
                pass

        self.input_resize_after_id = self.after_idle(self.refresh_input_box)
        return None

    def refresh_input_box(self):
        self.input_resize_after_id = None
        self.update_input_direction(schedule_resize=False)
        self.resize_input_box()

    def on_input_modified(self, event=None):
        text_widget = self.input_text_widget()
        try:
            if not text_widget.edit_modified():
                return None
            text_widget.edit_modified(False)
        except (tk.TclError, ValueError):
            pass

        self.schedule_input_refresh()
        return None

    def update_input_direction(self, event=None, schedule_resize=True):
        text = self.input_box.get("1.0", "end-1c")

        self.input_box.tag_remove("rtl", "1.0", "end")
        self.input_box.tag_remove("ltr", "1.0", "end")

        if not text:
            self.input_box.tag_add("rtl", "1.0", "end")
            if schedule_resize:
                self.schedule_input_refresh()
            return

        previous_direction = "rtl"
        for line_number, line in enumerate(text.split("\n"), start=1):
            direction = self.text_direction(line, default=previous_direction)
            if line.strip():
                previous_direction = direction

            start = f"{line_number}.0"
            end = f"{line_number}.0 lineend +1c"
            self.input_box.tag_add(direction, start, end)

        if schedule_resize:
            self.schedule_input_refresh()

    def estimate_input_display_lines(self, text_widget, font):
        text = text_widget.get("1.0", "end-1c")
        if not text:
            return 1

        available_width = max(
            120,
            text_widget.winfo_width() or self.input_box.winfo_width() or 360,
        )
        available_width -= 24
        line_count = 0

        for line in text.split("\n"):
            if not line:
                line_count += 1
                continue

            line_width = max(1, font.measure(line))
            line_count += max(1, int((line_width + available_width - 1) // available_width))

        return max(1, line_count)

    def resize_input_box(self):
        if not hasattr(self, "input_box"):
            return

        text_widget = self.input_text_widget()
        text_widget.update_idletasks()
        font = tkfont.Font(font=text_widget.cget("font"))
        line_count_result = text_widget.count("1.0", "end-1c", "displaylines")
        tk_line_count = max(1, int((line_count_result or (1,))[0] or 1))
        estimated_line_count = self.estimate_input_display_lines(text_widget, font)
        line_count = max(tk_line_count, estimated_line_count)
        line_height = max(18, font.metrics("linespace"))

        desired_height = (line_count * line_height) + INPUT_LINE_PADDING
        max_height = max(INPUT_MIN_HEIGHT, round(self.winfo_height() * INPUT_MAX_SCREEN_RATIO))
        new_height = max(INPUT_MIN_HEIGHT, min(desired_height, max_height))

        if abs(new_height - self.input_height) < 2:
            return

        self.input_height = new_height
        self.input_box.configure(height=new_height)
        self.send_button.configure(height=max(SEND_BUTTON_MIN_HEIGHT, new_height))

    @staticmethod
    def compact_preview_text(text, limit=120):
        text = " ".join((text or "").split())
        if len(text) > limit:
            return text[:limit - 1].rstrip() + "..."
        return text

    def update_reply_preview(self):
        if not hasattr(self, "reply_frame"):
            return

        if not self.reply_target:
            self.reply_frame.grid_remove()
            return

        author = self.reply_target.get("author", "Unknown")
        sent_at = self.reply_target.get("time", "")
        excerpt = self.reply_target.get("excerpt", "")
        self.reply_title_label.configure(text=f"Replying to {author} - {sent_at}")
        self.reply_excerpt_label.configure(text=excerpt)
        self.reply_frame.grid()

    def clear_reply_target(self):
        self.reply_target = None
        self.update_reply_preview()

    def set_reply_target(self, group_index, message_index):
        if group_index is None or group_index >= len(self.message_groups):
            return

        group = self.message_groups[group_index]
        messages = group.get("messages", [])
        if message_index is None or message_index >= len(messages):
            return

        message = messages[message_index]
        excerpt = self.compact_preview_text(image_preview_text(message.get("text", "")))
        if not excerpt:
            excerpt = "(empty message)"

        self.reply_target = {
            "author": group.get("title") or group.get("user", "Unknown"),
            "time": message.get("time", ""),
            "excerpt": excerpt,
            "message_id": message.get("message_id"),
            "comment_id": message.get("comment_id"),
        }
        self.update_reply_preview()
        self.input_text_widget().focus_set()

    def bind_input_clipboard_events(self):
        text_widget = self.input_text_widget()
        widgets = [text_widget]

        paste_events = (
            "<Control-v>",
            "<Control-V>",
            "<Control-KeyPress-v>",
            "<Control-KeyPress-V>",
            "<Shift-Insert>",
            "<Shift-KeyPress-Insert>",
            "<<Paste>>",
        )
        for widget in widgets:
            for sequence in paste_events:
                widget.bind(sequence, self.paste_into_input, add="+")
            widget.bind("<KeyRelease>", self.schedule_input_refresh, add="+")
            widget.bind("<<Modified>>", self.on_input_modified, add="+")
            widget.bind("<Control-KeyPress>", self.on_input_control_key, add="+")
            widget.bind("<Control-Insert>", self.copy_input_selection, add="+")
            widget.bind("<Button-3>", self.show_input_context_menu, add="+")

        try:
            text_widget.edit_modified(False)
        except (tk.TclError, ValueError):
            pass

        self.bind_all("<Control-KeyPress>", self.on_global_control_key, add="+")
        self.bind_all("<Control-Insert>", self.on_global_copy, add="+")
        self.bind_all("<Shift-Insert>", self.on_global_paste, add="+")

    def input_text_widget(self):
        return getattr(self.input_box, "_textbox", self.input_box)

    def style_input_text_widget(self):
        text_widget = self.input_text_widget()
        try:
            text_widget.configure(
                bg=self.color(INPUT_BG),
                fg=self.color(INPUT_TEXT_COLOR),
                insertbackground=self.color(TELEGRAM_BLUE),
                selectbackground=self.color(INPUT_SELECTION_BG),
                selectforeground=self.color(INPUT_TEXT_COLOR),
            )
        except (tk.TclError, ValueError):
            pass

    @staticmethod
    def compact_room_title(title, limit=34):
        title = " ".join((title or "").split())
        if len(title) > limit:
            return title[:limit - 1].rstrip() + "..."
        return title or "Untitled room"

    def fallback_active_room(self):
        return {
            "number": self.active_issue_number,
            "title": self.issue_title or f"Issue #{self.active_issue_number}",
            "state": "open",
            "comments": 0,
        }

    def room_list_items(self):
        if self.issue_rooms:
            return self.issue_rooms
        return [self.fallback_active_room()]

    def room_button_text(self, room):
        number = room.get("number", "?")
        state = (room.get("state") or "open").capitalize()
        comments = positive_int(room.get("comments"), 0, minimum=0)
        comment_text = f"{comments} comments" if comments != 1 else "1 comment"
        title = self.compact_room_title(room.get("title", ""))
        return f"{title}\n#{number} - {state} - {comment_text}"

    def render_room_list(self):
        if not hasattr(self, "rooms_list"):
            return

        for child in self.rooms_list.winfo_children():
            child.destroy()
        self.room_buttons = {}

        rooms = self.room_list_items()
        room_count = len(self.issue_rooms)
        if self.rooms_fetch_in_progress:
            count_text = "Refreshing issues..."
        elif self.rooms_error:
            count_text = self.rooms_error
        elif room_count == 1:
            count_text = "1 issue"
        elif room_count:
            count_text = f"{room_count} issues"
        elif self.rooms_loaded:
            count_text = "No issues found"
        else:
            count_text = "Loading issues..."
        self.rooms_count_label.configure(text=count_text)

        for row_index, room in enumerate(rooms):
            issue_number = room.get("number")
            is_active = issue_number == self.active_issue_number
            button = ctk.CTkButton(
                self.rooms_list,
                text=self.room_button_text(room),
                height=58,
                corner_radius=9,
                anchor="w",
                font=self.room_font,
                fg_color=ROOM_ACTIVE_BG if is_active else "transparent",
                hover_color=ROOM_HOVER_BG,
                text_color=ROOM_ACTIVE_TEXT_COLOR if is_active else ROOM_TEXT_COLOR,
                command=lambda number=issue_number: self.switch_issue_room(number),
            )
            button.grid(row=row_index, column=0, padx=4, pady=(0, 5), sticky="ew")
            self.room_buttons[issue_number] = button

    def is_input_widget(self, widget):
        return widget in (self.input_box, self.input_text_widget())

    @staticmethod
    def is_key(event, key, keycode):
        return (
            getattr(event, "keysym", "").lower() == key
            or getattr(event, "char", "").lower() == key
            or getattr(event, "keycode", None) == keycode
        )

    def on_input_control_key(self, event):
        if self.is_key(event, "v", 86):
            return self.paste_into_input(event)

        if self.is_key(event, "c", 67):
            return self.copy_input_selection(event)

        return None

    def on_global_control_key(self, event):
        if self.is_key(event, "v", 86):
            return self.on_global_paste(event)

        if self.is_key(event, "c", 67):
            return self.on_global_copy(event)

        return None

    def on_global_paste(self, event=None):
        if event is not None and self.is_input_widget(event.widget):
            return self.paste_into_input(event)

        self.input_text_widget().focus_set()
        return self.paste_into_input()

    def on_global_copy(self, event=None):
        if event is not None and self.is_input_widget(event.widget):
            return self.copy_input_selection(event)

        return self.copy_selected_message_group(event)

    def dark_menu(self, parent):
        return tk.Menu(
            parent,
            tearoff=0,
            bg=self.color(MENU_BG),
            fg=self.color(MENU_FG),
            activebackground=self.color(MENU_ACTIVE_BG),
            activeforeground=self.color(MENU_ACTIVE_FG),
            activeborderwidth=0,
            borderwidth=0,
            relief="flat",
        )

    def paste_into_input(self, event=None):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"

        if not text:
            return "break"

        widget = (
            event.widget
            if event is not None and self.is_input_widget(event.widget)
            else self.input_text_widget()
        )
        widget.focus_set()
        try:
            widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass

        widget.insert("insert", text)
        self.schedule_input_refresh()
        return "break"

    def copy_input_selection(self, event=None):
        try:
            text = self.input_box.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"

        if text:
            self.copy_text_to_clipboard(text)
        return "break"

    def cut_input_selection(self, event=None):
        try:
            text = self.input_box.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"

        if text:
            self.copy_text_to_clipboard(text)
            self.input_box.delete("sel.first", "sel.last")
            self.update_input_direction()
        return "break"

    def show_input_context_menu(self, event):
        menu = self.dark_menu(self)
        menu.add_command(label="Cut", command=self.cut_input_selection)
        menu.add_command(label="Copy", command=self.copy_input_selection)
        menu.add_command(label="Paste", command=self.paste_into_input)
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()
        return "break"

    def choose_image(self):
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Choose image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return

        self.send_image_message(file_path)

    def set_send_controls(self, enabled, send_text="Send", image_text="Attach"):
        state = "normal" if enabled else "disabled"
        self.send_button.configure(state=state, text=send_text)
        self.image_button.configure(state=state, text=image_text)

    def image_upload_message(self, file_path, image_url, caption):
        image_markdown = format_image_markdown(image_url, os.path.basename(file_path))
        if caption:
            return f"{caption}\n\n{image_markdown}"
        return image_markdown

    def send_image_message(self, file_path):
        try:
            file_size = os.path.getsize(file_path)
        except OSError as error:
            messagebox.showerror("Image Failed", f"Could not read image: {error}")
            return

        if file_size > self.max_image_upload_bytes:
            max_size_mb = self.max_image_upload_bytes / (1024 * 1024)
            messagebox.showerror(
                "Image Too Large",
                f"Choose an image smaller than {max_size_mb:.0f} MB.",
            )
            return

        caption = self.input_box.get("1.0", "end").strip()
        reply = self.reply_target.copy() if self.reply_target else None
        issue_number = self.active_issue_number
        self.set_send_controls(False, send_text="Sending...", image_text="Uploading...")

        def worker():
            try:
                with open(file_path, "rb") as image_file:
                    content = image_file.read()

                image_info, upload_rate_info = self.chat.upload_chat_image(
                    os.path.basename(file_path),
                    content,
                )
                image_url = image_info.get("url")
                if not image_url:
                    raise GitHubApiError("GitHub did not return an image URL.")

                message = self.image_upload_message(file_path, image_url, caption)
                data, rate_info = self.chat.send_message(
                    message,
                    reply=reply,
                    issue_number=issue_number,
                )
                self.safe_after(lambda: self.update_rate_info(upload_rate_info))
                self.safe_after(
                    lambda: self.handle_send_success(rate_info, issue_number)
                )
            except Exception as error:
                self.safe_after(lambda error=error: self.handle_send_error(error))
            finally:
                self.safe_after(lambda: self.set_send_controls(True))

        self.start_worker_thread(worker, "github-image-send")

    @staticmethod
    def hex_to_rgb(color):
        color = color.lstrip("#")
        return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))

    @staticmethod
    def rgb_to_hex(rgb):
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    @classmethod
    def blend_hex(cls, color, target, amount):
        red, green, blue = cls.hex_to_rgb(color)
        target_red, target_green, target_blue = cls.hex_to_rgb(target)
        blended = (
            round(red + (target_red - red) * amount),
            round(green + (target_green - green) * amount),
            round(blue + (target_blue - blue) * amount),
        )
        return cls.rgb_to_hex(blended)

    @staticmethod
    def generated_muted_color(index):
        hue = (index * 0.61803398875) % 1.0
        red, green, blue = colorsys.hls_to_rgb(hue, 0.45, 0.58)
        return "#{:02x}{:02x}{:02x}".format(
            round(red * 255),
            round(green * 255),
            round(blue * 255),
        )

    @staticmethod
    def stored_color_value(color_config):
        if isinstance(color_config, dict):
            return color_config.get("background") or color_config.get("color")
        return color_config

    def next_user_color(self):
        colors = self.config_data.setdefault("user_colors", {})
        used_colors = {
            self.stored_color_value(color_config)
            for color_config in colors.values()
            if self.stored_color_value(color_config)
        }

        for color in USER_COLOR_PALETTE:
            if color not in used_colors:
                return color

        index = len(used_colors)
        while True:
            color = self.generated_muted_color(index)
            if color not in used_colors:
                return color
            index += 1

    @staticmethod
    def palette_color_for_user(user):
        user_key = user or ""
        if not USER_COLOR_PALETTE:
            return "#229ed9"

        score = sum((index + 1) * ord(character) for index, character in enumerate(user_key))
        return USER_COLOR_PALETTE[score % len(USER_COLOR_PALETTE)]

    @classmethod
    def readable_accent_color(cls, color, user):
        try:
            red, green, blue = cls.hex_to_rgb(color)
        except (TypeError, ValueError):
            return cls.palette_color_for_user(user)

        luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)
        if luminance < 78 or luminance > 210:
            return cls.palette_color_for_user(user)

        return color

    def user_accent_color(self, user):
        colors = self.config_data.setdefault("user_colors", {})
        color = self.stored_color_value(colors.get(user))

        if not color:
            color = self.next_user_color()
            colors[user] = color
            self.save_config()

        return self.readable_accent_color(color, user)

    def is_current_user(self, user):
        return (
            bool(user)
            and bool(self.current_github_user)
            and user.casefold() == self.current_github_user.casefold()
        )

    def user_card_colors(self, user):
        if self.is_current_user(user):
            return self.color(MESSAGE_OUTGOING_BG), self.color(MESSAGE_OUTGOING_BORDER)

        return self.color(MESSAGE_INCOMING_BG), self.color(MESSAGE_INCOMING_BORDER)

    def refresh_message_group_styles(self):
        for group in self.message_groups:
            user = group.get("user", "")
            background, border = self.user_card_colors(user)
            is_outgoing = self.is_current_user(user)
            group["background"] = background
            group["border"] = border
            group["title_color"] = (
                self.color(TELEGRAM_BLUE) if is_outgoing else self.user_accent_color(user)
            )
            group["outgoing"] = is_outgoing

    @staticmethod
    def normalize_issue_room(issue):
        number = positive_int(issue.get("number"), 0, minimum=0)
        if number <= 0:
            return None

        title = " ".join((issue.get("title") or f"Issue #{number}").split())
        return {
            "number": number,
            "title": title or f"Issue #{number}",
            "state": issue.get("state") or "open",
            "comments": positive_int(issue.get("comments"), 0, minimum=0),
            "updated_at": issue.get("updated_at") or "",
        }

    def room_title_for_issue(self, issue_number):
        for room in self.issue_rooms:
            if room.get("number") == issue_number:
                return room.get("title", "")
        state = self.room_states.get(issue_number, {})
        return state.get("issue_title", "")

    def save_current_room_state(self):
        self.room_states[self.active_issue_number] = {
            "issue_title": self.issue_title,
            "last_seen_ids": self.last_seen_ids,
            "message_groups": self.message_groups,
            "selected_message_group_index": self.selected_message_group_index,
            "highlighted_message_index": self.highlighted_message_index,
            "unread_by_user": self.unread_by_user,
            "unread_total": self.unread_total,
            "initial_sync_done": self.initial_sync_done,
            "last_updated_at": self.last_updated_at,
            "status_error": self.status_error,
        }

    def load_room_state(self, issue_number):
        state = self.room_states.get(issue_number)
        if not state:
            return {
                "issue_title": self.room_title_for_issue(issue_number),
                "last_seen_ids": set(),
                "message_groups": [],
                "selected_message_group_index": None,
                "highlighted_message_index": None,
                "unread_by_user": {},
                "unread_total": 0,
                "initial_sync_done": False,
                "last_updated_at": None,
                "status_error": "",
            }
        return state

    def switch_issue_room(self, issue_number):
        issue_number = positive_int(issue_number, self.active_issue_number)
        if issue_number == self.active_issue_number:
            if not self.fetch_in_progress:
                self.refresh_messages()
            return

        self.save_current_room_state()
        self.cancel_scheduled_fetch()
        self.fetch_in_progress = False
        self.error_backoff_seconds = 0
        self.next_check_at = None
        self.active_issue_number = issue_number
        self.config_data["issue_number"] = issue_number
        self.save_config()
        self.chat.set_issue_number(issue_number)

        state = self.load_room_state(issue_number)
        self.issue_title = state["issue_title"]
        self.last_seen_ids = state["last_seen_ids"]
        self.message_groups = state["message_groups"]
        self.selected_message_group_index = state["selected_message_group_index"]
        self.highlighted_message_index = state["highlighted_message_index"]
        self.unread_by_user = state["unread_by_user"]
        self.unread_total = state["unread_total"]
        self.initial_sync_done = state["initial_sync_done"]
        self.last_updated_at = state["last_updated_at"]
        self.status_error = state["status_error"]
        self.reply_target = None

        self.update_chat_header()
        self.update_window_title()
        self.update_reply_preview()
        self.render_room_list()
        self.render_messages(scroll_to_bottom=True)
        self.update_status()
        self.fetch_issue_title_async(issue_number)
        self.schedule_next_fetch(delay_seconds=0)

    def on_messages_canvas_configure(self, event=None):
        new_width = self.messages_canvas.winfo_width()
        if abs(new_width - self.message_canvas_width) < 2:
            return

        yview = self.messages_canvas.yview()
        was_at_bottom = yview[1] >= 0.99
        self.render_messages(
            scroll_to_bottom=was_at_bottom,
            view_fraction=None if was_at_bottom else yview[0],
        )

    def scroll_messages_to_bottom(self):
        self.messages_canvas.yview_moveto(1.0)

    def on_messages_mousewheel(self, event):
        if self.message_total_height <= self.messages_canvas.winfo_height():
            return "break"

        if getattr(event, "num", None) == 4:
            pixels = -MESSAGE_SCROLL_PIXELS
        elif getattr(event, "num", None) == 5:
            pixels = MESSAGE_SCROLL_PIXELS
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return "break"
            if abs(delta) < 120:
                pixels = round(delta * -2.0)
            else:
                pixels = round((delta / 120) * -MESSAGE_SCROLL_PIXELS)

        self.messages_canvas.yview_scroll(pixels, "units")
        return "break"

    def bind_message_scroll(self, widget):
        widget.bind("<MouseWheel>", self.on_messages_mousewheel, add="+")
        widget.bind("<Button-4>", self.on_messages_mousewheel, add="+")
        widget.bind("<Button-5>", self.on_messages_mousewheel, add="+")

    def bind_message_copy_events(self):
        self.messages_canvas.bind("<Button-1>", self.on_message_click, add="+")
        self.messages_canvas.bind("<Double-Button-1>", self.copy_group_at_event, add="+")
        self.messages_canvas.bind("<Button-3>", self.show_message_copy_menu, add="+")
        self.messages_canvas.bind("<Control-c>", self.copy_selected_message_group, add="+")
        self.messages_canvas.bind("<Control-C>", self.copy_selected_message_group, add="+")
        self.messages_canvas.bind("<Motion>", self.on_messages_motion, add="+")

    def message_group_index_at_event(self, event):
        x = self.messages_canvas.canvasx(event.x)
        y = self.messages_canvas.canvasy(event.y)

        for region in self.message_hit_regions:
            left, top, right, bottom = region["bounds"]
            if left <= x <= right and top <= y <= bottom:
                return region["index"]

        return None

    def message_index_at_event(self, event):
        x = self.messages_canvas.canvasx(event.x)
        y = self.messages_canvas.canvasy(event.y)

        for region in self.message_item_regions:
            left, top, right, bottom = region["bounds"]
            if left <= x <= right and top <= y <= bottom:
                return region["group_index"], region["message_index"]

        group_index = self.message_group_index_at_event(event)
        if group_index is None:
            return None, None

        messages = self.message_groups[group_index].get("messages", [])
        if not messages:
            return group_index, None

        return group_index, len(messages) - 1

    def reply_region_at_event(self, event):
        x = self.messages_canvas.canvasx(event.x)
        y = self.messages_canvas.canvasy(event.y)

        for region in reversed(self.reply_hit_regions):
            left, top, right, bottom = region["bounds"]
            if left <= x <= right and top <= y <= bottom:
                return region

        return None

    def on_messages_motion(self, event):
        cursor = "hand2" if self.reply_region_at_event(event) else ""
        if self.messages_canvas.cget("cursor") != cursor:
            self.messages_canvas.configure(cursor=cursor)

    def select_message_group(self, group_index, mark_read=True):
        self.highlighted_message_index = None
        self.selected_message_group_index = group_index
        if mark_read and group_index is not None:
            self.mark_message_group_read(group_index, render=False)

        view_fraction = self.messages_canvas.yview()[0]
        self.render_messages(view_fraction=view_fraction)

    def on_message_click(self, event):
        self.messages_canvas.focus_set()
        reply_region = self.reply_region_at_event(event)
        if reply_region:
            return self.jump_to_replied_message(reply_region)

        group_index = self.message_group_index_at_event(event)
        if group_index is None:
            self.select_message_group(None)
            return None

        self.select_message_group(group_index)
        return "break"

    @staticmethod
    def normalize_reply_match(value):
        return " ".join((value or "").split()).casefold()

    def iter_message_positions(self):
        for group_index, group in enumerate(self.message_groups):
            for message_index, message in enumerate(group.get("messages", [])):
                yield group_index, message_index, group, message

    def normalized_message_times(self, message):
        values = {message.get("time", "")}
        sent_at = message.get("sent_at")
        if sent_at:
            values.add(sent_at.strftime("%H:%M"))
            values.add(sent_at.strftime("%Y-%m-%d %H:%M"))

        return {
            self.normalize_reply_match(value)
            for value in values
            if value
        }

    def find_replied_message(self, reply, source_group_index=None, source_message_index=None):
        target_message_id = str(reply.get("message_id") or "").strip()
        target_comment_id = str(reply.get("comment_id") or "").strip()
        source_position = (
            (source_group_index, source_message_index)
            if source_group_index is not None and source_message_index is not None
            else None
        )
        positions = list(self.iter_message_positions())

        if target_message_id:
            for group_index, message_index, _group, message in positions:
                if str(message.get("message_id") or "") == target_message_id:
                    return group_index, message_index

        if target_comment_id:
            for group_index, message_index, _group, message in positions:
                if str(message.get("comment_id") or "") == target_comment_id:
                    return group_index, message_index

        reply_author = self.normalize_reply_match(reply.get("author"))
        reply_time = self.normalize_reply_match(reply.get("time"))
        reply_excerpt = self.normalize_reply_match(reply.get("excerpt"))

        def is_source(group_index, message_index):
            return source_position == (group_index, message_index)

        def is_before_source(group_index, message_index):
            return source_position is not None and (group_index, message_index) < source_position

        def exact_match(group, message):
            author = self.normalize_reply_match(group.get("title") or group.get("user"))
            excerpt = self.normalize_reply_match(self.compact_preview_text(message.get("text", "")))
            if reply_excerpt == self.normalize_reply_match("(empty message)"):
                excerpt = self.normalize_reply_match("(empty message)") if not message.get("text") else excerpt

            return (
                (not reply_author or author == reply_author)
                and (not reply_time or reply_time in self.normalized_message_times(message))
                and (not reply_excerpt or excerpt == reply_excerpt)
            )

        preferred_positions = [
            position
            for position in reversed(positions)
            if is_before_source(position[0], position[1])
        ]
        fallback_positions = [
            position
            for position in positions
            if not is_source(position[0], position[1])
        ]

        for search_positions in (preferred_positions, fallback_positions):
            for group_index, message_index, group, message in search_positions:
                if exact_match(group, message):
                    return group_index, message_index

        best_match = None
        best_score = 0
        for group_index, message_index, group, message in fallback_positions:
            author = self.normalize_reply_match(group.get("title") or group.get("user"))
            text = self.normalize_reply_match(message.get("text"))
            excerpt = self.normalize_reply_match(self.compact_preview_text(message.get("text", "")))
            score = 0

            if reply_author and author == reply_author:
                score += 3
            if reply_time and reply_time in self.normalized_message_times(message):
                score += 2
            if reply_excerpt and excerpt == reply_excerpt:
                score += 4
            elif reply_excerpt and (reply_excerpt in text or text in reply_excerpt):
                score += 2
            if is_before_source(group_index, message_index):
                score += 1

            if score > best_score:
                best_match = (group_index, message_index)
                best_score = score

        return best_match if best_score >= 5 else None

    def message_region(self, group_index, message_index):
        for region in self.message_item_regions:
            if (
                region["group_index"] == group_index
                and region["message_index"] == message_index
            ):
                return region

        return None

    def scroll_to_message(self, group_index, message_index):
        region = self.message_region(group_index, message_index)
        if not region:
            return

        _left, top, _right, bottom = region["bounds"]
        visible_height = max(1, self.messages_canvas.winfo_height())
        scrollable_height = max(1, self.message_total_height - visible_height)
        target_top = max(0, top - (visible_height * 0.28))
        target_bottom = max(0, bottom - (visible_height * 0.72))
        target_y = min(target_top, target_bottom) if bottom - top < visible_height else target_top
        fraction = max(0.0, min(1.0, target_y / scrollable_height))
        self.messages_canvas.yview_moveto(fraction)

    def jump_to_replied_message(self, reply_region):
        self.messages_canvas.configure(cursor="")
        target = self.find_replied_message(
            reply_region["reply"],
            reply_region.get("group_index"),
            reply_region.get("message_index"),
        )
        if not target:
            self.status_error = "Original reply message was not found in loaded comments."
            self.update_status()
            try:
                self.bell()
            except tk.TclError:
                pass
            return "break"

        group_index, message_index = target
        self.status_error = ""
        self.selected_message_group_index = group_index
        self.highlighted_message_index = (group_index, message_index)
        self.mark_message_group_read(group_index, render=False)
        self.render_messages()
        self.scroll_to_message(group_index, message_index)
        self.update_status()
        return "break"

    @staticmethod
    def format_message_group_for_clipboard(group_data):
        lines = [group_data["title"]]
        for message_data in group_data["messages"]:
            timestamp = message_data.get("time", "")
            text = message_data.get("text", "")
            reply = message_data.get("reply")
            if reply:
                reply_author = reply.get("author", "Unknown")
                reply_time = reply.get("time", "")
                reply_excerpt = reply.get("excerpt", "")
                lines.append(
                    f"Reply to {reply_author} [{reply_time}]: {reply_excerpt}"
                    if reply_time
                    else f"Reply to {reply_author}: {reply_excerpt}"
                )
            lines.append(f"[{timestamp}] {text}" if timestamp else text)

        return "\n".join(lines)

    def copy_text_to_clipboard(self, text):
        if not text:
            return

        self.clipboard_clear()
        self.clipboard_append(text)

    def copy_selected_message_group(self, event=None):
        group_index = self.selected_message_group_index
        if group_index is None or group_index >= len(self.message_groups):
            return "break"

        text = self.format_message_group_for_clipboard(self.message_groups[group_index])
        self.copy_text_to_clipboard(text)
        return "break"

    def copy_group_at_event(self, event):
        group_index = self.message_group_index_at_event(event)
        if group_index is None:
            return None

        self.select_message_group(group_index)
        return self.copy_selected_message_group(event)

    def reply_to_message_at_event(self, event):
        group_index, message_index = self.message_index_at_event(event)
        if message_index is None:
            return None

        self.select_message_group(group_index, mark_read=False)
        self.set_reply_target(group_index, message_index)
        return "break"

    def copy_message_at_event(self, event):
        group_index, message_index = self.message_index_at_event(event)
        if message_index is None:
            return None

        group = self.message_groups[group_index]
        message = group["messages"][message_index]
        timestamp = message.get("time", "")
        text = message.get("text", "")
        reply = message.get("reply")
        lines = [f"{group['title']} [{timestamp}]" if timestamp else group["title"]]
        if reply:
            reply_author = reply.get("author", "Unknown")
            reply_time = reply.get("time", "")
            reply_excerpt = reply.get("excerpt", "")
            lines.append(
                f"Reply to {reply_author} [{reply_time}]: {reply_excerpt}"
                if reply_time
                else f"Reply to {reply_author}: {reply_excerpt}"
            )
        lines.append(text)
        clipboard_text = "\n".join(lines)
        self.copy_text_to_clipboard(clipboard_text)
        return "break"

    def copy_all_message_groups(self):
        text = "\n\n".join(
            self.format_message_group_for_clipboard(group)
            for group in self.message_groups
        )
        self.copy_text_to_clipboard(text)

    def unread_summary(self):
        if self.unread_total <= 0:
            return ""

        parts = [
            f"{user}: {count}"
            for user, count in self.unread_by_user.items()
            if count > 0
        ]
        return f"{self.unread_total} unread ({', '.join(parts)})"

    def rebuild_unread_counts(self):
        unread_by_user = {}
        unread_total = 0

        for group in self.message_groups:
            unread_count = sum(
                1 for message in group["messages"] if message.get("unread")
            )
            group["unread_count"] = unread_count
            if unread_count:
                user = group["user"]
                unread_by_user[user] = unread_by_user.get(user, 0) + unread_count
                unread_total += unread_count

        self.unread_by_user = unread_by_user
        self.unread_total = unread_total
        self.render_room_list()

    def update_window_title(self):
        prefix = f"({self.unread_total}) " if self.unread_total else ""
        self.title(f"{prefix}GitHub Chat")

    def mark_message_group_read(self, group_index, render=True):
        if group_index is None or group_index >= len(self.message_groups):
            return

        group = self.message_groups[group_index]
        if not group.get("unread_count"):
            return

        for message in group["messages"]:
            message["unread"] = False

        self.rebuild_unread_counts()
        self.update_window_title()
        self.update_status()
        if render:
            view_fraction = self.messages_canvas.yview()[0]
            self.render_messages(view_fraction=view_fraction)

    def show_message_copy_menu(self, event):
        group_index = self.message_group_index_at_event(event)
        if group_index is None:
            return None

        self.select_message_group(group_index, mark_read=False)
        reply_group_index, reply_message_index = self.message_index_at_event(event)

        menu = self.dark_menu(self)
        if reply_message_index is not None:
            menu.add_command(
                label="Reply",
                command=lambda: self.set_reply_target(reply_group_index, reply_message_index),
            )
            menu.add_command(label="Copy message", command=lambda: self.copy_message_at_event(event))
            menu.add_separator()
        menu.add_command(label="Copy chat", command=self.copy_selected_message_group)
        menu.add_command(label="Copy all chats", command=self.copy_all_message_groups)
        if self.message_groups[group_index].get("unread_count"):
            menu.add_separator()
            menu.add_command(
                label="Mark as read",
                command=lambda: self.mark_message_group_read(group_index),
            )
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()
        return "break"

    @staticmethod
    def rounded_rectangle_points(left, top, right, bottom, radius):
        radius = min(radius, (right - left) / 2, (bottom - top) / 2)
        return [
            left + radius, top,
            right - radius, top,
            right, top,
            right, top + radius,
            right, bottom - radius,
            right, bottom,
            right - radius, bottom,
            left + radius, bottom,
            left, bottom,
            left, bottom - radius,
            left, top + radius,
            left, top,
        ]

    def draw_rounded_rectangle(self, left, top, right, bottom, radius, fill, outline, width=1):
        return self.messages_canvas.create_polygon(
            self.rounded_rectangle_points(left, top, right, bottom, radius),
            smooth=True,
            splinesteps=12,
            fill=self.color(fill) if fill else fill,
            outline=self.color(outline) if outline else outline,
            width=width,
        )

    @staticmethod
    def parse_message_datetime(value):
        if not value:
            return None

        value = value.strip()
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None

        if parsed is None:
            for date_format in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(value, date_format)
                    break
                except ValueError:
                    continue

        if parsed and parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)

        return parsed

    @staticmethod
    def display_message_time(sent_at, previous_sent_at):
        if not sent_at:
            return ""

        if previous_sent_at and previous_sent_at.date() != sent_at.date():
            return sent_at.strftime("%Y-%m-%d %H:%M")

        return sent_at.strftime("%H:%M")

    @staticmethod
    def parse_reply_body(message):
        parsed_reply = REPLY_BLOCK_PATTERN.match(message.strip())
        if not parsed_reply:
            return None, message

        reply = {
            "author": parsed_reply.group("author").strip(),
            "time": parsed_reply.group("time").strip(),
            "excerpt": parsed_reply.group("excerpt").strip(),
            "message_id": (parsed_reply.group("message_id") or "").strip(),
            "comment_id": (parsed_reply.group("comment_id") or "").strip(),
        }
        return reply, parsed_reply.group("message").strip()

    @staticmethod
    def parse_message_metadata(message):
        parsed_metadata = MESSAGE_ID_PATTERN.match(message.strip())
        if not parsed_metadata:
            return "", message

        return (
            parsed_metadata.group("message_id").strip(),
            parsed_metadata.group("message").strip(),
        )

    def last_message_datetime(self):
        if not self.message_groups:
            return None

        messages = self.message_groups[-1]["messages"]
        if not messages:
            return None

        return messages[-1]["sent_at"]

    def start_image_download(self, url):
        if url in self.image_downloads_in_progress:
            return

        if not PIL_AVAILABLE:
            self.image_cache[url] = {"status": "error"}
            return

        self.image_downloads_in_progress.add(url)
        self.image_cache[url] = {"status": "loading"}

        def worker():
            try:
                response = requests.get(
                    url,
                    headers=self.chat.headers,
                    timeout=self.chat.timeout,
                )
                response.raise_for_status()
                image = Image.open(io.BytesIO(response.content))
                image.thumbnail(
                    (IMAGE_RENDER_MAX_WIDTH, IMAGE_RENDER_MAX_HEIGHT),
                    getattr(Image, "Resampling", Image).LANCZOS,
                )
                image = image.copy()
                self.safe_after(
                    lambda url=url, image=image: self.handle_image_download_success(url, image)
                )
            except Exception:
                self.safe_after(lambda url=url: self.handle_image_download_error(url))

        self.start_worker_thread(worker, "chat-image-download")

    def handle_image_download_success(self, url, image):
        if not self.running:
            return

        self.image_downloads_in_progress.discard(url)
        photo = ImageTk.PhotoImage(image)
        self.image_cache[url] = {
            "status": "ready",
            "photo": photo,
            "size": image.size,
        }
        if hasattr(self, "messages_canvas"):
            view_fraction = self.messages_canvas.yview()[0]
            self.render_messages(view_fraction=view_fraction)

    def handle_image_download_error(self, url):
        if not self.running:
            return

        self.image_downloads_in_progress.discard(url)
        self.image_cache[url] = {"status": "error"}
        if hasattr(self, "messages_canvas"):
            view_fraction = self.messages_canvas.yview()[0]
            self.render_messages(view_fraction=view_fraction)

    def draw_reply_preview(
        self,
        reply,
        top,
        left,
        right,
        content_width,
        group_index=None,
        message_index=None,
    ):
        if not reply:
            return top

        canvas = self.messages_canvas
        header = f"Reply to {reply.get('author', 'Unknown')} - {reply.get('time', '')}"
        excerpt = reply.get("excerpt", "")
        preview_left = left
        preview_right = right
        preview_width = max(120, content_width - 24)

        header_item = canvas.create_text(
            preview_left + 12,
            top + 5,
            anchor="nw",
            text=header,
            font=self.reply_meta_font,
            fill=self.color(REPLY_TEXT_COLOR),
            width=preview_width,
        )
        header_bbox = canvas.bbox(header_item) or (preview_left + 12, top + 5, preview_right, top + 17)
        excerpt_item = canvas.create_text(
            preview_left + 12,
            header_bbox[3] + 1,
            anchor="nw",
            text=excerpt,
            font=self.reply_font,
            fill=self.color(REPLY_MUTED_COLOR),
            width=preview_width,
        )
        excerpt_bbox = canvas.bbox(excerpt_item) or (
            preview_left + 12,
            header_bbox[3] + 1,
            preview_right,
            header_bbox[3] + 14,
        )
        preview_bottom = excerpt_bbox[3] + 6
        preview_bg = self.draw_rounded_rectangle(
            preview_left,
            top,
            preview_right,
            preview_bottom,
            7,
            self.color(REPLY_PREVIEW_BG),
            self.color(REPLY_PREVIEW_BORDER),
        )
        canvas.tag_lower(preview_bg, header_item)
        canvas.create_line(
            preview_left + 6,
            top + 6,
            preview_left + 6,
            preview_bottom - 6,
            fill=self.color(REPLY_ACCENT_COLOR),
            width=2,
        )
        if group_index is not None and message_index is not None:
            self.reply_hit_regions.append(
                {
                    "group_index": group_index,
                    "message_index": message_index,
                    "reply": reply,
                    "bounds": (preview_left, top, preview_right, preview_bottom),
                }
            )
        return preview_bottom + MESSAGE_REPLY_GAP

    def draw_text_block(self, text, top, left, right, content_width, default_direction):
        text = text.strip()
        if not text:
            return top, default_direction

        direction = self.text_direction(text, default=default_direction)
        is_rtl = direction == "rtl"
        text_item = self.messages_canvas.create_text(
            right if is_rtl else left,
            top,
            anchor="ne" if is_rtl else "nw",
            justify="right" if is_rtl else "left",
            text=text,
            font=self.message_font,
            fill=self.color(MESSAGE_TEXT_COLOR),
            width=content_width,
        )
        text_bbox = self.messages_canvas.bbox(text_item) or (left, top, right, top + 10)
        return text_bbox[3] + IMAGE_BLOCK_GAP, direction

    def draw_image_placeholder(self, top, left, right, text):
        bottom = top + IMAGE_PLACEHOLDER_HEIGHT
        self.draw_rounded_rectangle(
            left,
            top,
            right,
            bottom,
            8,
            self.color(REPLY_PREVIEW_BG),
            self.color(REPLY_PREVIEW_BORDER),
        )
        self.messages_canvas.create_text(
            (left + right) / 2,
            (top + bottom) / 2,
            anchor="center",
            text=text,
            font=self.reply_font,
            fill=self.color(REPLY_MUTED_COLOR),
            width=max(120, right - left - 24),
        )
        return bottom + IMAGE_BLOCK_GAP

    def draw_image_block(self, image_info, top, left, right, content_width, direction):
        url = image_info.get("url", "")
        cache = self.image_cache.get(url)
        if not cache:
            self.start_image_download(url)
            cache = self.image_cache.get(url, {"status": "loading"})

        if cache.get("status") != "ready":
            text = "Image unavailable" if cache.get("status") == "error" else "Loading image..."
            placeholder_width = min(content_width, IMAGE_RENDER_MAX_WIDTH)
            placeholder_left = right - placeholder_width if direction == "rtl" else left
            return self.draw_image_placeholder(
                top,
                placeholder_left,
                placeholder_left + placeholder_width,
                text,
            )

        photo = cache["photo"]
        width, height = cache["size"]
        image_left = right - width if direction == "rtl" else left
        image_right = image_left + width
        image_bottom = top + height
        self.draw_rounded_rectangle(
            image_left - 1,
            top - 1,
            image_right + 1,
            image_bottom + 1,
            8,
            "",
            self.color(REPLY_PREVIEW_BORDER),
        )
        self.messages_canvas.create_image(
            image_left,
            top,
            anchor="nw",
            image=photo,
        )
        return image_bottom + IMAGE_BLOCK_GAP

    def draw_message_blocks(self, text, top, left, right, content_width):
        bottom = top
        direction = self.text_direction(text, default="rtl")
        for kind, value in split_image_markdown(text):
            if kind == "image":
                bottom = self.draw_image_block(value, bottom, left, right, content_width, direction)
            else:
                bottom, direction = self.draw_text_block(
                    value,
                    bottom,
                    left,
                    right,
                    content_width,
                    direction,
                )

        return max(top, bottom - IMAGE_BLOCK_GAP)

    def draw_group_message(self, group_index, message_index, message_data, top, left, right, content_width):
        canvas = self.messages_canvas
        text = message_data["text"]
        timestamp = message_data["time"]
        message_region_top = top

        top = self.draw_reply_preview(
            message_data.get("reply"),
            top,
            left,
            right,
            content_width,
            group_index,
            message_index,
        )

        if not text:
            if self.highlighted_message_index == (group_index, message_index):
                self.draw_rounded_rectangle(
                    left - 5,
                    message_region_top - 3,
                    right + 5,
                    top + 2,
                    6,
                    "",
                    self.color(REPLY_ACCENT_COLOR),
                    width=2,
                )
            self.message_item_regions.append(
                {
                    "group_index": group_index,
                    "message_index": message_index,
                    "bounds": (left, message_region_top, right, top),
                }
            )
            return top

        message_parts = split_image_markdown(text)
        if any(kind == "image" for kind, _value in message_parts):
            content_bottom = self.draw_message_blocks(text, top, left, right, content_width)
            if timestamp:
                time_item = canvas.create_text(
                    right,
                    content_bottom + 2,
                    anchor="ne",
                    text=timestamp,
                    font=self.message_time_font,
                    fill=self.color(MESSAGE_TIME_COLOR),
                )
                time_bbox = canvas.bbox(time_item)
                if time_bbox:
                    content_bottom = time_bbox[3]

            if self.highlighted_message_index == (group_index, message_index):
                self.draw_rounded_rectangle(
                    left - 5,
                    message_region_top - 3,
                    right + 5,
                    content_bottom + 4,
                    6,
                    "",
                    self.color(REPLY_ACCENT_COLOR),
                    width=2,
                )

            self.message_item_regions.append(
                {
                    "group_index": group_index,
                    "message_index": message_index,
                    "bounds": (left, message_region_top, right, content_bottom),
                }
            )
            return content_bottom + MESSAGE_LINE_GAP

        direction = self.text_direction(text, default="rtl")
        is_rtl = direction == "rtl"
        text_item = canvas.create_text(
            right if is_rtl else left,
            top,
            anchor="ne" if is_rtl else "nw",
            justify="right" if is_rtl else "left",
            text=text,
            font=self.message_font,
            fill=self.color(MESSAGE_TEXT_COLOR),
            width=content_width,
        )
        text_bbox = canvas.bbox(text_item) or (left, top, right, top + 10)
        content_bottom = text_bbox[3]

        if timestamp:
            time_width = self.message_time_font.measure(timestamp)
            time_height = self.message_time_font.metrics("linespace")
            inline_y = max(text_bbox[1], text_bbox[3] - time_height)

            if is_rtl:
                inline_x = text_bbox[0] - MESSAGE_TIME_GAP
                if inline_x - time_width >= left:
                    time_item = canvas.create_text(
                        inline_x,
                        inline_y,
                        anchor="ne",
                        text=timestamp,
                        font=self.message_time_font,
                        fill=self.color(MESSAGE_TIME_COLOR),
                    )
                else:
                    time_item = canvas.create_text(
                        left,
                        text_bbox[3],
                        anchor="sw",
                        text=timestamp,
                        font=self.message_time_font,
                        fill=self.color(MESSAGE_TIME_COLOR),
                    )
            else:
                inline_x = text_bbox[2] + MESSAGE_TIME_GAP
                if inline_x + time_width <= right:
                    time_item = canvas.create_text(
                        inline_x,
                        inline_y,
                        anchor="nw",
                        text=timestamp,
                        font=self.message_time_font,
                        fill=self.color(MESSAGE_TIME_COLOR),
                    )
                else:
                    time_item = canvas.create_text(
                        right,
                        text_bbox[3],
                        anchor="se",
                        text=timestamp,
                        font=self.message_time_font,
                        fill=self.color(MESSAGE_TIME_COLOR),
                    )

            time_bbox = canvas.bbox(time_item)
            if time_bbox:
                content_bottom = max(content_bottom, time_bbox[3])

        if self.highlighted_message_index == (group_index, message_index):
            self.draw_rounded_rectangle(
                left - 5,
                message_region_top - 3,
                right + 5,
                content_bottom + 4,
                6,
                "",
                self.color(REPLY_ACCENT_COLOR),
                width=2,
            )

        self.message_item_regions.append(
            {
                "group_index": group_index,
                "message_index": message_index,
                "bounds": (left, message_region_top, right, content_bottom),
            }
        )
        return content_bottom + MESSAGE_LINE_GAP

    def estimate_text_width(self, font, text, max_width):
        text = (text or "").strip()
        if not text:
            return 0

        measured_width = 0
        for line in text.splitlines() or [text]:
            line = line.strip()
            if line:
                measured_width = max(measured_width, font.measure(line))

        return min(max_width, measured_width)

    def estimate_message_content_width(self, message_data, max_width):
        widths = []
        timestamp = message_data.get("time", "")
        timestamp_width = (
            self.message_time_font.measure(timestamp) + MESSAGE_TIME_GAP
            if timestamp
            else 0
        )

        reply = message_data.get("reply")
        if reply:
            reply_header = f"Reply to {reply.get('author', 'Unknown')}"
            widths.append(self.estimate_text_width(self.reply_meta_font, reply_header, max_width))
            widths.append(self.estimate_text_width(self.reply_font, reply.get("excerpt", ""), max_width))

        for kind, value in split_image_markdown(message_data.get("text", "")):
            if kind == "image":
                cache = self.image_cache.get(value.get("url", ""))
                if cache and cache.get("status") == "ready":
                    widths.append(min(max_width, cache["size"][0]))
                else:
                    widths.append(min(max_width, IMAGE_RENDER_MAX_WIDTH))
                continue

            text_width = self.estimate_text_width(self.message_font, value, max_width)
            if text_width:
                widths.append(min(max_width, text_width + timestamp_width))

        return max(widths or [0])

    def estimate_group_content_width(self, group_data, max_width):
        widths = []
        if not group_data.get("outgoing"):
            widths.append(self.estimate_text_width(self.message_meta_font, group_data["title"], max_width))

        for message_data in group_data["messages"]:
            widths.append(self.estimate_message_content_width(message_data, max_width))

        minimum_width = min(120, max_width)
        return max(minimum_width, min(max_width, max(widths or [minimum_width])))

    def draw_message_group(self, group_index, group_data, top, canvas_width):
        available_width = max(220, canvas_width - (MESSAGE_X_MARGIN * 2))
        max_card_width = min(
            MESSAGE_MAX_WIDTH,
            max(220, round(canvas_width * MESSAGE_MAX_WIDTH_RATIO)),
            available_width,
        )
        max_content_width = max(160, max_card_width - (MESSAGE_X_PADDING * 2))
        content_width = self.estimate_group_content_width(group_data, max_content_width)
        card_width = content_width + (MESSAGE_X_PADDING * 2)
        outgoing = group_data.get("outgoing", False)

        if outgoing:
            right = canvas_width - MESSAGE_X_MARGIN
            left = max(MESSAGE_X_MARGIN, right - card_width)
        else:
            left = MESSAGE_X_MARGIN
            right = min(canvas_width - MESSAGE_X_MARGIN, left + card_width)

        text_left = left + MESSAGE_X_PADDING
        text_right = right - MESSAGE_X_PADDING
        canvas = self.messages_canvas

        if outgoing:
            message_top = top + MESSAGE_TOP_PADDING
        else:
            meta_y = top + MESSAGE_TOP_PADDING
            meta_item = canvas.create_text(
                text_left,
                meta_y,
                anchor="nw",
                text=group_data["title"],
                font=self.message_meta_font,
                fill=group_data.get("title_color", self.color(MESSAGE_META_COLOR)),
                width=content_width,
            )
            meta_bbox = canvas.bbox(meta_item) or (
                left + MESSAGE_X_PADDING,
                meta_y,
                right - MESSAGE_X_PADDING,
                meta_y + 14,
            )
            message_top = meta_bbox[3] + MESSAGE_META_GAP

        for message_index, message_data in enumerate(group_data["messages"]):
            message_top = self.draw_group_message(
                group_index,
                message_index,
                message_data,
                message_top,
                text_left,
                text_right,
                content_width,
            )

        card_bottom = max(
            top + MESSAGE_MIN_HEIGHT,
            message_top - MESSAGE_LINE_GAP + MESSAGE_BOTTOM_PADDING,
        )
        is_selected = group_index == self.selected_message_group_index
        unread_count = group_data.get("unread_count", 0)
        border_color = group_data["border"]
        border_width = 1
        if unread_count:
            border_color = self.color(MESSAGE_UNREAD_BORDER)
            border_width = 2
        if is_selected:
            border_color = self.color(MESSAGE_SELECTED_BORDER)
            border_width = 2

        card_item = self.draw_rounded_rectangle(
            left,
            top,
            right,
            card_bottom,
            MESSAGE_CARD_RADIUS,
            group_data["background"],
            border_color,
            width=border_width,
        )
        self.message_hit_regions.append(
            {
                "index": group_index,
                "bounds": (left, top, right, card_bottom),
            }
        )
        canvas.tag_lower(card_item)
        if unread_count:
            canvas.create_text(
                right - MESSAGE_X_PADDING,
                top + MESSAGE_TOP_PADDING,
                anchor="ne",
                text=f"new {unread_count}",
                font=self.message_time_font,
                fill=self.color(MESSAGE_UNREAD_COLOR),
            )
        return card_bottom + MESSAGE_GAP

    def render_messages(self, scroll_to_bottom=False, view_fraction=None):
        if not hasattr(self, "messages_canvas"):
            return

        canvas = self.messages_canvas
        canvas.delete("all")
        self.message_hit_regions = []
        self.message_item_regions = []
        self.reply_hit_regions = []
        canvas_width = max(1, canvas.winfo_width())
        self.message_canvas_width = canvas_width

        top = MESSAGE_TOP_MARGIN
        for group_index, group_data in enumerate(self.message_groups):
            top = self.draw_message_group(group_index, group_data, top, canvas_width)

        self.message_total_height = max(top, canvas.winfo_height())
        canvas.configure(scrollregion=(0, 0, canvas_width, self.message_total_height))

        if scroll_to_bottom:
            self.scroll_messages_to_bottom()
        elif view_fraction is not None:
            canvas.yview_moveto(view_fraction)

    def append_message_card(
        self,
        user,
        display_name,
        sent_at,
        message,
        render=True,
        unread=False,
        reply=None,
        message_id=None,
        comment_id=None,
    ):
        background, border = self.user_card_colors(user)
        accent = self.user_accent_color(user)
        is_outgoing = self.is_current_user(user)
        previous_sent_at = self.last_message_datetime()
        sent_at = self.parse_message_datetime(sent_at)
        message_data = {
            "text": message,
            "sent_at": sent_at,
            "time": self.display_message_time(sent_at, previous_sent_at),
            "unread": unread,
            "reply": reply,
            "message_id": message_id,
            "comment_id": comment_id,
        }

        if self.message_groups and self.message_groups[-1]["user"] == user:
            self.message_groups[-1]["background"] = background
            self.message_groups[-1]["border"] = border
            self.message_groups[-1]["title_color"] = (
                self.color(TELEGRAM_BLUE) if is_outgoing else accent
            )
            self.message_groups[-1]["outgoing"] = is_outgoing
            self.message_groups[-1]["messages"].append(message_data)
            if unread:
                self.message_groups[-1]["unread_count"] = (
                    self.message_groups[-1].get("unread_count", 0) + 1
                )
        else:
            self.message_groups.append(
                {
                    "user": user,
                    "title": display_name.strip() if display_name else f"@{user}",
                    "background": background,
                    "border": border,
                    "title_color": self.color(TELEGRAM_BLUE) if is_outgoing else accent,
                    "outgoing": is_outgoing,
                    "unread_count": 1 if unread else 0,
                    "messages": [message_data],
                }
            )

        if unread:
            self.rebuild_unread_counts()
            self.update_window_title()
            self.update_status()

        if render:
            self.render_messages(scroll_to_bottom=True)

    def append_chat_message(
        self,
        user,
        created_at,
        body,
        render=True,
        unread=False,
        comment_id=None,
    ):
        parsed_comment = APP_COMMENT_PATTERN.match(body.strip())
        if parsed_comment:
            display_name = parsed_comment.group("name")
            sent_at = parsed_comment.group("sent_at")
            message = parsed_comment.group("message").strip()
        else:
            display_name = f"@{user}"
            sent_at = created_at
            message = body.strip()

        message_id, message = self.parse_message_metadata(message)
        reply, message = self.parse_reply_body(message)
        self.append_message_card(
            user,
            display_name,
            sent_at,
            message,
            render=render,
            unread=unread,
            reply=reply,
            message_id=message_id,
            comment_id=comment_id,
        )

    def set_status(self, text):
        self.status_label.configure(text=text)

    def bind_window_activity_events(self):
        self.bind("<FocusIn>", lambda event: self.set_app_active(True))
        self.bind("<FocusOut>", lambda event: self.after(200, self.refresh_app_activity))
        self.bind("<Map>", lambda event: self.after(200, self.refresh_app_activity))
        self.bind("<Unmap>", lambda event: self.after(200, self.refresh_app_activity))
        self.bind("<Configure>", lambda event: self.after_idle(self.resize_input_box), add="+")

    def refresh_app_activity(self):
        if not self.running:
            return

        active = self.state() != "iconic" and self.focus_displayof() is not None
        self.set_app_active(active)

    def set_app_active(self, active):
        if self.is_app_active == active:
            self.update_status()
            return

        self.is_app_active = active
        if not self.fetch_in_progress and self.error_backoff_seconds == 0:
            self.schedule_next_fetch(delay_seconds=0 if active else None)
        else:
            self.update_status()

    def current_poll_seconds(self):
        if self.is_app_active:
            return self.active_poll_seconds

        return self.background_poll_seconds

    def compact_error_message(self, error):
        message = str(error)
        token = self.config_data.get("token", "")
        if token:
            message = message.replace(token, "[token]")
        return " ".join(message.split())[:180]

    @staticmethod
    def format_next_check(next_check_at):
        if not next_check_at:
            return "-"

        return next_check_at.strftime("%H:%M:%S")

    def update_rate_info(self, rate_info):
        if rate_info:
            self.rate_remaining = rate_info.get("remaining", self.rate_remaining)
            self.rate_reset_at = rate_info.get("reset_at", self.rate_reset_at)

    def update_status(self):
        if not hasattr(self, "status_label"):
            return

        state = "Fetching" if self.fetch_in_progress else (
            "Online" if self.is_app_active else "Background"
        )
        last_updated = (
            self.last_updated_at.strftime("%H:%M:%S")
            if self.last_updated_at
            else "-"
        )
        parts = [
            state,
            f"Last updated: {last_updated}",
            f"Next check: {self.format_next_check(self.next_check_at)}",
            f"Rate remaining: {self.rate_remaining}",
        ]
        if self.rate_reset_at:
            parts.append(f"Rate reset: {self.rate_reset_at}")

        unread = self.unread_summary()
        if unread:
            parts.append(f"Unread: {unread}")

        if self.status_error:
            parts.append(f"Error: {self.status_error}")

        self.set_status(" | ".join(parts))

    def start_ui_queue_pump(self):
        if self.running and self.ui_queue_after_id is None:
            self.ui_queue_after_id = self.after(50, self.process_ui_queue)

    def process_ui_queue(self):
        self.ui_queue_after_id = None
        if not self.running:
            return

        while True:
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            callback()

        self.start_ui_queue_pump()

    def safe_after(self, callback):
        if not self.running:
            return

        self.ui_queue.put(callback)

    def cleanup_worker_threads(self):
        self.worker_threads = [
            thread for thread in self.worker_threads if thread.is_alive()
        ]

    def start_worker_thread(self, target, name):
        self.cleanup_worker_threads()
        thread = threading.Thread(target=target, name=name, daemon=True)
        self.worker_threads.append(thread)
        thread.start()

    def cancel_scheduled_fetch(self):
        if not self.poll_after_id:
            return

        try:
            self.after_cancel(self.poll_after_id)
        except tk.TclError:
            pass
        self.poll_after_id = None

    def schedule_next_fetch(self, delay_seconds=None):
        if not self.running:
            return

        self.cancel_scheduled_fetch()
        if delay_seconds is None:
            delay_seconds = self.current_poll_seconds()

        delay_seconds = max(0, positive_int(delay_seconds, 0, minimum=0))
        self.next_check_at = datetime.now() + timedelta(seconds=delay_seconds)
        self.update_status()
        self.poll_after_id = self.after(
            delay_seconds * 1000,
            self.polling_tick,
        )

    def polling_tick(self):
        self.poll_after_id = None
        self.start_fetch_worker()

    def start_polling(self):
        self.resolve_current_user_async()
        self.fetch_issues_async()
        self.fetch_issue_title_async(self.active_issue_number)
        self.schedule_next_fetch(delay_seconds=0)

    def refresh_messages(self):
        self.start_fetch_worker()

    def fetch_issues_async(self):
        if not self.running or self.rooms_fetch_in_progress:
            return

        self.rooms_fetch_in_progress = True
        self.render_room_list()

        def worker():
            try:
                issues, rate_info = self.chat.fetch_issues()
                self.safe_after(lambda: self.handle_issues_success(issues, rate_info))
            except Exception as error:
                self.safe_after(lambda error=error: self.handle_issues_error(error))

        self.start_worker_thread(worker, "github-issues")

    def handle_issues_success(self, issues, rate_info):
        if not self.running:
            return

        self.rooms_fetch_in_progress = False
        self.rooms_loaded = True
        self.rooms_error = ""
        self.update_rate_info(rate_info)
        rooms = [
            room for room in (self.normalize_issue_room(issue) for issue in issues)
            if room
        ]
        self.issue_rooms = rooms
        for room in rooms:
            state = self.room_states.get(room["number"])
            if state:
                state["issue_title"] = room["title"]

        active_room = next(
            (room for room in rooms if room.get("number") == self.active_issue_number),
            None,
        )
        if active_room:
            self.issue_title = active_room.get("title", self.issue_title)
            self.update_chat_header()
        elif rooms:
            self.switch_issue_room(rooms[0]["number"])
            return

        self.render_room_list()
        self.update_status()

    def handle_issues_error(self, error):
        if not self.running:
            return

        self.rooms_fetch_in_progress = False
        self.rooms_loaded = True
        self.rooms_error = "Could not load issues"
        if isinstance(error, GitHubApiError):
            self.update_rate_info(error.rate_info)
        self.render_room_list()
        self.update_status()

    def start_fetch_worker(self):
        if not self.running or self.fetch_in_progress:
            self.update_status()
            return

        issue_number = self.active_issue_number
        self.fetch_in_progress = True
        self.next_check_at = None
        self.update_status()

        def worker():
            try:
                comments, rate_info = self.chat.fetch_messages(issue_number)
                self.safe_after(
                    lambda: self.handle_fetch_success(comments, rate_info, issue_number)
                )
            except Exception as error:
                self.safe_after(
                    lambda error=error: self.handle_fetch_error(error, issue_number)
                )

        self.start_worker_thread(worker, "github-fetch")

    def resolve_current_user_async(self):
        if self.current_github_user:
            return

        def worker():
            try:
                login, rate_info = self.chat.fetch_current_user()
                self.safe_after(
                    lambda: self.handle_current_user_success(login, rate_info)
                )
            except Exception:
                pass

        self.start_worker_thread(worker, "github-user")

    def handle_current_user_success(self, login, rate_info):
        if login:
            self.current_github_user = login
            self.refresh_message_group_styles()
            if hasattr(self, "messages_canvas"):
                self.render_messages(view_fraction=self.messages_canvas.yview()[0])
        self.update_rate_info(rate_info)
        self.update_status()

    def fetch_issue_title_async(self, issue_number=None):
        issue_number = issue_number or self.active_issue_number

        def worker():
            try:
                title, rate_info = self.chat.fetch_issue_title(issue_number)
                self.safe_after(
                    lambda: self.handle_issue_title_success(title, rate_info, issue_number)
                )
            except Exception as error:
                self.safe_after(
                    lambda error=error: self.handle_issue_title_error(error, issue_number)
                )

        self.start_worker_thread(worker, "github-issue-title")

    def handle_issue_title_success(self, title, rate_info, issue_number):
        if not self.running:
            return

        title = " ".join((title or "").split())
        for room in self.issue_rooms:
            if room.get("number") == issue_number and title:
                room["title"] = title
                break

        if issue_number != self.active_issue_number:
            self.update_rate_info(rate_info)
            self.render_room_list()
            self.update_status()
            return

        if title:
            self.issue_title = title
            self.update_chat_header()

        self.update_rate_info(rate_info)
        self.render_room_list()
        self.update_status()

    def handle_issue_title_error(self, error, issue_number):
        if not self.running:
            return

        if issue_number != self.active_issue_number:
            return

        if isinstance(error, GitHubApiError):
            self.update_rate_info(error.rate_info)
        self.update_status()

    def process_comments(self, comments):
        new_count = 0
        unread_new_count = 0

        for comment in comments:
            comment_id = comment.get("id")
            if comment_id in self.last_seen_ids:
                continue

            self.last_seen_ids.add(comment_id)
            user = comment.get("user", {}).get("login", "unknown")
            created_at = comment.get("created_at", "")
            body = comment.get("body", "")
            unread = (
                self.initial_sync_done
                and not self.is_app_active
                and not self.is_own_comment(user)
            )

            self.append_chat_message(
                user,
                created_at,
                body,
                render=False,
                unread=unread,
                comment_id=comment_id,
            )
            new_count += 1

            if unread:
                unread_new_count += 1

        if new_count:
            self.render_messages(scroll_to_bottom=True)

        return new_count, unread_new_count

    def is_own_comment(self, user):
        if not self.current_github_user:
            return False

        return user.lower() == self.current_github_user.lower()

    def notify_new_messages(self, count):
        if self.is_app_active or count <= 0:
            return

        try:
            if os.name == "nt":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.bell()
        except Exception:
            try:
                self.bell()
            except tk.TclError:
                pass

    def handle_fetch_success(self, comments, rate_info, issue_number):
        if not self.running:
            return

        if issue_number != self.active_issue_number:
            self.update_rate_info(rate_info)
            return

        was_initial_sync = not self.initial_sync_done
        self.fetch_in_progress = False
        self.error_backoff_seconds = 0
        self.status_error = ""
        self.last_updated_at = datetime.now()
        self.update_rate_info(rate_info)

        new_count, unread_new_count = self.process_comments(comments)
        self.initial_sync_done = True

        if not was_initial_sync and not self.is_app_active:
            self.notify_new_messages(unread_new_count)

        self.schedule_next_fetch()

    def next_error_backoff(self, error):
        previous = self.error_backoff_seconds
        if previous <= 0:
            delay = ERROR_BACKOFF_START_SECONDS
        else:
            delay = min(previous * 2, ERROR_BACKOFF_MAX_SECONDS)

        if isinstance(error, GitHubApiError) and error.retry_after_seconds:
            delay = max(delay, positive_int(error.retry_after_seconds, delay))

        return min(delay, ERROR_BACKOFF_MAX_SECONDS)

    def handle_fetch_error(self, error, issue_number):
        if not self.running:
            return

        if issue_number != self.active_issue_number:
            return

        self.fetch_in_progress = False
        self.error_backoff_seconds = self.next_error_backoff(error)

        if isinstance(error, GitHubApiError):
            self.update_rate_info(error.rate_info)
            self.status_error = self.compact_error_message(error)
        elif isinstance(error, requests.Timeout):
            self.status_error = "GitHub request timed out."
        elif isinstance(error, requests.RequestException):
            self.status_error = "Network error while contacting GitHub."
        else:
            self.status_error = self.compact_error_message(error)

        self.schedule_next_fetch(delay_seconds=self.error_backoff_seconds)

    def handle_send_success(self, rate_info, issue_number=None):
        if not self.running:
            return

        self.update_rate_info(rate_info)
        if issue_number is not None and issue_number != self.active_issue_number:
            return

        self.input_box.delete("1.0", "end")
        self.clear_reply_target()
        self.update_input_direction()
        self.resize_input_box()
        self.refresh_messages()

    def handle_send_error(self, error):
        if isinstance(error, GitHubApiError):
            self.update_rate_info(error.rate_info)
            self.status_error = self.compact_error_message(error)
            self.update_status()

        messagebox.showerror("Send Failed", self.compact_error_message(error))

    def send_current_message(self):
        text = self.input_box.get("1.0", "end").strip()
        if not text:
            return

        reply = self.reply_target.copy() if self.reply_target else None
        issue_number = self.active_issue_number
        self.set_send_controls(False, send_text="Sending...")

        def worker():
            try:
                data, rate_info = self.chat.send_message(
                    text,
                    reply=reply,
                    issue_number=issue_number,
                )
                self.safe_after(
                    lambda: self.handle_send_success(rate_info, issue_number)
                )
            except Exception as error:
                self.safe_after(lambda error=error: self.handle_send_error(error))
            finally:
                self.safe_after(lambda: self.set_send_controls(True))

        self.start_worker_thread(worker, "github-send")

    def on_closing(self):
        self.running = False
        ctk.AppearanceModeTracker.remove(self.theme_callback)
        self.cancel_scheduled_fetch()
        if self.input_resize_after_id:
            try:
                self.after_cancel(self.input_resize_after_id)
            except tk.TclError:
                pass
            self.input_resize_after_id = None
        if self.ui_queue_after_id:
            try:
                self.after_cancel(self.ui_queue_after_id)
            except tk.TclError:
                pass
            self.ui_queue_after_id = None
        for thread in self.worker_threads:
            if thread.is_alive():
                thread.join(timeout=0.05)
        unload_bundled_fonts(self.loaded_font_paths)
        self.loaded_font_paths = []
        self.destroy()
