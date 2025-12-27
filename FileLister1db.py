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
from collections import defaultdict
try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

FILES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS Files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    extension TEXT,
    size_bytes INTEGER NOT NULL,
    storage_id TEXT NOT NULL,
    creation_date TEXT,
    full_path TEXT,
    UNIQUE(file_name, size_bytes)
);
"""
DB_SELECT_ALL = """
SELECT
    id,
    file_name,
    extension,
    size_bytes,
    storage_id,
    creation_date,
    full_path
FROM Files
ORDER BY id DESC;
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
            ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts"
        }

        self._font = tkfont.nametofont("TkDefaultFont")

        # File data stores
        self.all_files_info = []
        self.file_paths = {}

        # SQLite viewer state
        self.current_db_path = None
        self.db_records_cache = []
        self.all_filtered_rows = []
        self.current_page_rows = []
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
            # create empty DB structure
            conn = sqlite3.connect(self.master_db_path)
            cur = conn.cursor()
           
            cur.execute(FILES_TABLE_SQL)
            conn.commit()
            conn.close()
            self.ensure_global_unique_index()


            # load at startup
            self.load_db_records()
                
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
        if not self.current_db_path or not self.all_files_info:
            messagebox.showwarning("No Data", "Scan files first.")
            return

        conn = sqlite3.connect(self.current_db_path)
        cur = conn.cursor()
        
        unmatched = []

        for f in self.all_files_info:
            cur.execute("""
                SELECT storage_id FROM Files
                WHERE file_name = ? AND size_bytes = ?
            """, (f["name_without_ext"], f["size"]))

            row = cur.fetchone()

            if row:
                continue  # matched, skip

            else:
                if f["extension"].lower() not in self.allowed_video_exts:
                    reason = "Extension not tracked"
                else:
                    reason = "Not present in database"

            unmatched.append((f, reason))

        conn.close()

        if not unmatched:
            messagebox.showinfo("Result", "No unmatched scanned files found.")
            return

        self._show_unmatched_window(unmatched)

    def _show_unmatched_window(self, files):
        row_file_map = {}

        win = tk.Toplevel(self.root)
        win.title("Unmatched Scanned Files")
        win.geometry("900x400")
        count_lbl = tk.Label(
            win,
            text=f"Unmatched scanned files: {len(files)}",
            font=("Segoe UI", 10, "bold"),
            anchor="w"
        )
        count_lbl.pack(fill="x", padx=10, pady=(8, 4))

        cols = ("Name", "Size","Reason", "Path")
        tree = ttk.Treeview(win, columns=cols, show="headings")

        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=300)

        tree.pack(fill="both", expand=True)

        for f, reason in files:
            iid = tree.insert("", "end", values=(
                f["name_without_ext"],
                self.format_size(f["size"]),
                reason,
                f["full_path"]
            ))
            row_file_map[iid] = f
        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", pady=5)

        tk.Button(
            btn_frame,
            text="FORCE Insert Selected Files",
            fg="white",
            bg="darkred",
            command=lambda: self.force_insert_selected_files(
                tree, row_file_map, win
            )
        ).pack(side="right", padx=10)

    def force_insert_selected_files(self, tree, row_file_map, parent_win):
        selected = tree.selection()

        if not selected:
            messagebox.showwarning(
                "No Selection",
                "Please select one or more files to insert."
            )
            return

        if not messagebox.askyesno(
            "Expert Confirmation",
            "This will INSERT selected files into the database.\n"
            "Duplicates will be skipped.\n\nProceed?"
        ):
            return

        storage_id = self.get_storage_id()

        inserted = 0
        skipped = 0

        try:
            conn = sqlite3.connect(self.current_db_path)
            cur = conn.cursor()
                        
            insert_q = """
                INSERT INTO Files
                (file_name, extension, size_bytes,storage_id, creation_date, full_path)
                VALUES (?, ?, ?, ?, ?, ?)
            """

            for iid in selected:
                f = row_file_map.get(iid)
                if not f:
                    continue

                try:
                    
                    cur.execute("""
                        SELECT 1 FROM Files
                        WHERE file_name=? AND size_bytes=?
                    """, (f["name_without_ext"], f["size"]))

                    if cur.fetchone():
                        skipped += 1
                        continue
                    
                    cur.execute(insert_q, (
                        f["name_without_ext"],
                        f["extension"],
                        f["size"],
                        storage_id,
                        self.format_date(f["creation_date"]),
                        f["full_path"]
                        ))
                    inserted += 1
                except sqlite3.IntegrityError:
                    skipped += 1  # duplicate (name + size)

            conn.commit()
            conn.close()

            messagebox.showinfo(
                "Force Insert Complete",
                f"Inserted: {inserted}\nSkipped (duplicates): {skipped}"
            )

            parent_win.destroy()

            self.update_db_statistics()
            self.update_status_bar_db_info()

        except Exception as e:
            messagebox.showerror(
                "Insert Error",
                f"Failed to insert selected files:\n{e}"
            )

    

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
        if self.include_subdirs.get():
            for root, _, files in os.walk(folder):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in self.allowed_video_exts:
                        continue
                    path = os.path.join(root, f)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    try:
                        c = os.path.getctime(path)
                        cdate = datetime.datetime.fromtimestamp(c)
                    except Exception:
                        cdate = datetime.datetime.now()
                    results.append({
                        "name_without_ext": os.path.splitext(f)[0],
                        "full_path": path,
                        "extension": ext,
                        "size": size,
                        "creation_date": cdate
                    })
        else:
            for f in os.listdir(folder):
                path = os.path.join(folder, f)
                if not os.path.isfile(path):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext not in self.allowed_video_exts:
                    continue
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                try:
                    c = os.path.getctime(path)
                    cdate = datetime.datetime.fromtimestamp(c)
                except Exception:
                    cdate = datetime.datetime.now()
                results.append({
                    "name_without_ext": os.path.splitext(f)[0],
                    "full_path": path,
                    "extension": ext,
                    "size": size,
                    "creation_date": cdate
                })
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
                    id_, fname, ext, sizeb, storage_id, cdate, path_ = r
                    rows.append({
                        "File Name": fname,
                        "Extension": ext,
                        "Size (bytes)": sizeb,
                        "Size": self.format_size(sizeb),
                        "Storage ID": storage_id,
                        "Creation Date": self.format_date(cdate),
                        "Full Path": path_
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
                if f["extension"].lower() not in self.allowed_video_exts:
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

            cur.execute(FILES_TABLE_SQL)
            storage_id = self.get_storage_id()

            insert_q = """
                INSERT INTO Files
                (file_name, extension, size_bytes,storage_id, creation_date, full_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """
          

            for f in self.all_files_info:
                if f["extension"].lower() not in self.allowed_video_exts:
                    continue

                try:
                    cur.execute(insert_q, (
                        f["name_without_ext"],
                        f["extension"],
                        f["size"],
                        storage_id,
                        self.format_date(f["creation_date"]),
                        f["full_path"]
                    ))
                except sqlite3.IntegrityError:
                    # duplicate (same file_name + size)
                    pass


            conn.commit()
            conn.close()

            # Save as last DB
            self.save_settings({"last_db_path": db_path})
            self.current_db_path = db_path
            self.update_db_statistics()
            self.update_status_bar_db_info()

            messagebox.showinfo("Success", "Database updated successfully.")

        except Exception as e:
               messagebox.showerror("Error", f"SQLite export failed: {e}")
        finally: 
            if hasattr(self, "storage_id_entry"):
                self.storage_id_entry.config(state="normal")


                   


    def setup_db_viewer_tab(self, parent):
        top = tk.Frame(parent)
        top.pack(fill="x", pady=6)

        tk.Button(top, text="Open SQLite DB", command=self.open_sqlite_db).pack(side="left", padx=4)
        tk.Label(top, text="Search:").pack(side="left", padx=(8,0))
        self.db_search_var = tk.StringVar()
        tk.Entry(top, textvariable=self.db_search_var, width=40).pack(side="left", padx=4)
        self.db_search_var.trace_add("write", lambda *a: self.filter_db_records())

        tk.Label(top, text="Page size:").pack(side="left", padx=(8,0))
        self.page_size_var = tk.IntVar(value=self.page_size)
        e = tk.Entry(top, textvariable=self.page_size_var, width=6)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.apply_page_size())

        tk.Button(top, text="Export to Excel", command=self.export_db_to_excel).pack(side="right", padx=4)
        tk.Button(top, text="Delete ALL", command=self.delete_all_db_rows).pack(side="right", padx=4)
        tk.Button(top, text="Delete Selected", command=self.delete_selected_db_rows).pack(side="right", padx=4)

        cols = ("ID","File Name","Extension","Size","Storage ID","Creation Date","Full Path")

        frame = tk.Frame(parent)
        frame.pack(fill="both", expand=True)

        self.db_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            self.db_tree.heading(c, text=c, command=lambda _c=c: self.sort_db_by_column(_c))
            self.db_tree.column(c, width=180, anchor="w")

        self.db_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frame, command=self.db_tree.yview)
        scroll.pack(side="right", fill="y")
        self.db_tree.configure(yscrollcommand=scroll.set)

        self.db_tree.bind("<Double-1>", self.on_db_tree_double_click)

        pager = tk.Frame(parent)
        pager.pack(fill="x", pady=4)
        tk.Button(pager, text="|< First", command=self.first_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="<< Prev", command=self.prev_db_page).pack(side="left", padx=4)
        tk.Button(pager, text="Next >>", command=self.next_db_page).pack(side="left")
        tk.Button(pager, text="Last >|", command=self.last_db_page).pack(side="left", padx=4)
        self.page_label = tk.Label(pager, text="Page 0 / 0")
        self.page_label.pack(side="left", padx=8)

    def open_sqlite_db(self):
        db_path = filedialog.askopenfilename(title="Select DB", filetypes=[("SQLite","*.db"),("All files","*.*")])
        if not db_path:
            return
        self.current_db_path = db_path
        self.save_settings({"last_db_path": db_path})
        self.ensure_global_unique_index()
        self.load_db_records()

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

    def refresh_db_tree(self, rows):
        # Clear existing rows
        for i in self.db_tree.get_children():
            self.db_tree.delete(i)

        display = []

        # Compute sequential numbering based on current page
        start = self.current_page * self.page_size if hasattr(self, "current_page") else 0

        for idx, r in enumerate(rows):
            # Defensive safety (schema lock)
            if len(r) != 7:
                continue  # or raise AssertionError

            id_, fname, ext, sizeb, storage_id, cdate, path = r
            seq_no = start + idx + 1

            display.append((
                seq_no,
                fname,
                ext,
                self.format_size(sizeb),
                storage_id,
                self.format_date(cdate),
                path
            ))

        # Insert into Treeview
        for d in display:
            self.db_tree.insert("", "end", values=d)

        # Resize columns
        self.auto_resize_columns(display)

    
    def auto_resize_columns(self, display_rows):
        cols = ("ID","File Name","Extension","Size","Storage ID","Creation Date","Full Path")
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
        if not q:
            self.all_filtered_rows = list(self.db_records_cache)
        else:
            self.all_filtered_rows = [r for r in self.db_records_cache if any(q in (str(x).lower() if x is not None else "") for x in r)]
        total = len(self.all_filtered_rows)
        self.total_pages = (total-1)//self.page_size + 1 if total > 0 else 1
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
        start = page_num * self.page_size
        end = start + self.page_size
        self.current_page_rows = self.all_filtered_rows[start:end]
        self.refresh_db_tree(self.current_page_rows)
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
        map_idx = {"ID":0,"File Name":1,"Extension":2,"Size":3,"Creation Date":4,"Full Path":5}
        idx = map_idx.get(col, 0)
        rev = self._db_sort_reverse.get(col, False)
        try:
            if col in ("ID","Size"):
                sorted_rows = sorted(self.all_filtered_rows, key=lambda x: (x[idx] if x[idx] is not None else 0), reverse=not rev)
            elif col == "Creation Date":
                def keyd(x):
                    v = x[idx]
                    if isinstance(v, str):
                        try:
                            return datetime.datetime.fromisoformat(v)
                        except:
                            try:
                                return datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
                            except:
                                return str(v).lower()
                    if isinstance(v, (int,float)):
                        try:
                            return datetime.datetime.fromtimestamp(v)
                        except:
                            return v
                    return v
                sorted_rows = sorted(self.all_filtered_rows, key=keyd, reverse=not rev)
            else:
                sorted_rows = sorted(self.all_filtered_rows, key=lambda x: (str(x[idx]).lower() if x[idx] is not None else ""), reverse=not rev)
        except Exception:
            sorted_rows = sorted(self.all_filtered_rows, key=lambda x: str(x[idx]).lower(), reverse=not rev)
        self._db_sort_reverse[col] = not rev
        self.all_filtered_rows = sorted_rows
        total = len(self.all_filtered_rows)
        self.total_pages = (total-1)//self.page_size + 1 if total > 0 else 1
        self.current_page = 0
        self.show_db_page(0)

    def on_db_tree_double_click(self, event):
        item = self.db_tree.identify_row(event.y)
        if not item:
            return
        vals = self.db_tree.item(item, "values")
        if not vals:
            return
        path = vals[5] if len(vals) > 5 else None
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

            
            for i in sel:
                # Map the selected tree item to the underlying DB id using current_page_rows
                try:
                    idx = self.db_tree.index(i)
                    rid = int(self.current_page_rows[idx][0])
                except Exception:
                    # Fallback: try reading displayed value (older behavior)
                    try:
                        rid = int(self.db_tree.item(i)["values"][0])
                    except Exception:
                        continue
                cur.execute("DELETE FROM Files WHERE id=?", (rid,))
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
    app = FileListerApp(root)
    root.mainloop()
