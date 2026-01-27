import os
import datetime
import re
import ctypes

def get_files_info(folder, allowed_video_exts, include_subdirs):
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
        if include_subdirs:
            for root, _, files in os.walk(folder):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()

                    # only allowed video files
                    if ext not in allowed_video_exts:
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
                    if ext not in allowed_video_exts:
                        continue

                    process_file(path, f)

            except Exception:
                pass

        return results

def detect_storage_id_from_path(path):
        try:
            drive, _ = os.path.splitdrive(path)
            drive = drive.replace("\\", "").upper()  # C:
        except Exception:
            return "UNKNOWN"

        label = get_drive_label(drive)

        # your existing meaningful folder-based ID logic
        storage = "UNKNOWN"
        parts = os.path.normpath(path).split(os.sep)

        for p in parts:
            up = p.upper()
            if up.startswith(("HDD", "SSD", "USB", "MEDIA", "DRIVE")):
                storage = p
                break

        if label:
            return f"{label} ({drive})"
        else:
            return f"{drive}"

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