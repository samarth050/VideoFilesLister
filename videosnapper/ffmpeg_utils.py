import subprocess
import sys
import json
import os
import tempfile
import shutil
import cv2

def _find_tool(name):
    candidates = [
        shutil.which(name),
        rf"D:\Tools\ffmpeg\bin\{name}.exe",
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ffmpeg", "bin", f"{name}.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return name

FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

WIN_SUBPROCESS_KWARGS = {
    "creationflags": subprocess.CREATE_NO_WINDOW
} if sys.platform.startswith("win") else {}


def get_video_info(video_file):

    cmd = [
        FFPROBE,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        video_file
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        **WIN_SUBPROCESS_KWARGS
    )

    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            "FFprobe could not read the video. Make sure FFmpeg/FFprobe is installed "
            "and the selected file is a valid video."
        )
    data = json.loads(result.stdout)

    video_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise RuntimeError("No video stream was found in the selected file.")
    stream = video_streams[0]

    return {
        "width": stream["width"],
        "height": stream["height"],
        "codec": stream.get("codec_name", ""),
        "duration": float(
            data["format"]["duration"]
        )
    }


def extract_frame(video_file, seconds):

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".jpg",
        delete=False
    )

    temp_path = temp_file.name
    temp_file.close()

    cmd = [
        FFMPEG,
        "-y",
        "-ss",
        str(seconds),
        "-i",
        video_file,
        "-frames:v",
        "1",
        temp_path
    ]

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **WIN_SUBPROCESS_KWARGS
    )

    frame = cv2.imread(temp_path)

    return frame