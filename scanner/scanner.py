import os
import datetime
import re
import ctypes
from utils.helpers import get_folder_size_bytes

def get_files_info(folder, allowed_video_exts, include_subdirs):
        results = []
        # ✅ Track DVD folders already processed
        dvd_movies = set()

        def add_dvd_movie(dvd_root):
            """Add one database entry for a DVD folder, regardless of VOB count."""
            dvd_root = os.path.normpath(dvd_root)
            if dvd_root in dvd_movies:
                return
            try:
                size = get_folder_size_bytes(dvd_root)
                cdate = datetime.datetime.fromtimestamp(
                    os.path.getctime(dvd_root)
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return

            dvd_movies.add(dvd_root)
            results.append({
                "name_without_ext": os.path.basename(dvd_root),
                "full_path": dvd_root,
                "extension": "DVD",
                "size": size,
                "creation_date": cdate,
                "year": None,
                "category": None,
                "tracked": True
            })

        def dvd_root_for_vob(directory):
            """Return the movie folder represented by VOBs in *directory*."""
            if os.path.basename(directory).casefold() == "video_ts":
                return os.path.dirname(directory)
            return directory

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
        if include_subdirs:
            for root, dirs, files in os.walk(folder):

                # -------- DVD detection --------
                video_ts_dirs = [d for d in dirs if d.casefold() == "video_ts"]
                if video_ts_dirs:
                    dvd_root = root
                    add_dvd_movie(dvd_root)

                    # 🚫 Do not descend into VIDEO_TS
                    dirs[:] = [d for d in dirs if d not in video_ts_dirs]

                # -------- Normal video files --------
                vob_files = [f for f in files if os.path.splitext(f)[1].lower() == ".vob"]
                if vob_files:
                    add_dvd_movie(dvd_root_for_vob(root))

                for f in files:
                    ext = os.path.splitext(f)[1].lower()

                    # skip VOBs explicitly
                    if ext == ".vob":
                        continue

                    if ext not in allowed_video_exts:
                        continue

                    path = os.path.join(root, f)
                    process_file(path, f)


        # ---- only selected folder ----
        else:
            try:
                vob_files = []
                for f in os.listdir(folder):
                    path = os.path.join(folder, f)
                    if not os.path.isfile(path):
                        continue

                    ext = os.path.splitext(f)[1].lower()

                    if ext == ".vob":
                        vob_files.append(f)
                        continue

                    # only allowed video files
                    if ext not in allowed_video_exts:
                        continue

                    process_file(path, f)

                if vob_files:
                    add_dvd_movie(dvd_root_for_vob(folder))

            except Exception:
                pass

        return results


def normalize_path_for_compare(path):
        """Normalize path for comparison while ignoring the drive letter."""
        if not path:
            return ""

        drive, rest = os.path.splitdrive(path)
        candidate = rest if rest else path
        return os.path.normcase(os.path.normpath(candidate))


def paths_equal_ignore_drive(path1, path2):
        return normalize_path_for_compare(path1) == normalize_path_for_compare(path2)


def detect_storage_id_from_path(path):
        try:
            drive, _ = os.path.splitdrive(path)
            drive = drive.replace("\\", "").upper()  # C:
        except Exception:
            return "UNKNOWN"

        label = get_drive_label(drive)

        # If a drive label exists, use it as the canonical storage ID.
        if label:
            return label.strip() or drive

        # Fallback to meaningful folder-based ID logic if present.
        parts = os.path.normpath(path).split(os.sep)
        for p in parts:
            up = p.upper()
            if up.startswith(("HDD", "SSD", "USB", "MEDIA", "DRIVE")):
                return p.strip()

        return drive or "UNKNOWN"

def get_windows_drive_label(drive_letter):
        try:
            import ctypes

            volume_name_buffer = ctypes.create_unicode_buffer(1024)
            fs_name_buffer = ctypes.create_unicode_buffer(1024)
            serial_number = ctypes.c_ulong()
            max_component_len = ctypes.c_ulong()
            file_system_flags = ctypes.c_ulong()

            rc = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive_letter + "\\"),
                volume_name_buffer,
                ctypes.sizeof(volume_name_buffer),
                ctypes.byref(serial_number),
                ctypes.byref(max_component_len),
                ctypes.byref(file_system_flags),
                fs_name_buffer,
                ctypes.sizeof(fs_name_buffer)
            )

            if rc:
                label = volume_name_buffer.value.strip()
                return label if label else "NoLabel"
        except Exception:
            pass

        return "Unknown"

def get_drive_label(drive_letter):
        try:
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetVolumeInformationW(
                f"{drive_letter}\\",
                buf, 1024,
                None, None, None, None, 0
            )
            return buf.value
        except Exception:
            return ""
