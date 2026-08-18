#!/usr/bin/env python3
"""
FileListerWithSQLiteViewer_VideoOnly.py

Features:
- List video files in folder (optionally recursive)
- Export to Excel
- Export to SQLite (dedupe by file_name)
- SQLite Viewer tab with search/filter, sorting, pagination
- Bulk delete selected / Delete ALL
- Export SQLite -> Excel
- Auto-load last-used DB (app_settings.json)
- Column auto-resize in DB viewer
- Smart formatting (sizes, dates)
- Double-click open file (File list + DB viewer)
- Only accepts video file types (mp4, mkv, avi, etc.)
"""
# ===============================
# Standard Library
# ===============================
import shutil
import os
import json
import sqlite3
import re
import subprocess
import threading
import sys
import datetime
from pathlib import Path
from collections import defaultdict
from functools import partial

# ===============================
# Third-Party Libraries
# ===============================
import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font as tkfont

from PIL import Image, ImageTk
import requests
from typing import Any, Dict, Tuple, Optional


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
COVERS_DIR = str(APP_DIR / "covers")
os.makedirs(COVERS_DIR, exist_ok=True)


try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

# ===============================
# Internal Modules
# ===============================

from db.schema import (
    DROP_METADATA_VIEW,
    FILES_TABLE_SQL,
    MOVIE_TABLE_SQL,
    FILES_TABLE_INDEX,
    CATEGORIES_TABLE_SQL,
    DB_SELECT_ALL,
    DB_SELECT_STORAGE_ID,
    MOVIE_DETAILS_INSERT,
    MOVIE_DETAILS_INSERT_MANUAL,
    SELECT_MOVIE_DETAIL_VIEW,
    METADATA_STATUS_STATS,
    SELECT_MOVIE_METADATA_FULL,
    SELECT_METADATA_VIEW,
    CREATE_METADATA_VIEW,
    SELECT_MOVIE_METADATA,
    UPDATE_FILES_CATEGORY,
    UPDATE_FILE_MOVE,
    INSERT_FILE_RECORD,
    DELETE_FILE_BY_ID,
    SELECT_STORAGE_STATS,
    SELECT_TOTAL_COUNT,
    SELECT_TOTAL_SIZE,
    SELECT_EXTENSION_STATS,
    SELECT_ALL_CATEGORIES,
    INSERT_CATEGORY,
    SELECT_DISTINCT_FILE_CATEGORIES
)


from db.database import init_db, ensure_global_unique_index

from scanner.scanner import (
    get_files_info,
    detect_storage_id_from_path,
    paths_equal_ignore_drive,
)

from duplicates.duplicate_analyzer import analyze_duplicates

from utils.helpers import (
    format_size,
    format_bytes,
    format_db_total_size,
    format_date,
    get_folder_size_bytes
)

from utils.movie_scraper import scrape_movie, scrape_category_urls

class ExportDialog:
    def __init__(self, parent, options):
        self.result = None

        self.top = tk.Toplevel(parent)
        self.top.title("Export Options")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)

        ttk.Label(
            self.top,
            text="Select export type:",
            font=("Segoe UI", 10, "bold")
        ).pack(padx=12, pady=(12, 6))

        self.var = tk.StringVar(value=options[0])

        for opt in options:
            ttk.Radiobutton(
                self.top,
                text=opt,
                variable=self.var,
                value=opt
            ).pack(anchor="w", padx=20, pady=2)

        btn_frame = ttk.Frame(self.top)
        btn_frame.pack(pady=12)

        ttk.Button(btn_frame, text="Export", command=self.on_ok).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.on_cancel).pack(side="left", padx=6)

        self.top.protocol("WM_DELETE_WINDOW", self.on_cancel)

        # center dialog
        self.top.update_idletasks()
        x = parent.winfo_rootx() + 100
        y = parent.winfo_rooty() + 100
        self.top.geometry(f"+{x}+{y}")
        #self.after(100, self.load_storage_ids_from_db)


    def on_ok(self):
        self.result = self.var.get()
        self.top.destroy()

    def on_cancel(self):
        self.result = None
        self.top.destroy()

def normalize_name(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())

def normalize_title_for_match(text):
    if not text:
        return ""

    title = text.lower()
    title = re.sub(r"\[[^\]]*\]", " ", title)
    title = re.sub(r"\([^\)]*\)", " ", title)
    title = re.sub(r"(19|20)\d{2}", " ", title)
    title = re.sub(r"\b(the|a|an)\b", " ", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)
    title = re.sub(r"\b\d+\b", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title.replace(" ", "")

class FileListerApp:
    CONFIG_FILE = str(APP_DIR / "app_settings.json")
    LEGACY_CONFIG_FILE = str(APP_DIR / "config.json")
    DEFAULT_DB_NAME = "VideoFiles.db"


    def __init__(self, root):
        self.app_dir = APP_DIR
        self.master_db_path = str(self.app_dir / self.DEFAULT_DB_NAME)
        settings = self.load_settings()
        self.current_db_path = self.resolve_app_path(
            settings.get("last_db_path", self.master_db_path)
        )

        self.root = root
        self.root.title("Video File Lister")
        self.root.geometry("1280x820")

        # Allowed video types
        self.allowed_video_exts = {
            ".mp4", ".mkv", ".avi", ".mov", ".mpg", ".mpeg",
            ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts", ".divx","dvd"
        }

        self.known_video_exts = {
            ".mp4", ".mkv", ".avi", ".mov", ".mpg", ".mpeg", ".wmv", ".flv",
            ".webm", ".m4v", ".3gp", ".ts", ".divx",

            # other common video formats (not yet supported but detectable)
            ".rmvb", ".rm", ".vob", ".mts", ".m2ts", ".ogv", ".f4v",
            ".asf", ".mxf", ".roq", ".nsv"
        }


        self._font = tkfont.nametofont("TkDefaultFont")

        self.cancel_upgrade = False
        self.start_time = None
        self.error_log_path = str(self.app_dir / "upgrade_errors.log")
        self.unmatched_log_path = str(self.app_dir / "unmatched_urls.log")

        # File data stores
        self.all_files_info = []
        self.file_paths = {}
        self.scan_results = []
        self.scan_item_map = {}
        self.scan_inline_entry = None
        self.scan_operation_in_progress = False
        self._scan_sort_reverse = {}

        # SQLite viewer state
        #self.current_db_path = None
        self.selected_file_id = None
        #self.current_image_urls = []

        self.db_records_cache = []
        self.all_filtered_rows = []
        # DB load/cache tracking
        self._db_loaded = False
        self._db_last_path = None
        self._db_last_mtime = None
        self._db_needs_refresh = False
        self.selected_storage_filter = tk.StringVar(value="ALL")
        self.available_storage_ids = ["ALL"]
        self.missing_online_url_var = tk.StringVar()
        self.missing_online_category_var = tk.StringVar(value="All")
        self._missing_online_stop_event = threading.Event()
        self.missing_online_search_var = tk.StringVar()
        self.missing_online_rows = []
        self._missing_online_sort_reverse = {}

        #self.current_page_rows = []
        self.page_size = 50
        self.current_page = 0
        self.total_pages = 0
        self._db_sort_reverse = {}
        self.storage_id_var = tk.StringVar(value="UNKNOWN")
        # --- Gallery Pagination State ---
        self.gallery_offset = 0
        self.gallery_limit = 15   # 15 per page (safe for 300+)
        # --- Duplicate pagination ---
        self.dup_all_rows = []
        self.dup_current_page = 0
        self.dup_total_pages = 0
        # --- Covers local path
        self.category_var = tk.StringVar()
        self.db_category_var = tk.StringVar()
        self.cover1_local_path = tk.StringVar()
        self.cover2_local_path = tk.StringVar()

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)


        # Auto-create + load
        if not os.path.exists(self.current_db_path):
            init_db(self.current_db_path, fresh=True)
            self.load_db_records()
                # Load settings

        # Build UI here (tabs, combo boxes, etc.)

        # Populate combos AFTER UI + DB are ready
        self.root.after(100, self.load_storage_ids_from_db)

    def load_gallery_categories(self):

        if not self.current_db_path:
            return

        categories = self.get_all_categories()

        #print("Gallery categories from DB:", categories)

        category_list = ["All"] + categories

        self.gallery_category_combo["values"] = category_list
        self.gallery_category_var.set("All")

    def build_gallery_ui(self):

        # ==============================
        # TOP SEPARATOR
        # ==============================
        ttk.Separator(self.gallery_tab, orient="horizontal").pack(fill="x", pady=5)

        # ==============================
        # CATEGORY FILTER (CENTERED)
        # ==============================
        filter_frame = ttk.Frame(self.gallery_tab)
        filter_frame.pack(pady=5)

        ttk.Label(filter_frame, text="Category:", font=("Segoe UI", 10, "bold")).pack(side="left")

        self.gallery_category_var = tk.StringVar(value="All")

        self.gallery_category_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.gallery_category_var,
            state="readonly",
            width=25
        )
        self.gallery_category_combo.pack(side="left", padx=5)

        self.gallery_category_combo.bind(
            "<<ComboboxSelected>>",
            self.on_gallery_category_changed
        )

        # ==============================
        # SECOND SEPARATOR
        # ==============================
        ttk.Separator(self.gallery_tab, orient="horizontal").pack(fill="x", pady=5)

        # ==============================
        # SCROLLABLE THUMBNAIL AREA
        # ==============================
        self.gallery_canvas = tk.Canvas(self.gallery_tab)
        self.gallery_scrollbar = ttk.Scrollbar(
            self.gallery_tab,
            orient="vertical",
            command=self.gallery_canvas.yview
        )

        self.gallery_frame = ttk.Frame(self.gallery_canvas)

        self.gallery_frame.bind(
            "<Configure>",
            lambda e: self.gallery_canvas.configure(
                scrollregion=self.gallery_canvas.bbox("all")
            )
        )

        self.gallery_canvas.create_window((0, 0), window=self.gallery_frame, anchor="nw")
        self.gallery_canvas.configure(yscrollcommand=self.gallery_scrollbar.set)

        self.gallery_canvas.pack(side="left", fill="both", expand=True)
        self.gallery_scrollbar.pack(side="right", fill="y")

        self.thumbnail_refs = []

        # ==============================
        # BOTTOM SEPARATOR
        # ==============================
        ttk.Separator(self.gallery_tab, orient="horizontal").pack(fill="x", pady=5)

        # ==============================
        # LOAD MORE BUTTON
        # ==============================
        self.load_more_btn = ttk.Button(
            self.gallery_tab,
            text="Load More",
            command=self.load_more_gallery
        )
        self.load_more_btn.pack(pady=8)        

    def load_gallery(self):
        if not self.current_db_path:
            return

        # Reset
        self.gallery_offset = 0

        for widget in self.gallery_frame.winfo_children():
            widget.destroy()

        self.thumbnail_refs.clear()

        self.load_more_gallery()

    def on_gallery_category_changed(self, event=None):
        self.gallery_offset = 0

        for widget in self.gallery_frame.winfo_children():
            widget.destroy()

        self.thumbnail_refs.clear()

        self.load_more_btn.config(state="normal")

        self.load_more_gallery()

    def load_more_gallery(self):
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            selected_category = self.gallery_category_var.get()

            if selected_category == "All":
                query = """
                    SELECT file_id, cover1_path
                    FROM MovieDetails
                    WHERE cover1_path IS NOT NULL
                    AND cover1_path != ''
                    LIMIT ? OFFSET ?
                """
                params = (self.gallery_limit, self.gallery_offset)

            else:
                query = """
                    SELECT file_id, cover1_path
                    FROM MovieDetails
                    WHERE cover1_path IS NOT NULL
                    AND cover1_path != ''
                    AND category = ?
                    LIMIT ? OFFSET ?
                """
                params = (selected_category,
                        self.gallery_limit,
                        self.gallery_offset)

            cur.execute(query, params)

            rows = cur.fetchall()
            conn.close()

            if not rows:
                self.load_more_btn.config(state="disabled")
                return

            columns = 5
            existing_widgets = len(self.gallery_frame.winfo_children())
            row_num = existing_widgets // columns
            col_num = existing_widgets % columns

            for file_id, image_path in rows:

                resolved_image_path = self.resolve_cover_path(image_path)
                if not os.path.exists(resolved_image_path):
                    continue

                img = Image.open(resolved_image_path)
                img.thumbnail((160, 230))
                photo = ImageTk.PhotoImage(img)

                lbl = tk.Label(self.gallery_frame,
                            image=photo,
                            cursor="hand2",
                            bd=2,
                            relief="ridge")

                lbl.image = photo
                self.thumbnail_refs.append(photo)

                lbl.grid(row=row_num, column=col_num, padx=10, pady=10)

                lbl.bind(
                    "<Button-1>",
                    partial(self.open_movie_from_gallery, file_id)
                )

                col_num += 1
                if col_num >= columns:
                    col_num = 0
                    row_num += 1

            self.gallery_offset += self.gallery_limit

        except Exception as e:
            print("Gallery pagination error:", e)

    def open_movie_from_gallery(self, file_id, event=None):
        self.selected_file_id = int(file_id)

        # EXACT same logic as DB viewer
        self.load_movie_detail_view(self.selected_file_id)
        self.load_movie_metadata(self.selected_file_id)

    def copy_selected_searchable_filename(self):
        selected = self.db_tree.selection()
        if not selected:
            return

        filename = self.db_tree.set(selected[0], "Name")
        searchable_name = Path(filename).stem
        searchable_name = re.sub(r"[._]+", " ", searchable_name)
        searchable_name = re.sub(r"\s+\d{4}$", "", searchable_name)
        searchable_name = re.sub(r"\s+", " ", searchable_name).strip()

        self.root.clipboard_clear()
        self.root.clipboard_append(searchable_name)
        self.root.update()

    def get_connection(self):
        conn = sqlite3.connect(self.current_db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_storage_id(self):
        storage_id = "UNKNOWN"
        if hasattr(self, "storage_id_var"):
            storage_id = self.storage_id_var.get().strip()
        return storage_id if storage_id else "UNKNOWN"

    def resolve_app_path(self, path):
        if not path:
            return self.master_db_path

        candidate = Path(path)
        if candidate.is_absolute():
            return str(candidate)

        cwd_path = (Path.cwd() / candidate).resolve()
        if cwd_path.exists():
            return str(cwd_path)

        app_path = (self.app_dir / candidate).resolve()
        if app_path.exists():
            return str(app_path)

        return str(app_path)

    def make_portable_path(self, path):
        if not path:
            return path

        resolved = Path(path).resolve()
        try:
            return str(resolved.relative_to(self.app_dir))
        except ValueError:
            return str(resolved)

    def resolve_cover_path(self, path):
        if not path:
            return None

        path = str(path).strip()
        if not path:
            return None

        candidate_path = Path(path)
        filename_candidate = candidate_path.name

        search_candidates = []

        if candidate_path.is_absolute():
            search_candidates.append(candidate_path)
            candidate_path = Path(filename_candidate)
        else:
            search_candidates.append(Path.cwd() / candidate_path)
            if self.current_db_path:
                search_candidates.append((Path(self.current_db_path).resolve().parent / candidate_path).resolve())
            search_candidates.append((self.app_dir / candidate_path).resolve())

        search_candidates.append((Path("covers") / filename_candidate))
        if self.current_db_path:
            search_candidates.append((Path(self.current_db_path).resolve().parent / "covers" / filename_candidate).resolve())
        search_candidates.append((self.app_dir / "covers" / filename_candidate).resolve())

        for candidate in search_candidates:
            if candidate and candidate.exists():
                return str(candidate.resolve())

        return None

    def make_cover_db_path(self, path):
        if not path:
            return path

        resolved = Path(path).resolve()
        try:
            return str(resolved.relative_to(self.app_dir))
        except ValueError:
            return str(resolved)

    def get_cover_file_path(self, filename):
        return str(Path(COVERS_DIR) / filename)

    def cover_exists(self, path):
        resolved_path = self.resolve_cover_path(path)
        return bool(resolved_path and os.path.exists(resolved_path))

    def create_url_context_menu(self):
        self.url_menu = tk.Menu(self.root, tearoff=0)

        self.url_menu.add_command(label="Paste", command=self._paste_url)
        self.url_menu.add_command(label="Copy", command=self._copy_url)
        self.url_menu.add_command(label="Cut", command=self._cut_url)
        self.url_menu.add_separator()
        self.url_menu.add_command(label="Clear", command=lambda: self.meta_url_var.set(""))
    def _paste_url(self):
        try:
            clipboard = self.root.clipboard_get()
            self.meta_url_var.set(clipboard.strip())
        except:
            pass

    def bulk_update_from_category(self, category_url):
        urls = scrape_category_urls(category_url)

        if not urls:
            messagebox.showinfo("No URLs", "No movie links found.")
            return

        self.show_progress_window(len(urls))

        thread = threading.Thread(
            target=self._bulk_category_worker,
            args=(urls,),
            daemon=True
        )
        thread.start()

    def _bulk_category_worker(self, urls):
        total = len(urls)
        matched = 0
        skipped = 0

        for index, url in enumerate(urls, start=1):

            if self.cancel_upgrade:
                break

            try:
                file_id, data = self.find_movie_by_url(url)

                if file_id is None:
                    skipped += 1
                    self._log_unmatched(url)
                    continue

                # 🔥 Safeguard: Only update if category empty
                conn = self.get_connection()
                cur = conn.cursor()
                cur.execute("SELECT category FROM Files WHERE id=?", (file_id,))
                row = cur.fetchone()
                conn.close()

                current_category_in_db = row[0] if row else None

                if current_category_in_db:
                    skipped += 1
                    continue

                matched += 1
                self._fetch_metadata_worker(file_id, url)

            except Exception as e:
                self._log_error("CATEGORY", url, str(e))

            self.root.after(
                0,
                lambda i=index: self._update_progress_with_eta(i, total)
            )

        self.root.after(
            0,
            lambda: self._finish_category_update(matched, skipped, total)
        )     

    def _finish_category_update(self, matched, skipped, total):
        if self.progress_win:
            self.progress_win.destroy()

        messagebox.showinfo(
            "Category Update Completed",
            f"Total URLs: {total}\n"
            f"Matched & Updated: {matched}\n"
            f"Skipped (No Match): {skipped}"
        )

        self.status_var.set("Category batch update completed.")

    def _log_unmatched(self, url):
        try:
            with open(self.unmatched_log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now()} | {url}\n")
        except:
            pass

    def _copy_url(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.meta_url_var.get())
        except:
            pass


    def _cut_url(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.meta_url_var.get())
            self.meta_url_var.set("")
        except:
            pass        

    def display_image(self, url, label_widget):
        from PIL import Image, ImageTk
        from io import BytesIO
        import requests

        try:
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://rarelust.com/"
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            img_data = BytesIO(response.content)
            pil_image = Image.open(img_data)
            pil_image.thumbnail((200, 300))

            tk_image = ImageTk.PhotoImage(pil_image)

            label_widget.config(image=tk_image)
            label_widget.image = tk_image  # VERY IMPORTANT

        except Exception as e:
            print("Image load error:", e)

    def find_movie_by_url(self, url):
        data = scrape_movie(url)

        name = data.get("name")
        year = data.get("year")
        ext = data.get("extension")
        size_mb = data.get("size_mb")

        if not name:
            return None, None

        target_clean = re.sub(r'(19|20)\d{2}', '', name)
        target_norm = normalize_name(target_clean)

        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, file_name, extension, year, size_bytes
            FROM Files
        """)
        rows = cur.fetchall()
        conn.close()

        candidates = []

        for file_id, file_name, db_ext, db_year, size_bytes in rows:

            # Extract year from filename if DB year empty
            extracted_year = None
            match = re.search(r'(19|20)\d{2}', file_name)
            if match:
                extracted_year = match.group(0)

            effective_year = db_year if db_year else extracted_year

            # Normalize DB filename
            db_clean = re.sub(r'(19|20)\d{2}', '', file_name)
            db_norm = normalize_name(db_clean)

            # Name must match
            if db_norm != target_norm:
                continue

            # Year must match (if URL has year)
            if year:
                if not effective_year or str(year) != str(effective_year):
                    continue

            score = 0

            # Extension match bonus
            if ext and db_ext and db_ext.lower() == ext.lower():
                score += 3

            # Size match bonus
            if size_mb and size_bytes:
                db_size_mb = size_bytes / (1024 * 1024)
                if abs(db_size_mb - size_mb) <= 15:
                    score += 3

            # Prefer larger file
            if size_bytes:
                score += size_bytes / (1024 * 1024 * 1000)

            # Prefer MKV slightly
            if db_ext and db_ext.lower() == "mkv":
                score += 0.5

            candidates.append((score, file_id))

        if not candidates:
            return None, data

        # Choose highest score
        candidates.sort(reverse=True)
        best_match = candidates[0][1]

        return best_match, data

    def auto_update_by_url(self):
        url = self.meta_url_var.get().strip()

        if not url:
            messagebox.showwarning("URL Required", "Paste metadata URL.")
            return

        try:
            file_id, data = self.find_movie_by_url(url)

            if not file_id:
                messagebox.showwarning("Not Found", "No matching record found in database.")
                return

            # Set selected ID
            self.selected_file_id = file_id

            # Apply metadata using existing logic
            category = data["category"]
            description = data["description"]
            images = data["images"]

            conn = self.get_connection()
            cur = conn.cursor()

            # Paths
            img1_path = self.get_cover_file_path(f"{file_id}_1.jpg")
            img2_path = self.get_cover_file_path(f"{file_id}_2.jpg")

            # Download images
            if len(images) > 0:
                self.download_image(images[0], img1_path)

            if len(images) > 1:
                self.download_image(images[1], img2_path)

            # Update MovieDetails
            cur.execute(MOVIE_DETAILS_INSERT, (
                file_id,
                category,
                description,
                self.make_cover_db_path(img1_path),
                self.make_cover_db_path(img2_path),
                url
            ))

            # Sync Files category
            cur.execute(UPDATE_FILES_CATEGORY, (category, file_id))

            conn.commit()
            conn.close()

            # Refresh UI
            self.refresh_ui_after_db_update(file_id)

            self.status_var.set("Metadata updated automatically from URL.")

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def fetch_metadata(self):
        if not self.selected_file_id:
            messagebox.showwarning("Select Record", "Select a record first.")
            return

        url = self.meta_url_var.get().strip()

        if not url:
            messagebox.showwarning("URL Required", "Paste metadata URL.")
            return

        # Disable button during work
        self.status_var.set("Fetching metadata...")
        self.category_combo.configure(state="disabled")

        thread = threading.Thread(
            target=self._fetch_metadata_worker,
            args=(self.selected_file_id, url),
            daemon=True
        )
        thread.start()

    def _fetch_metadata_worker(self, file_id, url):
        try:
            data = scrape_movie(url)

            category = data["category"]
            description = data["description"]
            images = data["images"]

            img1_path = self.get_cover_file_path(f"{file_id}_1.jpg")
            img2_path = self.get_cover_file_path(f"{file_id}_2.jpg")

            if len(images) > 0:
                self.download_image(images[0], img1_path)

            if len(images) > 1:
                self.download_image(images[1], img2_path)

            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute(MOVIE_DETAILS_INSERT, (
                file_id,
                category,
                description,
                self.make_cover_db_path(img1_path),
                self.make_cover_db_path(img2_path),
                url
            ))

            cur.execute(UPDATE_FILES_CATEGORY, (category, file_id))

            conn.commit()
            conn.close()

            # UI update safely
            self.root.after(0, lambda: self._fetch_metadata_ui_update(file_id))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _fetch_metadata_ui_update(self, file_id):
        self.refresh_ui_after_db_update(file_id)
        self.category_combo.configure(state="normal")
        self.status_var.set("Metadata fetched successfully.")

    def show_progress_window(self, total):
        self.cancel_upgrade = False
        self.start_time = datetime.datetime.now()

        self.progress_win = tk.Toplevel(self.root)
        self.progress_win.title("Upgrading Covers...")
        self.progress_win.geometry("450x180")
        self.progress_win.transient(self.root)
        self.progress_win.grab_set()

        tk.Label(self.progress_win, text="Upgrading Covers...",
                font=("Segoe UI", 10, "bold")).pack(pady=8)

        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(
            self.progress_win,
            maximum=total,
            variable=self.progress_var,
            length=400
        )
        self.progress_bar.pack(pady=5)

        self.progress_label = tk.Label(self.progress_win, text=f"0 / {total}")
        self.progress_label.pack()

        self.eta_label = tk.Label(self.progress_win, text="ETA: Calculating...")
        self.eta_label.pack(pady=4)

        btn_frame = tk.Frame(self.progress_win)
        btn_frame.pack(pady=10)

        tk.Button(
            btn_frame,
            text="Cancel",
            width=12,
            command=self._cancel_upgrade
        ).pack()

    def _cancel_upgrade(self):
        self.cancel_upgrade = True
        self.status_var.set("Cancelling... Please wait.")

    def load_metadata_from_db(self):
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute(SELECT_MOVIE_METADATA, (self.selected_file_id,))

        row = cur.fetchone()
        conn.close()

        if row:
            self.category_var.set(row[0] or "")

            self.description_text.delete("1.0", tk.END)
            self.description_text.insert("1.0", row[1] or "")

            if row[2] and os.path.exists(self.resolve_cover_path(row[2])):
                self.display_image_from_file(row[2], self.image_label1)

            if row[3] and os.path.exists(self.resolve_cover_path(row[3])):
                self.display_image_from_file(row[3], self.image_label2)


    def download_image(self, url, save_path, retries=2):
        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        for attempt in range(retries + 1):
            try:
                response = requests.get(url, headers=headers, timeout=20)

                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")

                content_type = response.headers.get("Content-Type", "")
                if "image" not in content_type:
                    raise Exception("Invalid content type")

                if len(response.content) < 5000:
                    raise Exception("File too small (likely invalid)")

                with open(save_path, "wb") as f:
                    f.write(response.content)

                # Validate image before accepting
                from PIL import Image
                with Image.open(save_path) as img:
                    img.verify()

                return True

            except Exception as e:
                if os.path.exists(save_path):
                    os.remove(save_path)

                if attempt == retries:
                    print(f"Image download failed after retries: {url} | {e}")
                    return False

    def display_image_from_file(self, image_path, label_widget):
        try:
            image_path = self.resolve_cover_path(image_path)
            img = Image.open(image_path)
            img = img.resize((220, 280))
            photo = ImageTk.PhotoImage(img)

            label_widget.configure(image=photo)
            label_widget.image = photo
        except Exception as e:
            print("Image load failed:", e)

    def is_low_resolution(self,image_path, min_height=600):
        try:
            from PIL import Image
            image_path = self.resolve_cover_path(image_path)
            with Image.open(image_path) as img:
                width, height = img.size
                return height < min_height
        except:
            return True

    def upgrade_existing_covers(self):
        if not self.current_db_path:
            return

        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT file_id, metadata_url, cover1_path, cover2_path
            FROM MovieDetails
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return

        self.show_progress_window(len(rows))

        thread = threading.Thread(
            target=self._upgrade_worker,
            args=(rows,),
            daemon=True
        )
        thread.start()

    def _upgrade_worker(self, rows):
        total = len(rows)
        upgraded = 0

        for index, (file_id, url, cover1, cover2) in enumerate(rows, start=1):

            if self.cancel_upgrade:
                break

            if not url:
                continue

            try:
                data = scrape_movie(url)
                images = data.get("images", [])

                img1_path = self.get_cover_file_path(f"{file_id}_1.jpg")
                img2_path = self.get_cover_file_path(f"{file_id}_2.jpg")

                if len(images) > 0:
                    self.download_image(images[0], img1_path)

                if len(images) > 1:
                    self.download_image(images[1], img2_path)

                conn = self.get_connection()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE MovieDetails SET cover1_path=?, cover2_path=? WHERE file_id=?",
                    (
                        self.make_cover_db_path(img1_path),
                        self.make_cover_db_path(img2_path),
                        file_id
                    )
                )
                conn.commit()
                conn.close()

                upgraded += 1

            except Exception as e:
                self._log_error(file_id, url, str(e))

            # Update progress + ETA safely
            self.root.after(0, lambda i=index: self._update_progress_with_eta(i, total))

        self.root.after(0, lambda: self._finish_upgrade(upgraded, total))

    def _update_progress_with_eta(self, current, total):
        self.progress_var.set(current)
        self.progress_label.config(text=f"{current} / {total}")

        if current == 0:
            return

        elapsed = (datetime.datetime.now() - self.start_time).total_seconds()
        avg_time = elapsed / current
        remaining = avg_time * (total - current)

        eta_str = str(datetime.timedelta(seconds=int(remaining)))
        self.eta_label.config(text=f"ETA: {eta_str}")        

    def _log_error(self, file_id, url, error_message):
        try:
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"[{datetime.datetime.now()}] "
                    f"FileID: {file_id} | URL: {url} | Error: {error_message}\n"
                )
        except:
            pass

    def _update_progress(self, current, total):
        self.progress_var.set(current)
        self.progress_label.config(text=f"{current} / {total}")


    def _finish_upgrade(self, upgraded, total):
        if self.progress_win:
            self.progress_win.destroy()

        if self.cancel_upgrade:
            messagebox.showwarning(
                "Cancelled",
                f"Upgrade cancelled.\nCompleted: {self.progress_var.get()} / {total}"
            )
            self.status_var.set("Upgrade cancelled.")
        else:
            messagebox.showinfo(
                "Completed",
                f"{upgraded} covers upgraded successfully."
            )
            self.status_var.set("Bulk cover upgrade completed.")

    def repair_metadata_integrity(self):

        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("SELECT file_id, cover1_path, cover2_path FROM MovieDetails")
        rows = cur.fetchall()

        for fid, c1, c2 in rows:
            new_c1 = self.make_cover_db_path(self.resolve_cover_path(c1)) if self.cover_exists(c1) else None
            new_c2 = self.make_cover_db_path(self.resolve_cover_path(c2)) if self.cover_exists(c2) else None

            if new_c1 != c1 or new_c2 != c2:
                cur.execute(
                    "UPDATE MovieDetails SET cover1_path=?, cover2_path=? WHERE file_id=?",
                    (new_c1, new_c2, fid)
                )

        conn.commit()
        conn.close()

    def save_metadata(self):
        if not self.selected_file_id:
            messagebox.showwarning("Select Record", "Select a record first.")
            return

        category = self.category_var.get().strip()
        year_text = self.year_var.get().strip()
        description = self.description_text.get("1.0", tk.END).strip()
        metadata_url = self.meta_url_var.get().strip() if hasattr(self, "meta_url_var") else ""
        file_id = self.selected_file_id

        # 🔹 Manual cover paths (new)
        cover1_source = self.cover1_local_path.get().strip()
        cover2_source = self.cover2_local_path.get().strip()

        # Optional validation
        if not category:
            messagebox.showwarning("Category Required", "Select category.")
            return

        if year_text:
            try:
                year_value = int(year_text)
            except ValueError:
                messagebox.showwarning("Invalid Year", "Enter a numeric year.")
                return
        else:
            year_value = None

        if not description:
            messagebox.showwarning("Description Required", "Enter description.")
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            # --------------------------------------
            # 📁 Handle Manual Image Copy (NEW)
            # --------------------------------------
            covers_folder = os.path.join(os.path.dirname(self.current_db_path), "covers")
            os.makedirs(covers_folder, exist_ok=True)

            cover1_dest = None
            cover2_dest = None

            if cover1_source and os.path.exists(cover1_source):
                ext1 = os.path.splitext(cover1_source)[1]
                cover1_dest = os.path.join(covers_folder, f"{file_id}_cover1{ext1}")
                shutil.copy2(cover1_source, cover1_dest)

            if cover2_source and os.path.exists(cover2_source):
                ext2 = os.path.splitext(cover2_source)[1]
                cover2_dest = os.path.join(covers_folder, f"{file_id}_cover2{ext2}")
                shutil.copy2(cover2_source, cover2_dest)

            # --------------------------------------
            # 🗄 Upsert MovieDetails (UPDATED)
            # --------------------------------------
            cur.execute(
                MOVIE_DETAILS_INSERT_MANUAL,
                (
                    file_id,
                    category,
                    description,
                    self.make_cover_db_path(cover1_dest) if cover1_dest else None,
                    self.make_cover_db_path(cover2_dest) if cover2_dest else None
                )
            )

            # Sync Files table category and year
            cur.execute(UPDATE_FILES_CATEGORY, (category, file_id))
            cur.execute("UPDATE Files SET year=? WHERE id=?", (year_value, file_id))

            if metadata_url:
                cur.execute(
                    "UPDATE MovieDetails SET metadata_url=? WHERE file_id=?",
                    (metadata_url, file_id)
                )

            conn.commit()
            conn.close()

            # Clear manual image fields after save
            self.cover1_local_path.set("")
            self.cover2_local_path.set("")

            # Unified refresh
            self.refresh_ui_after_db_update(file_id)
            self.status_var.set("Metadata saved.")

        except Exception as e:
            messagebox.showerror("Error", str(e)) 

    def update_filelist_statistics(self, files_info):
        """
        files_info = self.all_files_info
        list of dicts with keys:
        name_without_ext, full_path, extension, size, creation_date, year, category, tracked
        """

        total_files = len(files_info)
        total_bytes = 0

        ext_map = {}
        storage_map = {}

        for info in files_info:
            size = info.get("size", 0) or 0
            ext = (info.get("extension") or "").lower()
            path = info.get("full_path", "")

            total_bytes += size

            # -------- extension stats --------
            ext_map.setdefault(ext, [0, 0])
            ext_map[ext][0] += 1
            ext_map[ext][1] += size

            # -------- storage stats --------
            storage_id = detect_storage_id_from_path(path)
            storage_map.setdefault(storage_id, [0, 0])
            storage_map[storage_id][0] += 1
            storage_map[storage_id][1] += size

        # -------- top totals --------
        self.total_files_var.set(f"Total Files: {total_files:,}")
        self.total_size_var.set(f"Total Size: {format_size(total_bytes)}")

        # -------- files by extension tree --------
        for i in self.file_ext_tree.get_children():
            self.file_ext_tree.delete(i)

        for ext in sorted(ext_map):
            cnt, sz = ext_map[ext]
            self.file_ext_tree.insert("", "end", values=(ext, cnt, format_size(sz)))

        # -------- storage summary tree --------
        if hasattr(self, "file_storage_tree"):
            for i in self.file_storage_tree.get_children():
                self.file_storage_tree.delete(i)

            for sid in sorted(storage_map):
                cnt, sz = storage_map[sid]
                self.file_storage_tree.insert(
                    "", "end",
                    values=(sid, cnt, format_size(sz))
                )

                
    def update_storage_statistics(self):
        if not self.current_db_path or not os.path.exists(self.current_db_path):
            return

        # Clear old
        for i in self.db_storage_tree.get_children():
            self.db_storage_tree.delete(i)

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute(SELECT_STORAGE_STATS)

            rows = cur.fetchall()
            conn.close()

            total_files = 0
            total_size = 0

            for sid, cnt, size in rows:
                total_files += cnt
                total_size += size or 0

                self.db_storage_tree.insert(
                    "", "end",
                    values=(sid, cnt, format_size(size))
                )

            # Optional TOTAL row
            self.db_storage_tree.insert(
                "", "end",
                values=("TOTAL", total_files, format_size(total_size))
            )

        except Exception as e:
            print("Storage stats error:", e)



    def extract_year_from_filename(self, filename):
        matches = re.findall(r'(19\d{2}|20\d{2})', filename)
        if matches:
            year = int(matches[0])
            if 1900 <= year <= 2099:
                return year
        return None

    def load_settings(self):
        for config_file in (self.CONFIG_FILE, self.LEGACY_CONFIG_FILE):
            if not os.path.exists(config_file):
                continue
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                continue
        return {}

    def save_settings(self, data):
        settings = self.load_settings()
        if "last_db_path" in data:
            data = dict(data)
            data["last_db_path"] = self.make_portable_path(data["last_db_path"])
        settings.update(data)

        try:
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            print("Failed to save settings:", e)

    def load_movie_detail_view(self, file_id):
        conn = self.get_connection()
        cur = conn.cursor()

        # Join Files + MovieDetails
        cur.execute(SELECT_MOVIE_DETAIL_VIEW, (file_id,))

        row = cur.fetchone()
        conn.close()

        if not row:
            return

        name, ext, year, storage, path, size_bytes, category, desc, cover1, cover2 = row
        # ✅ CLEAR OLD IMAGES FIRST
        if hasattr(self, "detail_image1"):
            self.detail_image1.config(image="")
            self.detail_image1.image = None

        if hasattr(self, "detail_image2"):
            self.detail_image2.config(image="")
            self.detail_image2.image = None

        self.detail_cover1_path = None
        self.detail_cover2_path = None

        self.detail_cover1_path = cover1
        self.detail_cover2_path = cover2
        self.detail_title.config(text=name)
        self.detail_category.config(text=f"Category: {category or 'N/A'}")
        self.detail_year.config(text=f"Year: {year or 'N/A'}")
        self.detail_format.config(text=f"Format: {ext}")
        self.detail_storage.config(text=f"Storage: {storage}")
        self.detail_size.config(
            text=f"Size: {format_size(size_bytes)}" if size_bytes else "Size: N/A"
        )

        self.detail_path.config(
            text=f"Path: {path}" if path else "Path: N/A"
        )
        # Enable temporarily
        self.detail_description.configure(state="normal")

        self.detail_description.delete("1.0", tk.END)
        self.detail_description.insert("1.0", desc or "")

        # Disable again
        self.detail_description.configure(state="disabled")

        # Load images
        cover1_path = cover1.strip() if cover1 else None
        cover2_path = cover2.strip() if cover2 else None

        cover1_path = self.resolve_cover_path(cover1_path) if cover1_path else None
        cover2_path = self.resolve_cover_path(cover2_path) if cover2_path else None

        if cover1_path and os.path.exists(cover1_path):
            self.display_image_from_file(cover1_path, self.detail_image1)

        if cover2_path and os.path.exists(cover2_path):
            self.display_image_from_file(cover2_path, self.detail_image2)

    def open_full_image(self, image_path):
        if not image_path:
            return

        image_path = self.resolve_cover_path(image_path)
        if not os.path.exists(image_path):
            return

        try:
            win = tk.Toplevel(self.root)
            win.title("Full Size Image")
            win.geometry("900x700")
            win.transient(self.root)

            # Scrollable Canvas
            canvas = tk.Canvas(win, bg="black")
            canvas.pack(fill="both", expand=True)

            h_scroll = tk.Scrollbar(win, orient="horizontal", command=canvas.xview)
            h_scroll.pack(side="bottom", fill="x")

            v_scroll = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
            v_scroll.pack(side="right", fill="y")

            canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

            img = Image.open(image_path)
            photo = ImageTk.PhotoImage(img)

            canvas.create_image(0, 0, anchor="nw", image=photo)
            canvas.image = photo

            canvas.config(scrollregion=canvas.bbox("all"))

        except Exception as e:
            messagebox.showerror("Image Error", str(e))

    def setup_movie_details_tab(self, parent):
        parent.columnconfigure(1, weight=1)

        # Title
        self.detail_title = tk.Label(
            parent,
            text="Select a movie...",
            font=("Segoe UI", 18, "bold")
        )
        self.detail_title.pack(pady=10)

        # Top Info Frame
        info_frame = tk.Frame(parent)
        info_frame.pack(fill="x", padx=20, pady=10)

        self.detail_category = tk.Label(info_frame, text="",font=("Segoe UI", 11, "bold"))
        self.detail_year = tk.Label(info_frame, text="",font=("Segoe UI", 11, "bold"))
        self.detail_format = tk.Label(info_frame, text="",font=("Segoe UI", 11, "bold"))
        self.detail_storage = tk.Label(info_frame, text="",font=("Segoe UI", 11, "bold"))

        self.detail_category.pack(anchor="w")
        self.detail_year.pack(anchor="w")
        self.detail_format.pack(anchor="w")
        self.detail_storage.pack(anchor="w")
        self.detail_size = tk.Label(info_frame, text="", font=("Segoe UI", 11))
        self.detail_size.pack(anchor="w")

        self.detail_path = tk.Label(
            info_frame,
            text="",
            font=("Segoe UI", 10),
            fg="gray",
            wraplength=900,
            justify="left"
        )
        self.detail_path.pack(anchor="w")
        # Images
        image_frame = tk.Frame(parent)
        image_frame.pack(pady=10)

        self.detail_image1 = tk.Label(image_frame)
        self.detail_image1.pack(side="left", padx=20)

        self.detail_image2 = tk.Label(image_frame)
        self.detail_image2.pack(side="left", padx=20)
        self.detail_image1.bind("<Button-1>", lambda e: self.open_full_image(self.detail_cover1_path))
        self.detail_image2.bind("<Button-1>", lambda e: self.open_full_image(self.detail_cover2_path))
        self.detail_image1.config(cursor="hand2")
        self.detail_image2.config(cursor="hand2")        
        # Description
        tk.Label(parent, text="Description:", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20)
        self.detail_description = tk.Text(
            parent,
            font=("Segoe UI", 14),
            height=8,
            wrap="word",
            state="disabled",
            bg="#f4f4f4",
            relief="flat"
        )
        #self.detail_description = tk.Text(parent,font=("Segoe UI", 14), height=8, wrap="word",state="disabled")
        self.detail_description.pack(fill="both", expand=True, padx=20, pady=10)

    def setup_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        main_tab = ttk.Frame(self.notebook)
        scan_tab = ttk.Frame(self.notebook)
        stats_tab = ttk.Frame(self.notebook)
        db_tab = ttk.Frame(self.notebook)
        self.gallery_tab = ttk.Frame(self.notebook)
        self.movie_details_tab = ttk.Frame(self.notebook)
        self.update_tab = ttk.Frame(self.notebook)
        self.missing_online_tab = ttk.Frame(self.notebook)
        dup_tab = ttk.Frame(self.notebook)

        self.notebook.add(main_tab, text="Files List")
        self.notebook.add(scan_tab, text="Folder Scan")
        self.notebook.add(stats_tab, text="Statistics")
        self.notebook.add(db_tab, text="SQLite Viewer")
        self.notebook.add(self.gallery_tab, text="Gallery")
        self.notebook.add(self.movie_details_tab, text="Movie Details")
        self.notebook.add(self.update_tab, text="Update")
        self.notebook.add(self.missing_online_tab, text="To Download")
        self.notebook.add(dup_tab, text="Duplicates")

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        self.setup_main_tab(main_tab)
        self.setup_folder_scan_tab(scan_tab)
        self.setup_stats_tab(stats_tab)
        self.setup_db_viewer_tab(db_tab)
        self.setup_movie_details_tab(self.movie_details_tab)
        self.setup_update_tab(self.update_tab)
        self.setup_missing_online_tab(self.missing_online_tab)
        self.setup_duplicates_tab(dup_tab)

        self.build_gallery_ui()   # ✅ initialize gallery UI

        self.status_var = tk.StringVar()
        self.db_loading_var = tk.StringVar()
        self.db_loading_running = False
        self.db_loading_dot_count = 0

        status_frame = tk.Frame(self.root, relief=tk.SUNKEN, bd=1)
        status_frame.pack(fill="x", side="bottom")

        tk.Label(status_frame, textvariable=self.status_var,
                anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(status_frame, textvariable=self.db_loading_var,
                anchor="e", width=14).pack(side="right")

    def on_tab_changed(self, event):
        selected_tab = self.notebook.tab(self.notebook.select(), "text")

        if selected_tab == "Statistics":
            self.update_db_statistics()
            self.update_storage_statistics()
            self.update_category_statistics()
            self.update_status_bar_db_info()
            self.draw_extension_pie_chart()

        elif selected_tab == "SQLite Viewer":
            if self.current_db_path:
                if not os.path.exists(self.current_db_path):
                    try:
                        init_db(self.current_db_path, fresh=True)
                    except Exception as e:
                        messagebox.showerror("Database Error", f"Failed to create database:\n{e}")
                        return
                # Only reload DB when necessary: first run, DB path changed,
                # DB file modified externally, or a refresh was requested.
                should_load = False
                if not getattr(self, "_db_loaded", False):
                    should_load = True
                elif self.current_db_path != getattr(self, "_db_last_path", None):
                    should_load = True
                else:
                    try:
                        cur_mtime = os.path.getmtime(self.current_db_path)
                        if getattr(self, "_db_last_mtime", None) != cur_mtime:
                            should_load = True
                    except Exception:
                        should_load = True

                if getattr(self, "_db_needs_refresh", False):
                    should_load = True

                if should_load:
                    self.load_db_records()
                    self._db_needs_refresh = False
                else:
                    self.status_var.set(f"Using cached DB data ({os.path.basename(self.current_db_path)})")
            else:
                self.status_var.set("No SQLite database selected.")

        elif selected_tab == "Gallery":
            self.load_gallery_categories()
            self.load_gallery()

        elif selected_tab == "Movie Details":
            if self.selected_file_id:
                self.load_movie_detail_view(self.selected_file_id)
                self.load_movie_metadata(self.selected_file_id)

        elif selected_tab == "Update":
            # Populate update form for currently selected DB record
            if self.selected_file_id:
                self.populate_update_form(self.selected_file_id)

        elif selected_tab == "To Download":
            # Do not auto-run the To Download check when switching tabs.
            # If we already have results cached, refresh the tree view; otherwise show instructions.
            if getattr(self, 'missing_online_rows', None):
                self.refresh_missing_online_tree(self.missing_online_rows)
            else:
                self.status_var.set("Paste a webpage URL in To Download and click Check Page.")

    def reset_scan(self):
        """Clear scanned file results and reset UI"""

        # Clear file table
        self.file_table.delete(*self.file_table.get_children())

        # Clear stored paths & scan cache
        self.file_paths.clear()
        self.all_files_info.clear()

        # Clear folder selection
        self.folder_path.set("")

        # Reset file counters
        self.files_count_var.set("Files: 0")
        self.total_files_var.set("Total Files: 0")
        self.total_size_var.set("Total Size: 0 MB")

        # Clear statistics tables
        if hasattr(self, "file_ext_tree"):
            self.file_ext_tree.delete(*self.file_ext_tree.get_children())

        if hasattr(self, "file_storage_tree"):
            self.file_storage_tree.delete(*self.file_storage_tree.get_children())

        # Clear file detail panel
        for var in self.detail_vars.values():
            var.set("")

        # Reset status
        self.status_var.set("Scan results cleared.")


    def setup_main_tab(self, parent):
        folder_frame = tk.Frame(parent)
        folder_frame.pack(fill="x", pady=5)

        tk.Label(folder_frame, text="Folder: ").pack(side="left")
        self.folder_path = tk.StringVar()
        tk.Entry(folder_frame, textvariable=self.folder_path, width=60).pack(side="left", padx=5)

        tk.Button(folder_frame, text="Browse", command=self.browse_folder).pack(side="left")
        tk.Button(
            folder_frame,
            text="Reset Scan",
            command=self.reset_scan
        ).pack(side="left", padx=5)

        opt_frame = tk.Frame(parent)
        opt_frame.pack(fill="x", pady=5)

        self.include_subdirs = tk.BooleanVar()
        tk.Checkbutton(opt_frame, text="Include subdirectories", variable=self.include_subdirs).pack(side="left")

        tk.Button(opt_frame, text="List Files", command=self.list_files).pack(side="right")

        tk.Button(
                opt_frame,
                text="Update Storage ID from Scan",
                command=self.update_storage_id_from_scan
            ).pack(side="right")

        tk.Button(
            opt_frame,
            text="Show Unmatched Files",
            command=self.show_unmatched_scanned_files
        ).pack(side="right", padx=5)


        # Split list + details
        split = tk.Frame(parent)
        split.pack(fill="both", expand=True)

        # LEFT: table (tabular view)
        left = tk.Frame(split)
        left.pack(side="left", fill="both", expand=True)

        self.files_count_var = tk.StringVar(value="Files: 0")
        tk.Label(left, textvariable=self.files_count_var).pack(anchor="w")

        table_frame = tk.Frame(left)
        table_frame.pack(fill="both", expand=True)

        cols = ("name", "ext", "size")
        self.file_table = ttk.Treeview(
            table_frame,
            columns=cols,
            show="headings"
        )

        self.file_table.heading("name", text="File Name")
        self.file_table.heading("ext", text="File Extension")
        self.file_table.heading("size", text="File Size")

        self.file_table.column("name", width=380, anchor="w")
        self.file_table.column("ext", width=120, anchor="center")
        self.file_table.column("size", width=120, anchor="e")

        ys = ttk.Scrollbar(table_frame, orient="vertical", command=self.file_table.yview)
        xs = ttk.Scrollbar(table_frame, orient="horizontal", command=self.file_table.xview)
        self.file_table.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)

        self.file_table.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")

        self.file_table.bind("<<TreeviewSelect>>", self.on_file_table_select)
        self.file_table.bind("<Double-1>", self.on_file_table_double_click)


        # RIGHT: details
        right = tk.Frame(split, width=350)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(right, text="File Details:", font=("Arial", 12, "bold")).pack(anchor="w")

        details_frame = tk.Frame(right)
        details_frame.pack(fill="x", pady=10)

        labels = ["File Name", "Extension", "Size", "Creation Date"]
        self.detail_vars = {}
        for i, lbl in enumerate(labels):
            tk.Label(details_frame, text=lbl + ":").grid(row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            tk.Label(details_frame, textvariable=var).grid(row=i, column=1, sticky="w", pady=4)
            self.detail_vars[lbl] = var

        # ================= FILE SCAN STATISTICS =================

        totals_frame = ttk.Frame(parent)
        totals_frame.pack(fill="x", padx=8, pady=(4,2))

        self.total_files_var = tk.StringVar(value="Total Files: 0")
        self.total_size_var = tk.StringVar(value="Total Size: 0 MB")

        ttk.Label(totals_frame, textvariable=self.total_files_var,
                font=("Segoe UI", 10, "bold")).pack(side="left", padx=10)

        ttk.Label(totals_frame, textvariable=self.total_size_var,
                font=("Segoe UI", 10, "bold")).pack(side="left", padx=20)

        # ---------- Files by Extension ----------
        ext_frame = ttk.LabelFrame(parent, text="Files by Extension (Scan Results)")
        ext_frame.pack(fill="x", padx=8, pady=4)

        self.file_ext_tree = ttk.Treeview(
            ext_frame, columns=("Ext", "Files", "Total Size"),
            show="headings", height=5
        )
        self.file_ext_tree.pack(fill="x", padx=6, pady=4)

        self.file_ext_tree.heading("Ext", text="Extension")
        self.file_ext_tree.heading("Files", text="File Count")
        self.file_ext_tree.heading("Total Size", text="Total Size")

        self.file_ext_tree.column("Ext", width=120, anchor="w")
        self.file_ext_tree.column("Files", width=100, anchor="e")
        self.file_ext_tree.column("Total Size", width=140, anchor="e")


        # ---------- Storage Summary ----------
        storage_stats_frame = ttk.LabelFrame(parent, text="Storage Summary (Scan Results)")
        storage_stats_frame.pack(fill="x", padx=8, pady=4)

        self.file_storage_tree = ttk.Treeview(
            storage_stats_frame, columns=("Storage", "Files", "Total Size"),
            show="headings", height=5
        )
        self.file_storage_tree.pack(fill="x", padx=6, pady=4)

        self.file_storage_tree.heading("Storage", text="Attached Media")
        self.file_storage_tree.heading("Files", text="File Count")
        self.file_storage_tree.heading("Total Size", text="Total Size")

        self.file_storage_tree.column("Storage", width=200, anchor="w")
        self.file_storage_tree.column("Files", width=100, anchor="e")
        self.file_storage_tree.column("Total Size", width=140, anchor="e")
        
        
        bottom = tk.Frame(parent)
        bottom.pack(fill="x", pady=10)

        storage_frame = tk.Frame(parent)
        storage_frame.pack(fill="x", padx=5, pady=3)

        tk.Label(storage_frame, text="Storage ID:").pack(side="left")

        self.storage_id_combo = ttk.Combobox(
            storage_frame,
            textvariable=self.storage_id_var,
            width=23,          # ttk uses slightly different sizing
            state="normal"     # <-- allows typing NEW Storage IDs
        )

        self.storage_id_combo.pack(side="left", padx=5)
        self.storage_id_entry = self.storage_id_combo


        tk.Label(
            storage_frame,
            text="(e.g. HDD_MEDIA_01)",
            fg="gray"
        ).pack(side="left")

        tk.Button(bottom, text="Export to Excel", command=self.export_to_excel).pack(side="right")
        tk.Button(bottom, text="Export to SQLite", command=self.export_to_sqlite).pack(side="right", padx=5)

    def setup_folder_scan_tab(self, parent):
        folder_frame = tk.Frame(parent)
        folder_frame.pack(fill="x", pady=5)

        tk.Label(folder_frame, text="Folder: ").pack(side="left")
        self.scan_folder_path = tk.StringVar()
        tk.Entry(folder_frame, textvariable=self.scan_folder_path, width=60).pack(side="left", padx=5)
        tk.Button(folder_frame, text="Browse", command=self.browse_scan_folder).pack(side="left")

        opt_frame = tk.Frame(parent)
        opt_frame.pack(fill="x", pady=5)

        self.scan_include_subdirs = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt_frame,
            text="Include subdirectories",
            variable=self.scan_include_subdirs
        ).pack(side="left")

        self.scan_match_ext = tk.BooleanVar(value=True)
        self.scan_match_size = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opt_frame,
            text="Use extension in comparison",
            variable=self.scan_match_ext
        ).pack(side="left", padx=(10, 0))
        tk.Checkbutton(
            opt_frame,
            text="Use size in comparison",
            variable=self.scan_match_size
        ).pack(side="left", padx=(10, 0))

        tk.Button(opt_frame, text="Reset", command=self.reset_scan_folder_tab).pack(side="right", padx=(0, 5))
        tk.Button(opt_frame, text="Scan Folder", command=self.scan_folder).pack(side="right")

        header_frame = tk.Frame(parent)
        header_frame.pack(fill="x", pady=2)
        self.scan_files_count_var = tk.StringVar(value="Files: 0")
        self.scan_matches_count_var = tk.StringVar(value="Matched Files: 0")
        tk.Label(header_frame, textvariable=self.scan_files_count_var).pack(side="left")
        tk.Label(header_frame, textvariable=self.scan_matches_count_var, fg="darkgreen").pack(side="left", padx=(20,0))

        table_frame = tk.Frame(parent)
        table_frame.pack(fill="both", expand=True)

        cols = ("name", "ext", "size", "storage", "matched", "dest_name")
        self.scan_tree = ttk.Treeview(table_frame, columns=cols, show="headings")
        self.scan_tree.heading("name", text="Source Name", command=lambda c="name": self.sort_scan_by_column(c))
        self.scan_tree.heading("ext", text="Extension", command=lambda c="ext": self.sort_scan_by_column(c))
        self.scan_tree.heading("size", text="File Size", command=lambda c="size": self.sort_scan_by_column(c))
        self.scan_tree.heading("storage", text="Storage ID", command=lambda c="storage": self.sort_scan_by_column(c))
        self.scan_tree.heading("matched", text="Match Count", command=lambda c="matched": self.sort_scan_by_column(c))
        self.scan_tree.heading("dest_name", text="Destination Name", command=lambda c="dest_name": self.sort_scan_by_column(c))

        self.scan_tree.column("name", width=260, anchor="w")
        self.scan_tree.column("ext", width=100, anchor="center")
        self.scan_tree.column("size", width=120, anchor="e")
        self.scan_tree.column("storage", width=140, anchor="center")
        self.scan_tree.column("matched", width=100, anchor="center")
        self.scan_tree.column("dest_name", width=220, anchor="w")

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.scan_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.scan_tree.xview)
        self.scan_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.scan_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")

        self.scan_tree.bind("<<TreeviewSelect>>", lambda e: None)
        self.scan_tree.bind("<Double-1>", self.on_scan_tree_double_click)

        dest_frame = tk.Frame(parent)
        dest_frame.pack(fill="x", pady=6)

        tk.Label(dest_frame, text="Destination Folder:").pack(side="left")
        self.scan_dest_path = tk.StringVar()
        tk.Entry(dest_frame, textvariable=self.scan_dest_path, width=60).pack(side="left", padx=5)
        tk.Button(dest_frame, text="Browse", command=self.browse_destination_folder).pack(side="left")

        action_frame = tk.Frame(parent)
        action_frame.pack(fill="x", pady=6)

        self.scan_operation = tk.StringVar(value="copy")
        tk.Radiobutton(action_frame, text="Copy selected", variable=self.scan_operation, value="copy").pack(side="left")
        tk.Radiobutton(action_frame, text="Move selected", variable=self.scan_operation, value="move").pack(side="left", padx=(10,0))

        self.scan_add_to_db = tk.BooleanVar(value=False)
        self.scan_add_to_db_checkbox = tk.Checkbutton(action_frame, text="Add moved/copied files to DB", variable=self.scan_add_to_db)
        self.scan_add_to_db_checkbox.pack(side="left", padx=(20,0))

        self.scan_edit_dest_button = tk.Button(action_frame, text="Edit Selected Destination Name", command=self.edit_selected_destination_name)
        self.scan_edit_dest_button.pack(side="right")
        self.scan_copy_move_button = tk.Button(action_frame, text="Copy/Move Selected", command=self.copy_or_move_selected_files)
        self.scan_copy_move_button.pack(side="right", padx=5)

        progress_frame = tk.Frame(parent)
        progress_frame.pack(fill="x", pady=4)
        self.scan_progress_var = tk.StringVar(value="")
        self.scan_progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", length=240, mode="determinate")
        self.scan_progress_bar.pack(side="left", padx=(0, 10), pady=2)
        tk.Label(progress_frame, textvariable=self.scan_progress_var).pack(side="left")

    def browse_scan_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.scan_folder_path.set(folder)
            self.status_var.set(f"Selected scan path: {folder}")

    def reset_scan_folder_tab(self):
        """Clear scan-folder results and restore the tab to its default state."""
        if hasattr(self, "scan_tree"):
            self.scan_tree.delete(*self.scan_tree.get_children())
            try:
                self.scan_tree.selection_remove(self.scan_tree.selection())
            except Exception:
                pass

        if hasattr(self, "scan_item_map"):
            self.scan_item_map.clear()

        if hasattr(self, "scan_results"):
            self.scan_results = []

        if hasattr(self, "scan_folder_path"):
            self.scan_folder_path.set("")

        if hasattr(self, "scan_dest_path"):
            self.scan_dest_path.set("")

        if hasattr(self, "scan_include_subdirs"):
            self.scan_include_subdirs.set(True)

        if hasattr(self, "scan_match_ext"):
            self.scan_match_ext.set(True)

        if hasattr(self, "scan_match_size"):
            self.scan_match_size.set(False)

        if hasattr(self, "scan_operation"):
            self.scan_operation.set("copy")

        if hasattr(self, "scan_add_to_db"):
            self.scan_add_to_db.set(False)

        if hasattr(self, "scan_files_count_var"):
            self.scan_files_count_var.set("Files: 0")

        if hasattr(self, "scan_matches_count_var"):
            self.scan_matches_count_var.set("Matched Files: 0")

        if hasattr(self, "scan_progress_var"):
            self.scan_progress_var.set("")

        if hasattr(self, "scan_progress_bar"):
            try:
                self.scan_progress_bar.config(value=0, maximum=100)
            except Exception:
                pass

        if hasattr(self, "scan_inline_entry") and self.scan_inline_entry:
            entry, _ = self.scan_inline_entry
            try:
                entry.destroy()
            except Exception:
                pass
            self.scan_inline_entry = None

        if hasattr(self, "scan_operation_in_progress"):
            self.scan_operation_in_progress = False

        if hasattr(self, "status_var"):
            self.status_var.set("Scan folder tab reset.")

    def scan_folder(self):
        folder = self.scan_folder_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder to scan.")
            return

        self.scan_tree.delete(*self.scan_tree.get_children())
        self.scan_item_map.clear()
        self.scan_results = get_files_info(
            folder,
            self.allowed_video_exts,
            self.scan_include_subdirs.get()
        )

        for info in self.scan_results:
            info["dest_name"] = info.get("name_without_ext", "")

        self.scan_results.sort(key=lambda i: i["name_without_ext"].lower())

        use_extension = self.scan_match_ext.get()
        use_size = self.scan_match_size.get()

        matched = 0
        for info in self.scan_results:
            storage_id, match_count = self.find_nearest_db_match(info, use_extension, use_size)
            if match_count > 0:
                matched += 1
            info["storage_id"] = storage_id or ""
            info["match_count"] = match_count
            iid = self.scan_tree.insert(
                "",
                "end",
                values=(
                    info["name_without_ext"],
                    info["extension"],
                    format_size(info["size"]),
                    info["storage_id"],
                    info["match_count"],
                    info["dest_name"]
                )
            )
            self.scan_item_map[iid] = info

        total = len(self.scan_results)
        self.scan_files_count_var.set(f"Files: {total}")
        self.scan_matches_count_var.set(f"Matched Files: {matched}")
        self.status_var.set(f"Scanned {total} files. Matches found: {matched}.")

    def refresh_scan_tree(self):
        self.scan_tree.delete(*self.scan_tree.get_children())
        self.scan_item_map.clear()
        for info in self.scan_results:
            iid = self.scan_tree.insert(
                "",
                "end",
                values=(
                    info.get("name_without_ext", ""),
                    info.get("extension", ""),
                    format_size(info.get("size", 0)),
                    info.get("storage_id", ""),
                    info.get("match_count", 0),
                    info.get("dest_name", "")
                )
            )
            self.scan_item_map[iid] = info

    def sort_scan_by_column(self, col):
        if not self.scan_results:
            return

        col_map = {
            "name": "name_without_ext",
            "ext": "extension",
            "size": "size",
            "storage": "storage_id",
            "matched": "match_count",
            "dest_name": "dest_name"
        }
        key = col_map.get(col)
        if not key:
            return

        reverse = self._scan_sort_reverse.get(col, False)

        def sort_key(info):
            if col in ("size", "matched"):
                try:
                    return int(info.get(key, 0) or 0)
                except Exception:
                    return 0
            return str(info.get(key, "") or "").lower()

        self.scan_results = sorted(self.scan_results, key=sort_key, reverse=not reverse)
        self._scan_sort_reverse[col] = not reverse
        self.refresh_scan_tree()

    def find_nearest_db_match(self, file_info, use_extension=True, use_size=False):
        if not self.current_db_path or not os.path.exists(self.current_db_path):
            return None, 0

        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT file_name, extension, size_bytes, storage_id, year FROM Files"
            )
            rows = cur.fetchall()
            conn.close()
        except Exception:
            return None, 0

        target_name = file_info.get("name_without_ext", "")
        target_ext = file_info.get("extension", "").lstrip(".").lower()
        target_size = file_info.get("size", 0)
        target_year = file_info.get("year")
        target_norm = normalize_title_for_match(target_name)

        best_storage = None
        best_score = -999
        match_count = 0

        for db_name, db_ext, db_size, storage_id, db_year in rows:
            db_norm = normalize_title_for_match(db_name or "")
            if db_norm != target_norm:
                continue

            match_count += 1
            score = 0
            if use_extension and db_ext and target_ext:
                if db_ext.lstrip(".").lower() == target_ext:
                    score += 6
                else:
                    score -= 2

            if target_year and db_year:
                if str(target_year) == str(db_year):
                    score += 4
                else:
                    score -= 1

            if use_size:
                size_diff = abs((db_size or 0) - (target_size or 0))
                if size_diff == 0:
                    score += 10
                else:
                    score += max(0, 5 - (size_diff / (1024 * 1024 * 5)))

            if score > best_score:
                best_score = score
                best_storage = storage_id

        return best_storage, match_count

    def browse_destination_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.scan_dest_path.set(folder)
            self.status_var.set(f"Selected destination: {folder}")

    def on_scan_tree_double_click(self, event):
        row = self.scan_tree.identify_row(event.y)
        col = self.scan_tree.identify_column(event.x)
        if not row or col != "#5":
            return
        self.edit_destination_name(row)

    def edit_selected_destination_name(self):
        selected = self.scan_tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a record to edit.")
            return
        if len(selected) > 1:
            messagebox.showwarning("Multiple Selection", "Please edit one record at a time.")
            return
        self.edit_destination_name(selected[0])

    def edit_destination_name(self, iid):
        if self.scan_inline_entry:
            self.finish_scan_dest_edit()

        bbox = self.scan_tree.bbox(iid, column="dest_name")
        if not bbox:
            return

        x, y, width, height = bbox
        self.scan_tree.update_idletasks()

        entry = tk.Entry(self.scan_tree)
        entry.insert(0, self.scan_item_map.get(iid, {}).get("dest_name", ""))
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()
        entry.selection_range(0, tk.END)

        entry.bind("<Return>", lambda e: self.finish_scan_dest_edit())
        entry.bind("<Escape>", lambda e: self.cancel_scan_dest_edit())
        entry.bind("<FocusOut>", lambda e: self.finish_scan_dest_edit())

        self.scan_inline_entry = (entry, iid)

    def finish_scan_dest_edit(self):
        if not self.scan_inline_entry:
            return

        entry, iid = self.scan_inline_entry
        new_name = entry.get().strip()
        entry.destroy()
        self.scan_inline_entry = None

        if not new_name:
            return

        info = self.scan_item_map.get(iid)
        if not info:
            return

        info["dest_name"] = new_name
        values = list(self.scan_tree.item(iid, "values"))
        if len(values) >= 5:
            values[4] = new_name
            self.scan_tree.item(iid, values=values)

    def cancel_scan_dest_edit(self):
        if not self.scan_inline_entry:
            return
        entry, _ = self.scan_inline_entry
        entry.destroy()
        self.scan_inline_entry = None

    def copy_or_move_selected_files(self):
        dest_folder = self.scan_dest_path.get().strip()
        if not dest_folder:
            messagebox.showerror("Destination Required", "Please select a destination folder.")
            return

        if not os.path.exists(dest_folder):
            try:
                os.makedirs(dest_folder, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Error", f"Unable to create destination folder:\n{e}")
                return

        selected = self.scan_tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select one or more records to move/copy.")
            return

        if self.scan_operation_in_progress:
            messagebox.showinfo("In Progress", "A copy/move operation is already running.")
            return

        operation = self.scan_operation.get()
        add_to_db = self.scan_add_to_db.get()
        selected_info = [self.scan_item_map.get(iid) for iid in selected if self.scan_item_map.get(iid)]

        self.scan_operation_in_progress = True
        self._set_folder_scan_controls(False)
        self.scan_progress_bar.config(maximum=len(selected_info), value=0)
        self.scan_progress_var.set("Starting transfer...")
        self.status_var.set("Copy/Move in progress...")

        thread = threading.Thread(
            target=self._copy_or_move_worker,
            args=(selected_info, dest_folder, operation, add_to_db),
            daemon=True
        )
        thread.start()

    def _unique_path(self, path):
        if not os.path.exists(path):
            return path

        base, ext = os.path.splitext(path)
        counter = 1
        candidate = f"{base}_{counter}{ext}"
        while os.path.exists(candidate):
            counter += 1
            candidate = f"{base}_{counter}{ext}"
        return candidate

    def _set_folder_scan_controls(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for widget in [
            self.scan_add_to_db_checkbox,
            self.scan_edit_dest_button,
            self.scan_copy_move_button,
            self.scan_dest_path,
            self.scan_operation,
            self.scan_match_ext,
            self.scan_match_size
        ]:
            try:
                if isinstance(widget, tk.Variable):
                    continue
                widget.configure(state=state)
            except Exception:
                pass

    def _copy_or_move_worker(self, selected_info, dest_folder, operation, add_to_db):
        moved = 0
        copied = 0
        db_added = 0
        skipped = 0
        problems = []

        conn = None
        cur = None
        if add_to_db:
            try:
                conn = self.get_connection()
                cur = conn.cursor()
            except Exception as e:
                self.root.after(0, lambda: self._finish_scan_copy_move(0, 0, 0, 0, [f"DB error: {e}"], len(selected_info)))
                return

        for idx, info in enumerate(selected_info, start=1):
            src_path = info["full_path"]
            ext = info["extension"]
            dest_base = info.get("dest_name", info.get("name_without_ext", ""))
            dest_name = f"{dest_base}{ext}"
            dest_path = os.path.join(dest_folder, dest_name)
            dest_path = self._unique_path(dest_path)

            try:
                if operation == "move":
                    shutil.move(src_path, dest_path)
                    moved += 1
                    info["full_path"] = dest_path
                else:
                    shutil.copy2(src_path, dest_path)
                    copied += 1
            except Exception as e:
                problems.append(f"{src_path}: {e}")
                self.root.after(0, lambda i=idx: self._update_scan_progress(i, len(selected_info)))
                continue

            if add_to_db and cur:
                dest_storage = detect_storage_id_from_path(dest_path)
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO Files (file_name, extension, size_bytes, storage_id, creation_date, full_path, year, category) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            dest_base,
                            ext,
                            info["size"],
                            dest_storage,
                            info.get("creation_date"),
                            dest_path,
                            info.get("year"),
                            info.get("category")
                        )
                    )
                    if cur.rowcount > 0:
                        db_added += 1
                    else:
                        skipped += 1
                except sqlite3.IntegrityError:
                    skipped += 1
                except Exception as e:
                    problems.append(f"DB add {dest_path}: {e}")

            self.root.after(0, lambda i=idx: self._update_scan_progress(i, len(selected_info)))

        if conn:
            conn.commit()
            conn.close()

        self.root.after(0, lambda: self._finish_scan_copy_move(moved, copied, db_added, skipped, problems, len(selected_info)))

    def _update_scan_progress(self, current, total):
        self.scan_progress_bar.config(value=current, maximum=total)
        self.scan_progress_var.set(f"{current}/{total} files processed")
        self.status_var.set(self.scan_progress_var.get())

    def _finish_scan_copy_move(self, moved, copied, db_added, skipped, problems, total):
        summary = []
        if moved:
            summary.append(f"Moved: {moved}")
        if copied:
            summary.append(f"Copied: {copied}")
        if self.scan_add_to_db.get():
            summary.append(f"DB added: {db_added}")
            summary.append(f"Skipped DB: {skipped}")
        if problems:
            summary.append(f"Errors: {len(problems)}")

        if summary:
            messagebox.showinfo("Operation Complete", "\n".join(summary))
        else:
            messagebox.showinfo("Operation Complete", "No files were moved or copied.")

        self.scan_operation_in_progress = False
        self._set_folder_scan_controls(True)
        self.scan_progress_var.set("Completed")
        self.status_var.set("Move/Copy completed.")
        value = self.storage_id_var.get().strip()
        return value if value else "UNKNOWN"

    def show_unmatched_scanned_files(self):
        if not self.folder_path.get():
            messagebox.showwarning("Warning", "Please select a folder first.")
            return

        scanned_files = get_files_info(
            self.folder_path.get(),
            self.allowed_video_exts,
            self.include_subdirs.get()
        )



        if not scanned_files:
            messagebox.showinfo("Info", "No video files found in selected path.")
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()
        except Exception as e:
            messagebox.showerror("Database Error", str(e))
            return

        current_sid = self.get_storage_id()
        unmatched = []

        for f in scanned_files:
            cur.execute("""
                SELECT id, size_bytes, full_path, storage_id
                FROM Files
                WHERE file_name=? AND size_bytes=?
            """, (f["name_without_ext"], f["size"]))

            row = cur.fetchone()

            if row:
                db_id, db_size, db_path, db_sid = row

                if db_sid == current_sid:
                    if not paths_equal_ignore_drive(db_path, f["full_path"]):
                        reason = "Movie moved (update path/storage)"
                        unmatched.append((f, reason, db_id))
                    else:
                        continue  # perfectly in sync

                else:
                    reason = "Duplicate video on another storage (waste)"
                    unmatched.append((f, reason, db_id))

            else:
                # check if same name but different size exists
                cur.execute("""
                    SELECT 1 FROM Files WHERE file_name=?
                """, (f["name_without_ext"],))
                if cur.fetchone():
                    reason = "Name match, size mismatch"
                else:
                    reason = "Not present in database"

                unmatched.append((f, reason, None))

        conn.close()

        if not unmatched:
            messagebox.showinfo(
                "Result",
                "No unmatched video files found.\nDisk and database are in sync for this storage."
            )
            return

        self._show_unmatched_window(unmatched)

    def _show_unmatched_window(self, files):
        win = tk.Toplevel(self.root)
        win.title("Unmatched Video Files")
        win.geometry("1150x620")
        win.transient(self.root)
        win.grab_set()

        from collections import Counter
        reason_counts = Counter([r for _, r, _ in files])
        summary_text = "   |   ".join([f"{k}: {v}" for k, v in reason_counts.items()])

        tk.Label(win, text=summary_text,
                fg="darkblue", font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 2))

        # ---------- Filter ----------
        filter_frame = tk.Frame(win)
        filter_frame.pack(fill="x", padx=10, pady=4)

        tk.Label(filter_frame, text="Filter:").pack(side="left")

        reasons = ["ALL"] + sorted(reason_counts.keys())
        reason_var = tk.StringVar(value="ALL")

        combo = ttk.Combobox(filter_frame, values=reasons,
                            state="readonly", textvariable=reason_var, width=45)
        combo.pack(side="left", padx=6)

        # ---------- Table ----------
        cols = ("Name", "Size", "Reason", "Full Path")
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="extended")
        tree.pack(fill="both", expand=True, padx=10, pady=6)

        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, anchor="w")

        tree.column("Name", width=260)
        tree.column("Size", width=100, anchor="e")
        tree.column("Reason", width=320)
        tree.column("Full Path", width=520)

        row_file_map: Dict[str, Tuple[dict, str, Optional[int]]] = {}

        def populate(selected="ALL"):
            tree.delete(*tree.get_children())
            row_file_map.clear()

            for f, reason, db_id in files:
                if selected != "ALL" and reason != selected:
                    continue

                iid = tree.insert("", "end", values=(
                    f["name_without_ext"],
                    format_size(f["size"]),
                    reason,
                    f["full_path"]
                ))
                row_file_map[iid] = (f, reason, db_id)

        populate()
        combo.bind("<<ComboboxSelected>>", lambda e: populate(reason_var.get()))

        # ---------- Buttons ----------
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", padx=10, pady=6)

        tk.Button(btn_frame, text="Apply Action", command=lambda: apply_action()).pack(side="left")
        tk.Button(btn_frame, text="Open Location", command=lambda: open_location()).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Close", command=win.destroy).pack(side="right")

        # ---------- Helpers ----------
        def open_location():
            sel = tree.selection()
            if not sel:
                return
            val = row_file_map.get(sel[0])
            if not val:
                return
            f, _, _ = val
            os.startfile(os.path.dirname(f["full_path"]))

        def apply_action():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Warning", "Select files first.")
                return

            reasons = {row_file_map[i][1] for i in sel}

            # 🚨 HARD BLOCK
            if any("Duplicate video on another storage" in r for r in reasons):
                messagebox.showerror(
                    "Blocked",
                    "Some selected files already exist on another storage.\n\n"
                    "This represents duplicate storage waste and is NOT allowed."
                )
                return

            try:
                conn = self.get_connection()
                cur = conn.cursor()

                inserted = 0
                updated = 0

                for iid in sel:
                    f, reason, db_id = row_file_map[iid]

                    if reason == "Movie moved (update path/storage)":
                        cur.execute(UPDATE_FILE_MOVE, (
                            self.get_storage_id(),
                            f["full_path"],
                            format_date(f["creation_date"]),
                            db_id
                        ))
                        updated += 1

                    elif reason in ("Not present in database", "Name match, size mismatch"):
                        cur.execute(INSERT_FILE_RECORD, (
                            f["name_without_ext"],
                            f["extension"],
                            f["size"],
                            self.get_storage_id(),
                            format_date(f["creation_date"]),
                            f["full_path"],
                            f.get("year"),
                            f.get("category")
                        ))
                        inserted += 1

                conn.commit()
                conn.close()

            except Exception as e:
                messagebox.showerror("Database Error", str(e))
                return

            messagebox.showinfo(
                "Completed",
                f"Inserted: {inserted}\nUpdated (moved): {updated}"
            )

            win.destroy()
            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

        # ---------- Double click open ----------
        def on_double_click(event):
            item = tree.identify_row(event.y)
            if not item:
                return
            val = row_file_map.get(item)
            if not val:
                return
            f, _, _ = val
            os.startfile(f["full_path"])

        tree.bind("<Double-1>", on_double_click)


    def force_insert_selected_files(self, tree, row_file_map: Dict[str, Any], parent_win) -> None:
        selected = tree.selection()

        if not selected:
            messagebox.showwarning("No Selection", "Please select one or more files.")
            return

        storage_id = self.get_storage_id()

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            select_exact = """
                SELECT id, storage_id, full_path
                FROM Files
                WHERE file_name=? AND size_bytes=?
            """

            select_name = """
                SELECT 1 FROM Files WHERE file_name=?
            """

            inserted = 0
            updated = 0
            blocked = 0
            skipped = 0

            for iid in selected:
                f = row_file_map.get(iid)
                if not f:
                    continue

                file_name = f["name_without_ext"]
                size = f["size"]
                full_path = f["full_path"]
                creation_date = format_date(f["creation_date"])
                year = f.get("year")
                category = f.get("category")

                cur.execute(select_exact, (file_name, size))
                row = cur.fetchone()

                if row:
                    db_id, db_sid, db_path = row

                    if db_sid == storage_id:
                        if not paths_equal_ignore_drive(db_path, full_path):
                            # 🔄 moved movie
                            cur.execute(UPDATE_FILE_MOVE, (
                                storage_id,
                                full_path,
                                creation_date,
                                db_id
                            ))
                            updated += 1
                        else:
                            skipped += 1
                    else:
                        # 🚨 waste duplicate
                        blocked += 1
                        continue

                else:
                    # no exact match → check name collision
                    cur.execute(select_name, (file_name,))
                    # even if name exists with different size → allowed
                    cur.execute(INSERT_FILE_RECORD, (
                        file_name,
                        f["extension"],
                        size,
                        storage_id,
                        creation_date,
                        full_path,
                        year,
                        category
                    ))
                    inserted += 1

            conn.commit()
            conn.close()

            messagebox.showinfo(
                "Force Action Complete",
                f"Inserted: {inserted}\n"
                f"Updated (moved): {updated}\n"
                f"Blocked (waste duplicates): {blocked}\n"
                f"Skipped (already in sync): {skipped}"
            )

            parent_win.destroy()
            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

        except Exception as e:
            messagebox.showerror("Insert Error", f"Operation failed:\n{e}")

    def get_all_categories(self):
        try:
            #print("Current DB path:", self.current_db_path)  # DEBUG

            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(SELECT_ALL_CATEGORIES)
            rows = cur.fetchall()
            conn.close()

            #print("Category rows:", rows)  # DEBUG

            return [r[0] for r in rows]

        except Exception as e:
            print("CATEGORY FETCH ERROR:", e)
            return []

    def add_new_category(self, name):
        if not name.strip():
            return False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(INSERT_CATEGORY, (name.strip(),))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def update_category_statistics(self):

        if not self.current_db_path:
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT COALESCE(m.category,f.category) AS cat,
                    COUNT(*)
                FROM Files f
                LEFT JOIN MovieDetails m
                ON f.id = m.file_id
                WHERE cat IS NOT NULL AND TRIM(cat) != ''
                GROUP BY cat
                ORDER BY COUNT(*) DESC
            """)

            rows = cur.fetchall()
            conn.close()

            self.category_tree.delete(*self.category_tree.get_children())

            for cat, cnt in rows:
                self.category_tree.insert("", "end", values=(cat, cnt))

        except Exception as e:
            print("Category stats error:", e)   

    def setup_stats_tab(self, parent):
        """
        summary = tk.Frame(parent)
        summary.pack(fill="x", pady=5)

        self.total_files_var = tk.StringVar(value="Total Files: 0")
        self.total_size_var = tk.StringVar(value="Total Size: 0 bytes")

        tk.Label(summary, textvariable=self.total_files_var, font=("Arial", 10, "bold")).pack(anchor="w")
        tk.Label(summary, textvariable=self.total_size_var, font=("Arial", 10, "bold")).pack(anchor="w")

        ext_frame = tk.Frame(parent)
        ext_frame.pack(fill="both", expand=True)

        tk.Label(ext_frame, text="Files By Extension:", font=("Arial", 10, "bold")).pack(anchor="w", pady=4)

        columns = ("Extension", "Count", "Total Size")
        self.ext_tree = ttk.Treeview(ext_frame, columns=columns, show="headings")

        for col in columns:
            self.ext_tree.heading(col, text=col)
            self.ext_tree.column(col, width=160)

        scroll = ttk.Scrollbar(ext_frame, command=self.ext_tree.yview)
        self.ext_tree.configure(yscrollcommand=scroll.set)

        self.ext_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
    """
        # -------- DATABASE STATISTICS ----------
        db_frame = tk.LabelFrame(parent, text="Database Statistics")
        db_frame.pack(fill="both", expand=True, padx=6, pady=6)

        '''        self.db_total_records_var = tk.StringVar(value="DB Records: 0")
        tk.Label(db_frame, textvariable=self.db_total_records_var,
             font=("Arial", 10, "bold")).pack(anchor="w")
        '''

        db_cols = ("Extension", "Count", "Total Size")
        self.db_ext_tree = ttk.Treeview(db_frame, columns=db_cols, show="headings")

        for col in db_cols:
            self.db_ext_tree.heading(col, text=col)
            self.db_ext_tree.column(col, width=180)

        db_scroll = ttk.Scrollbar(db_frame, command=self.db_ext_tree.yview)
        self.db_ext_tree.configure(yscrollcommand=db_scroll.set)

        self.db_ext_tree.pack(side="left", fill="both", expand=True)
        db_scroll.pack(side="right", fill="y") 

        self.db_total_records_var = tk.StringVar(value="DB Records: 0")
        tk.Label(db_frame, textvariable=self.db_total_records_var,
            font=("Arial", 10, "bold")).pack(anchor="w")

        self.db_files_size_var = tk.StringVar(value="Total Files Size: 0 MB")
        tk.Label(db_frame, textvariable=self.db_files_size_var,
            font=("Arial", 9, "bold")).pack(anchor="w")

        self.db_size_var = tk.StringVar(value="DB Size: 0 MB")
        tk.Label(db_frame, textvariable=self.db_size_var,
         font=("Arial", 9)).pack(anchor="w")
        
        # ---------------- STORAGE-WISE STATS ----------------
        storage_frame = ttk.LabelFrame(parent, text="Storage Summary")
        storage_frame.pack(fill="x", padx=8, pady=6)

        self.db_storage_tree = ttk.Treeview(
            storage_frame,
            columns=("Storage", "Files", "Total Size"),
            show="headings",
            height=4
        )
        self.db_storage_tree.pack(fill="x", padx=6, pady=4)

        self.db_storage_tree.heading("Storage", text="Storage ID")
        self.db_storage_tree.heading("Files", text="File Count")
        self.db_storage_tree.heading("Total Size", text="Total Size")

        self.db_storage_tree.column("Storage", width=200, anchor="w")
        self.db_storage_tree.column("Files", width=100, anchor="e")
        self.db_storage_tree.column("Total Size", width=140, anchor="e")
        # ---------------- CATEGORY STATISTICS ----------------
        category_frame = ttk.LabelFrame(parent, text="Category Distribution")
        category_frame.pack(fill="x", padx=8, pady=6)

        self.category_tree = ttk.Treeview(
            category_frame,
            columns=("Category", "Movies"),
            show="headings",
            height=6
        )

        self.category_tree.pack(fill="x", padx=6, pady=4)

        self.category_tree.heading("Category", text="Category")
        self.category_tree.heading("Movies", text="Movie Count")

        self.category_tree.column("Category", width=240, anchor="w")
        self.category_tree.column("Movies", width=120, anchor="e")        

        tk.Button(parent, text="Export Statistics to Excel",
          command=self.export_db_statistics_to_excel).pack(anchor="w", padx=6, pady=4)


        chart_frame = tk.LabelFrame(parent, text="Extension Distribution (DB)")
        chart_frame.pack(fill="both", expand=True, padx=6, pady=6)

        self.chart_canvas = None
        tk.Button(chart_frame, text="Refresh Pie Chart",
          command=self.draw_extension_pie_chart).pack(anchor="w", padx=4, pady=4)


        self.chart_container = tk.Frame(chart_frame)
        self.chart_container.pack(fill="both", expand=True)

    def setup_missing_online_tab(self, parent):
        top = tk.Frame(parent)
        top.pack(fill="x", padx=8, pady=6)

        tk.Label(top, text="Webpage URL:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.missing_online_url_entry = tk.Entry(
            top,
            textvariable=self.missing_online_url_var,
            width=70
        )
        self.missing_online_url_entry.pack(side="left", padx=6, fill="x", expand=True)

        # Category selector for filtering which DB categories to compare against
        cats = ["All"] + self.get_all_categories()
        self.missing_online_category_combo = ttk.Combobox(top, values=cats, textvariable=self.missing_online_category_var, width=24)
        self.missing_online_category_combo.pack(side="left", padx=6)

        # Progress bar to show checking progress
        self.missing_online_progress = ttk.Progressbar(top, orient="horizontal", length=180, mode="determinate")
        self.missing_online_progress.pack(side="left", padx=6)

        # Right-click context menu for Cut/Copy/Paste on the URL entry
        try:
            _url_entry_menu = tk.Menu(self.root, tearoff=0)
            _url_entry_menu.add_command(label="Cut", command=lambda: self.missing_online_url_entry.event_generate("<<Cut>>"))
            _url_entry_menu.add_command(label="Copy", command=lambda: self.missing_online_url_entry.event_generate("<<Copy>>"))
            _url_entry_menu.add_command(label="Paste", command=lambda: self.missing_online_url_entry.event_generate("<<Paste>>"))

            def _show_url_entry_menu(event):
                try:
                    _url_entry_menu.tk_popup(event.x_root, event.y_root)
                finally:
                    _url_entry_menu.grab_release()

            # Windows/Linux right-click
            self.missing_online_url_entry.bind("<Button-3>", _show_url_entry_menu)
            # macOS secondary click
            self.missing_online_url_entry.bind("<Button-2>", _show_url_entry_menu)
        except Exception:
            pass

        tk.Button(top, text="Check Page", width=14,
              command=self.load_missing_online_records).pack(side="left", padx=4)
        self.missing_online_stop_btn = tk.Button(top, text="Stop", width=10, state="disabled", command=self.stop_missing_online_check)
        self.missing_online_stop_btn.pack(side="left", padx=4)
        tk.Button(top, text="Open URL", width=10,
                  command=self.open_selected_missing_online_url).pack(side="left", padx=4)
        tk.Button(top, text="Copy URL", width=10,
                  command=self.copy_selected_missing_online_url).pack(side="left", padx=4)

        filter_frame = tk.Frame(parent)
        filter_frame.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(filter_frame, text="Search:").pack(side="left")
        search_entry = tk.Entry(filter_frame, textvariable=self.missing_online_search_var, width=45)
        search_entry.pack(side="left", padx=4)
        self.missing_online_search_var.trace_add("write", lambda *a: self.filter_missing_online_records())

        tk.Button(filter_frame, text="Refresh", width=12,
                  command=self.load_missing_online_records).pack(side="left", padx=6)
        tk.Button(top, text="Export to Excel", width=16,
                  command=self.export_missing_online_to_excel).pack(side="right", padx=4)

        self.missing_online_summary_var = tk.StringVar(value="Paste a webpage URL, select a category, and click Check Page.")
        tk.Label(parent, textvariable=self.missing_online_summary_var,
                 anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=8, pady=2)

        cols = ("No", "Name", "Year", "Size", "URL")
        frame = tk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=8, pady=6)

        self.missing_online_tree = ttk.Treeview(
            frame,
            columns=cols,
            show="headings",
            selectmode="extended"
        )

        for col in cols:
            self.missing_online_tree.heading(
                col,
                text=col,
                command=lambda c=col: self.sort_missing_online_by_column(c)
            )
            self.missing_online_tree.column(col, width=150, anchor="w")

        self.missing_online_tree.column("No", width=60, anchor="center")
        self.missing_online_tree.column("Year", width=70, anchor="center")
        self.missing_online_tree.column("Name", width=360)
        self.missing_online_tree.column("Size", width=120, anchor="center")
        self.missing_online_tree.column("URL", width=560)

        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.missing_online_tree.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.missing_online_tree.xview)
        self.missing_online_tree.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set
        )

        self.missing_online_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.missing_online_tree.bind("<Double-1>", lambda e: self.open_selected_missing_online_url())

    def load_missing_online_records(self):
        page_url = self.missing_online_url_var.get().strip()
        if not page_url:
            messagebox.showwarning("URL Required", "Paste the webpage URL first.")
            return

        if not self.current_db_path:
            self.missing_online_rows = []
            self.refresh_missing_online_tree([])
            self.missing_online_summary_var.set("No SQLite database selected.")
            return

        self.missing_online_summary_var.set("Checking online page...")
        self.status_var.set("Checking online page against database...")
        # prepare stop event
        self._missing_online_stop_event.clear()
        # disable main controls but enable Stop button
        self._set_missing_online_enabled(False)
        try:
            self.missing_online_stop_btn.configure(state="normal")
        except Exception:
            pass

        selected_category = self.missing_online_category_var.get()
        thread = threading.Thread(
            target=self._missing_online_worker,
            args=(page_url, selected_category),
            daemon=True
        )
        thread.start()

    def _missing_online_worker(self, page_url, category_filter="All"):
        try:
            urls = scrape_category_urls(page_url)

            conn = self.get_connection()
            cur = conn.cursor()
            # fetch name, year, metadata_url and effective category (MovieDetails.category or Files.category)
            cur.execute("""
                SELECT f.file_name, f.year, m.metadata_url, COALESCE(m.category, f.category) as category
                FROM Files f
                LEFT JOIN MovieDetails m ON f.id = m.file_id
            """)
            db_rows = cur.fetchall()
            conn.close()

            db_names = set()
            db_name_years = set()
            db_names_without_year = set()
            db_urls = set()

            for file_name, year, metadata_url, row_category in db_rows:
                norm_name = self.normalize_movie_compare_name(file_name)
                if norm_name:
                    db_names.add(norm_name)
                    effective_year = year
                    if not effective_year:
                        year_match = re.search(r"(19|20)\d{2}", str(file_name))
                        effective_year = year_match.group(0) if year_match else None

                    if effective_year:
                        db_name_years.add((norm_name, str(effective_year)))
                    else:
                        db_names_without_year.add(norm_name)

                if metadata_url:
                    db_urls.add(metadata_url.strip().rstrip("/").lower())

            missing = []
            seen = set()
            added_names = set()

            total = len(urls)
            # initialize progress bar on main thread
            self.root.after(0, lambda: self.missing_online_progress.configure(maximum=max(1, total), value=0))

            for idx, url in enumerate(urls, start=1):
                # stop requested?
                if getattr(self, "_missing_online_stop_event", None) and self._missing_online_stop_event.is_set():
                    # notify main thread to clean up
                    self.root.after(0, lambda: self._missing_online_stopped(len(urls), missing))
                    return

                normalized_url = url.strip().rstrip("/").lower()

                # skip fragment links that point to page anchors/comments
                if any(frag in normalized_url for frag in ("#comments", "#more", "#respond")):
                    continue

                # update progress UI
                self.root.after(0, lambda i=idx, t=total: (
                    self.missing_online_summary_var.set(f"Checking {i}/{t} pages..."),
                    self.status_var.set(f"Checking online page ({i}/{t})...") ,
                    self.missing_online_progress.configure(value=i)
                ))

                if normalized_url in seen:
                    continue

                # Fetch movie details to get canonical name, year, and category
                try:
                    meta = scrape_movie(url)
                except Exception:
                    # If scraping fails, skip this URL
                    continue

                # check stop again after network call
                if getattr(self, "_missing_online_stop_event", None) and self._missing_online_stop_event.is_set():
                    self.root.after(0, lambda: self._missing_online_stopped(len(urls), missing))
                    return

                name = meta.get("name", "")
                year = meta.get("year", "")
                scraped_category = meta.get("category", "") or ""

                # Skip items that look like comments or anchors
                if name.strip().startswith("#"):
                    continue

                # If a specific category is selected, ensure scraped category matches
                if category_filter and category_filter != "All":
                    if not scraped_category or category_filter.lower() not in scraped_category.lower():
                        continue

                norm_name = self.normalize_movie_compare_name(name)

                if not norm_name:
                    continue

                # avoid listing same movie name multiple times when different URLs point to it
                if norm_name in added_names:
                    seen.add(normalized_url)
                    continue

                added_names.add(norm_name)
                seen.add(normalized_url)

                exists_by_url = normalized_url in db_urls
                exists_by_name_year = bool(year and (norm_name, str(year)) in db_name_years)
                exists_by_name_without_year = bool(year and norm_name in db_names_without_year)
                exists_by_name_only = not year and norm_name in db_names

                if exists_by_url or exists_by_name_year or exists_by_name_without_year or exists_by_name_only:
                    continue

                size_text = meta.get("size_text") or ""
                size_bytes = meta.get("size_bytes")
                missing.append((name, year, size_text, size_bytes, url))

            missing.sort(key=lambda row: (row[1] or "", row[0].lower()))
            self.root.after(0, lambda: self._finish_missing_online_check(len(urls), missing))

        except Exception as e:
            self.root.after(0, lambda: self._missing_online_error(e))

    def _finish_missing_online_check(self, online_count, missing):
        self._set_missing_online_enabled(True)
        self.missing_online_rows = missing
        self.filter_missing_online_records()
        # finalize progress UI
        try:
            self.missing_online_progress.configure(value=0)
        except Exception:
            pass
        try:
            self.missing_online_stop_btn.configure(state="disabled")
        except Exception:
            pass

        self.missing_online_summary_var.set(
            f"Online movies found: {online_count} | Not in database: {len(missing)}"
        )
        self.status_var.set(f"To Download check complete: {len(missing)} missing.")

    def _missing_online_error(self, error):
        self._set_missing_online_enabled(True)
        messagebox.showerror("To Download Error", str(error))
        self.status_var.set("Failed to check online page.")
        try:
            self.missing_online_stop_btn.configure(state="disabled")
        except Exception:
            pass

    def stop_missing_online_check(self):
        # Signal the background worker to stop
        try:
            self._missing_online_stop_event.set()
        except Exception:
            pass
        try:
            self.missing_online_stop_btn.configure(state="disabled")
        except Exception:
            pass
        self.status_var.set("Stopping To Download search...")
        self.missing_online_summary_var.set("Stopping...")

    def _missing_online_stopped(self, processed_count, missing):
        # Called on main thread when worker stops early via stop event
        self._set_missing_online_enabled(True)
        try:
            self.missing_online_progress.configure(value=0)
        except Exception:
            pass
        try:
            self.missing_online_stop_btn.configure(state="disabled")
        except Exception:
            pass

        # keep partial results
        self.missing_online_rows = missing
        self.filter_missing_online_records()

        self.missing_online_summary_var.set(f"Search stopped after checking {processed_count} pages. Not in database: {len(missing)}")
        self.status_var.set("To Download search stopped by user.")

    def _set_missing_online_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in (
            getattr(self, "missing_online_url_entry", None),
            getattr(self, "missing_online_tree", None),
        ):
            if not widget:
                continue
            try:
                widget.configure(state=state)
            except Exception:
                try:
                    widget.state(("!disabled",) if enabled else ("disabled",))
                except Exception:
                    pass
        # stop button handled separately (enabled only while running)
        try:
            if enabled:
                self.missing_online_stop_btn.configure(state="disabled")
            else:
                self.missing_online_stop_btn.configure(state="normal")
        except Exception:
            pass

    def movie_name_year_from_url(self, url):
        slug = url.rstrip("/").split("/")[-1]
        match = re.search(r"(.+)-((?:19|20)\d{2})$", slug)
        if match:
            name = match.group(1).replace("-", " ").title()
            year = match.group(2)
        else:
            name = slug.replace("-", " ").title()
            year = ""
        return name, year

    def normalize_movie_compare_name(self, name):
        if not name:
            return ""

        base = os.path.splitext(str(name))[0]
        base = re.sub(r"(19|20)\d{2}", "", base)
        return normalize_name(base)

    def filter_missing_online_records(self):
        rows = list(self.missing_online_rows)
        q = self.missing_online_search_var.get().strip().lower()

        if q:
            tokens = [re.sub(r"[^0-9a-z]", "", t) for t in re.findall(r"\w+", q) if t]

            def matches(row):
                searchable = " ".join(str(v or "") for v in row)
                normalized = re.sub(r"[^0-9a-z]", "", searchable.lower())
                return all(token in normalized for token in tokens if token)

            rows = [row for row in rows if matches(row)]

        self.refresh_missing_online_tree(rows)

    def refresh_missing_online_tree(self, rows):
        self.missing_online_tree.delete(*self.missing_online_tree.get_children())

        for idx, row in enumerate(rows, start=1):
            if len(row) >= 5:
                name, year, size_text, size_bytes, url = row[0], row[1], row[2], row[3], row[4]
            elif len(row) == 4:
                name, year, size_text, url = row[0], row[1], row[2], row[3]
                size_bytes = None
            else:
                name, year, url = row[0], row[1], row[2] if len(row) > 2 else ""
                size_text = ""
                size_bytes = None

            self.missing_online_tree.insert(
                "",
                "end",
                values=(
                    idx,
                    name,
                    year if year else "",
                    size_text or "N/A",
                    url
                )
            )

        total = len(rows)
        source_total = len(self.missing_online_rows)
        if total == source_total:
            self.missing_online_summary_var.set(f"Not in database: {source_total}")
        else:
            self.missing_online_summary_var.set(
                f"Not in database: {source_total} | Filtered: {total}"
            )

    def sort_missing_online_by_column(self, col):
        col_map = {
            "No": None,
            "Name": 0,
            "Year": 1,
            "Size": "size",
            "URL": 4,
        }
        idx = col_map.get(col)
        if idx is None:
            return

        reverse = self._missing_online_sort_reverse.get(col, False)

        def sort_key(row):
            if col == "Size":
                try:
                    return int(row[3] or 0)
                except Exception:
                    return 0

            value = row[idx]
            if col == "Year":
                try:
                    return int(value)
                except Exception:
                    return 0
            return str(value or "").lower()

        self.missing_online_rows = sorted(
            self.missing_online_rows,
            key=sort_key,
            reverse=not reverse
        )
        self._missing_online_sort_reverse[col] = not reverse
        self.filter_missing_online_records()

    def get_selected_missing_online_url(self):
        selected = self.missing_online_tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Select a To Download row first.")
            return None

        values = self.missing_online_tree.item(selected[0], "values")
        return values[4] if values and len(values) > 4 else None

    def open_selected_missing_online_url(self):
        url = self.get_selected_missing_online_url()
        if not url:
            return

        try:
            os.startfile(url)
        except Exception as e:
            messagebox.showerror("Open URL Error", str(e))

    def copy_selected_missing_online_url(self):
        url = self.get_selected_missing_online_url()
        if not url:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status_var.set("Movie URL copied to clipboard.")

    def export_missing_online_to_excel(self):
        if not self.current_db_path:
            messagebox.showinfo("Info", "Open DB first")
            return

        rows = []
        for item in self.missing_online_tree.get_children():
            rows.append(self.missing_online_tree.item(item, "values"))

        if not rows:
            messagebox.showinfo("Info", "No To Download rows to export.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")]
        )
        if not path:
            return

        try:
            df = pd.DataFrame(rows, columns=self.missing_online_tree["columns"])
            df.to_excel(path, index=False)
            messagebox.showinfo("Success", f"Exported to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")

    
    def setup_duplicates_tab(self, parent):
        top = tk.Frame(parent)
        top.pack(fill="x", padx=5, pady=5)

        tk.Button(top, text="Scan Duplicates",
                command=self.load_duplicate_records).pack(side="left", padx=4)

        tk.Button(top, text="Delete Selected Duplicate",
                command=self.delete_selected_duplicate).pack(side="left", padx=4)

        cols = ("Type", "ID", "File Name", "Ext", "Size", "Storage ID", "Full Path", "Created")

        self.dup_tree = ttk.Treeview(parent, columns=cols, show="headings")

        self.dup_tree.heading("Type", text="Duplicate Type")
        self.dup_tree.heading("ID", text="ID")
        self.dup_tree.heading("File Name", text="File Name")
        self.dup_tree.heading("Ext", text="Ext")
        self.dup_tree.heading("Size", text="Size")
        self.dup_tree.heading("Storage ID", text="Storage ID")
        self.dup_tree.heading("Full Path", text="Full Path")
        self.dup_tree.heading("Created", text="Created")

        self.dup_tree.column("Type", width=170, anchor="w")
        self.dup_tree.column("ID", width=70, anchor="center")
        self.dup_tree.column("File Name", width=220, anchor="w")
        self.dup_tree.column("Ext", width=60, anchor="center")
        self.dup_tree.column("Size", width=90, anchor="e")
        self.dup_tree.column("Storage ID", width=120, anchor="center")
        self.dup_tree.column("Full Path", width=350, anchor="w")
        self.dup_tree.column("Created", width=150, anchor="center")

        self.dup_tree.tag_configure(
            "group_header",
            background="#e6e6e6",
            font=("Segoe UI", 9, "bold")
        )

        # --- Color coding by duplicate type ---

        self.dup_tree.tag_configure(
            "dup_exact",      # Duplicate Record
            background="#ffe6e6"   # light red
        )

        self.dup_tree.tag_configure(
            "dup_versions",   # Two Versions Exist
            background="#fff4cc"   # light yellow
        )

        self.dup_tree.tag_configure(
            "dup_upgrade",    # Upgraded Version Exists
            background="#e6f0ff"   # light blue
        )

        self.dup_tree.tag_configure(
            "dup_partial",    # Partial Match
            background="#e9ffe9"   # light green
        )


        self.dup_tree.pack(fill="both", expand=True, padx=5, pady=5)
        # --- Duplicate pager ---
        pager = tk.Frame(parent)
        pager.pack(fill="x", pady=4)

        tk.Button(pager, text="|< First", command=self.first_dup_page).pack(side="left", padx=4)
        tk.Button(pager, text="<< Prev", command=self.prev_dup_page).pack(side="left", padx=4)
        tk.Button(pager, text="Next >>", command=self.next_dup_page).pack(side="left", padx=4)
        tk.Button(pager, text="Last >|", command=self.last_dup_page).pack(side="left", padx=4)

        self.dup_page_label = tk.Label(pager, text="Page 0 / 0")
        self.dup_page_label.pack(side="left", padx=8)

    def show_dup_page(self, page_num):
        self.dup_tree.delete(*self.dup_tree.get_children())

        if not self.dup_all_rows:
            self.dup_page_label.config(text="Page 0 / 0")
            return

        page_num = max(0, min(page_num, self.dup_total_pages - 1))
        self.dup_current_page = page_num

        start = page_num * self.page_size
        end = start + self.page_size

        for item in self.dup_all_rows[start:end]:
            if item[0] == "header":
                _, values, tags = item
                self.dup_tree.insert("", "end", values=values, tags=tags)
            else:
                _, values, tags, iid = item
                self.dup_tree.insert("", "end", iid=iid, values=values, tags=tags)

        self.dup_page_label.config(
            text=f"Page {self.dup_current_page + 1} / {self.dup_total_pages}"
        )

    def first_dup_page(self):
        self.show_dup_page(0)

    def last_dup_page(self):
        self.show_dup_page(self.dup_total_pages - 1)

    def next_dup_page(self):
        if self.dup_current_page + 1 < self.dup_total_pages:
            self.show_dup_page(self.dup_current_page + 1)

    def prev_dup_page(self):
        if self.dup_current_page > 0:
            self.show_dup_page(self.dup_current_page - 1)


    def load_duplicate_records(self):
        if not self.current_db_path:
            return

        self.dup_tree.delete(*self.dup_tree.get_children())
        self.dup_tree.delete(*self.dup_tree.get_children())
        self.dup_all_rows.clear()


        try:
            conn = self.get_connection()
            groups = analyze_duplicates(conn)
            conn.close()

            if not groups:
                self.status_var.set("No duplicates or versions found.")
                return

            total = 0
            potential_saving = 0

            for group in groups:

                # -------- choose color tag based on duplicate type --------
                if group["type"] == "Duplicate Record":
                    row_tag = "dup_exact"
                elif group["type"] == "Two Versions Exist" or group["type"] == "Versions Exist":
                    row_tag = "dup_versions"
                elif group["type"] == "Upgraded Version Exists":
                    row_tag = "dup_upgrade"
                else:
                    row_tag = "dup_partial"

                records = group["records"]

                # -------- calculate potential saving (keep largest only) --------
                if len(records) > 1:
                    largest = max(r["size_bytes"] for r in records)
                    deletable = sum(r["size_bytes"] for r in records) - largest
                    potential_saving += deletable

                # -------- group header --------
                """
                self.dup_tree.insert(
                    "", "end",
                    values=(f"[{group['type']}]", "", "", "", "", "", "", ""),
                    tags=("group_header",)
                )
                """
                self.dup_all_rows.append((
                    "header",
                    (f"[{group['type']}]", "", "", "", "", "", "", ""),
                    ("group_header",)
                ))

                # -------- records --------
                for rec in records:
                    self.dup_all_rows.append((
                        "row",
                        (
                            group["type"],
                            rec["id"],
                            rec["file_name"],
                            rec["extension"],
                            format_size(rec["size_bytes"]),
                            rec["storage_id"],
                            rec["full_path"],
                            rec["creation_date"]
                        ),
                        (row_tag,),
                        f"dup_{rec['id']}"
                    ))
                    total += 1

            saved_str = format_bytes(potential_saving)

            self.status_var.set(
                f"Duplicate / version records: {total} | "
                f"Potential space saving (keep largest only): {saved_str}"
            )

        except Exception as e:
            self.status_var.set(f"Duplicate scan error: {e}")
        total = len(self.dup_all_rows)
        self.dup_total_pages = (total - 1) // self.page_size + 1 if total > 0 else 1
        self.dup_current_page = 0
        self.show_dup_page(0)
    




    def delete_selected_duplicate(self):
        sel = self.dup_tree.selection()
        if not sel:
            return

        ids = []
        for item in sel:
            if item.startswith("dup_"):
                ids.append(int(item.replace("dup_", "")))

        if not ids:
            return

        if not messagebox.askyesno(
            "Confirm Delete",
            "Delete selected database records?\n\n(This will NOT delete physical files)"
        ):
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.executemany(DELETE_FILE_BY_ID, [(i,) for i in ids])

            conn.commit()
            conn.close()

            self.load_duplicate_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

        except Exception as e:
            messagebox.showerror("Error", f"Delete failed: {e}")




    def export_db_statistics_to_excel(self):
        if not self.current_db_path:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")]
            )
        if not path:
            return

        try:
            conn = self.get_connection()

            stats_df = pd.read_sql_query(SELECT_EXTENSION_STATS, conn)

            summary_df = pd.DataFrame([{
                "Total Records": stats_df["count"].sum(),
                "DB Size (MB)": round(os.path.getsize(self.current_db_path)/(1024*1024), 2)
                }])

            dup_df = pd.read_sql_query("""
                SELECT file_name, size_bytes, COUNT(*) AS copies
                FROM Files
                GROUP BY file_name, size_bytes
                HAVING copies > 1
                """, conn)

            conn.close()

            # Try preferred engine first, fallback if not installed
            try:
                writer = pd.ExcelWriter(path, engine="xlsxwriter")
            except ModuleNotFoundError:
                try:
                    writer = pd.ExcelWriter(path, engine="openpyxl")
                except Exception as e:
                    messagebox.showerror("Error", f"No suitable Excel writer available: {e}")
                    return

            try:
                with writer:
                    summary_df.to_excel(writer, sheet_name="Summary", index=False)
                    stats_df.to_excel(writer, sheet_name="By Extension", index=False)
                    dup_df.to_excel(writer, sheet_name="Duplicates", index=False)
                messagebox.showinfo("Success", "Statistics exported successfully")
            except Exception as e:
                messagebox.showerror("Error", f"Excel export failed: {e}")

        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")


    def draw_extension_pie_chart(self):
        if not self.current_db_path or not os.path.exists(self.current_db_path):
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT extension, COUNT(*)
                FROM Files
                GROUP BY extension
                """)
            rows = cur.fetchall()
            conn.close()

            if not rows:
                return

            labels = [r[0] for r in rows]
            sizes = [r[1] for r in rows]

            plt.close("all")  # prevent orphan figures
            fig, ax = plt.subplots(figsize=(5, 4))

            ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=140)
            ax.set_title("Files by Extension")

            if self.chart_canvas:
                self.chart_canvas.get_tk_widget().destroy()

            self.chart_canvas = FigureCanvasTkAgg(fig, master=self.chart_container)
            self.chart_canvas.draw()
            self.chart_canvas.get_tk_widget().pack(fill="both", expand=True)

        except Exception as e:
            self.status_var.set(f"Chart error: {e}")

    def update_db_statistics(self):
        # Clear old rows
        for i in self.db_ext_tree.get_children():
            self.db_ext_tree.delete(i)

        # Clear storage stats
        if hasattr(self, "db_storage_tree"):
            for i in self.db_storage_tree.get_children():
                self.db_storage_tree.delete(i)


        if not self.current_db_path or not os.path.exists(self.current_db_path):
            self.db_total_records_var.set("DB Records: 0")
            self.db_size_var.set("DB Size: 0 MB")
            self.db_files_size_var.set("Total Files Size: 0 MB")
            return

        try:
            # DB size
            size_mb = os.path.getsize(self.current_db_path) / (1024 * 1024)
            self.db_size_var.set(f"DB Size: {size_mb:.2f} MB")

            conn = self.get_connection()
            cur = conn.cursor()
           
            # Total records
            cur.execute(SELECT_TOTAL_COUNT)
            total = cur.fetchone()[0]
            self.db_total_records_var.set(f"DB Records: {total}")

            # Total size of ALL files in DB
            cur.execute(SELECT_TOTAL_SIZE)
            total_bytes = cur.fetchone()[0]

            formatted = format_db_total_size(total_bytes)
            self.db_files_size_var.set(
                f"Total Files Size: {formatted}"# ({total_bytes:,} bytes)" #Include if size required in bytes
            )

            # Per-extension stats
            cur.execute(SELECT_EXTENSION_STATS)
            rows = cur.fetchall()
            conn.close()

            for ext, cnt, size in rows:
                self.db_ext_tree.insert(
                    "", "end",
                    values=(ext, cnt, format_size(size))
                    )
            
            self.update_storage_statistics()  
        except Exception as e:
            self.db_total_records_var.set("DB Records: Error")
            self.db_size_var.set("DB Size: Error")
            self.status_var.set(f"DB stats error: {e}")
          
   

    def update_status_bar_db_info(self):
        if not self.current_db_path or not os.path.exists(self.current_db_path):
            return
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            

            cur.execute(SELECT_TOTAL_COUNT)
            total = cur.fetchone()[0]
            conn.close()

            size_mb = os.path.getsize(self.current_db_path) / (1024 * 1024)
            self.status_var.set(
            f"DB Records: {total} | DB Size: {size_mb:.2f} MB"
        )
        except Exception:
            pass

    def on_app_close(self):
        try:
            # Destroy matplotlib canvas safely
            if hasattr(self, "chart_canvas") and self.chart_canvas:
                self.chart_canvas.get_tk_widget().destroy()
                self.chart_canvas = None

            # Close all matplotlib figures
            try:
                import matplotlib.pyplot as plt
                plt.close("all")
            except Exception:
                pass

        finally:
            # Destroy Tk window
            self.root.destroy()

    # ---------------- File scanning ----------------
    def browse_folder(self):
        folder_selected = filedialog.askdirectory()

        if folder_selected:
            # If a different folder is selected → auto reset
            if self.folder_path.get() and self.folder_path.get() != folder_selected:
                self.reset_scan()

            self.folder_path.set(folder_selected)
            self.status_var.set(f"Selected folder: {folder_selected}")


    def on_file_table_select(self, event):
        sel = self.file_table.selection()
        if not sel:
            return

        iid = sel[0]
        path = self.file_paths.get(iid)
        if not path:
            return

        try:
            self.detail_vars["File Name"].set(os.path.basename(path).rsplit(".", 1)[0])
            self.detail_vars["Extension"].set(os.path.splitext(path)[1])
            self.detail_vars["Size"].set(format_size(os.path.getsize(path)))
            self.detail_vars["Creation Date"].set(
                format_date(datetime.datetime.fromtimestamp(os.path.getctime(path)))
            )
        except Exception as e:
            self.status_var.set(f"Error reading file: {e}")


    def on_file_table_double_click(self, event):
        item = self.file_table.identify_row(event.y)
        if not item:
            return

        path = self.file_paths.get(item)
        if path and os.path.exists(path):
            os.startfile(path)


    def list_files(self):
        folder = self.folder_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return

        # reset
        self.file_table.delete(*self.file_table.get_children())
        self.file_paths.clear()
        self.all_files_info.clear()


        # get files (video-only)
        self.all_files_info = get_files_info(
            folder,
            self.allowed_video_exts,
            self.include_subdirs.get()
        )


        # sort by name
        self.all_files_info.sort(key=lambda x: x["name_without_ext"].lower())

        # populate listbox; disambiguate duplicate display names
        for info in self.all_files_info:
            name = info["name_without_ext"]
            ext = info.get("extension", "")
            size = info.get("size", 0)

            size_text = format_size(size)

            iid = self.file_table.insert(
                "", "end",
                values=(name, ext, size_text)
            )

        self.file_paths[iid] = info["full_path"]

        total = len(self.all_files_info)
        self.files_count_var.set(f"Files: {total}")
        self.status_var.set(f"Found {total} video files")
        
        self.update_filelist_statistics(self.all_files_info)

        # clear details
        for v in self.detail_vars.values():
            v.set("")

  
    def open_file(self, path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Error", f"Cannot open file: {e}")

    def export_to_excel(self):
        if not self.all_filtered_rows:
            messagebox.showinfo("Info", "No records to export.")
            return

        options = [
            "File Names Only",
            "Complete File Information",
            "Extension Statistics"
        ]

        dlg = ExportDialog(self.root, options)
        self.root.wait_window(dlg.top)

        if not dlg.result:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx")]
        )

        if not path:
            return

        try:
            if dlg.result == "File Names Only":
                df = pd.DataFrame({
                    "File Name": [r[1] for r in self.all_filtered_rows]
                })

            elif dlg.result == "Complete File Information":
                rows = []
                for r in self.all_filtered_rows:
                    id_, fname, ext, sizeb, storage_id, cdate, path_, year, category = r

                    rows.append({
                        "File Name": fname,
                        "Extension": ext,
                        "Size (bytes)": sizeb,
                        "Size": format_size(sizeb),
                        "Storage ID": storage_id,
                        "Creation Date": format_date(cdate),
                        "Full Path": path_,
                        "Year": year if year else "",
                        "Category": category if category else ""
                    })

                df = pd.DataFrame(rows)

            else:  # Extension Statistics
                stats = defaultdict(lambda: {"count": 0, "size": 0})

                for r in self.all_filtered_rows:
                    ext = r[2]
                    sizeb = r[3]
                    stats[ext]["count"] += 1
                    stats[ext]["size"] += sizeb

                df = pd.DataFrame([
                    {
                        "Extension": ext,
                        "Count": v["count"],
                        "Total Size (bytes)": v["size"],
                        "Total Size": format_size(v["size"])
                    }
                    for ext, v in stats.items()
                ])

            df.to_excel(path, index=False)
            messagebox.showinfo("Success", f"Exported to {path}")

        except Exception as e:
            messagebox.showerror("Error", f"Excel export failed:\n{e}")



    def update_storage_id_from_scan(self):
        if not self.current_db_path or not self.all_files_info:
            messagebox.showwarning(
                "No Data",
                "Scan files first before updating Storage ID."
            )
            return

        storage_id = self.get_storage_id()

        if storage_id == "UNKNOWN":
            messagebox.showwarning(
                "Invalid Storage ID",
                "Please enter a valid Storage ID."
            )
            return

        updated = 0

        try:
            conn = self.get_connection()
            cur = conn.cursor()
            

            for f in self.all_files_info:
                if not f.get("tracked", True):
                    continue

                cur.execute("""
                    UPDATE Files
                    SET storage_id = ?
                    WHERE file_name = ?
                    AND size_bytes = ?
                    AND storage_id = 'UNKNOWN'
                """, (
                    storage_id,
                    f["name_without_ext"],
                    f["size"]
                ))

                updated += cur.rowcount

            conn.commit()
            conn.close()

            messagebox.showinfo(
                "Storage ID Updated",
                f"Storage ID '{storage_id}' assigned to {updated} record(s)."
            )

            self.update_db_statistics()
            self.update_status_bar_db_info()

        except Exception as e:
            messagebox.showerror(
                "Update Error",
                f"Failed to update Storage ID:\n{e}"
            )

    def export_to_sqlite(self):
        if hasattr(self, "storage_id_combo"):
            self.storage_id_combo.config(state="disabled")

        if not self.all_files_info:
            messagebox.showinfo("Info", "No files to export.")
            return

        db_path = self.master_db_path  # ALWAYS USE ONE DB

        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            # Ensure table & indexes exist (updated schema)
            cur.execute(FILES_TABLE_SQL)
            cur.execute(MOVIE_TABLE_SQL)
            cur.execute(FILES_TABLE_INDEX)
            cur.execute(CATEGORIES_TABLE_SQL)

            storage_id = self.get_storage_id()

            select_q = """
                SELECT id, storage_id, full_path
                FROM Files
                WHERE file_name=? AND size_bytes=?
            """

            new_count = 0
            moved_count = 0
            waste_duplicates = 0

            for f in self.all_files_info:
                if f["extension"].lower() not in self.allowed_video_exts:
                    continue

                file_name = f["name_without_ext"]
                size = f["size"]
                full_path = f["full_path"]
                creation_date = format_date(f["creation_date"])
                year = f.get("year")
                category = f.get("category")

                cur.execute(select_q, (file_name, size))
                row = cur.fetchone()

                if row is None:
                    # ✅ Brand new movie
                    cur.execute(INSERT_FILE_RECORD, (
                        file_name,
                        f["extension"],
                        size,
                        storage_id,
                        creation_date,
                        full_path,
                        year,
                        category
                    ))
                    new_count += 1

                else:
                    db_id, db_storage, db_path_existing = row

                    if db_storage == storage_id:
                        if not paths_equal_ignore_drive(db_path_existing, full_path):
                            # 🔄 Movie moved
                            cur.execute(UPDATE_FILE_MOVE, (
                                storage_id,
                                full_path,
                                creation_date,
                                db_id
                            ))
                            moved_count += 1
                    else:
                        # 🚨 Waste duplicate on another storage
                        waste_duplicates += 1
                        # Do NOT insert, do NOT update

            conn.commit()
            conn.close()
            self.current_db_path = db_path
            self.save_settings({
                "last_db_path": db_path,
                "last_storage_id": storage_id
            })

            self.update_db_statistics()
            self.update_status_bar_db_info()

            # ✅ AUTO refresh SQLite tab after export
            self.current_page = 0
            self.load_db_records()

            messagebox.showinfo(
                "Export complete",
                f"New movies added: {new_count}\n"
                f"Moved movies updated: {moved_count}\n"
                f"Duplicate waste detected: {waste_duplicates}"
            )

        except Exception as e:
            messagebox.showerror("Error", f"SQLite export failed: {e}")

        finally:
            if hasattr(self, "storage_id_combo"):
                self.storage_id_combo.config(state="normal")


    def open_bulk_category_editor(self):
        if not self.current_db_path:
            messagebox.showwarning("No Database", "Please open a database first.")
            return

        sel = self.db_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select one or more records first.")
            return

        win = tk.Toplevel(self.root)
        win.title("Assign Category")
        win.geometry("420x220")
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="Select or Enter Category", font=("Segoe UI", 10, "bold")).pack(pady=8)

        categories = self.get_all_categories()
        cat_var = tk.StringVar()

        combo = ttk.Combobox(win, values=categories, textvariable=cat_var, width=35)
        combo.pack(pady=4)

        tk.Label(win, text="(You can select from list or type a new one)").pack(pady=(2,8))

        def save():
            final_cat = cat_var.get().strip()

            if not final_cat:
                messagebox.showwarning("Missing", "Please select or enter a category.")
                return

            final_cat = final_cat.title()
            self.add_new_category(final_cat)

            ids = [self.db_tree.item(i)["tags"][0] for i in sel]

            try:
                conn = self.get_connection()
                cur = conn.cursor()
                cur.executemany(
                    "UPDATE Files SET category=? WHERE id=?",
                    [(final_cat, i) for i in ids]
                )
                conn.commit()
                conn.close()
            except Exception as e:
                messagebox.showerror("DB Error", str(e))
                return

            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

            messagebox.showinfo("Updated", f"Category '{final_cat}' applied to {len(ids)} records.")
            win.destroy()

        btnf = tk.Frame(win)
        btnf.pack(pady=14)

        tk.Button(btnf, text="Apply", width=12, command=save).pack(side="left", padx=8)
        tk.Button(btnf, text="Cancel", width=12, command=win.destroy).pack(side="left")


    def on_db_tree_click(self, event):
        if self.db_tree.identify_region(event.x, event.y) == "heading":
            col = self.db_tree.identify_column(event.x)
            col_name = self.db_tree["columns"][int(col.replace("#",""))-1]
            self.sort_db_by_column(col_name)

    def load_category_dropdown(self):
        if not self.current_db_path:
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(SELECT_DISTINCT_FILE_CATEGORIES)
            cats = [r[0] for r in cur.fetchall() if r[0]]
            conn.close()
        except:
            cats = []

        # Remove duplicates and sort
        cats = sorted(set(cats))

        values = ["All"] + cats + ["Uncategorized"]

        current = self.db_category_var.get()

        # Update dropdown values
        self.db_category_combo["values"] = values

        # Restore previous selection if possible
        if current in values:
            self.db_category_var.set(current)
        else:
            self.db_category_var.set("All")

    def load_year_dropdown(self):
        if not self.current_db_path:
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT year FROM Files WHERE year IS NOT NULL ORDER BY year DESC")
            years = [r[0] for r in cur.fetchall() if r[0]]
            conn.close()
        except Exception:
            years = []

        # convert to strings
        years = [str(int(y)) for y in years]

        values = ["All"] + years

        current = self.db_year_var.get() if hasattr(self, "db_year_var") else "All"
        self.db_year_combo["values"] = values
        if current in values:
            self.db_year_var.set(current)
        else:
            self.db_year_var.set("All")

    def load_storage_ids_from_db(self):
        if not self.current_db_path or not os.path.exists(self.current_db_path):
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT DISTINCT storage_id
                FROM Files
                WHERE storage_id IS NOT NULL AND TRIM(storage_id) <> ''
                ORDER BY storage_id
            """)

            raw_ids = [r[0] for r in cur.fetchall()]

            # Normalize duplicate forms like "LABEL" and "LABEL (D:)" to a single storage ID.
            normalized = {}
            for sid in raw_ids:
                if sid is None:
                    continue
                normalized_id = sid.strip()
                # Collapse "LABEL (D:)" to just "LABEL" when label is present.
                m = re.match(r"^(.*) \([A-Z]:\)$", normalized_id)
                if m:
                    normalized_id = m.group(1).strip()
                normalized[normalized_id] = normalized_id

            ids = sorted(normalized)

            # ---------- SQLite Viewer tab combo ----------
            self.available_storage_ids = ["ALL"] + ids
            self.storage_filter_combo["values"] = self.available_storage_ids

            if self.selected_storage_filter.get() not in self.available_storage_ids:
                self.selected_storage_filter.set("ALL")

            # ---------- File Lister tab combo (NEW) ----------
            if hasattr(self, "storage_id_combo"):
                self.storage_id_combo["values"] = ids

        except Exception as e:
            print("Storage ID dropdown load error:", e)

        finally:
            conn.close()

    def clear_metadata_panel(self):
        self.category_var.set("")
        if hasattr(self, "year_var"):
            self.year_var.set("")
        self.description_text.delete("1.0", tk.END)

        # Clear metadata URL field (UI only)
        if hasattr(self, "meta_url_var"):
            self.meta_url_var.set("")

        # Clear images safely
        if hasattr(self, "image_label1"):
            self.image_label1.config(image="")
            self.image_label1.image = None

        if hasattr(self, "image_label2"):
            self.image_label2.config(image="")
            self.image_label2.image = None

        # Reset selected file metadata state
        #self.current_image_urls = None


    def load_movie_metadata(self, file_id):
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT f.year,
                       m.category,
                       m.description,
                       m.cover1_path,
                       m.cover2_path,
                       m.metadata_url
                FROM Files f
                LEFT JOIN MovieDetails m ON f.id = m.file_id
                WHERE f.id=?
            """, (file_id,))

            row = cur.fetchone()
            conn.close()

            # Clear UI first
            self.clear_metadata_panel()

            if not row:
                return  # No metadata stored yet

            year, category, description, cover1_path, cover2_path, metadata_url = row

            # Load text
            self.category_var.set(category or "")
            self.year_var.set(str(year) if year else "")
            if hasattr(self, "meta_url_var"):
                self.meta_url_var.set(metadata_url or "")

            self.description_text.delete("1.0", tk.END)
            self.description_text.insert("1.0", description or "")

            cover1_path = cover1_path.strip() if cover1_path else None
            cover2_path = cover2_path.strip() if cover2_path else None

            cover1_resolved = self.resolve_cover_path(cover1_path) if cover1_path else None
            cover2_resolved = self.resolve_cover_path(cover2_path) if cover2_path else None

            self.cover1_local_path.set(cover1_resolved or "")
            self.cover2_local_path.set(cover2_resolved or "")

            if cover1_resolved and os.path.exists(cover1_resolved):
                self.display_image_from_file(cover1_resolved, self.image_label1)

            if cover2_resolved and os.path.exists(cover2_resolved):
                self.display_image_from_file(cover2_resolved, self.image_label2)

            self.status_var.set("Metadata loaded from local database.")

        except Exception as e:
            messagebox.showerror("Error", str(e))



    def on_db_row_select(self, event):
        selected = self.db_tree.selection()

        if not selected:
            self.selected_file_id = None
            self.clear_metadata_panel()
            return

        item_id = selected[0]
        tags = self.db_tree.item(item_id, "tags")

        if tags:
            self.selected_file_id = int(tags[0])
            self.load_movie_detail_view(self.selected_file_id)
            self.load_movie_metadata(self.selected_file_id)
        else:
            self.selected_file_id = None
            self.clear_metadata_panel()

    def _show_url_menu(self, event):
        try:
            self.url_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.url_menu.grab_release()

    def _show_db_tree_context_menu(self, event):
        """Right-click context menu to copy filename from db_tree"""
        item = self.db_tree.identify_row(event.y)
        if not item:
            return
        
        # Select the item if not already selected
        if item not in self.db_tree.selection():
            self.db_tree.selection_set(item)
        
        # Get filename from tags (third element: fname)
        tags = self.db_tree.item(item, "tags")
        if not tags or len(tags) < 3:
            return
        
        filename = tags[2]
        
        # Create context menu
        context_menu = tk.Menu(self.root, tearoff=False)

        context_menu.add_command(
            label="Copy Searchable Filename",
            command=self.copy_selected_searchable_filename,
        )
     
        try:
            context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            context_menu.grab_release()
    

    def metadata_dot(self, status):

        if status == "COMPLETE":
            return "🟢"

        elif status == "INCOMPLETE":
            return "🟡"

        else:
            return "🔴"

    def update_metadata_status_summary(self):

        if not self.current_db_path:
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute(METADATA_STATUS_STATS)
            row = cur.fetchone()

            conn.close()

            (
                total,
                complete,
                incomplete,
                missing_meta,
                miss_cat,
                miss_desc,
                miss_c1,
                miss_c2
            ) = row

            text = (
                f"Metadata Status  |  "
                f"Total: {total}   "
                f"Complete: {complete}   "
                f"Incomplete: {incomplete}   "
                f"No Metadata: {missing_meta}   "
                f"| Missing → "
                f"Category:{miss_cat}  "
                f"Description:{miss_desc}  "
                f"Cover1:{miss_c1}  "
                f"Cover2:{miss_c2}"
            )

            self.meta_status_label.config(text=text)

        except Exception as e:
            print("Metadata summary error:", e)

    def setup_db_viewer_tab(self, parent):
        
        # ---------- Storage ID Filter ----------
        filter_frame = tk.Frame(parent)
        filter_frame.pack(fill="x", padx=8, pady=4)

        tk.Label(filter_frame, text="Storage ID:", font=("Segoe UI", 9, "bold")).pack(side="left")

        self.storage_filter_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.selected_storage_filter,
            values=["ALL"],
            state="readonly",
            width=30
        )
        self.storage_filter_combo.pack(side="left", padx=6)

        self.storage_filter_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self.load_db_records()
        )

        top = tk.Frame(parent)
        top.pack(fill="x", pady=6)

        self.db_action_buttons = []

        btn = tk.Button(top, text="Recreate DB (Clean)", command=self.recreate_database)
        btn.pack(side="left", padx=6)
        self.db_action_buttons.append(btn)

        btn = tk.Button(top, text="Open SQLite DB", command=self.open_sqlite_db)
        btn.pack(side="left", padx=4)
        self.db_action_buttons.append(btn)

        btn = tk.Button(top, text="Verify DB vs Disk", command=self.verify_db_vs_disk)
        btn.pack(side="left", padx=6)
        self.db_action_buttons.append(btn)

        btn = tk.Button(top, text="Upgrade Covers", command=self.upgrade_existing_covers)
        btn.pack(side="left", padx=6)
        self.db_action_buttons.append(btn)

        tk.Label(top, text="Search:").pack(side="left", padx=(8,0))
        self.db_search_var = tk.StringVar()
        self.db_search_entry = tk.Entry(top, textvariable=self.db_search_var, width=40)
        self.db_search_entry.pack(side="left", padx=4)
        self.db_search_var.trace_add("write", lambda *a: self.filter_db_records())
        self.create_db_search_context_menu()
        self.db_search_entry.bind("<Button-3>", self._show_db_search_menu)
        self.db_search_entry.bind("<Control-Button-1>", self._show_db_search_menu)
        self.db_search_entry.bind("<Control-v>", self._paste_db_search_event)
        self.db_search_entry.bind("<Control-V>", self._paste_db_search_event)

        tk.Label(top, text="Category:").pack(side="left", padx=(8,0))

        self.db_category_var = tk.StringVar(value="All")
        self.db_category_combo = ttk.Combobox(
            top, textvariable=self.db_category_var,
            state="readonly", width=18
        )
        self.db_category_combo.pack(side="left", padx=4)
        self.db_category_combo.bind("<<ComboboxSelected>>", lambda e: self.filter_db_records())

        tk.Label(top, text="Year:").pack(side="left", padx=(8,0))
        self.db_year_var = tk.StringVar(value="All")
        self.db_year_combo = ttk.Combobox(
            top, textvariable=self.db_year_var,
            state="readonly", width=10
        )
        self.db_year_combo.pack(side="left", padx=4)
        self.db_year_combo.bind("<<ComboboxSelected>>", lambda e: self.filter_db_records())

        tk.Label(top, text="Metadata:").pack(side="left", padx=(8,0))

        self.meta_filter_var = tk.StringVar(value="All")

        self.meta_filter_combo = ttk.Combobox(
            top,
            textvariable=self.meta_filter_var,
            values=["All","Complete","Incomplete","No Metadata"],
            state="readonly",
            width=15
        )

        self.meta_filter_combo.pack(side="left", padx=4)
        self.meta_filter_combo.bind("<<ComboboxSelected>>", lambda e: self.load_db_records())    
        
        tk.Label(top, text="Page size:").pack(side="left", padx=(8,0))
        self.page_size_var = tk.IntVar(value=self.page_size)
        e = tk.Entry(top, textvariable=self.page_size_var, width=6)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.apply_page_size())

        btn = tk.Button(top, text="Reset", width=12, command=self.reset_db_viewer_filters)
        btn.pack(side="left", padx=4)
        self.db_action_buttons.append(btn)

        btn = tk.Button(top, text="Export to Excel", width=16, command=self.export_db_to_excel)
        btn.pack(side="right", padx=6)
        self.db_action_buttons.append(btn)

        btn = tk.Button(top, text="Delete ALL", width=14, command=self.delete_all_db_rows)
        btn.pack(side="right", padx=6)
        self.db_action_buttons.append(btn)

        btn = tk.Button(top, text="Delete Selected", width=16, command=self.delete_selected_db_rows)
        btn.pack(side="right", padx=6)
        self.db_action_buttons.append(btn)
        # -----------------------------
        # Metadata Status Summary Label
        # -----------------------------
        self.meta_status_label = tk.Label(
            parent,
            text="Metadata Status",
            anchor="w",
            font=("Segoe UI", 9)
        )
        self.meta_status_label.pack(fill="x", padx=8, pady=2)        

        self.db_viewer_controls = [
            self.storage_filter_combo,
            self.db_search_entry,
            self.db_category_combo,
            self.db_year_combo,
            self.meta_filter_combo,
            e,  # page size entry
        ]

        self.db_tree_controls = []

        cols = ("No", "Name", "Ext", "Size", "Storage", "Year", "Category")

        frame = tk.Frame(parent)
        frame.pack(fill="both", expand=True)

        # ✅ CREATE TREE FIRST
        self.db_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        self.db_tree.bind("<<TreeviewSelect>>", self.on_db_row_select)
        self.db_tree_controls = [self.db_tree]
        
        # ✅ RIGHT-CLICK CONTEXT MENU FOR COPY FILENAME
        self.db_tree.bind("<Button-3>", self._show_db_tree_context_menu)


        # ✅ HEADINGS + SORT
        for c in cols:
            self.db_tree.heading(c, text=c, command=lambda _c=c: self.sort_db_by_column(_c))
            self.db_tree.column(c, width=180, anchor="w")

        # ✅ SPECIAL COLUMN FORMATTING (MUST be after creation)
        self.db_tree.column("No", width=60, anchor="center")
        self.db_tree.column("Ext", width=70, anchor="center")
        self.db_tree.column("Size", width=100, anchor="e")
        self.db_tree.column("Year", width=70, anchor="center")

        self.db_tree.pack(side="left", fill="both", expand=True)

        self.db_tree.tag_configure("meta_complete", background="#e6ffe6")
        self.db_tree.tag_configure("meta_incomplete", background="#fff5cc")
        self.db_tree.tag_configure("meta_missing", background="#ffe6e6")
        scroll = ttk.Scrollbar(frame, command=self.db_tree.yview)
        scroll.pack(side="right", fill="y")
        self.db_tree.configure(yscrollcommand=scroll.set)

        self.db_tree.bind("<Double-1>", self.edit_cell)

        pager = tk.Frame(parent)
        pager.pack(fill="x", pady=4)
        # ---------------------------
        # Bulk Category Update Frame
        # ---------------------------
        bulk_frame = tk.LabelFrame(parent, text="Bulk Update From Category Page", padx=8, pady=6)
        bulk_frame.pack(fill="x", padx=8, pady=6)

        self.category_url_var = tk.StringVar()

        ttk.Label(bulk_frame, text="Category Page URL:").pack(anchor="w")

        self.category_url_entry = ttk.Entry(
            bulk_frame,
            textvariable=self.category_url_var
        )
        self.category_url_entry.pack(fill="x", pady=3)
        # Create context menu
        self.create_category_url_context_menu()

        # Bind right-click (Windows)
        self.category_url_entry.bind("<Button-3>", self._show_category_url_menu)

        # Optional macOS support
        self.category_url_entry.bind("<Control-Button-1>", self._show_category_url_menu)
        ttk.Button(
            bulk_frame,
            text="Update Movies From Page",
            command=self._start_category_bulk_update
        ).pack(pady=4)        

        # ---------------------------
        # Metadata Details Frame (Redesigned)
        # ---------------------------
        details_frame = tk.LabelFrame(parent, text="Movie Metadata", padx=8, pady=6)
        details_frame.pack(fill="x", padx=8, pady=8)

        # Configure 2-column layout
        details_frame.columnconfigure(0, weight=1)  # Form
        details_frame.columnconfigure(1, weight=0)  # Cover 1
        details_frame.columnconfigure(2, weight=0)  # Cover 2
        details_frame.rowconfigure(0, weight=1)

        # ---------------- LEFT SIDE (FORM) ----------------
        form_frame = ttk.Frame(details_frame)
        form_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        form_frame.columnconfigure(1, weight=1)
        form_frame.rowconfigure(4, weight=1)  # description row expands

        # Metadata URL
        ttk.Label(form_frame, text="Metadata URL:").grid(row=0, column=0, sticky="w")
        self.meta_url_var = tk.StringVar()

        self.meta_url_entry = ttk.Entry(
            form_frame,
            textvariable=self.meta_url_var,
            width=50
        )
        self.meta_url_entry.grid(row=0, column=1, sticky="ew", pady=2)

        self.create_url_context_menu()
        self.meta_url_entry.bind("<Button-3>", self._show_url_menu)

        # Buttons row
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Button(btn_frame, text="Fetch Metadata",
                command=self.fetch_metadata).pack(side="left", padx=4)

        ttk.Button(btn_frame, text="🔄 Refresh Metadata",
                command=self.refresh_metadata).pack(side="left", padx=4)

        ttk.Button(btn_frame, text="⚡ Auto Update",
                command=self.auto_update_by_url).pack(side="left", padx=4)

        # Year
        ttk.Label(form_frame, text="Year:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.year_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.year_var, width=12).grid(row=2, column=1, sticky="w", pady=(6, 0))

        # Category
        ttk.Label(form_frame, text="Category:").grid(row=3, column=0, sticky="w", pady=(6, 0))

        self.category_combo = ttk.Combobox(
            form_frame,
            textvariable=self.category_var,
            values=self.get_all_categories(),
            state="normal",
            width=30
        )
        self.category_combo.grid(row=3, column=1, sticky="w", pady=(6, 0))

        # Description
        ttk.Label(form_frame, text="Description:").grid(row=4, column=0, sticky="nw", pady=(6, 0))

        self.description_text = tk.Text(form_frame, height=4, width=50)
        self.description_text.grid(row=4, column=1, sticky="nsew", pady=(6, 0))
        self.create_description_context_menu()
        self.description_text.bind("<Button-3>", self._show_description_menu)
        self.description_text.bind("<Control-Button-1>", self._show_description_menu)

        # Cover 1
        ttk.Label(form_frame, text="Cover 1:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form_frame, textvariable=self.cover1_local_path,
            width=50).grid(row=5, column=1, sticky="ew")

        ttk.Button(form_frame, text="Browse",
            command=self.browse_cover1).grid(row=5, column=2, padx=4)

        # Cover 2
        ttk.Label(form_frame, text="Cover 2:").grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form_frame, textvariable=self.cover2_local_path,
            width=50).grid(row=6, column=1, sticky="ew")

        ttk.Button(form_frame, text="Browse",
            command=self.browse_cover2).grid(row=6, column=2, padx=4)

        # Save Button
        ttk.Button(form_frame, text="Save Metadata",
            command=self.save_metadata).grid(row=7, column=1, pady=8, sticky="w")

        # ---------------- RIGHT SIDE (IMAGES) ----------------
        # ---------------- COVER 1 ----------------
        cover1_frame = ttk.Frame(details_frame)
        cover1_frame.grid(row=0, column=1, sticky="n", padx=10)

        ttk.Label(cover1_frame, text="Cover 1").pack()

        self.image_label1 = tk.Label(
            cover1_frame,
            relief="solid",
            bd=1
        )
        self.image_label1.pack(pady=5)


        # ---------------- COVER 2 ----------------
        cover2_frame = ttk.Frame(details_frame)
        cover2_frame.grid(row=0, column=2, sticky="n", padx=10)

        ttk.Label(cover2_frame, text="Cover 2").pack()

        self.image_label2 = tk.Label(
            cover2_frame,
            relief="solid",
            bd=1
        )
        self.image_label2.pack(pady=5)

        tk.Button(pager, text="|< First", command=self.first_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="<< Prev", command=self.prev_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="Next >>", command=self.next_db_page).pack(side="left")
        tk.Button(pager, text="Last >|", command=self.last_db_page).pack(side="left", padx=4)
        self.page_label = tk.Label(pager, text="Page 0 / 0")
        self.page_label.pack(side="left", padx=8)


    def _show_category_url_menu(self, event):
        try:
            self.category_url_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.category_url_menu.grab_release()

    def create_category_url_context_menu(self):
        self.category_url_menu = tk.Menu(self.root, tearoff=0)

        self.category_url_menu.add_command(
            label="Paste",
            command=self._paste_category_url
        )
        self.category_url_menu.add_command(
            label="Copy",
            command=self._copy_category_url
        )
        self.category_url_menu.add_command(
            label="Cut",
            command=self._cut_category_url
        )
        self.category_url_menu.add_separator()
        self.category_url_menu.add_command(
            label="Clear",
            command=lambda: self.category_url_var.set("")
        )

    def create_description_context_menu(self):
        self.description_menu = tk.Menu(self.root, tearoff=0)
        self.description_menu.add_command(label="Paste", command=self._paste_description)
        self.description_menu.add_command(label="Copy", command=self._copy_description)
        self.description_menu.add_command(label="Cut", command=self._cut_description)
        self.description_menu.add_separator()
        self.description_menu.add_command(label="Clear", command=self._clear_description)

    def _show_description_menu(self, event):
        try:
            self.description_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.description_menu.grab_release()

    def _paste_description(self):
        try:
            clipboard = self.root.clipboard_get()
            self.description_text.insert(tk.INSERT, clipboard)
        except:
            pass

    def _copy_description(self):
        try:
            selection = self.description_text.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(selection)
        except tk.TclError:
            pass

    def _cut_description(self):
        try:
            selection = self.description_text.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(selection)
            self.description_text.delete("sel.first", "sel.last")
        except tk.TclError:
            pass

    def _clear_description(self):
        self.description_text.delete("1.0", tk.END)

    def _paste_category_url(self):
        try:
            clipboard = self.root.clipboard_get()
            self.category_url_var.set(clipboard.strip())
        except:
            pass


    def _copy_category_url(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.category_url_var.get())
        except:
            pass


    def _cut_category_url(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.category_url_var.get())
            self.category_url_var.set("")
        except:
            pass


    def create_db_search_context_menu(self):
        self.db_search_menu = tk.Menu(self.root, tearoff=0)
        self.db_search_menu.add_command(label="Paste", command=self._paste_db_search)
        self.db_search_menu.add_command(label="Copy", command=self._copy_db_search)
        self.db_search_menu.add_command(label="Cut", command=self._cut_db_search)
        self.db_search_menu.add_separator()
        self.db_search_menu.add_command(label="Clear", command=lambda: self.db_search_var.set(""))

    def _show_db_search_menu(self, event):
        try:
            self.db_search_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.db_search_menu.grab_release()

    def _paste_db_search(self):
        try:
            clipboard = self.root.clipboard_get()
            self.db_search_var.set(clipboard.strip())
        except:
            pass

    def _paste_db_search_event(self, event):
        self._paste_db_search()
        return "break"

    def _copy_db_search(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.db_search_var.get())
        except:
            pass

    def _cut_db_search(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.db_search_var.get())
            self.db_search_var.set("")
        except:
            pass


    def _start_category_bulk_update(self):
        url = self.category_url_var.get().strip()

        if not url:
            messagebox.showwarning("Missing URL", "Please enter a category page URL.")
            return

        if "rarelust.com/category/" not in url:
            messagebox.showwarning("Invalid URL", "Please enter a valid Rarelust category URL.")
            return

        self.bulk_update_from_category(url)

    def browse_cover1(self):
        path = filedialog.askopenfilename(
            filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp")]
        )
        if path:
            self.cover1_local_path.set(path)

    def browse_cover2(self):
        path = filedialog.askopenfilename(
            filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp")]
        )
        if path:
            self.cover2_local_path.set(path)

    def refresh_ui_after_db_update(self, file_id):
        # Reload all records
        self.load_db_records()

        # Find index in full dataset
        target_index = None
        for idx, row in enumerate(self.db_records_cache):
            if str(row[0]) == str(file_id):
                target_index = idx
                break

        if target_index is None:
            return  # record not found

        # Calculate correct page
        page = target_index // self.page_size

        # Jump to that page
        self.show_db_page(page)

        # Select inside page
        for item in self.db_tree.get_children():
            tags = self.db_tree.item(item, "tags")
            if tags and str(tags[0]) == str(file_id):
                self.db_tree.selection_set(item)
                self.db_tree.focus(item)
                self.db_tree.see(item)
                break

        # Reload metadata panel
        self.load_movie_metadata(file_id)


    def refresh_metadata(self):
        confirm = messagebox.askyesno(
            "Confirm Refresh",
            "This will overwrite existing metadata.\nContinue?"
        )
        if not confirm:
            return

        if not self.selected_file_id:
            messagebox.showwarning("Select Record", "Select a record first.")
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            # Get stored metadata_url
            cur.execute("""
                SELECT metadata_url
                FROM MovieDetails
                WHERE file_id=?
            """, (self.selected_file_id,))
            row = cur.fetchone()
            if not row:
                messagebox.showwarning("Missing Record", "Metadata record not found.")
                conn.close()
                return

            stored_url = row[0].strip() if row and row[0] else None
            user_url = self.meta_url_var.get().strip()

            # Priority: DB URL first, else user URL
            url = stored_url if stored_url else user_url

            if not url:
                messagebox.showwarning(
                    "URL Required",
                    "No stored URL found.\nPlease enter a metadata URL first."
                )
                conn.close()
                return

            self.status_var.set("Refreshing metadata from internet...")
            self.root.update_idletasks()
            # 🔥 Disable manual category editing during fetch
            self.category_combo.configure(state="disabled")
            # Scrape fresh data
            data = scrape_movie(url)

            category = data["category"]
            # 🔥 Force metadata panel to reflect scraped category
            self.category_var.set(category)

            # 🔥 Immediately sync combobox display
            self.category_combo.set(category)
            description = data["description"]
            images = data["images"]
            # 🔥 Immediate UI update
            self.category_var.set(category)
            self.description_text.delete("1.0", tk.END)
            self.description_text.insert("1.0", description)

            file_id = self.selected_file_id

            img1_path = os.path.join(COVERS_DIR, f"{file_id}_1.jpg")
            img2_path = os.path.join(COVERS_DIR, f"{file_id}_2.jpg")

            # Remove old covers if scraper returned fewer images
            if len(images) == 0 and os.path.exists(img1_path):
                os.remove(img1_path)

            if len(images) < 2 and os.path.exists(img2_path):
                os.remove(img2_path)

            # Download new covers
            if len(images) > 0:
                self.download_image(images[0], img1_path)

            if len(images) > 1:
                self.download_image(images[1], img2_path)

            # Update MovieDetails
            cur.execute("""
                UPDATE MovieDetails
                SET category=?,
                    description=?,
                    cover1_path=?,
                    cover2_path=?,
                    metadata_url=?
                WHERE file_id=?
            """, (
                category,
                description,
                img1_path,
                img2_path,
                url,
                file_id
            ))

            # Sync Files table category
            cur.execute("""
                UPDATE Files
                SET category=?
                WHERE id=?
            """, (category, file_id))

            conn.commit()
            conn.close()

            # -------- UI Refresh Sequence --------
            self.refresh_ui_after_db_update(file_id)
            # 🔥 Re-enable manual category editing
            self.category_combo.configure(state="normal")
            self.status_var.set("Metadata refreshed successfully.")

        except Exception as e:
            messagebox.showerror("Error", str(e))


    # ----------------------
    # Update Tab (Manual)
    # ----------------------
    def setup_update_tab(self, parent):
        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Update Record", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=8, pady=(8,4))

        frame = ttk.Frame(parent)
        frame.pack(fill="x", padx=8, pady=6)

        ttk.Label(frame, text="File Name:").grid(row=0, column=0, sticky="w", pady=4)
        self.update_name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.update_name_var, width=60).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(frame, text="Year:").grid(row=1, column=0, sticky="w", pady=4)
        self.update_year_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.update_year_var, width=20).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(frame, text="Category:").grid(row=2, column=0, sticky="w", pady=4)
        self.update_category_var = tk.StringVar()
        self.update_category_combo = ttk.Combobox(
            frame,
            textvariable=self.update_category_var,
            values=self.get_all_categories(),
            state="normal",
            width=40
        )
        self.update_category_combo.grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(frame, text="Path:").grid(row=3, column=0, sticky="w", pady=4)
        self.update_path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.update_path_var, width=60).grid(row=3, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Browse", command=self.browse_update_path).grid(row=3, column=2, padx=6)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill="x", padx=8, pady=8)

        ttk.Button(btn_frame, text="Save Changes", command=self.save_update_record).pack(side="left")
        ttk.Button(btn_frame, text="Delete Record", command=self.delete_update_record).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Clear", command=self.clear_update_form).pack(side="left", padx=6)

    def browse_update_path(self):
        path = filedialog.askopenfilename(title="Select file path")
        if path:
            self.update_path_var.set(path)

    def clear_update_form(self):
        self.update_name_var.set("")
        self.update_year_var.set("")
        self.update_category_var.set("")
        self.update_path_var.set("")

    def populate_update_form(self, file_id):
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(SELECT_MOVIE_DETAIL_VIEW, (file_id,))
            row = cur.fetchone()
            conn.close()

            if not row:
                self.clear_update_form()
                return

            name, ext, year, storage, path, size_bytes, category, desc, cover1, cover2 = row

            self.update_name_var.set(name or "")
            self.update_year_var.set(str(year) if year else "")
            self.update_category_var.set(category or "")
            self.update_path_var.set(path or "")

            # Refresh category choices
            try:
                cats = self.get_all_categories()
                self.update_category_combo["values"] = cats
            except:
                pass

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load record: {e}")

    def save_update_record(self):
        if not self.selected_file_id:
            messagebox.showwarning("No Selection", "Select a DB record first in the viewer.")
            return

        file_id = self.selected_file_id
        name = self.update_name_var.get().strip()
        year = self.update_year_var.get().strip()
        category = self.update_category_var.get().strip()
        path = self.update_path_var.get().strip()

        if not name:
            messagebox.showwarning("Validation", "File Name cannot be empty.")
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            # Update Files table
            cur.execute("""
                UPDATE Files
                SET file_name = ?, year = ?, category = ?, full_path = ?
                WHERE id = ?
            """, (name, int(year) if year else None, category if category else None, path if path else None, file_id))

            # Update or insert MovieDetails category if present
            cur.execute("UPDATE MovieDetails SET category = ? WHERE file_id = ?", (category if category else None, file_id))
            if cur.rowcount == 0 and category:
                # Insert minimal MovieDetails row
                try:
                    cur.execute(MOVIE_DETAILS_INSERT_MANUAL, (file_id, category, "", None, None))
                except Exception:
                    pass

            conn.commit()
            conn.close()

            messagebox.showinfo("Saved", "Record updated successfully.")

            # Refresh UI
            self.refresh_ui_after_db_update(file_id)

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def delete_update_record(self):
        """Delete the currently selected record with confirmation."""
        if not self.selected_file_id:
            messagebox.showwarning("No Selection", "Select a DB record first in the viewer.")
            return

        file_id = self.selected_file_id
        file_name = self.update_name_var.get().strip()

        if not file_name:
            messagebox.showwarning("Error", "No record data loaded.")
            return

        # Ask for confirmation
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the record '{file_name}'?\n\n"
            "This will also delete:\n"
            "- Metadata record\n"
            "- Cover images from the covers folder"
        ):
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            # Get cover paths from MovieDetails before deletion
            cur.execute("SELECT cover1_path, cover2_path FROM MovieDetails WHERE file_id = ?", (file_id,))
            cover_result = cur.fetchone()

            cover_paths = []
            if cover_result:
                cover1_path, cover2_path = cover_result
                if cover1_path:
                    cover_paths.append(cover1_path)
                if cover2_path:
                    cover_paths.append(cover2_path)

            # Delete from MovieDetails
            cur.execute("DELETE FROM MovieDetails WHERE file_id = ?", (file_id,))

            # Delete from Files
            cur.execute(DELETE_FILE_BY_ID, (file_id,))

            conn.commit()
            conn.close()

            # Delete cover images from disk
            for cover_path in cover_paths:
                try:
                    resolved_path = self.resolve_cover_path(cover_path)
                    if resolved_path and os.path.exists(resolved_path):
                        os.remove(resolved_path)
                except Exception as e:
                    # Log error but continue with other files
                    print(f"Warning: Could not delete cover file {cover_path}: {e}")

            messagebox.showinfo("Deleted", f"Record '{file_name}' deleted successfully.")

            # Clear the form
            self.clear_update_form()
            self.selected_file_id = None

            # Refresh UI
            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete record: {e}")


    def recreate_database(self):
        if not self.current_db_path:
            messagebox.showwarning("No Database", "Please open or create a database first.")
            return

        if not messagebox.askyesno(
            "Confirm Full Reset",
            "This will DELETE ALL database records and recreate tables.\n\nProceed?"
        ):
            return

        try:
            init_db(self.current_db_path, fresh=True)
            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

            messagebox.showinfo("Done", "Database recreated successfully.")

        except Exception as e:
            messagebox.showerror("Error", f"DB reset failed:\n{e}")
    

    def open_sqlite_db(self):
        db_path = filedialog.askopenfilename(title="Select DB", filetypes=[("SQLite","*.db"),("All files","*.*")])
        if not db_path:
            return
        self.current_db_path = self.resolve_app_path(db_path)
        self.save_settings({"last_db_path": self.current_db_path})
        try:
                ensure_global_unique_index(self.current_db_path)
        except Exception as e:
                 messagebox.showerror("Uniqueness Error", f"Failed to ensure uniqueness:\n{e}")

        self.load_db_records()

    def verify_db_vs_disk(self):

        if not self.current_db_path:
            messagebox.showwarning("No DB", "Open a database first.")
            return

        storage_id = self.select_storage_id_dialog()
        if not storage_id:
            return

        scan_root = filedialog.askdirectory(
            title="Select root folder of this physical disk"
        )
        if not scan_root:
            return

        # ---- Scan disk: (base_name, size) -> [full_path,...] ----
        disk_index = {}

        for root, _, files in os.walk(scan_root):
            for f in files:
                base, ext = os.path.splitext(f)
                if ext.lower() not in self.allowed_video_exts:
                    continue

                full = os.path.join(root, f)
                try:
                    size = int(os.path.getsize(full))
                except:
                    continue

                key = (base.lower(), size)
                disk_index.setdefault(key, []).append(full)

        # ---- Scan disk for DVD folders ----
        dvd_disk_index = {}

        for root, dirs, files in os.walk(scan_root):
            if "VIDEO_TS" in dirs:
                dvd_root = root
                dvd_name = os.path.basename(dvd_root).lower()
                size = get_folder_size_bytes(dvd_root)

                dvd_disk_index[(dvd_name, size)] = dvd_root

                # 🚫 do not descend into VIDEO_TS
                dirs[:] = []

        # ---- Load DB rows for selected storage id ----
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, file_name, extension, size_bytes, full_path
            FROM Files
            WHERE storage_id = ?
        """, (storage_id,))

        rows = cur.fetchall()
        conn.close()

        if not rows:
            messagebox.showinfo("Not found", "No records for this storage ID.")
            return

        problems = []

        for rid, name, ext, sizeb, old_path in rows:

            # -------- DVD record --------
            if ext.upper() == "DVD":
                key = (name.lower(), int(sizeb))

                if key not in dvd_disk_index:
                    problems.append((
                        rid,
                        name,
                        format_bytes(sizeb),
                        old_path,
                        "DVD folder missing or VIDEO_TS not found"
                    ))
                continue

            # -------- Normal file --------
            key = (name.lower(), int(sizeb))

            if key not in disk_index:
                problems.append((
                    rid,
                    name,
                    format_bytes(sizeb),
                    old_path,
                    "Missing on disk"
                ))


        # ---- Load ALL DB rows for cross-storage detection ----
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT file_name, size_bytes, storage_id
            FROM Files
        """)
        all_db_rows = cur.fetchall()
        conn.close()

        global_db_map = {}
        for n, s, sid in all_db_rows:
            key = (n.lower(), int(s))
            global_db_map.setdefault(key, set()).add(sid)
        

        # ---------- Disk -> DB check ----------
        db_index = set(
            (name.lower(), int(sizeb))
            for _, name, ext, sizeb, _ in rows
        )
        db_dvd_index = set(
            (name.lower(), int(sizeb))
            for _, name, ext, sizeb, _ in rows
            if ext.upper() == "DVD"
        )

        for (base, size), paths in disk_index.items():
            key = (base, size)

            if key not in db_index:
                for p in paths:

                    if key in global_db_map:
                        found_in = ", ".join(global_db_map[key])
                        msg = f"Exists in DB under storage(s): {found_in}"
                    else:
                        msg = "Exists on disk but missing in DB"

                    problems.append((
                        "—",
                        base,
                        format_bytes(size),
                        p,
                        msg
                    ))
        # -------- Disk DVD → DB check --------
        for (dvd_name, dvd_size), dvd_path in dvd_disk_index.items():
            key = (dvd_name, dvd_size)

            if key not in db_dvd_index:
                problems.append((
                    "—",
                    dvd_name,
                    format_bytes(dvd_size),
                    dvd_path,
                    "DVD exists on disk but missing in DB"
                ))

        if not problems:
            messagebox.showinfo("Verification Complete",
                                "No discrepancies found for this disk.")
            return

        self.show_db_disk_problems(problems, disk_index, storage_id, scan_root)
  
    def show_db_disk_problems(self, problems, disk_index, storage_id, scan_root):

        win = tk.Toplevel(self.root)
        win.title("DB vs Disk Verification")
        win.geometry("1200x520")

        tk.Label(win,
                text=f"Storage ID: {storage_id}   |   Scan root: {scan_root}   |   Problems: {len(problems)}",
                font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=6)

        cols = ("ID", "File", "DB Size", "DB Path", "Problem")
        tree = ttk.Treeview(win, columns=cols, show="headings")

        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=230)

        tree.pack(fill="both", expand=True, padx=6, pady=6)

        for row in problems:
            tree.insert("", "end", values=row)

        btns = tk.Frame(win)
        btns.pack(fill="x", pady=6)
        

        tk.Button(btns, text="Auto-fix path",
                command=lambda: self.fix_by_filename(tree, disk_index))\
            .pack(side="left", padx=6)

        tk.Button(btns, text="Edit path manually",
                command=lambda: self.edit_selected_path(tree))\
            .pack(side="left", padx=6)

        tk.Button(btns, text="Delete DB record",
                fg="white", bg="darkred",
                command=lambda: self.delete_selected_problem(tree, win))\
            .pack(side="right", padx=10)

    def edit_cell(self, event):
        region = self.db_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        if not self.db_tree.identify_row(event.y):
            return

        row_id = self.db_tree.identify_row(event.y)
        col = self.db_tree.identify_column(event.x)

        if not row_id or not col:
            return

        col_index = int(col.replace("#", "")) - 1
        col_name = self.db_tree["columns"][col_index]

        if col_name not in ("Year", "Category"):
            return

        x, y, w, h = self.db_tree.bbox(row_id, col)
        value = self.db_tree.item(row_id, "values")[col_index]
        record_id = self.db_tree.item(row_id, "tags")[0]


        # ---------------- YEAR EDITOR ----------------
        if col_name == "Year":
            edit = tk.Entry(self.db_tree)
            edit.place(x=x, y=y, width=w, height=h)
            edit.insert(0, value)
            edit.focus()

            def save_year(event=None):
                new_val = edit.get().strip()

                if new_val and not new_val.isdigit():
                    messagebox.showwarning("Invalid Year", "Year must be numeric.")
                    return

                try:
                    new_val = int(new_val) if new_val else None

                    conn = self.get_connection()
                    cur = conn.cursor()
                    cur.execute("UPDATE Files SET year=? WHERE id=?", (new_val, record_id))
                    conn.commit()
                    conn.close()

                    values = list(self.db_tree.item(row_id, "values"))
                    values[col_index] = new_val if new_val else ""
                    self.db_tree.item(row_id, values=values)

                except Exception as e:
                    messagebox.showerror("Update Error", str(e))

                edit.destroy()

            edit.bind("<Return>", save_year)
            edit.bind("<FocusOut>", save_year)

        # ---------------- CATEGORY EDITOR ----------------
        else:
            cats = self.get_all_categories()

            combo = ttk.Combobox(self.db_tree, values=cats)
            combo.place(x=x, y=y, width=w, height=h)
            combo.set(value)
            combo.focus()

            def save_category(event=None):
                new_val = combo.get().strip()
                if not new_val:
                    combo.destroy()
                    return

                new_val = new_val.title()
                self.add_new_category(new_val)

                try:
                    conn = self.get_connection()
                    cur = conn.cursor()
                    cur.execute("UPDATE Files SET category=? WHERE id=?", (new_val, record_id))
                    conn.commit()
                    conn.close()

                    values = list(self.db_tree.item(row_id, "values"))
                    values[col_index] = new_val
                    self.db_tree.item(row_id, values=values)

                except Exception as e:
                    messagebox.showerror("Update Error", str(e))

                combo.destroy()

            combo.bind("<<ComboboxSelected>>", save_category)
            combo.bind("<FocusOut>", save_category)
     

    def fix_by_filename(self, tree, disk_index):

        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select at least one record.")
            return

        conn = self.get_connection()
        cur = conn.cursor()
        fixed = 0

        for item in sel:
            rid, name, sizeb, _, problem = tree.item(item, "values")
            key = (name.lower(), int(sizeb))

            if key not in disk_index:
                continue

            real_path = disk_index[key][0]  # take first match

            cur.execute(
                "UPDATE Files SET full_path=? WHERE id=?",
                (real_path, rid)
            )

            tree.delete(item)
            fixed += 1

        conn.commit()
        conn.close()

        self.load_db_records()
        messagebox.showinfo("Auto-fix", f"Paths updated: {fixed}")
 

    def relocate_selected_file(self, tree):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a row first.")
            return

        folder = filedialog.askdirectory(title="Select root folder to search")
        if not folder:
            return

        conn = self.get_connection()
        cur = conn.cursor()

        fixed = 0

        for item in sel:
            rid, name, sizeb, old_path, _ = tree.item(item, "values")
            name = str(name).lower()

            for root, _, files in os.walk(folder):
                for f in files:
                    if os.path.splitext(f)[0].lower() == name:
                        full = os.path.join(root, f)
                        try:
                            if os.path.getsize(full) == int(sizeb):
                                cur.execute(
                                    "UPDATE Files SET full_path=? WHERE id=?",
                                    (full, rid)
                                )
                                fixed += 1
                                tree.delete(item)
                                raise StopIteration
                        except Exception:
                            pass
            try:
                raise StopIteration
            except StopIteration:
                pass

        conn.commit()
        conn.close()

        self.load_db_records()
        messagebox.showinfo("Relocate Done", f"Updated paths: {fixed}")

    def edit_selected_path(self, tree):
        sel = tree.selection()
        if not sel:
            return

        item = sel[0]
        rid, name, sizeb, old_path, _ = tree.item(item, "values")

        new = filedialog.askopenfilename(title="Select correct file")
        if not new:
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("UPDATE Files SET full_path=? WHERE id=?", (new, rid))
            conn.commit()
            conn.close()

            tree.delete(item)
            self.load_db_records()

        except Exception as e:
            messagebox.showerror("Update failed", str(e))

    def delete_selected_problem(self, tree, win):
        sel = tree.selection()
        if not sel:
            return

        if not messagebox.askyesno("Confirm", "Delete selected DB records?"):
            return

        conn = self.get_connection()
        cur = conn.cursor()

        for item in sel:
            rid = tree.item(item, "values")[0]
            cur.execute(DELETE_FILE_BY_ID, (rid,))
            tree.delete(item)

        conn.commit()
        conn.close()
        self.load_db_records()
        if not tree.get_children():
            win.destroy()
        
    def select_storage_id_dialog(self):
        """Show dropdown of unique storage_ids from DB and return selected one"""

        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT storage_id FROM Files ORDER BY storage_id")
        ids = [row[0] for row in cur.fetchall()]
        conn.close()

        if not ids:
            messagebox.showwarning("No Storage IDs", "No storage IDs found in database.")
            return None

        win = tk.Toplevel(self.root)
        win.title("Select Storage ID")
        win.geometry("320x130")
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="Select Storage ID to verify:").pack(pady=8)

        var = tk.StringVar(value=ids[0])
        combo = ttk.Combobox(win, textvariable=var, values=ids, state="readonly", width=32)
        combo.pack(pady=5)

        result = {"value": None}

        def confirm():
            result["value"] = var.get()
            win.destroy()

        tk.Button(win, text="OK", width=12, command=confirm).pack(pady=10)

        win.wait_window()
        return result["value"]


    def load_db_records(self):
        if not self.current_db_path:
            return

        if getattr(self, "db_load_in_progress", False):
            return

        self.db_load_in_progress = True
        self.status_var.set("Loading database records...")
        self._start_db_loading_indicator()

        thread = threading.Thread(
            target=self._load_db_records_worker,
            daemon=True
        )
        thread.start()

    def _start_db_loading_indicator(self):
        self.db_loading_running = True
        self.db_loading_dot_count = 0
        self._update_db_loading_indicator()
        self._set_db_viewer_enabled(False)

    def _stop_db_loading_indicator(self):
        self.db_loading_running = False
        self.db_loading_var.set("")
        self._set_db_viewer_enabled(True)

    def _update_db_loading_indicator(self):
        if not self.db_loading_running:
            return

        dots = "." * ((self.db_loading_dot_count % 3) + 1)
        self.db_loading_var.set(f"Loading{dots}")
        self.db_loading_dot_count += 1
        self.root.after(400, self._update_db_loading_indicator)

    def _set_db_viewer_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"

        for widget in getattr(self, "db_viewer_controls", []):
            try:
                widget.configure(state=state)
            except Exception:
                pass

        for button in getattr(self, "db_action_buttons", []):
            try:
                button.configure(state=state)
            except Exception:
                pass

        for widget in getattr(self, "db_tree_controls", []):
            try:
                if enabled:
                    widget.state(("!disabled",))
                else:
                    widget.state(("disabled",))
            except Exception:
                pass

    def _load_db_records_worker(self):
        try:
            self.repair_metadata_integrity()
            conn = self.get_connection()
            cur = conn.cursor()

            # Ensure metadata status view exists
            cur.execute(DROP_METADATA_VIEW)
            cur.execute(CREATE_METADATA_VIEW)
            selected_sid = self.selected_storage_filter.get()

            meta_filter = getattr(self, "meta_filter_var", tk.StringVar(value="All")).get()

            query = SELECT_METADATA_VIEW
            params = []

            # Storage filter
            if selected_sid != "ALL":
                query += " WHERE storage_id = ?"
                params.append(selected_sid)

            # Metadata filter
            if meta_filter == "Complete":
                query += " AND metadata_status='COMPLETE'" if params else " WHERE metadata_status='COMPLETE'"

            elif meta_filter == "Incomplete":
                query += " AND metadata_status='INCOMPLETE'" if params else " WHERE metadata_status='INCOMPLETE'"

            elif meta_filter == "No Metadata":
                query += " AND metadata_status='NO_METADATA'" if params else " WHERE metadata_status='NO_METADATA'"

            query += " ORDER BY id DESC"

            cur.execute(query, params)
            rows = cur.fetchall()
            conn.close()

            self.root.after(0, lambda: self._finish_load_db_records(rows))
        except Exception as e:
            self.root.after(0, lambda: self._load_db_records_error(e))

    def _finish_load_db_records(self, rows):
        self.db_load_in_progress = False
        self._stop_db_loading_indicator()

        self.db_records_cache = rows
        self.all_filtered_rows = list(rows)

        total = len(self.all_filtered_rows)
        self.total_pages = (total - 1) // self.page_size + 1 if total > 0 else 1
        self.current_page = 0

        self.show_db_page(0)
        self.status_var.set(f"Loaded {total} rows from {self.current_db_path}")
        self.update_db_statistics()
        self.update_status_bar_db_info()
        self.load_category_dropdown()
        self.load_year_dropdown()
        self.update_metadata_status_summary()
        # Mark DB as loaded/cached and remember timestamp
        try:
            if self.current_db_path and os.path.exists(self.current_db_path):
                self._db_last_mtime = os.path.getmtime(self.current_db_path)
            else:
                self._db_last_mtime = None
        except Exception:
            self._db_last_mtime = None
        self._db_last_path = self.current_db_path
        self._db_loaded = True

    def _load_db_records_error(self, error):
        self.db_load_in_progress = False
        self._stop_db_loading_indicator()
        messagebox.showerror("Error", f"Failed reading DB: {error}")
        self.status_var.set("Failed to load database records.")

    def refresh_db_tree(self, rows):

        self.db_tree.delete(*self.db_tree.get_children())

        start = self.current_page * self.page_size

        for idx, r in enumerate(rows[start:start + self.page_size], start=1 + start):

            id_, fname, ext, sizeb, storage_id, cdate, path, year, category, status = r

            # Metadata status dot
            if status == "COMPLETE":
                tag = "meta_complete"
            elif status == "INCOMPLETE":
                tag = "meta_incomplete"
            else:
                tag = "meta_missing"

            self.db_tree.insert(
                "",
                "end",
                values=(
                    idx,
                    fname,
                    ext,
                    format_size(sizeb),
                    storage_id,
                    year if year else "",
                    category if category else ""
                ),
                tags=(id_, tag, fname)   # ✔ id first, color tag second, filename third for context menu
            )

    
    def auto_resize_columns(self, display_rows):
        cols = ("ID", "Name", "Ext", "Size", "Storage", "Year", "Category")
        maxw = [self._font.measure(c+"  ") for c in cols]
        for row in display_rows:
            for i, cell in enumerate(row):
                w = self._font.measure(str(cell)+"  ")
                if w > maxw[i]:
                    maxw[i] = w
        for i, c in enumerate(cols):
            self.db_tree.column(c, width=min(maxw[i]+10, 900))

    def filter_db_records(self):
        q = self.db_search_var.get().lower().strip() if hasattr(self, "db_search_var") else ""
        selected_cat = self.db_category_var.get() if hasattr(self, "db_category_var") else "All"
        selected_year = self.db_year_var.get() if hasattr(self, "db_year_var") else "All"

        rows = list(self.db_records_cache)

        # 🔍 text search filter
        if q:
            # Normalize query into alphanumeric tokens
            q_tokens = [re.sub(r'[^0-9a-z]', '', t) for t in re.findall(r"\w+", q.lower()) if t]

            def row_matches(r):
                # Combine several text fields to search: file_name, full_path, category, extension
                parts = []
                try:
                    parts.append(str(r[1]))
                except Exception:
                    pass
                try:
                    parts.append(str(r[6]))
                except Exception:
                    pass
                try:
                    parts.append(str(r[8] or ""))
                except Exception:
                    pass
                try:
                    parts.append(str(r[2] or ""))
                except Exception:
                    pass

                combined = " ".join([p for p in parts if p])
                norm = re.sub(r'[^0-9a-z]', '', combined.lower())

                # All tokens must appear in normalized combined string (order not required)
                for t in q_tokens:
                    if t and t not in norm:
                        return False
                return True

            rows = [r for r in rows if row_matches(r)]

        # 🏷 category filter
        if selected_cat and selected_cat != "All":
            if selected_cat == "Uncategorized":
                rows = [r for r in rows if not r[8]]
            else:
                rows = [r for r in rows if r[8] == selected_cat]

        # 🗓 year filter
        if selected_year and selected_year != "All":
            def year_match(val):
                try:
                    return str(int(val)) == str(selected_year)
                except Exception:
                    return False

            rows = [r for r in rows if year_match(r[7])]

        self.all_filtered_rows = rows

        total = len(self.all_filtered_rows)
        self.total_pages = (total - 1) // self.page_size + 1 if total > 0 else 1
        self.current_page = 0
        self.show_db_page(0)


    def show_db_page(self, page_num):
        if not self.all_filtered_rows:
            self.refresh_db_tree([])
            self.page_label.config(text="Page 0 / 0 | Total: 0 records")
            return

        if page_num < 0:
            page_num = 0

        if self.total_pages <= 0:
            self.total_pages = 1

        if page_num >= self.total_pages:
            page_num = self.total_pages - 1

        self.current_page = page_num

        # ✅ PASS FULL LIST (not sliced)
        self.refresh_db_tree(self.all_filtered_rows)

        total_records = len(self.all_filtered_rows)
        self.page_label.config(text=f"Page {self.current_page+1} / {self.total_pages} | Total: {total_records} records")


    def first_db_page(self):
        # Go to first page (index 0)
        if self.total_pages <= 0:
            return
        self.show_db_page(0)
    def next_db_page(self):
        if self.current_page + 1 < self.total_pages:
            self.show_db_page(self.current_page + 1)

    def prev_db_page(self):
        if self.current_page > 0:
            self.show_db_page(self.current_page - 1)
    def last_db_page(self):
        # Go to last page (index total_pages - 1)
        if self.total_pages <= 0:
            return
        self.show_db_page(self.total_pages - 1)


    def apply_page_size(self):
        try:
            v = int(self.page_size_var.get())
            if v <= 0:
                raise ValueError
            self.page_size = v
            total = len(self.all_filtered_rows)
            self.total_pages = (total-1)//self.page_size + 1 if total > 0 else 1
            self.current_page = 0
            self.show_db_page(0)
        except Exception:
            messagebox.showwarning("Invalid Page Size", "Enter a positive integer for page size.")

    def reset_db_viewer_filters(self):
        if hasattr(self, "db_search_var"):
            self.db_search_var.set("")
        if hasattr(self, "db_category_var"):
            self.db_category_var.set("All")
        if hasattr(self, "db_year_var"):
            self.db_year_var.set("All")
        if hasattr(self, "meta_filter_var"):
            self.meta_filter_var.set("All")
        if hasattr(self, "selected_storage_filter"):
            self.selected_storage_filter.set("ALL")

        self.load_db_records()

        try:
            self.db_tree.selection_remove(self.db_tree.selection())
        except Exception:
            try:
                self.db_tree.selection_set(())
            except Exception:
                pass
            self.show_db_page(0)
        except Exception:
            messagebox.showerror("Error", "Invalid page size")

    def sort_db_by_column(self, col):
        map_idx = {
            "No": None,        # 👈 serial number only, not DB data
            "Name": 1,
            "Ext": 2,
            "Size": 3,
            "Storage": 4,
            "Year": 5,
            "Category": 6
        }

        idx = map_idx.get(col, None)

        # Do nothing if user clicks "No"
        if idx is None:
            return

        rev = self._db_sort_reverse.get(col, False)

        try:
            if col == "Size":
                sorted_rows = sorted(
                    self.all_filtered_rows,
                    key=lambda x: (x[idx] if x[idx] is not None else 0),
                    reverse=not rev
                )

            else:
                sorted_rows = sorted(
                    self.all_filtered_rows,
                    key=lambda x: (str(x[idx]).lower() if x[idx] is not None else ""),
                    reverse=not rev
                )

        except Exception:
            sorted_rows = self.all_filtered_rows

        self._db_sort_reverse[col] = not rev
        self.all_filtered_rows = sorted_rows

        total = len(self.all_filtered_rows)
        self.total_pages = (total - 1) // self.page_size + 1 if total > 0 else 1
        self.current_page = 0
        self.show_db_page(0)

    def on_db_tree_double_click(self, event):
        item = self.db_tree.identify_row(event.y)
        if not item:
            return

        vals = self.db_tree.item(item, "values")
        if not vals or len(vals) < 7:
            return

        path = vals[6]

        if path and os.path.exists(path):
            self.open_file(path)
        else:
            messagebox.showerror("Error", "File not found on disk.")


    def delete_selected_db_rows(self):
        if not self.current_db_path:
            messagebox.showinfo("Info", "Open DB first")
            return

        sel = self.db_tree.selection()
        if not sel:
            messagebox.showinfo("Info", "No rows selected")
            return

        if not messagebox.askyesno("Confirm", f"Delete {len(sel)} selected rows?"):
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            for item in sel:
                try:
                    record_id = self.db_tree.item(item, "tags")[0]   # ✅ REAL DB ID
                    cur.execute(DELETE_FILE_BY_ID, (record_id,))
                except Exception:
                    continue

            conn.commit()
            conn.close()

            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

            messagebox.showinfo("Success", "Deleted selected rows")

        except Exception as e:
            messagebox.showerror("Error", f"Delete failed: {e}")


    def delete_all_db_rows(self):
        if not self.current_db_path:
            messagebox.showinfo("Info", "Open DB first")
            return
        if not messagebox.askyesno("Confirm", "Delete ALL rows from DB?"):
            return
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            
            cur.execute("DELETE FROM Files")
            conn.commit()
            conn.close()
            self.load_db_records()
            self.update_db_statistics()
            self.update_status_bar_db_info()

            messagebox.showinfo("Success", "All rows deleted")
        except Exception as e:
            messagebox.showerror("Error", f"Delete all failed: {e}")

    def export_db_to_excel(self):
        if not self.current_db_path:
            messagebox.showinfo("Info", "Open DB first")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel","*.xlsx")])
        if not path:
            return
        try:
            conn = self.get_connection()
            df = pd.read_sql_query(DB_SELECT_ALL, conn)
            conn.close()
            df.to_excel(path, index=False)
            messagebox.showinfo("Success", f"Exported to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")
