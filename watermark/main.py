#!/usr/bin/env python3
# Fully Optimized 480p Watermark Bot – Heroku Ready (2025)
# Your original code + only 3 tiny fixes (FFmpeg, /tmp, single document)

import os
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, types

from config import telegram_config, watermark_config

# ==================== HEROKU FIXES ====================
os.makedirs("/tmp/downloads", exist_ok=True)  # Make sure /tmp exists

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("WM-Bot")

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
        self.step = "waiting_media"
        self.watermark_text = ""

session_manager = {}
lock = asyncio.Lock()

async def get_session(user_id: int) -> UserSession:
    async with lock:
        if user_id not in session_manager:
            session_manager[user_id] = UserSession(user_id=user_id)
        return session_manager[user_id]

# ==================== UTILS ====================
def format_size(b):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if b < 1024: return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"

class Progress:
    def __init__(self, msg): 
        self.msg = msg
        self.last = 0
    async def __call__(self, current, total):
        if time.time() - self.last < 1: return
        self.last = time.time()
        pct = current / total * 100
        bar = "█" * int(pct//5) + "░" * (20 - int(pct//5))
        try:
            await self.msg.edit_text(f"Downloading...\n[{bar}] {pct:.1f}%\n{format_size(current)} / {format_size(total)}")
        except: pass

# ==================== WATERMARK IMAGE ====================
def get_font(size: int):
    for path in [watermark_config.FONT_PATH, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except: pass
    return ImageFont.load_default()

def create_watermark(text: str, font_size=42, color=(255,255,255,255)):
    font = get_font(font_size)
    dummy = Image.new("RGBA", (1,1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0,0), text, font=font)
    w = bbox[2] - bbox[0] + 40
    h = bbox[3] - bbox[1] + 30
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    try:
        d.rounded_rectangle((0,0,w-1,h-1), radius=12, fill=watermark_config.BOX_COLOR)
    except:
        d.rectangle((0,0,w-1,h-1), fill=watermark_config.BOX_COLOR)
    d.text((20, 12 - bbox[1]), text, font=font, fill=color)
    return img

# ==================== PROCESSING (FIXED – NO MORE EXIT 234) ====================
def process_video_480p(input_path, text, output_path, crf=23, speed=50, font_size=42, color=(255,255,255,255)):
    try:
        wm = create_watermark(text, font_size, color)
        wm_path = f"/tmp/wm_{os.getpid()}_{int(time.time())}.png"
        wm.save(wm_path)

        overlay = f"overlay=x='20+mod(t*{speed},W-w-40)':y='H-h-20-mod(t*{speed}*0.7,H-h-40)':shortest=1"

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path, "-i", wm_path,
            "-filter_complex", f"[0:v]scale=-2:480[bg];[bg][1:v]{overlay}[v]",  # ← [v] added
            "-map", "[v]", "-map", "0:a?",                                        # ← [v] instead of [bg]
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-threads", "2",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5400)
        os.remove(wm_path)
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"FFmpeg error: {e}")
        return False
    finally:
        if 'wm_path' in locals() and os.path.exists(wm_path):
            try: os.remove(wm_path)
            except: pass

def process_image(input_path, text, output_path):
    try:
        img = Image.open(input_path).convert("RGBA")
        wm = create_watermark(text)
        img.paste(wm, (img.width - wm.width - 20, img.height - wm.height - 20), wm)
        img.convert("RGB").save(output_path, "JPEG", quality=92)
        return True
    except: return False

# ==================== WORKER (SINGLE 2GB DOCUMENT) ====================
async def worker(user_id: int):
    sess = await get_session(user_id)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        input_path, text, ftype = sess.queue.pop(0)
        out_path = f"/tmp/output_{user_id}_{int(time.time())}.{ 'jpg' if ftype=='photo' else 'mp4' }"

        status = await app.send_message(user_id, "Processing → 480p + animated watermark...")

        success = process_image(input_path, text, out_path) if ftype == "photo" else \
                  process_video_480p(input_path, text, out_path, sess.crf, sess.speed, sess.font_size, sess.font_color)

        await status.delete()

        if not success or not os.path.exists(out_path):
            await app.send_message(user_id, "Processing failed!")
            if os.path.exists(input_path): os.remove(input_path)
            continue

        caption = f"Watermark: {text}\nResolution: 480p"

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
        for p in (input_path, out_path):
            if os.path.exists(p): os.remove(p)

    sess.is_processing = False

# ==================== BOT ====================
app = Client(
    "wm-bot",
    api_id=telegram_config.API_ID,
    api_hash=telegram_config.API_HASH,
    bot_token=telegram_config.BOT_TOKEN,
    workdir="/tmp"   # Critical for Heroku
)

@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply("480p Animated Watermark Bot\nSend /w to start")

@app.on_message(filters.command("w"))
async def w(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.step = "waiting_text"
    await m.reply("Send watermark text:")

@app.on_message(filters.text & ~filters.command(["start","w","crf","size","color","speed","cancel"]))
async def text(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_text": return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply("Now send photo/video (will be converted to 480p)")

@app.on_message(filters.photo | filters.video | filters.document)
async def media(client, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media" or not sess.watermark_text:
        return await m.reply("Use /w first")

    prog = await m.reply("Downloading...")
    path = await client.download_media(
        m,
        file_name=f"/tmp/dl_{m.from_user.id}_{m.id}",
        progress=Progress(prog)
    )
    await prog.delete()
    if not path:
        return await m.reply("Download failed")

    ftype = "photo" if m.photo or (m.document and "image" in (m.document.mime_type or "")) else "video"
    sess.queue.append((path, sess.watermark_text, ftype))
    asyncio.create_task(worker(m.from_user.id))
    await m.reply("Queued! Processing in background...")

# Settings commands
@app.on_message(filters.command("crf"))
async def crf(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.crf = int(m.text.split()[1])
        await m.reply(f"CRF = {sess.crf}")
    except: await m.reply("Usage: /crf 23")

@app.on_message(filters.command("size"))
async def size(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.font_size = int(m.text.split()[1])
        await m.reply(f"Font size = {sess.font_size}")
    except: await m.reply("Usage: /size 48")

@app.on_message(filters.command("color"))
async def color(_, m):
    sess = await get_session(m.from_user.id)
    try:
        hexcode = m.text.split()[1].lstrip('#').upper()
        if len(hexcode) != 6: raise ValueError
        r, g, b = int(hexcode[0:2], 16), int(hexcode[2:4], 16), int(hexcode[4:6], 16)
        sess.font_color = (r, g, b, 255)
        await m.reply(f"Color set to #{hexcode}")
    except: await m.reply("Usage: /color FF0066")

@app.on_message(filters.command("speed"))
async def speed(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.speed = int(m.text.split()[1])
        await m.reply(f"Speed = {sess.speed}")
    except: await m.reply("Usage: /speed 60")

@app.on_message(filters.command("cancel"))
async def cancel(_, m):
    sess = await get_session(m.from_user.id)
    sess.queue.clear()
    sess.reset()
    await m.reply("Cancelled")

# ==================== RUN ====================
if __name__ == "__main__":
    logger.info("Watermark Bot Starting...")
    app.run()
