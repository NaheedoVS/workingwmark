#!/usr/bin/env python3
# Ultimate 480p Animated Watermark Bot – 2025 Heroku Ready
# Inline playable videos + all fixes applied

import os
import time
import json
import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, types
from pyrogram.errors import FloodWait

# ==================== CONFIG (create config.py or use env vars) ====================
class telegram_config:
    API_ID = int(os.environ.get("API_ID", "YOUR_API_ID"))
    API_HASH = os.environ.get("API_HASH", "your_api_hash")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "your:bot_token")

class watermark_config:
    FONT_PATH = "fonts/Roboto-Bold.ttf"  # Optional: upload font to repo
    FONT_SIZE = 42
    FONT_COLOR = (255, 255, 255, 230)       # White with slight transparency
    BOX_COLOR = (0, 0, 0, 140)              # Dark semi-transparent box
    VIDEO_CRF = 24

# ==================== HEROKU FIXES ====================
os.makedirs("/tmp/downloads", exist_ok=True)
os.makedirs("/tmp", exist_ok=True)

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
        self.step = "waiting_text"
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
        self.last_pct = -1

    async def __call__(self, current, total):
        if time.time() - self.last < 2: return
        pct = int(current / total * 100)
        if pct == self.last_pct: return
        self.last = time.time()
        self.last_pct = pct
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        try:
            await self.msg.edit_text(f"Downloading...\n[{bar}] {pct}%\n{format_size(current)} / {format_size(total)}")
        except: pass

# ==================== WATERMARK IMAGE ====================
def get_font(size: int):
    for path in [watermark_config.FONT_PATH, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except: pass
    return ImageFont.load_default()

def create_watermark(text: str, font_size=42, color=(255,255,255,230)):
    font = get_font(font_size)
    dummy = Image.new("RGBA", (1,1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0,0), text, font=font)
    w = bbox[2] - bbox[0] + 50
    h = bbox[3] - bbox[1] + 30
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    try:
        d.rounded_rectangle((0,0,w-1,h-1), radius=15, fill=watermark_config.BOX_COLOR)
    except:
        d.rectangle((0,0,w-1,h-1), fill=watermark_config.BOX_COLOR)
    d.text((25, 15 - bbox[1]), text, font=font, fill=color)
    return img

# ==================== VIDEO TOOLS ====================
def get_video_info(path: str):
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        for s in data['streams']:
            if s['codec_type'] == 'video':
                return {
                    'duration': int(float(s.get('duration', 0)) or 0),
                    'width': int(s.get('width', 854)),
                    'height': int(s.get('height', 480)),
                }
    except: pass
    return {'duration': 0, 'width': 854, 'height': 480}

def generate_thumbnail(video_path: str, thumb_path: str) -> str:
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", "00:00:08", "-vframes", "1",
            "-vf", "scale=480:360:force_original_aspect_ratio=decrease,pad=480:360:(ow-iw)/2:(oh-ih)/2",
            thumb_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) < 180_000:
            return thumb_path
    except: pass
    return None

# ==================== PROCESSING ====================
def process_video_480p(input_path, text, output_path, crf=24, speed=50, font_size=42, color=(255,255,255,230)):
    try:
        wm = create_watermark(text, font_size, color)
        wm_path = f"/tmp/wm_{os.getpid()}_{int(time.time())}.png"
        wm.save(wm_path)

        overlay = f"overlay=x='if(gte(t,0),20+mod(t*{speed},W-w-40),W-w20)':y='H-h-20-mod(t*{speed}*0.7,H-h-40)':shortest=1"

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path, "-i", wm_path,
            "-filter_complex",
                "[0:v]scale=-2:480:flags=lanczos[bg];[bg][1:v]overlay=" + overlay + "[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "film",
            "-crf", str(crf), "-maxrate", "1400k", "-bufsize", "2800k",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-threads", "2",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6000)
        os.remove(wm_path)

        if result.returncode != 0:
            logger.error(f"FFmpeg failed: {result.stderr}")
            return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 100_000
    except Exception as e:
        logger.error(f"Process error: {e}")
        return False
    finally:
        if 'wm_path' in locals() and os.path.exists(wm_path):
            try: os.remove(wm_path)
            except: pass

def process_image(input_path, text, output_path):
    try:
        img = Image.open(input_path).convert("RGBA")
        wm = create_watermark(text)
        x = img.width - wm.width - 30
        y = img.height - wm.height - 30
        img.paste(wm, (x, y), wm)
        img.convert("RGB").save(output_path, "JPEG", quality=94, optimize=True)
        return True
    except Exception as e:
        logger.error(f"Image process failed: {e}")
        return False

# ==================== WORKER ====================
async def worker(user_id: int):
    sess = await get_session(user_id)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        input_path, text, ftype = sess.queue.pop(0)
        out_path = f"/tmp/out_{user_id}_{int(time.time())}.{'jpg' if ftype == 'photo' else 'mp4'}"

        status = await app.send_message(user_id, "Processing 480p + animated watermark...")

        success = process_image(input_path, text, out_path) if ftype == "photo" else \
                  process_video_480p(input_path, text, out_path, sess.crf, sess.speed, sess.font_size, sess.font_color)

        await status.delete()
        await asyncio.sleep(1)

        if not success or not os.path.exists(out_path):
            await app.send_message(user_id, "Processing failed!")
            if os.path.exists(input_path): os.remove(input_path)
            continue

        caption = f"Watermark: {text}\nResolution: 480p | Quality: CRF {sess.crf}"

        try:
            if ftype == "photo":
                await app.send_photo(user_id, out_path, caption=caption)
            else:
                info = get_video_info(out_path)
                thumb_path = f"/tmp/thumb_{user_id}_{int(time.time())}.jpg"
                thumb = generate_thumbnail(out_path, thumb_path)

                await app.send_document(
                    user_id,
                    out_path,
                    caption=caption,
                    file_name=f"『{text}』 480p.mp4",
                    thumb=thumb,
                    duration=info['duration'],
                    width=info['width'],
                    height=info['height'],
                    mime_type="video/mp4",
                    supports_streaming=True
                )
                await app.send_message(user_id, "Done! Video plays inline")

                if thumb and os.path.exists(thumb):
                    os.remove(thumb)

            await app.send_message(user_id, "Success!")
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            await app.send_message(user_id, f"Upload error: {e}")

        # Cleanup
        for p in (input_path, out_path):
            if os.path.exists(p):
                try: os.remove(p)
                except: pass

    sess.is_processing = False

# ==================== BOT ====================
app = Client(
    "wm-bot",
    api_id=telegram_config.API_ID,
    api_hash=telegram_config.API_HASH,
    bot_token=telegram_config.BOT_TOKEN,
    workdir="/tmp"
)

@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply("480p Animated Watermark Bot 2025\n\nSend /w to add watermark")

@app.on_message(filters.command("w"))
async def w(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    await m.reply("Send the watermark text:")

@app.on_message(filters.text & ~filters.command(["start","w","crf","size","color","speed","cancel"]))
async def text(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_text": return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply("Now send photo or video\n→ Will be converted to 480p with moving watermark")

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

    ftype = "photo" if (m.photo or (m.document and m.document.mime_type and "image" in m.document.mime_type)) else "video"
    sess.queue.append((path, sess.watermark_text, ftype))
    asyncio.create_task(worker(m.from_user.id))
    await m.reply("Added to queue! Processing in background...")

# Settings
@app.on_message(filters.command("crf"))
async def crf(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.crf = int(m.text.split(maxsplit=1)[1])
        await m.reply(f"CRF set to {sess.crf} (18=best, 28=small)")
    except: await m.reply("Usage: /crf 24")

@app.on_message(filters.command("size"))
async def size(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.font_size = int(m.text.split()[1])
        await m.reply(f"Font size = {sess.font_size}")
    except: await m.reply("Usage: /size 50")

@app.on_message(filters.command("color"))
async def color(_, m):
    sess = await get_session(m.from_user.id)
    try:
        hexcode = m.text.split()[1].lstrip('#').upper()
        if len(hexcode) != 6: raise ValueError
        r = int(hexcode[0:2], 16)
        g = int(hexcode[2:4], 16)
        b = int(hexcode[4:6], 16)
        sess.font_color = (r, g, b, 230)
        await m.reply(f"Color set to #{hexcode}")
    except: await m.reply("Usage: /color FF0066")

@app.on_message(filters.command("speed"))
async def speed(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.speed = int(m.text.split()[1])
        await m.reply(f"Watermark speed = {sess.speed}")
    except: await m.reply("Usage: /speed 60")

@app.on_message(filters.command("cancel"))
async def cancel(_, m):
    sess = await get_session(m.from_user.id)
    sess.queue.clear()
    sess.reset()
    await m.reply("Cancelled all tasks")

# ==================== RUN ====================
if __name__ == "__main__":
    logger.info("Ultimate Watermark Bot Starting...")
    app.run()
