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

import os
import json
import sqlite3
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font as tkfont
import pandas as pd
from pathlib import Path
import datetime
import re
from collections import defaultdict
try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

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

    def on_ok(self):
        self.result = self.var.get()
        self.top.destroy()

    def on_cancel(self):
        self.result = None
        self.top.destroy()

FILES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS Files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,

    storage_id TEXT NOT NULL,
    full_path TEXT NOT NULL,
    creation_date TEXT,

    year INTEGER,
    category TEXT,

    added_on TEXT DEFAULT CURRENT_TIMESTAMP,
    file_hash TEXT,

    UNIQUE(file_name, size_bytes)
);
"""
FILES_TABLE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_file_global
ON Files (file_name, size_bytes);
"""
CATEGORIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS Categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
"""


DB_SELECT_ALL = """
SELECT id, file_name, extension, size_bytes, storage_id,
       creation_date, full_path, year, category
FROM Files
ORDER BY id DESC
"""

class FileListerApp:
    CONFIG_FILE = "app_settings.json"


    def __init__(self, root):
        self.master_db_path = "VideoFiles.db"
        self.current_db_path = self.master_db_path

        self.root = root
        self.root.title("Video File Lister")
        self.root.geometry("1280x820")

        # Allowed video types
        self.allowed_video_exts = {
            ".mp4", ".mkv", ".avi", ".mov", ".mpg", ".mpeg",
            ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts", ".divx"
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
        self.current_db_path = None
        self.db_records_cache = []
        self.all_filtered_rows = []
        #self.current_page_rows = []
        self.page_size = 50
        self.current_page = 0
        self.total_pages = 0
        self._db_sort_reverse = {}
        self.storage_id_var = tk.StringVar(value="UNKNOWN")


        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)


        # Always use the one master DB
        self.current_db_path = self.master_db_path

        # Auto-create + load
        if not os.path.exists(self.master_db_path):
            self.init_db(fresh=True)
            self.load_db_records()

                
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
                json.dump(settings, f)
        except:
            pass

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


    def setup_main_tab(self, parent):
        folder_frame = tk.Frame(parent)
        folder_frame.pack(fill="x", pady=5)

        tk.Label(folder_frame, text="Folder: ").pack(side="left")
        self.folder_path = tk.StringVar()
        tk.Entry(folder_frame, textvariable=self.folder_path, width=60).pack(side="left", padx=5)

        tk.Button(folder_frame, text="Browse", command=self.browse_folder).pack(side="left")

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

        # LEFT: listbox
        left = tk.Frame(split)
        left.pack(side="left", fill="both", expand=True)

        self.files_count_var = tk.StringVar(value="Files: 0")
        tk.Label(left, textvariable=self.files_count_var).pack(anchor="w")

        lb_frame = tk.Frame(left)
        lb_frame.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(lb_frame)
        scrollbar.pack(side="right", fill="y")

        self.file_listbox = tk.Listbox(lb_frame, yscrollcommand=scrollbar.set)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.file_listbox.yview)

        self.file_listbox.bind("<<ListboxSelect>>", self.on_file_select)
        self.file_listbox.bind("<Double-Button-1>", self.on_file_list_double_click)

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

        bottom = tk.Frame(parent)
        bottom.pack(fill="x", pady=10)

        storage_frame = tk.Frame(parent)
        storage_frame.pack(fill="x", padx=5, pady=3)

        tk.Label(storage_frame, text="Storage ID:").pack(side="left")

        self.storage_id_entry = tk.Entry(
            storage_frame,
            textvariable=self.storage_id_var,
            width=25
        )
        self.storage_id_entry.pack(side="left", padx=5)


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

        scanned_files = self.get_files_info(self.folder_path.get())

        if not scanned_files:
            messagebox.showinfo("Info", "No video files found in selected path.")
            return

        try:
            conn = sqlite3.connect(self.current_db_path)
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
                    self.format_size(f["size"]),
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
                conn = sqlite3.connect(self.current_db_path)
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
                            self.format_date(f["creation_date"]),
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
                            self.format_date(f["creation_date"]),
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
            conn = sqlite3.connect(self.current_db_path)
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
                creation_date = self.format_date(f["creation_date"])
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
            conn = sqlite3.connect(self.current_db_path)
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
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO Categories(name) VALUES (?)", (name.strip(),))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    

    def setup_stats_tab(self, parent):
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
        
        tk.Button(parent, text="Export Statistics to Excel",
          command=self.export_db_statistics_to_excel).pack(anchor="w", padx=6, pady=4)


        chart_frame = tk.LabelFrame(parent, text="Extension Distribution (DB)")
        chart_frame.pack(fill="both", expand=True, padx=6, pady=6)

        self.chart_canvas = None
        tk.Button(chart_frame, text="Refresh Pie Chart",
          command=self.draw_extension_pie_chart).pack(anchor="w", padx=4, pady=4)


        self.chart_container = tk.Frame(chart_frame)
        self.chart_container.pack(fill="both", expand=True)

    def format_db_total_size(self, size_bytes):
        try:
            size = int(size_bytes or 0)
        except:
            return "0 MB"

        if size >= 1024**4:  # 1024 GB = 1 TB
            return f"{size / (1024**4):.2f} TB"
        else:
            return f"{size / (1024**2):.2f} MB"

    def setup_duplicates_tab(self, parent):
        top = tk.Frame(parent)
        top.pack(fill="x", padx=5, pady=5)

        tk.Button(top, text="Scan Duplicates",
                  command=self.load_duplicate_records).pack(side="left", padx=4)

        tk.Button(top, text="Delete Selected Duplicate",
              command=self.delete_selected_duplicate).pack(side="left", padx=4)

        cols = ("ID", "File Name", "Size", "Storage ID", "Full Path")
        self.dup_tree = ttk.Treeview(parent, columns=cols, show="headings")

        for c in cols:
            self.dup_tree.heading(c, text=c)
            self.dup_tree.column(c, width=220)

        self.dup_tree.pack(fill="both", expand=True, padx=5, pady=5)

    def load_duplicate_records(self):
        if not self.current_db_path:
            return

        for i in self.dup_tree.get_children():
                self.dup_tree.delete(i)
        try:
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()

            
            cur.execute("""
                SELECT id, file_name, size_bytes, storage_id, full_path
                FROM Files
                WHERE (file_name, size_bytes) IN (
                    SELECT file_name, size_bytes
                    FROM Files
                    GROUP BY file_name, size_bytes
                    HAVING COUNT(*) > 1
                    )
                ORDER BY file_name, size_bytes
                """)

            rows = cur.fetchall()
            conn.close()

            for rid, name, size,storage,path in rows:
                self.dup_tree.insert(
                    "", "end",
                    values=(rid, name, self.format_size(size), storage, path)
                )
            self.status_var.set(f"Duplicate records found: {len(rows)}")

        except Exception as e:
            self.status_var.set(f"Duplicate scan error: {e}")

    def ensure_global_unique_index(self):
        if not self.current_db_path:
            return

        try:
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()

            # Create unique index safely
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_file_global
                ON Files (file_name, size_bytes);
            """)

            conn.commit()
            conn.close()

        except Exception as e:
            messagebox.showerror(
                "Uniqueness Error",
                f"Failed to ensure uniqueness:\n{e}"
            )

    def init_db(self, fresh=False):
        """
        Initialize database.
        fresh=True → drops Files and Categories tables and recreates them (CLEAN RESET).
        """

        conn = sqlite3.connect(self.master_db_path)
        cur = conn.cursor()

        if fresh:
            cur.execute("DROP TABLE IF EXISTS Files")
            cur.execute("DROP TABLE IF EXISTS Categories")

        cur.execute(FILES_TABLE_SQL)
        cur.execute(FILES_TABLE_INDEX)
        cur.execute(CATEGORIES_TABLE_SQL)

        conn.commit()
        conn.close()


    def delete_selected_duplicate(self):
        sel = self.dup_tree.selection()
        if not sel:
            return

        if not messagebox.askyesno(
            "Confirm",
            "Delete selected duplicate record(s)?\n(This does NOT delete the file)"
            ):
            return

        try:
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()
            
            for item in sel:
                record_id = self.dup_tree.item(item, "values")[0]
                cur.execute("DELETE FROM Files WHERE id = ?", (record_id,))
                self.dup_tree.delete(item)

            conn.commit()
            conn.close()

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
            conn = sqlite3.connect(self.current_db_path)

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
            conn = sqlite3.connect(self.current_db_path)
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

        if not self.current_db_path or not os.path.exists(self.current_db_path):
            self.db_total_records_var.set("DB Records: 0")
            self.db_size_var.set("DB Size: 0 MB")
            self.db_files_size_var.set("Total Files Size: 0 MB")
            return

        try:
            # DB size
            size_mb = os.path.getsize(self.current_db_path) / (1024 * 1024)
            self.db_size_var.set(f"DB Size: {size_mb:.2f} MB")

            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()
            

            # Total records
            cur.execute("SELECT COUNT(*) FROM Files")
            total = cur.fetchone()[0]
            self.db_total_records_var.set(f"DB Records: {total}")

            # Total size of ALL files in DB
            cur.execute("SELECT IFNULL(SUM(size_bytes),0) FROM Files")
            total_bytes = cur.fetchone()[0]

            formatted = self.format_db_total_size(total_bytes)
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
                    values=(ext, cnt, self.format_size(size))
                    )

        except Exception as e:
            self.db_total_records_var.set("DB Records: Error")
            self.db_size_var.set("DB Size: Error")
            self.status_var.set(f"DB stats error: {e}")


    def update_status_bar_db_info(self):
        if not self.current_db_path or not os.path.exists(self.current_db_path):
            return
        try:
            conn = sqlite3.connect(self.current_db_path)
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
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path.set(folder)
            self.status_var.set(f"Selected: {folder}")

    def list_files(self):
        folder = self.folder_path.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return

        # reset
        self.file_listbox.delete(0, tk.END)
        self.file_paths.clear()
        self.all_files_info.clear()

        # get files (video-only)
        self.all_files_info = self.get_files_info(folder)

        # sort by name
        self.all_files_info.sort(key=lambda x: x["name_without_ext"].lower())

        # populate listbox; disambiguate duplicate display names
        for info in self.all_files_info:
            display = info["name_without_ext"]
            if display in self.file_paths:
                base = display
                n = 2
                while f"{base} ({n})" in self.file_paths:
                    n += 1
                display = f"{base} ({n})"
            self.file_listbox.insert(tk.END, display)
            self.file_paths[display] = info["full_path"]

        total = len(self.all_files_info)
        self.files_count_var.set(f"Files: {total}")
        self.status_var.set(f"Found {total} video files")

        self.update_statistics()

        # clear details
        for v in self.detail_vars.values():
            v.set("")

    def get_files_info(self, folder):
        results = []

        def process_file(path, f):
            try:
                size = os.path.getsize(path)
                cdate = datetime.datetime.fromtimestamp(
                    os.path.getctime(path)
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return

            name_without_ext = os.path.splitext(f)[0]
            ext = os.path.splitext(f)[1].lower()

            # extract year from filename (if present)
            year = None
            try:
                matches = re.findall(r'(19\d{2}|20\d{2})', f)
                if matches:
                    y = int(matches[0])
                    if 1900 <= y <= 2099:
                        year = y
            except Exception:
                year = None

            results.append({
                "name_without_ext": name_without_ext,
                "full_path": path,
                "extension": ext,
                "size": size,
                "creation_date": cdate,
                "year": year,          # NEW
                "category": None,      # NEW (user editable later)
                "tracked": True
            })

        # ---- include subfolders ----
        if self.include_subdirs.get():
            for root, _, files in os.walk(folder):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()

                    # only allowed video files
                    if ext not in self.allowed_video_exts:
                        continue

                    path = os.path.join(root, f)
                    process_file(path, f)

        # ---- only selected folder ----
        else:
            try:
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if not os.path.isfile(path):
                        continue

                    ext = os.path.splitext(f)[1].lower()

                    # only allowed video files
                    if ext not in self.allowed_video_exts:
                        continue

                    process_file(path, f)

            except Exception:
                pass

        return results
    
    
    def update_statistics(self):
        for item in self.ext_tree.get_children():
            self.ext_tree.delete(item)
        if not self.all_files_info:
            self.total_files_var.set("Total Files: 0")
            self.total_size_var.set("Total Size: 0 bytes")
            return
        total_files = len(self.all_files_info)
        total_size = sum(x["size"] for x in self.all_files_info)
        self.total_files_var.set(f"Total Files: {total_files}")
        self.total_size_var.set(f"Total Size: {self.format_size(total_size)}")

        exts = defaultdict(lambda: {"count": 0, "size": 0})
        for x in self.all_files_info:
            exts[x["extension"]]["count"] += 1
            exts[x["extension"]]["size"] += x["size"]
        for ext, stats in sorted(exts.items()):
            self.ext_tree.insert("", "end", values=(ext, stats["count"], self.format_size(stats["size"])))

    def format_size(self, size_bytes):
        try:
            size = int(size_bytes or 0)
        except:
            return str(size_bytes)
        if size < 1024:
            return f"{size} bytes"
        if size < 1024**2:
            return f"{size/1024:.2f} KB"
        if size < 1024**3:
            return f"{size/(1024**2):.2f} MB"
        return f"{size/(1024**3):.2f} GB"

    def format_date(self, d):
        if d is None:
            return ""
        if isinstance(d, str):
            return d
        if isinstance(d, datetime.datetime):
            return d.strftime("%Y-%m-%d %H:%M:%S")
        try:
            return datetime.datetime.fromtimestamp(float(d)).strftime("%Y-%m-%d %H:%M:%S")
        except:
            return str(d)

    def on_file_select(self, event):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        name = self.file_listbox.get(sel[0])
        path = self.file_paths.get(name)
        if not path:
            return
        try:
            self.detail_vars["File Name"].set(os.path.basename(path).rsplit(".", 1)[0])
            self.detail_vars["Extension"].set(os.path.splitext(path)[1])
            self.detail_vars["Size"].set(self.format_size(os.path.getsize(path)))
            self.detail_vars["Creation Date"].set(self.format_date(datetime.datetime.fromtimestamp(os.path.getctime(path))))
            self.status_var.set(f"Selected: {os.path.basename(path)}")
        except Exception as e:
            self.status_var.set(f"Error reading file: {e}")
    def on_file_list_double_click(self, event):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        name = self.file_listbox.get(sel[0])
        path = self.file_paths.get(name)
        if path and os.path.exists(path):
            self.open_file(path)
        else:
            messagebox.showerror("Error", "File not found on disk.")
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
                        "Size": self.format_size(sizeb),
                        "Storage ID": storage_id,
                        "Creation Date": self.format_date(cdate),
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
                        "Total Size": self.format_size(v["size"])
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
            conn = sqlite3.connect(self.current_db_path)
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
        if hasattr(self, "storage_id_entry"):
            self.storage_id_entry.config(state="disabled")

        if not self.all_files_info:
            messagebox.showinfo("Info", "No files to export.")
            return

        db_path = self.master_db_path  # ALWAYS USE ONE DB

        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            # Ensure table & indexes exist (updated schema)
            cur.execute(FILES_TABLE_SQL)
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
                creation_date = self.format_date(f["creation_date"])
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

            self.save_settings({"last_db_path": db_path})
            self.current_db_path = db_path
            self.update_db_statistics()
            self.update_status_bar_db_info()

            messagebox.showinfo(
                "Export complete",
                f"New movies added: {new_count}\n"
                f"Moved movies updated: {moved_count}\n"
                f"Duplicate waste detected: {waste_duplicates}"
            )

        except Exception as e:
            messagebox.showerror("Error", f"SQLite export failed: {e}")

        finally:
            if hasattr(self, "storage_id_entry"):
                self.storage_id_entry.config(state="normal")

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
                conn = sqlite3.connect(self.current_db_path)
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
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT category FROM Files ORDER BY category")
            cats = [r[0] for r in cur.fetchall() if r[0]]
            conn.close()
        except:
            cats = []

        values = ["All"] + cats + ["Uncategorized"]
        self.db_category_combo["values"] = values
        self.db_category_var.set("All")


    def setup_db_viewer_tab(self, parent):
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
        tk.Button(pager, text="|< First", command=self.first_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="<< Prev", command=self.prev_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="Next >>", command=self.next_db_page).pack(side="left")
        tk.Button(pager, text="Last >|", command=self.last_db_page).pack(side="left", padx=4)
        self.page_label = tk.Label(pager, text="Page 0 / 0")
        self.page_label.pack(side="left", padx=8)


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
            self.init_db(fresh=True)
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
        self.ensure_global_unique_index()
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


        # ---- Load DB rows for selected storage id ----
        conn = sqlite3.connect(self.current_db_path)
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
            key = (name.lower(), int(sizeb))

            if key not in disk_index:
                problems.append((rid, name, sizeb, old_path, "Missing on disk"))

        # ---------- Disk -> DB check ----------
        db_index = set(
            (name.lower(), int(sizeb))
            for _, name, ext, sizeb, _ in rows
        )

        for (base, size), paths in disk_index.items():
            if (base, size) not in db_index:
                for p in paths:
                    problems.append((
                        "—",
                        base,
                        size,
                        p,
                        "Exists on disk but missing in DB"
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

                    conn = sqlite3.connect(self.current_db_path)
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
                    conn = sqlite3.connect(self.current_db_path)
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

        conn = sqlite3.connect(self.current_db_path)
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

        conn = sqlite3.connect(self.current_db_path)
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
            conn = sqlite3.connect(self.current_db_path)
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

        conn = sqlite3.connect(self.current_db_path)
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

        conn = sqlite3.connect(self.current_db_path)
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
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()

            # Load ALL records (no pagination here)
            cur.execute(DB_SELECT_ALL)
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


    def refresh_db_tree(self, rows):
        self.db_tree.delete(*self.db_tree.get_children())

        start = self.current_page * self.page_size

        for idx, r in enumerate(rows[start:start + self.page_size], start=1 + start):
            id_, fname, ext, sizeb, storage_id, cdate, path, year, category = r

            self.db_tree.insert("", "end", values=(
                idx,                       # 👈 serial number
                fname,
                ext,
                self.format_size(sizeb),
                storage_id,
                self.format_date(cdate),
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
            conn = sqlite3.connect(self.current_db_path)
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
            conn = sqlite3.connect(self.current_db_path)
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
            conn = sqlite3.connect(self.current_db_path)
            df = pd.read_sql_query("SELECT id, file_name, extension, size_bytes, storage_id, creation_date, full_path FROM Files", conn)
            conn.close()
            df.to_excel(path, index=False)
            messagebox.showinfo("Success", f"Exported to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")
if __name__ == "__main__":
    root = tk.Tk()
    root.title("File Lister Database Manager")
    # ✅ Set proper default size so buttons & columns are visible
    root.geometry("1700x820")

    # ✅ Prevent UI from collapsing too small
    root.minsize(1500, 700)

    app = FileListerApp(root)
    root.mainloop()
