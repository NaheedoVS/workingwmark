#!/usr/bin/env python3
# FINAL 2025 Heroku-Ready Watermark Bot – Single 2GB Document Output
# All previous bugs fixed + no splitting

import os
import time
import asyncio
import logging
import subprocess
import signal
import sys
import shutil
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, types
from pyrogram.errors import FloodWait

from config import telegram_config, watermark_config, bot_config

# ==================== FORCE /tmp (HEROKU FIX) ====================
os.makedirs("/tmp/downloads", exist_ok=True)

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("WM-Bot")

# ==================== GRACEFUL SHUTDOWN ====================
def cleanup(signum=None, frame=None):
    logger.info("Shutting down – cleaning /tmp...")
    shutil.rmtree("/tmp", ignore_errors=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

# ==================== SESSION ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    queue: List[Tuple[str, str, str]] = field(default_factory=list)
    is_processing: bool = False
    crf: int = field(default=watermark_config.VIDEO_CRF)
    font_size: int = field(default=watermark_config.FONT_SIZE)
    font_color: Tuple[int, int, int, int] = field(default=watermark_config.FONT_COLOR)
    speed: int = field(default=50)

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""

session_manager = {}
lock = asyncio.Lock()

async def get_session(user_id: int) -> UserSession:
    async with lock:
        if user_id not in session_manager:
            session_manager[user_id] = UserSession(user_id=user_id)
        return session_manager[user_id]

# ==================== PROGRESS (FIXED) ====================
def format_size(b):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if b < 1024: return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"

class DownloadProgress:
    def __init__(self, msg):
        self.msg = msg
        self.last = 0

    async def progress(self, current, total):
        if time.time() - self.last < 1: return
        self.last = time.time()
        pct = current * 100 / total
        bar = "█" * int(pct//5) + "░" * (20 - int(pct//5))
        try:
            await self.msg.edit_text(f"Downloading...\n[{bar}] {pct:.1f}%\n{format_size(current)} / {format_size(total)}")
        except: pass

# ==================== WATERMARK & PROCESSING (UNCHANGED + IMPROVED) ====================
# (keep your create_watermark, get_font, process_image exactly as before)

def process_video_480p(input_path, text, output_path, crf=23, speed=50, font_size=42, color=(255,255,255,255)):
    try:
        wm = create_watermark(text, font_size, color)
        wm_path = f"/tmp/wm_{os.getpid()}_{int(time.time())}.png"
        wm.save(wm_path)

        overlay = f"overlay=x='if(lte(t,0),20,20+mod(t*{speed},W-w-40))':y='H-h-20-mod(t*{speed}*0.7,H-h-40)':shortest=1"

        cmd = [
            "ffmpeg", "-y", "-i", input_path, "-i", wm_path,
            "-filter_complex", f"[0:v]scale=-2:480[bg];[bg][1:v]{overlay}[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-threads", "2",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5400)
        os.remove(wm_path)
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"FFmpeg exception: {e}")
        return False
    finally:
        if 'wm_path' in locals() and os.path.exists(wm_path):
            try: os.remove(wm_path)
            except: pass

# ==================== WORKER (NOW SENDS SINGLE 2GB DOCUMENT) ====================
async def worker(user_id: int):
    sess = await get_session(user_id)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        input_path, text, ftype = sess.queue.pop(0)
        out_path = f"/tmp/output_{user_id}_{int(time.time())}.{'jpg' if ftype=='photo' else 'mp4'}"

        status = await app.send_message(user_id, "Processing → 480p + animated watermark...")

        success = process_image(input_path, text, out_path) if ftype == "photo" else \
                  process_video_480p(input_path, text, out_path, sess.crf, sess.speed, sess.font_size, sess.font_color)

        await status.delete()

        if not success or not os.path.exists(out_path):
            await app.send_message(user_id, "Processing failed!")
            if os.path.exists(input_path): os.remove(input_path)
            continue

        # === SINGLE FILE OUTPUT (UP TO 2 GB) ===
        caption = f"Watermark: {text}\nResolution: 480p • CRF: {sess.crf} • Speed: {sess.speed}"

        try:
            if ftype == "photo":
                await app.send_photo(user_id, out_path, caption=caption)
            else:
                await app.send_document(
                    user_id,
                    out_path,
                    caption=caption,
                    file_name=f"Watermarked - {text}.mp4"
                )
            await app.send_message(user_id, "Done! Full video sent as single file")
        except Exception as e:
            await app.send_message(user_id, f"Upload failed: {e}")

        # Cleanup
        for p in (out_path, input_path):
            if os.path.exists(p): os.remove(p)

    sess.is_processing = False

# ==================== BOT SETUP (workdir=/tmp FIX) ====================
app = Client(
    "wm-bot",
    api_id=telegram_config.API_ID,
    api_hash=telegram_config.API_HASH,
    bot_token=telegram_config.BOT_TOKEN,
    workdir="/tmp"          # ← CRITICAL FOR HEROKU
)

# ==================== HANDLERS (only change: force /tmp download) ====================
@app.on_message(filters.photo | filters.video | filters.document)
async def media(client, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media" or not sess.watermark_text:
        return await m.reply("Use /w first")

    prog_msg = await m.reply("Downloading...")
    dl_progress = DownloadProgress(prog_msg)

    path = await client.download_media(
        m,
        file_name=f"/tmp/dl_{m.from_user.id}_{m.id}",
        progress=dl_progress.progress
    )
    await prog_msg.delete()

    if not path:
        return await m.reply("Download failed")

    ftype = "photo" if m.photo or (m.document and m.document.mime_type and "image" in m.document.mime_type) else "video"
    sess.queue.append((path, sess.watermark_text, ftype))
    asyncio.create_task(worker(m.from_user.id))
    await m.reply("Queued! Processing now...")

# (Keep all your other handlers: /start, /w, /crf, /size, /color, /speed, /cancel exactly as before)

# ==================== RUN ====================
if __name__ == "__main__":
    logger.info("Bot starting...")
    app.run()
