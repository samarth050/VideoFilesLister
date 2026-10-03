"""
VideoSnapper features integrated into FileLister Portable.

This module contains the two original VideoSnapper workflows:
1. Contact Sheet
2. Cover Creator

It deliberately does not create a second Tk root.  The parent FileLister
window owns the Notebook and application lifetime.
"""
import os
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
import tkinter.font as tkfont

from PIL import Image, ImageTk, ImageFont, ImageDraw, ImageOps

from .worker import generate_sheet


class VideoSnapperTabs:
    """Owns the Contact Sheet and Cover Creator tabs."""

    def __init__(self, app, notebook):
        self.app = app
        self.root = app.root
        self.notebook = notebook

        settings = app.load_settings()
        self.video_var = tk.StringVar(value=settings.get("videosnapper_video", ""))
        self.output_var = tk.StringVar(value=settings.get("videosnapper_output_folder", ""))
        self.rows_var = tk.IntVar(value=int(settings.get("videosnapper_rows", 5)))
        self.cols_var = tk.IntVar(value=int(settings.get("videosnapper_cols", 3)))
        self.format_var = tk.StringVar(value=settings.get("videosnapper_format", "JPEG"))

        self.preview_file = None
        self.cover_preview_file = None
        self.snapshot_paths = []
        self.snapshot_var = tk.StringVar()
        self.font_color_var = tk.StringVar(value="white")
        self.cover_sample_font = tkfont.Font(family="Arial", size=48)
        self._generation_running = False
        self._video_path_request = 0

        self.contact_tab = ttk.Frame(notebook, style="Main.TFrame")
        self.cover_tab = ttk.Frame(notebook, style="Main.TFrame")
        notebook.add(self.contact_tab, text="Contact Sheet")
        notebook.add(self.cover_tab, text="Cover Creator")

        self._build_contact_tab()
        self._build_cover_tab()

    # ---------- UI ----------
    def _build_contact_tab(self):
        container = ttk.Frame(self.contact_tab)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        container.columnconfigure(0, weight=0)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=1)

        controls = ttk.LabelFrame(container, text="Contact Sheet Settings", padding=10)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Video File").grid(row=0, column=0, sticky="w", pady=3)
        self.video_entry = ttk.Entry(controls, textvariable=self.video_var, width=58)
        self.video_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self._enable_optional_drop(self.video_entry)
        ttk.Button(controls, text="Browse", command=self.select_video).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(controls, text="Output Folder").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(controls, textvariable=self.output_var, width=58).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(controls, text="Browse", command=self.select_output).grid(row=1, column=2, padx=(8, 0))

        ttk.Label(controls, text="Rows").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Spinbox(controls, from_=1, to=20, textvariable=self.rows_var, width=7).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(controls, text="Cols").grid(row=2, column=2, sticky="w", padx=(8, 0))
        ttk.Spinbox(controls, from_=1, to=20, textvariable=self.cols_var, width=7).grid(row=2, column=2, sticky="e", pady=3)

        actions = ttk.Frame(controls)
        actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 4))
        ttk.Button(actions, text="Generate", command=self.start_generation).pack(side="left", padx=(0, 5))
        self.save_button = ttk.Button(actions, text="Save", command=self.save_preview, state="disabled")
        self.save_button.pack(side="left", padx=(0, 5))
        ttk.Button(actions, text="Reset", command=self.reset_form).pack(side="left")

        ttk.Separator(controls, orient="horizontal").grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        self.progress = ttk.Progressbar(controls, length=280, maximum=100)
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.status = ttk.Label(controls, text="Ready")
        self.status.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.stats_label = ttk.Label(controls, text="Contact preview stats: none")
        self.stats_label.grid(row=7, column=0, columnspan=3, sticky="w")

        preview_frame = ttk.LabelFrame(container, text="Contact Sheet Preview")
        preview_frame.grid(row=0, column=1, sticky="nsew")
        self.sheet_preview = ttk.Label(preview_frame, anchor="center")
        self.sheet_preview.pack(fill="both", expand=True, padx=8, pady=8)

    def _build_cover_tab(self):
        container = ttk.Frame(self.cover_tab)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        container.columnconfigure(0, weight=0)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=1)

        controls = ttk.LabelFrame(container, text="Cover Creator Settings", padding=10)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Video File").grid(row=0, column=0, sticky="w", pady=3)
        self.cover_video_entry = ttk.Entry(controls, textvariable=self.video_var, width=58)
        self.cover_video_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self._enable_optional_drop(self.cover_video_entry)
        ttk.Button(controls, text="Browse", command=self.select_video).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(controls, text="Output Folder").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(controls, textvariable=self.output_var, width=58).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(controls, text="Browse", command=self.select_output).grid(row=1, column=2, padx=(8, 0))

        ttk.Button(controls, text="Generate Snapshots", command=self.start_generation).grid(row=2, column=0, sticky="w", pady=(8, 3))
        ttk.Label(controls, text="Snapshot").grid(row=2, column=1, sticky="w", pady=(8, 3))
        self.snapshot_cb = ttk.Combobox(controls, textvariable=self.snapshot_var, values=[], state="readonly", width=25)
        self.snapshot_cb.grid(row=2, column=2, pady=(8, 3))
        self.snapshot_cb.bind("<<ComboboxSelected>>", lambda event: self.on_snapshot_selected())

        ttk.Separator(controls, orient="horizontal").grid(row=3, column=0, columnspan=3, sticky="ew", pady=10)

        ttk.Label(controls, text="Cover Layout").grid(row=4, column=0, sticky="w", pady=3)
        self.layout_cb = ttk.Combobox(controls, values=["Center Title", "Top Banner", "Bottom Banner"], state="readonly")
        self.layout_cb.current(0)
        self.layout_cb.grid(row=4, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(controls, text="Title").grid(row=5, column=0, sticky="w", pady=3)
        self.title_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.title_var, width=58).grid(row=5, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(controls, text="Subtitle").grid(row=6, column=0, sticky="w", pady=3)
        self.subtitle_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.subtitle_var, width=58).grid(row=6, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(controls, text="Title Size").grid(row=7, column=0, sticky="w", pady=3)
        self.title_size = tk.IntVar(value=48)
        self.title_size.trace_add("write", lambda *args: self._cover_style_changed())
        ttk.Spinbox(controls, from_=8, to=200, textvariable=self.title_size, width=7).grid(row=7, column=1, sticky="w", pady=3)

        ttk.Label(controls, text="Subtitle Size").grid(row=8, column=0, sticky="w", pady=3)
        self.subtitle_size = tk.IntVar(value=24)
        self.subtitle_size.trace_add("write", lambda *args: self._cover_style_changed())
        ttk.Spinbox(controls, from_=8, to=200, textvariable=self.subtitle_size, width=7).grid(row=8, column=1, sticky="w", pady=3)

        ttk.Label(controls, text="Font Color").grid(row=9, column=0, sticky="w", pady=3)
        ttk.Entry(controls, textvariable=self.font_color_var, width=20).grid(row=9, column=1, sticky="w", pady=3)
        ttk.Button(controls, text="Choose Color", command=self.choose_font_color).grid(row=9, column=2, padx=(8, 0), pady=3)
        self.color_swatch = tk.Label(controls, width=3, background="white", relief="groove", borderwidth=1)
        self.color_swatch.grid(row=9, column=3, padx=(8, 0), pady=3)

        self.font_sample = ttk.Label(controls, text="Font sample", foreground=self.font_color_var.get(), font=self.cover_sample_font)
        self.font_sample.grid(row=10, column=0, columnspan=4, sticky="w", pady=(8, 10))

        actions = ttk.Frame(controls)
        actions.grid(row=11, column=0, columnspan=4, sticky="w")
        ttk.Button(actions, text="Preview Cover", command=self.compose_cover_preview).pack(side="left", padx=(0, 5))
        ttk.Button(actions, text="Reset", command=self.reset_cover_tab).pack(side="left", padx=(0, 5))
        self.save_cover_button = ttk.Button(actions, text="Save Cover Image", command=self.save_cover, state="disabled")
        self.save_cover_button.pack(side="left")

        preview_frame = ttk.LabelFrame(container, text="Cover Preview")
        preview_frame.grid(row=0, column=1, sticky="nsew")
        self.cover_preview = ttk.Label(preview_frame, anchor="center")
        self.cover_preview.pack(fill="both", expand=True, padx=8, pady=8)
        self.cover_stats_label = ttk.Label(preview_frame, text="Cover preview stats: none")
        self.cover_stats_label.pack(pady=(0, 6))

        self._cover_style_changed()

    def _enable_optional_drop(self, widget):
        """Preserve VideoSnapper drag/drop when tkinterdnd2 is installed.

        FileLister does not require tkinterdnd2, so the integrated application
        remains fully usable without adding that dependency.
        """
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        data = event.data.strip()
        if data.startswith("{") and data.endswith("}"):
            data = data[1:-1]
        self.video_var.set(data)

    # ---------- File selection ----------
    def populate_video_path_from_selected_record(self):
        self._video_path_request += 1
        request_id = self._video_path_request
        file_id = self.app.selected_file_id
        if file_id is None or not self.app.current_db_path:
            return

        try:
            conn = self.app.get_connection()
            try:
                row = conn.execute(
                    "SELECT file_name, extension, size_bytes, storage_id, full_path "
                    "FROM Files WHERE id = ?",
                    (file_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            messagebox.showerror("Video Path Lookup Failed", str(exc), parent=self.root)
            return

        if not row:
            return

        file_name, extension, size_bytes, storage_id, full_path = row
        storage_id = (storage_id or "").strip()
        if not storage_id:
            return

        previous_path = self.video_var.get()
        threading.Thread(
            target=self._find_selected_video_path,
            args=(
                request_id, file_id, previous_path, file_name, extension,
                size_bytes, storage_id, full_path,
            ),
            daemon=True,
        ).start()

    def _find_selected_video_path(
        self, request_id, file_id, previous_path, file_name, extension,
        size_bytes, storage_id, full_path
    ):
        try:
            video_path = self.app.find_video_path_for_storage(
                file_name, extension, size_bytes, storage_id, full_path
            )
            self.root.after(
                0,
                lambda: self._apply_selected_video_path(
                    request_id, file_id, previous_path, storage_id, video_path
                ),
            )
        except Exception as exc:
            self.root.after(
                0,
                lambda error=exc: messagebox.showerror(
                    "Video Path Lookup Failed", str(error), parent=self.root
                ),
            )

    def _apply_selected_video_path(
        self, request_id, file_id, previous_path, storage_id, video_path
    ):
        if request_id != self._video_path_request:
            return
        if self.app.selected_file_id != file_id:
            return
        if self.video_var.get() != previous_path:
            return
        if self.notebook.tab(self.notebook.select(), "text") not in (
            "Contact Sheet", "Cover Creator"
        ):
            return

        if video_path:
            self.video_var.set(video_path)
            self.status.configure(text=f"Selected video loaded from {storage_id}.")
        else:
            self.status.configure(
                text=f"Selected video not found on attached storage '{storage_id}'."
            )

    def select_video(self):
        path = filedialog.askopenfilename(
            title="Select Video",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.avi *.mov *.mpg *.mpeg *.wmv *.flv *.webm *.m4v *.3gp *.ts *.divx"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.video_var.set(path)

    def select_output(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_var.set(folder)

    # ---------- Generation ----------
    def start_generation(self):
        if self._generation_running:
            messagebox.showinfo("Busy", "VideoSnapper is already generating snapshots.", parent=self.root)
            return

        video = self.video_var.get().strip()
        if not video:
            messagebox.showerror("Missing video file", "Please select a video file before generating.", parent=self.root)
            return
        if not os.path.isfile(video):
            messagebox.showerror("Invalid video file", "The selected video path must be a file.", parent=self.root)
            return

        try:
            rows = max(1, min(20, int(self.rows_var.get())))
            cols = max(1, min(20, int(self.cols_var.get())))
            self.rows_var.set(rows)
            self.cols_var.set(cols)
        except Exception:
            messagebox.showerror("Invalid grid", "Rows and columns must be valid numbers.", parent=self.root)
            return

        self.snapshot_paths = []
        self.snapshot_var.set("")
        self.snapshot_cb.configure(values=[])
        self.preview_file = None
        self.save_button.configure(state="disabled")
        self.progress.configure(value=0)
        self.status.configure(text="Starting...")
        self._generation_running = True

        threading.Thread(
            target=self._generate_worker,
            args=(video, rows, cols),
            daemon=True,
        ).start()

    def _generate_worker(self, video, rows, cols):
        name = os.path.splitext(os.path.basename(video))[0]
        preview = os.path.join(tempfile.gettempdir(), f"{name}_preview.jpg")
        try:
            generate_sheet(
                video, preview, rows, cols,
                self.update_progress, self.update_status,
                frame_callback=self._collect_snapshot,
            )
            self.preview_file = preview
            self.root.after(0, self._generation_complete)
        except Exception as exc:
            self.root.after(0, lambda exc=exc: self._generation_failed(exc))

    def generate_contact_sheet_for_cover(self, video, on_complete):
        if self._generation_running:
            messagebox.showinfo("Busy", "VideoSnapper is already generating snapshots.", parent=self.root)
            return
        if not os.path.isfile(video):
            messagebox.showerror("Invalid video file", "The selected video path must be a file.", parent=self.root)
            return

        try:
            rows = max(1, min(20, int(self.rows_var.get())))
            cols = max(1, min(20, int(self.cols_var.get())))
        except Exception:
            messagebox.showerror("Invalid grid", "Rows and columns must be valid numbers.", parent=self.root)
            return

        output_file = tempfile.NamedTemporaryFile(suffix="_contact_sheet.jpg", delete=False).name
        self._generation_running = True
        self.progress.configure(value=0)
        self.status.configure(text="Generating Cover 2 contact sheet...")
        threading.Thread(
            target=self._generate_cover_sheet_worker,
            args=(video, output_file, rows, cols, on_complete),
            daemon=True,
        ).start()

    def _generate_cover_sheet_worker(self, video, output_file, rows, cols, on_complete):
        try:
            generate_sheet(video, output_file, rows, cols, self.update_progress, self.update_status)
            self.root.after(0, lambda: self._cover_sheet_generation_complete(output_file, on_complete))
        except Exception as exc:
            try:
                os.remove(output_file)
            except OSError:
                pass
            self.root.after(0, lambda exc=exc: self._generation_failed(exc))

    def _cover_sheet_generation_complete(self, output_file, on_complete):
        self._generation_running = False
        self.status.configure(text="Ready")
        on_complete(output_file)

    def _generation_complete(self):
        self._generation_running = False
        self.show_preview(self.preview_file, self.sheet_preview)
        self.update_preview_stats(self.preview_file, self.stats_label)
        self.update_snapshot_selector()
        self.save_button.configure(state="normal")
        self.status.configure(text="Ready")

    def _generation_failed(self, exc):
        self._generation_running = False
        self.progress.configure(value=0)
        self.status.configure(text="Generation failed")
        messagebox.showerror(
            "Contact Sheet Generation Failed",
            f"Could not generate the contact sheet.\n\n{exc}",
            parent=self.root,
        )

    def update_progress(self, value):
        self.root.after(0, lambda: self.progress.configure(value=max(0, min(100, float(value)))))

    def update_status(self, text):
        self.root.after(0, lambda: self.status.configure(text=str(text)))

    def show_preview(self, image_file, preview_widget):
        try:
            with Image.open(image_file) as img:
                preview = img.copy()
            preview.thumbnail((760, 650), Image.LANCZOS)
            photo = ImageTk.PhotoImage(preview)
        except Exception as exc:
            self.update_status(f"Preview failed: {exc}")
            return
        preview_widget.configure(image=photo)
        preview_widget.image = photo

    def update_preview_stats(self, image_file, stats_label=None):
        stats_label = stats_label or self.stats_label
        if not image_file or not os.path.exists(image_file):
            stats = "Preview stats: none"
        else:
            try:
                with Image.open(image_file) as img:
                    width, height = img.size
                kb = os.path.getsize(image_file) / 1024
                stats = f"Preview image: {width}x{height}, {kb:.1f} KB"
            except Exception:
                stats = "Preview stats unavailable"
        stats_label.configure(text=stats)

    def _collect_snapshot(self, path):
        self.snapshot_paths.append(path)

    def on_snapshot_selected(self):
        index = self.snapshot_cb.current()
        if 0 <= index < len(self.snapshot_paths):
            self.show_preview(self.snapshot_paths[index], self.cover_preview)
            self.update_preview_stats(self.snapshot_paths[index], self.cover_stats_label)

    def update_snapshot_selector(self):
        values = [f"Snapshot {i + 1}" for i in range(len(self.snapshot_paths))]
        self.snapshot_cb.configure(values=values)
        if values:
            self.snapshot_var.set(values[0])
            self.snapshot_cb.current(0)
            self.on_snapshot_selected()
        else:
            self.snapshot_var.set("")

    # ---------- Contact sheet save/reset ----------
    def save_preview(self):
        if not self.preview_file or not os.path.exists(self.preview_file):
            messagebox.showerror("No preview", "Generate a preview before saving.", parent=self.root)
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("Missing output folder", "Please select an output folder before saving.", parent=self.root)
            return
        if not os.path.isdir(output):
            messagebox.showerror("Invalid output folder", "The selected output folder does not exist.", parent=self.root)
            return

        name = os.path.splitext(os.path.basename(self.video_var.get().strip()))[0]
        output_file = os.path.join(output, f"{name}_csheet.jpg")
        try:
            shutil.copy2(self.preview_file, output_file)
        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not save the image: {exc}", parent=self.root)
            return
        messagebox.showinfo("Saved", f"Saved preview to:\n{output_file}", parent=self.root)
        self.update_status("Saved preview")

    def reset_form(self):
        self.video_var.set("")
        self.output_var.set("")
        self.stats_label.configure(text="Contact preview stats: none")
        self.status.configure(text="Ready")
        self.progress.configure(value=0)
        self.sheet_preview.configure(image="")
        self.sheet_preview.image = None
        self.preview_file = None
        self.snapshot_paths = []
        self.snapshot_cb.configure(values=[])
        self.snapshot_var.set("")
        self.save_button.configure(state="disabled")

    # ---------- Cover creator ----------
    def choose_font_color(self):
        chosen = colorchooser.askcolor(color=self.font_color_var.get(), title="Choose font color", parent=self.root)
        if chosen and chosen[1]:
            self.font_color_var.set(chosen[1])
            self._cover_style_changed()

    def _cover_style_changed(self):
        try:
            self.cover_sample_font.configure(size=max(8, int(self.title_size.get())))
        except Exception:
            pass
        try:
            self.font_sample.configure(
                foreground=self.font_color_var.get(),
                text=f"Sample Title {self.title_size.get()} / Subtitle {self.subtitle_size.get()}",
            )
            self.color_swatch.configure(background=self.font_color_var.get())
        except Exception:
            pass
        if self.cover_preview_file and os.path.exists(self.cover_preview_file):
            self.compose_cover_preview()

    def reset_cover_tab(self):
        self.title_var.set("")
        self.subtitle_var.set("")
        self.title_size.set(48)
        self.subtitle_size.set(24)
        self.font_color_var.set("white")
        self.layout_cb.current(0)
        self.cover_preview_file = None
        self.cover_preview.configure(image="")
        self.cover_preview.image = None
        self.save_cover_button.configure(state="disabled")
        self.update_preview_stats("", self.cover_stats_label)

    def compose_cover_preview(self):
        if not self.preview_file or not os.path.exists(self.preview_file):
            messagebox.showerror(
                "No snapshots",
                "Generate snapshots first by clicking 'Generate' or 'Generate Snapshots'.",
                parent=self.root,
            )
            return

        start_file = self.preview_file
        idx = self.snapshot_cb.current()
        if 0 <= idx < len(self.snapshot_paths):
            start_file = self.snapshot_paths[idx]

        try:
            with Image.open(start_file) as base:
                cover = base.copy().convert("RGB")

            target_width, target_height = 380, 580
            cover = ImageOps.fit(cover, (target_width, target_height), Image.LANCZOS, centering=(0.5, 0.5))
            w, h = cover.size
            draw = ImageDraw.Draw(cover)

            def _text_size(d, txt, fnt):
                try:
                    bbox = d.textbbox((0, 0), txt, font=fnt)
                    return bbox[2] - bbox[0], bbox[3] - bbox[1]
                except Exception:
                    return fnt.getsize(txt)

            def _load_font(size):
                for font_name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
                    try:
                        return ImageFont.truetype(font_name, size)
                    except Exception:
                        continue
                return ImageFont.load_default()

            def _fit_font(text, desired_size, max_width, max_height=None):
                if not text:
                    return _load_font(desired_size)
                for size in range(max(8, desired_size), 7, -1):
                    font = _load_font(size)
                    tw, th = _text_size(draw, text, font)
                    if tw <= max_width and (max_height is None or th <= max_height):
                        return font
                return _load_font(8)

            title = self.title_var.get().strip()
            subtitle = self.subtitle_var.get().strip()
            layout = self.layout_cb.get()
            text_color = self.font_color_var.get() or "white"
            margin = int(w * 0.05)
            max_width = max(w - 2 * margin, 1)

            title_font = _fit_font(title, int(self.title_size.get()), max_width, int(h * 0.25))
            subtitle_font = _fit_font(subtitle, int(self.subtitle_size.get()), max_width, int(h * 0.20))

            if layout == "Center Title":
                if title:
                    tw, th = _text_size(draw, title, title_font)
                    draw.text(((w - tw) / 2, h * 0.30), title, font=title_font, fill=text_color)
                if subtitle:
                    sw, sh = _text_size(draw, subtitle, subtitle_font)
                    draw.text(((w - sw) / 2, h * 0.42), subtitle, font=subtitle_font, fill=text_color)
            elif layout == "Top Banner":
                banner_h = int(h * 0.25)
                draw.rectangle([0, 0, w, banner_h], fill=(0, 0, 0))
                title_h = 0
                if title:
                    tw, title_h = _text_size(draw, title, title_font)
                    draw.text(((w - tw) / 2, (banner_h - title_h) / 2), title, font=title_font, fill=text_color)
                if subtitle:
                    sw, sh = _text_size(draw, subtitle, subtitle_font)
                    draw.text(((w - sw) / 2, (banner_h - sh) / 2 + title_h), subtitle, font=subtitle_font, fill=text_color)
            else:
                banner_h = int(h * 0.25)
                draw.rectangle([0, h - banner_h, w, h], fill=(0, 0, 0))
                title_h = 0
                if title:
                    tw, title_h = _text_size(draw, title, title_font)
                    draw.text(((w - tw) / 2, h - banner_h + (banner_h - title_h) / 2), title, font=title_font, fill=text_color)
                if subtitle:
                    sw, sh = _text_size(draw, subtitle, subtitle_font)
                    draw.text(((w - sw) / 2, h - banner_h + (banner_h - sh) / 2 + title_h), subtitle, font=subtitle_font, fill=text_color)

            name = os.path.splitext(os.path.basename(self.video_var.get().strip()))[0]
            self.cover_preview_file = os.path.join(tempfile.gettempdir(), f"{name}_cover_preview.jpg")
            cover.save(self.cover_preview_file, quality=95)

            self.show_preview(self.cover_preview_file, self.cover_preview)
            self.update_preview_stats(self.cover_preview_file, self.cover_stats_label)
            self.save_cover_button.configure(state="normal")
        except Exception as exc:
            messagebox.showerror("Preview failed", f"Could not compose cover preview: {exc}", parent=self.root)

    def save_cover(self):
        if not self.cover_preview_file or not os.path.exists(self.cover_preview_file):
            messagebox.showerror("No cover", "Generate and preview the cover first before saving.", parent=self.root)
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("Missing output folder", "Please select an output folder before saving.", parent=self.root)
            return
        if not os.path.isdir(output):
            messagebox.showerror("Invalid output folder", "The selected output folder does not exist.", parent=self.root)
            return

        name = os.path.splitext(os.path.basename(self.video_var.get().strip()))[0]
        output_file = os.path.join(output, f"{name}_cover.jpg")
        try:
            shutil.copy2(self.cover_preview_file, output_file)
        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not save the cover image: {exc}", parent=self.root)
            return
        messagebox.showinfo("Saved", f"Saved cover to:\n{output_file}", parent=self.root)
        self.update_status("Saved cover image")

    # ---------- Persistence ----------
    def save_settings(self):
        self.app.save_settings({
            "videosnapper_video": self.video_var.get().strip(),
            "videosnapper_output_folder": self.output_var.get().strip(),
            "videosnapper_rows": self.rows_var.get(),
            "videosnapper_cols": self.cols_var.get(),
            "videosnapper_format": self.format_var.get(),
        })

    def update_status(self, text):
        # Keep the VideoSnapper local status while also reflecting it in the
        # FileLister global status bar, without interfering with DB operations.
        self.root.after(0, lambda: self.status.configure(text=str(text)))
        try:
            self.root.after(0, lambda: self.app.status_var.set(f"VideoSnapper: {text}"))
        except Exception:
            pass
