#!/usr/bin/env python3
# ==============================================================
#  Fully Optimized main.py for Heroku Standard-2X (1 GB RAM)
#  - ALL MoviePy removed
#  - Pure FFmpeg processing (low RAM)
#  - Safe for 1–2 GB videos
#  - No R14 memory crashes
# ==============================================================

import os
import sys
import time
import asyncio
import logging
import random
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from PIL import Image, ImageDraw, ImageFont

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from config import telegram_config, watermark_config, bot_config

# ==============================================================
# LOGGING
# ==============================================================
logger = logging.getLogger("WM-Bot")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
logger.addHandler(handler)

# Warn if API creds missing
if not telegram_config.BOT_TOKEN:
    logger.warning("BOT_TOKEN not set! Set via heroku config:set BOT_TOKEN=...")

# ==============================================================
# USER SESSION
# ==============================================================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    downloaded_file_path: Optional[str] = None
    file_type: Optional[str] = None
    message_ids: List[int] = field(default_factory=list)
    user_message_ids: List[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    # Use config defaults
    crf: int = field(default=watermark_config.VIDEO_CRF)
    font_size: int = field(default=watermark_config.FONT_SIZE)
    font_color: Tuple[int, int, int, int] = field(default=watermark_config.FONT_COLOR)
    speed: int = field(default=50)
    # For queue: (input_path, text, ftype)
    queue: List[Tuple[str, str, str]] = field(default_factory=list)
    is_processing: bool = False

    def add_bot_message(self, mid: int):
        if mid not in self.message_ids:
            self.message_ids.append(mid)

    def add_user_message(self, mid: int):
        if mid not in self.user_message_ids:
            self.user_message_ids.append(mid)

    def reset(self, keep_file: bool = False):
        self.step = "waiting_media"
        if not keep_file:
            self.downloaded_file_path = None
            self.file_type = None
        self.message_ids = []
        self.user_message_ids = []

# Session manager
class SessionManager:
    def __init__(self):
        self.sessions = {}
        self.lock = asyncio.Lock()

    async def get(self, uid: int) -> UserSession:
        async with self.lock:
            if uid not in self.sessions:
                self.sessions[uid] = UserSession(user_id=uid)
            return self.sessions[uid]

    async def clear(self, uid: int):
        async with self.lock:
            if uid in self.sessions:
                sess = self.sessions[uid]
                for path in [sess.downloaded_file_path]:
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except:
                            pass
                # Clean queue files
                for job in sess.queue:
                    if os.path.exists(job[0]):
                        try:
                            os.remove(job[0])
                        except:
                            pass
                self.sessions[uid] = UserSession(user_id=uid)

session_manager = SessionManager()

# ==============================================================
# UTILITIES
# ==============================================================
def format_size(b):
    for u in ['B','KB','MB','GB']:
        if b < 1024:
            return f"{b:.2f} {u}"
        b /= 1024
    return f"{b:.2f} TB"

class Progress:
    def __init__(self, msg: Message, action="Downloading"):
        self.msg = msg
        self.action = action
        self.last = 0

    async def __call__(self, cur, total):
        if time.time() - self.last < 1:
            return
        self.last = time.time()
        pct = (cur / total) * 100
        bar = int(pct // 5) * "█" + (20 - int(pct // 5)) * "░"
        try:
            await self.msg.edit_text(f"""**{self.action}**
[{bar}] {pct:.1f}%
{format_size(cur)} / {format_size(total)}""")
        except:
            pass

# ==============================================================
# WATERMARK GENERATION (PIL)
# ==============================================================
def get_font(size):
    candidates = [
        watermark_config.FONT_PATH,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for f in candidates:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except:
                pass
    return ImageFont.load_default()

def create_watermark_image_advanced(text, font_size=40, font_color=(255,255,255,255)):
    font = get_font(font_size)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = watermark_config.BOX_PADDING

    w, h = tw + pad * 2, th + pad * 2
    wm = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(wm)

    # Rounded rectangle (fallback to rectangle if not supported)
    try:
        d.rounded_rectangle((0, 0, w, h), radius=watermark_config.BOX_CORNER_RADIUS,
                            fill=watermark_config.BOX_COLOR)
    except AttributeError:
        d.rectangle((0, 0, w, h), fill=watermark_config.BOX_COLOR)

    d.text((pad, pad - bbox[1]), text, font=font, fill=font_color)
    return wm

# ==============================================================
# PURE FFMPEG VIDEO PROCESSING (Updated for large files)
# ==============================================================
def process_video(input_path, wm_text, output_path, crf=watermark_config.VIDEO_CRF,
                  move_speed=50, font_color=watermark_config.FONT_COLOR,
                  font_size=watermark_config.FONT_SIZE):
    """
    Animated watermark using FFmpeg only (VERY low RAM).
    """
    try:
        logger.info(f"Processing video (Animated FFmpeg): {input_path}")

        input_size = os.path.getsize(input_path)
        # Auto-adjust for large inputs: faster preset
        preset = "ultrafast" if input_size > 500 * 1024 * 1024 else watermark_config.VIDEO_PRESET  # >500MB = fast

        # Create watermark image with custom font size + color
        wm_img = create_watermark_image_advanced(wm_text, font_size, font_color)
        wm_tmp = os.path.join(bot_config.OUTPUT_DIR, f"wm_{int(time.time())}.png")
        wm_img.save(wm_tmp)

        # Animated overlay filter
        overlay_filter = (
            f"overlay=x='mod(t*{move_speed}, W-w-{watermark_config.MARGIN})':"
            f"y=H-h-{watermark_config.MARGIN}"
        )

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", wm_tmp,
            "-filter_complex", overlay_filter,
            "-c:v", watermark_config.VIDEO_CODEC,
            "-preset", preset,
            "-crf", str(crf),
            "-c:a", "copy",
            output_path
        ]

        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        try:
            os.remove(wm_tmp)
        except:
            pass

        if result.returncode != 0:
            logger.error(result.stderr)
            return False, "FFmpeg failed to process video"

        if not os.path.exists(output_path):
            return False, "Output not created"

        size = os.path.getsize(output_path)
        if size > bot_config.MAX_FILE_SIZE:
            return False, f"Output too large: {format_size(size)}"

        return True, None

    except Exception as e:
        return False, str(e)

# ==============================================================
# IMAGE PROCESSING
# ==============================================================
def process_image(input_path, text, output_path):
    try:
        img = Image.open(input_path).convert("RGBA")
        wm = create_watermark_image_advanced(text)
        x = img.width - wm.width - watermark_config.MARGIN
        y = img.height - wm.height - watermark_config.MARGIN
        img.paste(wm, (x, y), wm)
        img.convert("RGB").save(output_path, quality=95)
        return True
    except Exception as e:
        logger.error(e)
        return False

# ==============================================================
# ASYNC DOWNLOAD HELPERS
# ==============================================================
async def download_media(client: Client, msg: Message, progress: Progress):
    base_name = f"media_{int(time.time())}"
    dl_dir = bot_config.DOWNLOAD_DIR
    os.makedirs(dl_dir, exist_ok=True)

    if msg.photo:
        file_path = os.path.join(dl_dir, f"{base_name}.jpg")
        await client.download_media(msg.photo, file_path, progress=progress)
        return file_path, "photo"
    elif msg.video:
        file_path = os.path.join(dl_dir, f"{base_name}.mp4")
        await client.download_media(msg.video, file_path, progress=progress)
        return file_path, "video"
    elif msg.document:
        mime = msg.document.mime_type or ""
        ext = ".jpg" if mime.startswith("image/") else ".mp4"
        file_path = os.path.join(dl_dir, f"{base_name}{ext}")
        await client.download_media(msg.document, file_path, progress=progress)
        ftype = "photo" if ext == ".jpg" else "video"
        return file_path, ftype
    return None, None

# ==============================================================
# QUEUE WORKER (Updated for Splitting + Merge)
# ==============================================================
async def video_queue_worker(user_id):
    sess = await session_manager.get(user_id)
    if sess.is_processing:
        return
    sess.is_processing = True

    while sess.queue:
        input_path, text, ftype = sess.queue.pop(0)
        out_dir = bot_config.OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, Path(input_path).stem + "_done" + Path(input_path).suffix)

        if ftype == "photo":
            ok = process_image(input_path, text, out_path)
            err = None if ok else "Image processing failed"
            is_large = False
        else:
            ok, err = process_video(
                input_path,
                text,
                out_path,
                crf=sess.crf,
                move_speed=sess.speed,
                font_color=sess.font_color,
                font_size=sess.font_size
            )
            is_large = ok and os.path.getsize(out_path) > 50 * 1024 * 1024  # >50MB

        if not ok:
            await app.send_message(user_id, f"❌ Failed: {err}")
        else:
            if ftype == "photo" or not is_large:
                # Send single file
                await app.send_photo(user_id, out_path) if ftype == "photo" else await app.send_video(user_id, out_path)
                try:
                    os.remove(out_path)
                except:
                    pass
            else:
                # Split large video
                await app.send_message(user_id, "📦 Large file detected. Splitting into parts...")
                chunk_dir = os.path.join(out_dir, f"chunks_{int(time.time())}")
                os.makedirs(chunk_dir, exist_ok=True)
                
                # FFmpeg split: No re-encode, 5-min chunks (~40MB for HD)
                split_cmd = [
                    "ffmpeg", "-y", "-i", out_path,
                    "-c", "copy", "-map", "0",
                    "-segment_time", "00:05:00",  # 5 minutes per chunk
                    "-f", "segment", "-reset_timestamps", "1",
                    "-segment_format", "mp4",
                    f"{chunk_dir}/chunk_%03d.mp4"
                ]
                split_result = subprocess.run(split_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                if split_result.returncode != 0:
                    await app.send_message(user_id, "❌ Splitting failed. Sending full file anyway (may not upload).")
                    await app.send_video(user_id, out_path)
                else:
                    chunks = sorted(Path(chunk_dir).glob("chunk_*.mp4"))
                    num_chunks = len(chunks)
                    await app.send_message(user_id, f"✅ Split into {num_chunks} parts. Sending now...")
                    
                    concat_list = []
                    for i, chunk in enumerate(chunks, 1):
                        chunk_path = str(chunk)
                        await app.send_video(user_id, chunk_path, caption=f"Part {i}/{num_chunks}")
                        concat_list.append(chunk.name)
                        try:
                            os.remove(chunk_path)
                        except:
                            pass
                    
                    # Clean chunk dir
                    try:
                        os.rmdir(chunk_dir)
                    except:
                        pass
                    
                    # Send merge instructions
                    concat_str = "|".join(concat_list)
                    merge_cmd = f'ffmpeg -i "concat:{concat_str}" -c copy "merged_video.mp4"'
                    merge_msg = f"""🔗 **To merge parts into one video:**

Run this command in terminal (install FFmpeg first from ffmpeg.org):
        
Or use free tools like:
- HandBrake (GUI): Import parts → Queue → Merge.
- Online: Clideo or Kapwing (upload parts → combine).

Total duration: ~{format_size(os.path.getsize(out_path))} | Parts: {num_chunks}"""
                    await app.send_message(user_id, merge_msg)
                
                # Clean original output
                try:
                    os.remove(out_path)
                except:
                    pass

        try:
            os.remove(input_path)
        except:
            pass

    sess.is_processing = False

# ==============================================================
# PYROGRAM BOT
# ==============================================================
app = Client(
    "wm-bot",
    api_id=telegram_config.API_ID,
    api_hash=telegram_config.API_HASH,
    bot_token=telegram_config.BOT_TOKEN,
)

# ==============================================================
# COMMANDS
# ==============================================================
@app.on_message(filters.command("start"))
async def start_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    sess.add_user_message(msg.id)
    r = await msg.reply_text("👋 Welcome! Use /w to start watermarking.")
    sess.add_bot_message(r.id)

@app.on_message(filters.command("w"))
async def w_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    sess.reset()
    sess.step = "waiting_text"
    r = await msg.reply_text("✏️ Send the watermark text:")
    sess.add_bot_message(r.id)

@app.on_message(filters.text & ~filters.command())
async def text_handler(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    sess.add_user_message(msg.id)

    if sess.step != "waiting_text":
        return

    sess.watermark_text = msg.text.strip()
    sess.step = "waiting_media"

    r = await msg.reply_text("📤 Now send a photo or video")
    sess.add_bot_message(r.id)

# ==============================================================
# MEDIA HANDLER
# ==============================================================
@app.on_message((filters.photo | filters.video | filters.document))
async def media_handler(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    sess.add_user_message(msg.id)

    if sess.step != "waiting_media" or not sess.watermark_text:
        return await msg.reply_text("Use /w first.")

    # Progress message
    prog_msg = await msg.reply_text("⬇️ Downloading...")
    progress = Progress(prog_msg, "Downloading")

    # Download file
    file_path, ftype = await download_media(client, msg, progress)
    if not file_path:
        return await prog_msg.edit_text("❌ Failed to download media.")

    sess.downloaded_file_path = file_path
    sess.file_type = ftype
    await prog_msg.edit_text("🔄 Processing...")

    # Queue the job
    sess.queue.append((file_path, sess.watermark_text, ftype))
    asyncio.create_task(video_queue_worker(msg.from_user.id))

    # Reset session after queuing
    sess.reset(keep_file=False)  # Don't keep file; queue handles
    await prog_msg.edit_text("✅ Queued for processing!")

# ================= USER SETTINGS COMMANDS =================
@app.on_message(filters.command("crf"))
async def set_crf_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        value = int(msg.text.split()[1])
        sess.crf = value
        await msg.reply_text(f"CRF set to {value}")
    except:
        await msg.reply_text("Usage: /crf 22")

@app.on_message(filters.command("size"))
async def set_size_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        value = int(msg.text.split()[1])
        sess.font_size = value
        await msg.reply_text(f"Font size set to {value}")
    except:
        await msg.reply_text("Usage: /size 48")

@app.on_message(filters.command("color"))
async def set_color_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        hexcode = msg.text.split()[1].lstrip('#')
        r, g, b = tuple(int(hexcode[i:i+2], 16) for i in (0, 2, 4))
        sess.font_color = (r, g, b, 255)
        await msg.reply_text(f"Color set to #{hexcode.upper()}")
    except:
        await msg.reply_text("Usage: /color FF00FF")

@app.on_message(filters.command("speed"))
async def set_speed_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        value = int(msg.text.split()[1])
        sess.speed = value
        await msg.reply_text(f"Animation speed set to {value}")
    except:
        await msg.reply_text("Usage: /speed 60")

# ==============================================================
# RUN BOT
# ==============================================================
if __name__ == "__main__":
    app.run()
