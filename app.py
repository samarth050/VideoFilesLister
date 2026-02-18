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
import os
import json
import sqlite3
import re
import ctypes
import subprocess
import sys
import datetime
from pathlib import Path
from collections import defaultdict

# ===============================
# Third-Party Libraries
# ===============================
import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font as tkfont

from PIL import Image, ImageTk
from io import BytesIO
import requests


COVERS_DIR = os.path.join(os.getcwd(), "covers")
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
    FILES_TABLE_SQL,
    MOVIE_TABLE_SQL,
    FILES_TABLE_INDEX,
    CATEGORIES_TABLE_SQL,
    DB_SELECT_ALL,
    DB_SELECT_STORAGE_ID,
    MOVIE_DETAILS_INSERT
)

from db.database import init_db, ensure_global_unique_index

from scanner.scanner import (
    get_files_info,
    detect_storage_id_from_path,
    get_windows_drive_label,
    get_drive_label
)

from duplicates.duplicate_analyzer import analyze_duplicates

from utils.helpers import (
    format_size,
    format_bytes,
    format_db_total_size,
    format_date,
    get_folder_size_bytes
)

from utils.movie_scraper import scrape_movie


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


class FileListerApp:
    CONFIG_FILE = "app_settings.json"


    def __init__(self, root):
        self.master_db_path = "VideoFiles.db"
        settings = self.load_settings()
        self.current_db_path = settings.get("last_db_path", self.master_db_path)

        #self.current_db_path = self.master_db_path

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

        # File data stores
        self.all_files_info = []
        self.file_paths = {}

        # SQLite viewer state
        #self.current_db_path = None
        self.selected_file_id = None
        #self.current_image_urls = []

        self.db_records_cache = []
        self.all_filtered_rows = []
        self.selected_storage_filter = tk.StringVar(value="ALL")
        self.available_storage_ids = ["ALL"]

        #self.current_page_rows = []
        self.page_size = 50
        self.current_page = 0
        self.total_pages = 0
        self._db_sort_reverse = {}
        self.storage_id_var = tk.StringVar(value="UNKNOWN")

        # --- Duplicate pagination ---
        self.dup_all_rows = []
        self.dup_current_page = 0
        self.dup_total_pages = 0


        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)


        # Always use the one master DB
        self.current_db_path = self.master_db_path

        # Auto-create + load
        if not os.path.exists(self.master_db_path):
            init_db(self.current_db_path, fresh=True)
            self.load_db_records()
                # Load settings

        # Build UI here (tabs, combo boxes, etc.)

        # Populate combos AFTER UI + DB are ready
        self.root.after(100, self.load_storage_ids_from_db)

    def get_connection(self):
        conn = sqlite3.connect(self.current_db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


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

    def fetch_metadata(self):
        if not self.selected_file_id:
            messagebox.showwarning("Select Record", "Select a record first.")
            return

        url = self.meta_url_var.get().strip()

        if not url:
            messagebox.showwarning("URL Required", "Paste metadata URL.")
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            # 🔥 Check if metadata already exists
            cur.execute("""
                SELECT category, description, cover1_path, cover2_path, metadata_url
                FROM MovieDetails
                WHERE file_id=?
            """, (self.selected_file_id,))

            row = cur.fetchone()

            # If already fetched and URL unchanged → Load from DB
            if row and row[0] and row[4] == url:
                self.category_var.set(row[0])

                self.description_text.delete("1.0", tk.END)
                self.description_text.insert("1.0", row[1])

                if row[2] and os.path.exists(row[2]):
                    self.display_image_from_file(row[2], self.image_label1)

                if row[3] and os.path.exists(row[3]):
                    self.display_image_from_file(row[3], self.image_label2)

                self.status_var.set("Metadata loaded from local database.")
                conn.close()
                return

            # ----------------------------
            # SCRAPE (ONLY IF NOT STORED)
            # ----------------------------
            data = scrape_movie(url)

            category = data["category"]
            description = data["description"]
            images = data["images"]

            file_id = self.selected_file_id

            img1_path = os.path.join(COVERS_DIR, f"{file_id}_1.jpg")
            img2_path = os.path.join(COVERS_DIR, f"{file_id}_2.jpg")

            # ✅ Clear old covers if scraper returned fewer images

            # Remove cover1 if no first image
            if len(images) == 0 and os.path.exists(img1_path):
                os.remove(img1_path)

            # Remove cover2 if no second image
            if len(images) < 2 and os.path.exists(img2_path):
                os.remove(img2_path)

            # Download covers (only if not already present)
            if len(images) > 0 and not os.path.exists(img1_path):
                self.download_image(images[0], img1_path)

            if len(images) > 1 and not os.path.exists(img2_path):
                self.download_image(images[1], img2_path)

            # Insert or Update MovieDetails
            cur.execute(MOVIE_DETAILS_INSERT,(
                file_id,
                category,
                description,
                img1_path,
                img2_path,
                url
            ))
            # 🔥 IMPORTANT ADD THIS
            cur.execute("""
                UPDATE Files
                SET category=?
                WHERE id=?
            """, (category, file_id))
            conn.commit()
            conn.close()
            self.refresh_ui_after_db_update(file_id)
            self.status_var.set("Metadata fetched and stored locally.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_metadata_from_db(self):
        conn = self.get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT category, description, cover1_path, cover2_path
            FROM MovieDetails
            WHERE file_id=?
        """, (self.selected_file_id,))

        row = cur.fetchone()
        conn.close()

        if row:
            self.category_var.set(row[0] or "")

            self.description_text.delete("1.0", tk.END)
            self.description_text.insert("1.0", row[1] or "")

            if row[2] and os.path.exists(row[2]):
                self.display_image_from_file(row[2], self.image_label1)

            if row[3] and os.path.exists(row[3]):
                self.display_image_from_file(row[3], self.image_label2)


    def download_image(self, url, save_path):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            with open(save_path, "wb") as f:
                f.write(response.content)

            return True
        except Exception as e:
            print("Image download failed:", e)
            return False

    def display_image_from_file(self, image_path, label_widget):
        try:
            img = Image.open(image_path)
            img = img.resize((200, 300))
            photo = ImageTk.PhotoImage(img)

            label_widget.configure(image=photo)
            label_widget.image = photo
        except Exception as e:
            print("Image load failed:", e)

  
    def save_metadata(self):
        if not self.selected_file_id:
            messagebox.showwarning("Select Record", "Select a record first.")
            return

        category = self.category_var.get().strip()
        description = self.description_text.get("1.0", tk.END).strip()
        file_id = self.selected_file_id

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            # Upsert MovieDetails
            cur.execute("""
                INSERT INTO MovieDetails (file_id, category, description)
                VALUES (?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    category=excluded.category,
                    description=excluded.description
            """, (file_id, category, description))

            # Sync Files table category
            cur.execute("""
                UPDATE Files
                SET category=?
                WHERE id=?
            """, (category, file_id))

            conn.commit()
            conn.close()

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

            cur.execute("""
                SELECT storage_id,
                    COUNT(*) AS cnt,
                    SUM(size_bytes) AS total_size
                FROM Files
                GROUP BY storage_id
                ORDER BY storage_id
            """)

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
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_settings(self, data):
        settings = self.load_settings()
        settings.update(data)

        try:
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            print("Failed to save settings:", e)


    def setup_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        main_tab = ttk.Frame(self.notebook)
        stats_tab = ttk.Frame(self.notebook)
        db_tab = ttk.Frame(self.notebook)
        dup_tab = ttk.Frame(self.notebook)
        self.notebook.add(main_tab, text="Files List")
        self.notebook.add(stats_tab, text="Statistics")
        self.notebook.add(db_tab, text="SQLite Viewer")
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.notebook.add(dup_tab, text="Duplicates")

        self.setup_main_tab(main_tab)
        self.setup_stats_tab(stats_tab)
        self.setup_db_viewer_tab(db_tab)    
        self.setup_duplicates_tab(dup_tab)


        self.status_var = tk.StringVar()
        tk.Label(self.root, textvariable=self.status_var,
                 relief=tk.SUNKEN, bd=1, anchor="w").pack(fill="x", side="bottom")

    def on_tab_changed(self, event):
        if self.notebook.tab(self.notebook.select(), "text") == "Statistics":
            self.update_db_statistics()
            self.update_status_bar_db_info()
            self.draw_extension_pie_chart()

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
        self.file_table.configure(yscroll=ys.set, xscroll=xs.set)

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
    
    def get_storage_id(self):
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
                    if os.path.normcase(db_path) != os.path.normcase(f["full_path"]):
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

        row_file_map = {}

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
            f, _, _ = row_file_map.get(sel[0])
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
                        cur.execute("""
                            UPDATE Files
                            SET storage_id=?, full_path=?, creation_date=?
                            WHERE id=?
                        """, (
                            self.get_storage_id(),
                            f["full_path"],
                            format_date(f["creation_date"]),
                            db_id
                        ))
                        updated += 1

                    elif reason in ("Not present in database", "Name match, size mismatch"):
                        cur.execute("""
                            INSERT INTO Files
                            (file_name, extension, size_bytes, storage_id,
                            creation_date, full_path, year, category)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
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
            f, _, _ = row_file_map[item]
            os.startfile(f["full_path"])

        tree.bind("<Double-1>", on_double_click)


    def force_insert_selected_files(self, tree, row_file_map, parent_win):
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

            insert_q = """
                INSERT INTO Files
                (file_name, extension, size_bytes, storage_id,
                creation_date, full_path, year, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """

            update_q = """
                UPDATE Files
                SET storage_id=?, full_path=?, creation_date=?
                WHERE id=?
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
                        if os.path.normcase(db_path) != os.path.normcase(full_path):
                            # 🔄 moved movie
                            cur.execute(update_q, (
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
                    cur.execute(insert_q, (
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
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT name FROM Categories ORDER BY name")
            rows = cur.fetchall()
            conn.close()
            return [r[0] for r in rows]
        except:
            return []

    def add_new_category(self, name):
        if not name.strip():
            return False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO Categories(name) VALUES (?)", (name.strip(),))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    

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
            height=6
        )
        self.db_storage_tree.pack(fill="x", padx=6, pady=4)

        self.db_storage_tree.heading("Storage", text="Storage ID")
        self.db_storage_tree.heading("Files", text="File Count")
        self.db_storage_tree.heading("Total Size", text="Total Size")

        self.db_storage_tree.column("Storage", width=200, anchor="w")
        self.db_storage_tree.column("Files", width=100, anchor="e")
        self.db_storage_tree.column("Total Size", width=140, anchor="e")
        
        tk.Button(parent, text="Export Statistics to Excel",
          command=self.export_db_statistics_to_excel).pack(anchor="w", padx=6, pady=4)


        chart_frame = tk.LabelFrame(parent, text="Extension Distribution (DB)")
        chart_frame.pack(fill="both", expand=True, padx=6, pady=6)

        self.chart_canvas = None
        tk.Button(chart_frame, text="Refresh Pie Chart",
          command=self.draw_extension_pie_chart).pack(anchor="w", padx=4, pady=4)


        self.chart_container = tk.Frame(chart_frame)
        self.chart_container.pack(fill="both", expand=True)

    
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

            cur.executemany("DELETE FROM Files WHERE id = ?", [(i,) for i in ids])

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

            stats_df = pd.read_sql_query("""
                SELECT extension,
                    COUNT(*) AS count,
                    SUM(size_bytes) AS total_size_bytes
                FROM Files
                GROUP BY extension
                ORDER BY extension
             """, conn)

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
            cur.execute("SELECT COUNT(*) FROM Files")
            total = cur.fetchone()[0]
            self.db_total_records_var.set(f"DB Records: {total}")

            # Total size of ALL files in DB
            cur.execute("SELECT IFNULL(SUM(size_bytes),0) FROM Files")
            total_bytes = cur.fetchone()[0]

            formatted = format_db_total_size(total_bytes)
            self.db_files_size_var.set(
                f"Total Files Size: {formatted}"# ({total_bytes:,} bytes)" #Include if size required in bytes
            )

            # Per-extension stats
            cur.execute("""
                SELECT extension,
                   COUNT(*) AS cnt,
                   SUM(size_bytes) AS total_size
                FROM Files
                GROUP BY extension
                ORDER BY extension
                """)
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
            

            cur.execute("SELECT COUNT(*) FROM Files")
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

    def load_movie_metadata(self, file_id):
        if not self.current_db_path:
            return

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("""
                SELECT category, description, cover1_path, cover2_path
                FROM MovieDetails
                WHERE file_id = ?
            """, (file_id,))

            row = cur.fetchone()
            conn.close()

            # Clear panel first
            self.category_var.set("")
            self.description_text.delete("1.0", tk.END)

            if hasattr(self, "image_label1"):
                self.image_label1.config(image="")
                self.image_label1.image = None

            if hasattr(self, "image_label2"):
                self.image_label2.config(image="")
                self.image_label2.image = None

            if not row:
                return

            category, description, cover1_path, cover2_path = row

            # Load category
            if category:
                self.category_var.set(category)

            # Load description
            if description:
                self.description_text.insert("1.0", description)

            # Load images from local files
            if cover1_path and os.path.exists(cover1_path):
                self.display_image_from_file(cover1_path, self.image_label1)

            if cover2_path and os.path.exists(cover2_path):
                self.display_image_from_file(cover2_path, self.image_label2)

            self.status_var.set("Metadata loaded from database.")

        except Exception as e:
            print("Metadata load error:", e)

 
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

            insert_q = """
                INSERT INTO Files
                (file_name, extension, size_bytes, storage_id,
                creation_date, full_path, year, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """

            update_q = """
                UPDATE Files
                SET storage_id=?, full_path=?, creation_date=?
                WHERE id=?
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
                    cur.execute(insert_q, (
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
                        if db_path_existing != full_path:
                            # 🔄 Movie moved
                            cur.execute(update_q, (
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
            cur.execute("SELECT DISTINCT category FROM Files ORDER BY category")
            cats = [r[0] for r in cur.fetchall() if r[0]]
            conn.close()
        except:
            cats = []

        values = ["All"] + cats + ["Uncategorized"]
        self.db_category_combo["values"] = values
        self.db_category_var.set("All")

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

            ids = [r[0] for r in cur.fetchall()]

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
                SELECT category, description, cover1_path, cover2_path
                FROM MovieDetails
                WHERE file_id=?
            """, (file_id,))

            row = cur.fetchone()
            conn.close()

            # Clear UI first
            self.clear_metadata_panel()

            if not row:
                return  # No metadata stored yet

            category, description, cover1_path, cover2_path = row

            # Load text
            self.category_var.set(category or "")

            self.description_text.delete("1.0", tk.END)
            self.description_text.insert("1.0", description or "")

            # Load images from local files ONLY
            if cover1_path and os.path.exists(cover1_path):
                self.display_image_from_file(cover1_path, self.image_label1)

            if cover2_path and os.path.exists(cover2_path):
                self.display_image_from_file(cover2_path, self.image_label2)

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
            self.load_movie_metadata(self.selected_file_id)
        else:
            self.selected_file_id = None
            self.clear_metadata_panel()



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

        tk.Button(top, text="Recreate DB (Clean)",
            command=self.recreate_database).pack(side="left", padx=6)

        tk.Button(top, text="Open SQLite DB", command=self.open_sqlite_db).pack(side="left", padx=4)
        tk.Button(top, text="Verify DB vs Disk", command=self.verify_db_vs_disk)\
            .pack(side="left", padx=6)
        tk.Button(top, text="Set Category", command=self.open_bulk_category_editor).pack(side="left", padx=6)

        tk.Label(top, text="Search:").pack(side="left", padx=(8,0))
        self.db_search_var = tk.StringVar()
        tk.Entry(top, textvariable=self.db_search_var, width=40).pack(side="left", padx=4)
        self.db_search_var.trace_add("write", lambda *a: self.filter_db_records())

        tk.Label(top, text="Category:").pack(side="left", padx=(8,0))

        self.db_category_var = tk.StringVar(value="All")
        self.db_category_combo = ttk.Combobox(
            top, textvariable=self.db_category_var,
            state="readonly", width=18
        )
        self.db_category_combo.pack(side="left", padx=4)
        self.db_category_combo.bind("<<ComboboxSelected>>", lambda e: self.filter_db_records())
        
        
        tk.Label(top, text="Page size:").pack(side="left", padx=(8,0))
        self.page_size_var = tk.IntVar(value=self.page_size)
        e = tk.Entry(top, textvariable=self.page_size_var, width=6)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.apply_page_size())

        tk.Button(top, text="Export to Excel", width=16,
                command=self.export_db_to_excel).pack(side="right", padx=6)

        tk.Button(top, text="Delete ALL", width=14,
                command=self.delete_all_db_rows).pack(side="right", padx=6)

        tk.Button(top, text="Delete Selected", width=16,
                command=self.delete_selected_db_rows).pack(side="right", padx=6)

        cols = ("No", "Name", "Ext", "Size", "Storage", "Date", "Path", "Year", "Category")

        frame = tk.Frame(parent)
        frame.pack(fill="both", expand=True)

        # ✅ CREATE TREE FIRST
        self.db_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        self.db_tree.bind("<<TreeviewSelect>>", self.on_db_row_select)

        # ✅ HEADINGS + SORT
        for c in cols:
            self.db_tree.heading(c, text=c, command=lambda _c=c: self.sort_db_by_column(_c))
            self.db_tree.column(c, width=180, anchor="w")

        # ✅ SPECIAL COLUMN FORMATTING (MUST be after creation)
        self.db_tree.column("No", width=60, anchor="center")
        self.db_tree.column("Ext", width=70, anchor="center")
        self.db_tree.column("Size", width=100, anchor="e")
        self.db_tree.column("Year", width=70, anchor="center")
        self.db_tree.column("Path", width=380)

        self.db_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frame, command=self.db_tree.yview)
        scroll.pack(side="right", fill="y")
        self.db_tree.configure(yscrollcommand=scroll.set)

        self.db_tree.bind("<Double-1>", self.edit_cell)

        pager = tk.Frame(parent)
        pager.pack(fill="x", pady=4)
        # ---------------------------
        # Metadata Details Frame
        # ---------------------------
        details_frame = tk.LabelFrame(parent, text="Movie Metadata", padx=8, pady=6)
        details_frame.pack(fill="x", padx=8, pady=8)
        # URL entry
        ttk.Label(details_frame, text="Metadata URL:").pack(anchor="w")

        self.meta_url_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=self.meta_url_var).pack(fill="x", pady=3)

        btn_frame = tk.Frame(details_frame)
        btn_frame.pack(pady=4)

        ttk.Button(btn_frame, text="Fetch Metadata",
                command=self.fetch_metadata).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="🔄 Refresh Metadata",
                command=self.refresh_metadata).pack(side="left", padx=5)

        # Category field
        ttk.Label(details_frame, text="Category:").pack(anchor="w")
        self.category_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=self.category_var).pack(fill="x", pady=3)

        # Description field
        ttk.Label(details_frame, text="Description:").pack(anchor="w")
        self.description_text = tk.Text(details_frame, height=6)
        self.description_text.pack(fill="both", pady=3)

        ttk.Button(details_frame, text="Save Metadata",
                command=self.save_metadata).pack(pady=5)
        
        # Image preview frame
        image_frame = tk.Frame(details_frame)
        image_frame.pack(pady=6)

        self.image_label1 = tk.Label(image_frame)
        self.image_label1.pack(side="left", padx=10)

        self.image_label2 = tk.Label(image_frame)
        self.image_label2.pack(side="left", padx=10)

        tk.Button(pager, text="|< First", command=self.first_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="<< Prev", command=self.prev_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="Next >>", command=self.next_db_page).pack(side="left")
        tk.Button(pager, text="Last >|", command=self.last_db_page).pack(side="left", padx=4)
        self.page_label = tk.Label(pager, text="Page 0 / 0")
        self.page_label.pack(side="left", padx=8)

    def refresh_ui_after_db_update(self, file_id):
        self.load_db_records()

        for item in self.db_tree.get_children():
            tags = self.db_tree.item(item, "tags")
            if tags and str(tags[0]) == str(file_id):
                self.db_tree.selection_set(item)
                self.db_tree.focus(item)
                self.db_tree.see(item)
                break

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

            # Scrape fresh data
            data = scrape_movie(url)

            category = data["category"]
            description = data["description"]
            images = data["images"]

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

            self.status_var.set("Metadata refreshed successfully.")

        except Exception as e:
            messagebox.showerror("Error", str(e))


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
        self.current_db_path = db_path
        self.save_settings({"last_db_path": db_path})
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
            cur.execute("DELETE FROM Files WHERE id=?", (rid,))
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

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            selected_sid = self.selected_storage_filter.get()

            if selected_sid == "ALL":
                cur.execute(DB_SELECT_ALL)
            else:
                cur.execute(DB_SELECT_STORAGE_ID, (selected_sid,))

            rows = cur.fetchall()
            conn.close()

        except Exception as e:
            messagebox.showerror("Error", f"Failed reading DB: {e}")
            return

        # Cache full dataset
        self.db_records_cache = rows
        self.all_filtered_rows = list(rows)

        total = len(self.all_filtered_rows)
        self.total_pages = (total - 1) // self.page_size + 1 if total > 0 else 1
        self.current_page = 0

        # Show first page
        self.show_db_page(0)

        self.status_var.set(f"Loaded {total} rows from {self.current_db_path}")
        self.update_db_statistics()
        self.update_status_bar_db_info()
        self.load_category_dropdown()

        # ✅ refresh Storage ID dropdown
        #self.load_storage_ids_from_db()



    def refresh_db_tree(self, rows):
        self.db_tree.delete(*self.db_tree.get_children())

        start = self.current_page * self.page_size

        for idx, r in enumerate(rows[start:start + self.page_size], start=1 + start):
            id_, fname, ext, sizeb, storage_id, cdate, path, year, category = r

            self.db_tree.insert("", "end", values=(
                idx,                       # 👈 serial number
                fname,
                ext,
                format_size(sizeb),
                storage_id,
                format_date(cdate),
                path,
                year if year else "",
                category if category else ""
            ), tags=(id_,))          # 👈 store real DB id safely

    
    def auto_resize_columns(self, display_rows):
        cols = ("ID", "Name", "Ext", "Size", "Storage", "Date", "Path", "Year", "Category")
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

        rows = list(self.db_records_cache)

        # 🔍 text search filter
        if q:
            rows = [
                r for r in rows
                if any(q in (str(x).lower() if x is not None else "") for x in r)
            ]

        # 🏷 category filter
        if selected_cat and selected_cat != "All":
            if selected_cat == "Uncategorized":
                rows = [r for r in rows if not r[8]]
            else:
                rows = [r for r in rows if r[8] == selected_cat]

        self.all_filtered_rows = rows

        total = len(self.all_filtered_rows)
        self.total_pages = (total - 1) // self.page_size + 1 if total > 0 else 1
        self.current_page = 0
        self.show_db_page(0)


    def show_db_page(self, page_num):
        if not self.all_filtered_rows:
            self.refresh_db_tree([])
            self.page_label.config(text="Page 0 / 0")
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

        self.page_label.config(text=f"Page {self.current_page+1} / {self.total_pages}")


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
            messagebox.showerror("Error", "Invalid page size")

    def sort_db_by_column(self, col):
        map_idx = {
            "No": None,        # 👈 serial number only, not DB data
            "Name": 1,
            "Ext": 2,
            "Size": 3,
            "Storage": 4,
            "Date": 5,
            "Path": 6,
            "Year": 7,
            "Category": 8
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

            elif col == "Date":
                def keyd(x):
                    v = x[idx]
                    if not v:
                        return datetime.datetime.min
                    try:
                        return datetime.datetime.fromisoformat(v)
                    except:
                        try:
                            return datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
                        except:
                            return datetime.datetime.min

                sorted_rows = sorted(self.all_filtered_rows, key=keyd, reverse=not rev)

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
                    cur.execute("DELETE FROM Files WHERE id=?", (record_id,))
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
            df = pd.read_sql_query("SELECT id, file_name, extension, size_bytes, storage_id, creation_date, full_path FROM Files", conn)
            conn.close()
            df.to_excel(path, index=False)
            messagebox.showinfo("Success", f"Exported to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")
