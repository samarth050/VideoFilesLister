"""Database statistics dashboard for FileLister Portable.

This module is deliberately isolated from the rest of the application.  It
uses the application's existing database connection and formatting helpers,
but owns all Statistics-tab presentation.  No FileLister database schema or
record-management behaviour is changed here.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Any


class StatisticsDashboard:
    """Responsive database dashboard embedded in the existing Statistics tab."""

    def __init__(self, host: Any, parent: tk.Misc):
        self.host = host
        self.parent = parent
        self.colors = host.colors
        self._after_id = None
        # Data containers are initialized before widgets so early Tk resize
        # events cannot race the first dashboard draw.
        self._extension_rows = []
        self._storage_rows = []
        self._category_rows = []
        self._metadata = {}

        self._build_styles()
        self._build_ui()

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_bytes(value: int | float | None) -> str:
        value = float(value or 0)
        units = ("B", "KB", "MB", "GB", "TB", "PB")
        idx = 0
        while abs(value) >= 1024 and idx < len(units) - 1:
            value /= 1024.0
            idx += 1
        if idx == 0:
            return f"{int(value):,} {units[idx]}"
        if value >= 100:
            return f"{value:,.0f} {units[idx]}"
        if value >= 10:
            return f"{value:,.1f} {units[idx]}"
        return f"{value:,.2f} {units[idx]}"

    @staticmethod
    def _pct(part: int | float, total: int | float) -> float:
        return (float(part) * 100.0 / float(total)) if total else 0.0

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_styles(self):
        style = ttk.Style(self.host.root)
        c = self.colors
        style.configure(
            "Stats.TFrame", background=c["background"]
        )
        style.configure(
            "StatsCard.TFrame", background=c["surface"], relief="solid",
            borderwidth=1
        )
        style.configure(
            "StatsTitle.TLabel", background=c["surface"],
            foreground=c["text_dark"], font=("Segoe UI", 10, "bold")
        )
        style.configure(
            "StatsValue.TLabel", background=c["surface"],
            foreground=c["blue_dark"], font=("Segoe UI", 19, "bold")
        )
        style.configure(
            "StatsCaption.TLabel", background=c["surface"],
            foreground="#607D94", font=("Segoe UI", 8, "bold")
        )
        style.configure(
            "StatsMuted.TLabel", background=c["surface"],
            foreground="#6D8598", font=("Segoe UI", 8)
        )
        style.configure(
            "StatsSection.TLabelframe", background=c["surface"],
            bordercolor=c["border"], relief="solid", borderwidth=1
        )
        style.configure(
            "StatsSection.TLabelframe.Label", background=c["surface"],
            foreground=c["text_dark"], font=("Segoe UI", 10, "bold")
        )

    def _build_ui(self):
        c = self.colors

        # Fixed toolbar; dashboard content can scroll vertically on smaller
        # displays while retaining all controls.
        toolbar = tk.Frame(self.parent, bg=c["background"], height=44)
        toolbar.pack(fill="x", padx=10, pady=(8, 2))
        toolbar.pack_propagate(False)

        tk.Label(
            toolbar, text="Database Statistics", bg=c["background"],
            fg=c["text_dark"], font=("Segoe UI", 15, "bold")
        ).pack(side="left")
        self.subtitle_var = tk.StringVar(value="VideoFiles.db")
        tk.Label(
            toolbar, textvariable=self.subtitle_var, bg=c["background"],
            fg="#64819A", font=("Segoe UI", 9)
        ).pack(side="left", padx=(10, 0), pady=(5, 0))

        ttk.Button(
            toolbar, text="Export to Excel",
            command=self.host.export_db_statistics_to_excel
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            toolbar, text="Refresh", command=self.refresh
        ).pack(side="right")

        # Scrollable dashboard body.
        body = tk.Frame(self.parent, bg=c["background"])
        body.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        self.canvas = tk.Canvas(
            body, background=c["background"], highlightthickness=0, bd=0
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content = tk.Frame(self.canvas, bg=c["background"])
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse-wheel scrolling only while the pointer is over the dashboard.
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        # KPI cards
        self.kpi_vars: dict[str, tk.StringVar] = {}
        self.kpi_caption_vars: dict[str, tk.StringVar] = {}
        kpi_frame = tk.Frame(self.content, bg=c["background"])
        kpi_frame.pack(fill="x", pady=(0, 8))
        for index, (key, title) in enumerate([
            ("records", "DATABASE RECORDS"),
            ("filesize", "TOTAL FILE SIZE"),
            ("dbsize", "DATABASE SIZE"),
            ("extensions", "FILE EXTENSIONS"),
            ("storage", "STORAGE LOCATIONS"),
        ]):
            kpi_frame.columnconfigure(index, weight=1, uniform="kpi")
            card = tk.Frame(
                kpi_frame, bg=c["surface"], highlightbackground=c["border"],
                highlightthickness=1
            )
            card.grid(row=0, column=index, sticky="nsew", padx=3)
            value_var = tk.StringVar(value="0")
            caption_var = tk.StringVar(value=title)
            self.kpi_vars[key] = value_var
            self.kpi_caption_vars[key] = caption_var
            tk.Label(
                card, textvariable=value_var, bg=c["surface"],
                fg=c["blue_dark"], font=("Segoe UI", 18, "bold")
            ).pack(anchor="w", padx=12, pady=(8, 0))
            tk.Label(
                card, textvariable=caption_var, bg=c["surface"],
                fg="#607D94", font=("Segoe UI", 8, "bold")
            ).pack(anchor="w", padx=12, pady=(0, 8))

        # Main visual row.
        visual = tk.Frame(self.content, bg=c["background"])
        visual.pack(fill="x", pady=(0, 8))
        visual.columnconfigure(0, weight=1, uniform="visual")
        visual.columnconfigure(1, weight=1, uniform="visual")

        ext_box = ttk.LabelFrame(
            visual, text="Files by Extension", style="StatsSection.TLabelframe",
            padding=(8, 7)
        )
        ext_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.ext_canvas = tk.Canvas(
            ext_box, height=155, bg=c["surface"], highlightthickness=0
        )
        self.ext_canvas.pack(fill="both", expand=True)
        self.ext_canvas.bind("<Configure>", lambda e: self._draw_extension_chart())

        storage_box = ttk.LabelFrame(
            visual, text="Storage Distribution", style="StatsSection.TLabelframe",
            padding=(8, 7)
        )
        storage_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.storage_canvas = tk.Canvas(
            storage_box, height=155, bg=c["surface"], highlightthickness=0
        )
        self.storage_canvas.pack(fill="both", expand=True)
        self.storage_canvas.bind("<Configure>", lambda e: self._draw_storage_chart())

        # Extension details table.
        detail_box = ttk.LabelFrame(
            self.content, text="Extension Details", style="StatsSection.TLabelframe",
            padding=(6, 6)
        )
        detail_box.pack(fill="x", pady=(0, 8))

        detail_columns = ("extension", "files", "size", "file_pct", "size_pct")
        self.extension_tree = ttk.Treeview(
            detail_box, columns=detail_columns, show="headings", height=5
        )
        headings = {
            "extension": "Extension", "files": "Files", "size": "Total Size",
            "file_pct": "% Files", "size_pct": "% Storage"
        }
        widths = {"extension": 120, "files": 110, "size": 170,
                  "file_pct": 110, "size_pct": 110}
        for col in detail_columns:
            self.extension_tree.heading(col, text=headings[col])
            self.extension_tree.column(
                col, width=widths[col], anchor="e" if col != "extension" else "w"
            )
        ext_scroll = ttk.Scrollbar(detail_box, orient="vertical", command=self.extension_tree.yview)
        self.extension_tree.configure(yscrollcommand=ext_scroll.set)
        self.extension_tree.pack(side="left", fill="x", expand=True)
        ext_scroll.pack(side="right", fill="y")

        # Lower information row.
        lower = tk.Frame(self.content, bg=c["background"])
        lower.pack(fill="x", pady=(0, 8))
        lower.columnconfigure(0, weight=1, uniform="lower")
        lower.columnconfigure(1, weight=1, uniform="lower")

        meta_box = ttk.LabelFrame(
            lower, text="Metadata Coverage", style="StatsSection.TLabelframe",
            padding=(10, 8)
        )
        meta_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.meta_canvas = tk.Canvas(
            meta_box, height=150, bg=c["surface"], highlightthickness=0
        )
        self.meta_canvas.pack(fill="both", expand=True)

        category_box = ttk.LabelFrame(
            lower, text="Category Distribution", style="StatsSection.TLabelframe",
            padding=(10, 8)
        )
        category_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.category_canvas = tk.Canvas(
            category_box, height=150, bg=c["surface"], highlightthickness=0
        )
        self.category_canvas.pack(fill="both", expand=True)
        self.category_canvas.bind("<Configure>", lambda e: self._draw_category_chart())

        # Full category table remains available beneath the visual summary.
        cat_table_box = ttk.LabelFrame(
            self.content, text="All Categories", style="StatsSection.TLabelframe",
            padding=(6, 6)
        )
        cat_table_box.pack(fill="x", pady=(0, 8))
        self.category_tree = ttk.Treeview(
            cat_table_box, columns=("category", "movies", "pct"),
            show="headings", height=5
        )
        self.category_tree.heading("category", text="Category")
        self.category_tree.heading("movies", text="Movie Count")
        self.category_tree.heading("pct", text="% of Records")
        self.category_tree.column("category", width=360, anchor="w")
        self.category_tree.column("movies", width=130, anchor="e")
        self.category_tree.column("pct", width=130, anchor="e")
        cat_scroll = ttk.Scrollbar(cat_table_box, orient="vertical", command=self.category_tree.yview)
        self.category_tree.configure(yscrollcommand=cat_scroll.set)
        self.category_tree.pack(side="left", fill="x", expand=True)
        cat_scroll.pack(side="right", fill="y")

        # Compatibility attributes used by the existing application code.
        self.db_ext_tree = self.extension_tree
        self.db_storage_tree = self._make_compat_storage_tree()
        self.db_total_records_var = tk.StringVar(value="DB Records: 0")
        self.db_files_size_var = tk.StringVar(value="Total Files Size: 0 MB")
        self.db_size_var = tk.StringVar(value="DB Size: 0 MB")
        self.chart_canvas = None
        self.chart_container = None

        self.parent.after_idle(self.refresh)

    def _make_compat_storage_tree(self):
        # Hidden compatibility Treeview. Existing FileLister methods and
        # integrations can still address db_storage_tree without displaying a
        # second legacy table that would consume dashboard space.
        tree = ttk.Treeview(
            self.parent, columns=("Storage", "Files", "Total Size"), show="headings", height=1
        )
        return tree

    def _on_content_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._mousewheel)

    def _unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _mousewheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _query(self):
        if not self.host.current_db_path or not os.path.exists(self.host.current_db_path):
            return {
                "records": 0, "total_size": 0, "db_size": 0,
                "extensions": [], "storage": [], "categories": [],
                "metadata": {
                    "complete": 0, "incomplete": 0, "no_metadata": 0,
                    "missing_category": 0, "missing_description": 0,
                    "missing_cover1": 0, "missing_cover2": 0,
                }
            }

        conn = self.host.get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM Files")
            records, total_size = cur.fetchone()

            cur.execute("""
                SELECT extension, COUNT(*), COALESCE(SUM(size_bytes), 0)
                FROM Files
                GROUP BY extension
                ORDER BY COUNT(*) DESC, extension COLLATE NOCASE
            """)
            extensions = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(storage_id, 'UNKNOWN'), COUNT(*),
                       COALESCE(SUM(size_bytes), 0)
                FROM Files
                GROUP BY COALESCE(storage_id, 'UNKNOWN')
                ORDER BY SUM(size_bytes) DESC, storage_id COLLATE NOCASE
            """)
            storage = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(NULLIF(TRIM(m.category), ''),
                                NULLIF(TRIM(f.category), ''),
                                'Uncategorized') AS category,
                       COUNT(*)
                FROM Files f
                LEFT JOIN MovieDetails m ON f.id = m.file_id
                GROUP BY 1
                ORDER BY COUNT(*) DESC, 1 COLLATE NOCASE
            """)
            categories = cur.fetchall()

            # Use the same definition of metadata completeness already used by
            # FileLister's MetadataStatusView, without requiring that the view
            # has been created by an older database.
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN m.file_id IS NULL THEN 1 ELSE 0 END) AS no_metadata,
                    SUM(CASE WHEN m.file_id IS NOT NULL AND
                        (COALESCE(NULLIF(TRIM(m.category), ''), NULLIF(TRIM(f.category), '')) IS NULL
                         OR NULLIF(TRIM(m.description), '') IS NULL
                         OR NULLIF(TRIM(m.cover1_path), '') IS NULL
                         OR NULLIF(TRIM(m.cover2_path), '') IS NULL)
                        THEN 1 ELSE 0 END) AS incomplete,
                    SUM(CASE WHEN m.file_id IS NOT NULL AND
                        COALESCE(NULLIF(TRIM(m.category), ''), NULLIF(TRIM(f.category), '')) IS NOT NULL
                        AND NULLIF(TRIM(m.description), '') IS NOT NULL
                        AND NULLIF(TRIM(m.cover1_path), '') IS NOT NULL
                        AND NULLIF(TRIM(m.cover2_path), '') IS NOT NULL
                        THEN 1 ELSE 0 END) AS complete,
                    SUM(CASE WHEN COALESCE(NULLIF(TRIM(m.category), ''), NULLIF(TRIM(f.category), '')) IS NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN NULLIF(TRIM(m.description), '') IS NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN NULLIF(TRIM(m.cover1_path), '') IS NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN NULLIF(TRIM(m.cover2_path), '') IS NULL THEN 1 ELSE 0 END)
                FROM Files f
                LEFT JOIN MovieDetails m ON f.id = m.file_id
            """)
            meta = cur.fetchone()
        finally:
            conn.close()

        meta = meta or (0,) * 8
        db_size = os.path.getsize(self.host.current_db_path) if os.path.exists(self.host.current_db_path) else 0
        return {
            "records": int(records or 0),
            "total_size": int(total_size or 0),
            "db_size": int(db_size),
            "extensions": extensions,
            "storage": storage,
            "categories": categories,
            "metadata": {
                "complete": int(meta[3] or 0),
                "incomplete": int(meta[2] or 0),
                "no_metadata": int(meta[1] or 0),
                "missing_category": int(meta[4] or 0),
                "missing_description": int(meta[5] or 0),
                "missing_cover1": int(meta[6] or 0),
                "missing_cover2": int(meta[7] or 0),
            }
        }

    def refresh(self):
        """Reload all dashboard data and redraw the dashboard."""
        try:
            data = self._query()
        except Exception as exc:
            self.host.status_var.set(f"DB statistics error: {exc}")
            return

        self._extension_rows = list(data["extensions"])
        self._storage_rows = list(data["storage"])
        self._category_rows = list(data["categories"])
        self._metadata = data["metadata"]

        records = data["records"]
        self.kpi_vars["records"].set(f"{records:,}")
        self.kpi_vars["filesize"].set(self._format_bytes(data["total_size"]))
        self.kpi_vars["dbsize"].set(self._format_bytes(data["db_size"]))
        self.kpi_vars["extensions"].set(f"{len(data['extensions']):,}")
        self.kpi_vars["storage"].set(f"{len(data['storage']):,}")

        db_name = os.path.basename(self.host.current_db_path) if self.host.current_db_path else "No database selected"
        self.subtitle_var.set(f"{db_name}  •  {records:,} records")

        self.db_total_records_var.set(f"DB Records: {records:,}")
        self.db_files_size_var.set(f"Total Files Size: {self._format_bytes(data['total_size'])}")
        self.db_size_var.set(f"DB Size: {self._format_bytes(data['db_size'])}")

        self._populate_extension_table(data["extensions"], records, data["total_size"])
        self._populate_category_table(data["categories"], records)
        self._draw_extension_chart()
        self._draw_storage_chart()
        self._draw_metadata_chart()
        self._draw_category_chart()

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    def _populate_extension_table(self, rows, total_records, total_size):
        self.extension_tree.delete(*self.extension_tree.get_children())
        for ext, count, size in rows:
            self.extension_tree.insert(
                "", "end",
                values=(
                    ext or "(none)",
                    f"{int(count):,}",
                    self._format_bytes(size),
                    f"{self._pct(count, total_records):.2f}%",
                    f"{self._pct(size, total_size):.2f}%",
                )
            )

    def _populate_category_table(self, rows, total_records):
        self.category_tree.delete(*self.category_tree.get_children())
        for category, count in rows:
            self.category_tree.insert(
                "", "end",
                values=(category, f"{int(count):,}", f"{self._pct(count, total_records):.2f}%")
            )

    # ------------------------------------------------------------------
    # Canvas charts
    # ------------------------------------------------------------------
    def _draw_bars(self, canvas, rows, value_index, formatter, empty_text, max_rows=7):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 160)
        if not rows:
            canvas.create_text(width / 2, height / 2, text=empty_text,
                               fill="#7890A4", font=("Segoe UI", 10))
            return

        rows = rows[:max_rows]
        max_value = max(float(row[value_index] or 0) for row in rows) or 1
        label_width = 120
        value_width = 82
        bar_left = label_width
        bar_right = width - value_width
        bar_width = max(50, bar_right - bar_left)
        row_h = max(24, min(34, (height - 10) / len(rows)))
        c = self.colors

        for i, row in enumerate(rows):
            y = 8 + i * row_h
            label = str(row[0] or "(none)")
            if len(label) > 18:
                label = label[:17] + "…"
            value = float(row[value_index] or 0)
            ratio = value / max_value
            canvas.create_text(4, y + row_h / 2, anchor="w", text=label,
                               fill=c["text_dark"], font=("Segoe UI", 8))
            canvas.create_rectangle(
                bar_left, y + 6, bar_left + bar_width, y + row_h - 7,
                fill="#E5F0F8", outline=""
            )
            canvas.create_rectangle(
                bar_left, y + 6, bar_left + bar_width * ratio, y + row_h - 7,
                fill=c["blue"], outline=""
            )
            canvas.create_text(
                width - 5, y + row_h / 2, anchor="e",
                text=formatter(value), fill=c["text_dark"],
                font=("Segoe UI", 8, "bold")
            )

        if len(rows) < max_rows:
            return
        canvas.create_text(
            width - 5, height - 5, anchor="se",
            text=f"Top {max_rows}", fill="#7890A4", font=("Segoe UI", 7, "italic")
        )

    def _draw_extension_chart(self):
        if not hasattr(self, "ext_canvas"):
            return
        self._draw_bars(
            self.ext_canvas,
            self._extension_rows,
            1,
            lambda v: f"{int(v):,}",
            "No file records",
            max_rows=8,
        )

    def _draw_storage_chart(self):
        if not hasattr(self, "storage_canvas"):
            return
        self._draw_bars(
            self.storage_canvas,
            self._storage_rows,
            2,
            lambda v: self._format_bytes(v),
            "No storage records",
            max_rows=7,
        )

    def _draw_category_chart(self):
        if not hasattr(self, "category_canvas"):
            return
        self._draw_bars(
            self.category_canvas,
            self._category_rows,
            1,
            lambda v: f"{int(v):,}",
            "No categories",
            max_rows=7,
        )

    def _draw_metadata_chart(self):
        canvas = self.meta_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 150)
        c = self.colors
        m = self._metadata
        total = m.get("complete", 0) + m.get("incomplete", 0) + m.get("no_metadata", 0)
        if total <= 0:
            canvas.create_text(width / 2, 55, text="No metadata records",
                               fill="#7890A4", font=("Segoe UI", 10))
            return

        complete = m.get("complete", 0)
        incomplete = m.get("incomplete", 0)
        no_meta = m.get("no_metadata", 0)
        values = [
            ("Complete", complete, "#55B91F"),
            ("Incomplete", incomplete, "#F0A83A"),
            ("No Metadata", no_meta, "#C7D2DA"),
        ]

        x = 12
        y = 12
        bar_w = width - 24
        bar_h = 22
        cursor = x
        for label, value, color in values:
            segment = bar_w * value / total
            if segment > 0:
                canvas.create_rectangle(cursor, y, cursor + segment, y + bar_h,
                                         fill=color, outline="")
                cursor += segment

        for i, (label, value, _color) in enumerate(values):
            yy = 58 + i * 31
            canvas.create_text(12, yy, anchor="w", text=label,
                               fill=c["text_dark"], font=("Segoe UI", 9, "bold"))
            canvas.create_text(
                width - 12, yy, anchor="e",
                text=f"{value:,}  ({self._pct(value, total):.1f}%)",
                fill=c["text_dark"], font=("Segoe UI", 9)
            )

        missing = (
            m.get("missing_category", 0),
            m.get("missing_description", 0),
            m.get("missing_cover1", 0),
            m.get("missing_cover2", 0),
        )
        canvas.create_text(
            12, height - 12, anchor="sw",
            text=(f"Missing fields  •  Category: {missing[0]:,}  •  "
                  f"Description: {missing[1]:,}  •  Cover 1: {missing[2]:,}  •  Cover 2: {missing[3]:,}"),
            fill="#71899D", font=("Segoe UI", 7)
        )

    # ------------------------------------------------------------------
    # Compatibility / shutdown
    # ------------------------------------------------------------------
    def refresh_compat_storage_tree(self, rows):
        self.db_storage_tree.delete(*self.db_storage_tree.get_children())
        for sid, cnt, size in rows:
            self.db_storage_tree.insert("", "end", values=(sid, cnt, self._format_bytes(size)))

    def destroy(self):
        try:
            self._unbind_mousewheel()
        except Exception:
            pass
        try:
            self.db_storage_tree.destroy()
        except Exception:
            pass
