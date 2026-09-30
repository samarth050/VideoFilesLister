import os
import tempfile
import cv2

from PIL import Image
from PIL import ImageDraw

from .ffmpeg_utils import (
    extract_frame,
    get_video_info
)


def generate_sheet(
        video_file,
        output_file,
        rows,
        cols,
        progress_callback,
        status_callback,
        frame_callback=None):

    info = get_video_info(video_file)

    duration = info["duration"]

    total_images = rows * cols

    thumb_w = 320
    thumb_h = 180

    header_height = 80

    sheet_w = cols * thumb_w
    sheet_h = rows * thumb_h + header_height

    sheet = Image.new(
        "RGB",
        (sheet_w, sheet_h),
        "black"
    )

    draw = ImageDraw.Draw(sheet)

    header = (
        f"{os.path.basename(video_file)}\n"
        f"{info['width']}x{info['height']}   "
        f"{info['codec']}   "
        f"{duration:.0f}s"
    )

    draw.text(
        (10, 10),
        header,
        fill="white"
    )

    for i in range(total_images):

        progress = (
            (i + 1) / total_images
        ) * 100

        progress_callback(progress)

        status_callback(
            f"Frame {i+1}/{total_images}"
        )

        timestamp = (
            duration *
            (i + 1) /
            (total_images + 1)
        )

        frame = extract_frame(
            video_file,
            timestamp
        )

        if frame is None:
            continue

        frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        img = Image.fromarray(frame)

        img.thumbnail(
            (
                thumb_w,
                thumb_h
            )
        )

        if frame_callback:
            frame_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            frame_path = frame_file.name
            frame_file.close()
            img.save(frame_path, quality=95)
            frame_callback(frame_path)

        x = (
            i % cols
        ) * thumb_w

        y = (
            i // cols
        ) * thumb_h + header_height

        sheet.paste(
            img,
            (x, y)
        )

        hh = int(timestamp // 3600)
        mm = int(
            (timestamp % 3600) // 60
        )
        ss = int(timestamp % 60)

        draw.rectangle(
            [
                x,
                y + thumb_h - 25,
                x + 110,
                y + thumb_h
            ],
            fill="black"
        )

        draw.text(
            (
                x + 5,
                y + thumb_h - 20
            ),
            f"{hh:02}:{mm:02}:{ss:02}",
            fill="white"
        )

    sheet.save(
        output_file,
        quality=95
    )