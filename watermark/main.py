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
from typing import Optional, List

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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
                if sess.downloaded_file_path and os.path.exists(sess.downloaded_file_path):
                    try:
                        os.remove(sess.downloaded_file_path)
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
            await self.msg.edit_text(f"**{self.action}**
            [{bar}] {pct:.1f}%
            {format_size(cur)} / {format_size(total)}")
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


def create_watermark_image(text):
    return create_watermark_image_advanced(text, watermark_config.FONT_SIZE, watermark_config.FONT_COLOR)


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

    d.rounded_rectangle((0, 0, w, h), radius=watermark_config.BOX_CORNER_RADIUS,
                         fill=watermark_config.BOX_COLOR)

    d.text((pad, pad - bbox[1]), text, font=font, fill=font_color)
    return wm
(text):
    font = get_font(watermark_config.FONT_SIZE)
    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = watermark_config.BOX_PADDING

    w, h = tw + pad * 2, th + pad * 2
    wm = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(wm)

    # Rounded rectangle
    d.rounded_rectangle((0, 0, w, h), radius=watermark_config.BOX_CORNER_RADIUS,
                         fill=watermark_config.BOX_COLOR)

    d.text((pad, pad - bbox[1]), text, font=font, fill=watermark_config.FONT_COLOR)
    return wm

# ==============================================================
# ==============================================================
# PURE FFMPEG VIDEO PROCESSING WITH ANIMATION + CRF + COLOR + FONT SIZE
# ==============================================================

def process_video(input_path, wm_text, output_path, crf=watermark_config.CRF,
                  move_speed=50, font_color=watermark_config.FONT_COLOR,
                  font_size=watermark_config.FONT_SIZE):
    """
    Animated watermark using FFmpeg only (VERY low RAM).

    Features added:
    - Moving watermark: x=(t*move_speed) % (W-w)
    - CRF quality control
    - Dynamic font size
    - Dynamic font color
    """
    try:
        logger.info(f"Processing video (Animated FFmpeg): {input_path}")

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
            "-preset", watermark_config.VIDEO_PRESET,
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
 (LOW RAM)
# ==============================================================
def process_video(input_path, wm_text, output_path):
    try:
        logger.info(f"Processing video (FFmpeg): {input_path}")

        # Save watermark as PNG
        wm_img = create_watermark_image(wm_text)
        wm_tmp = os.path.join(bot_config.OUTPUT_DIR, f"wm_{int(time.time())}.png")
        wm_img.save(wm_tmp)

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", wm_tmp,
            "-filter_complex",
            f"overlay=W-w-{watermark_config.MARGIN}:H-h-{watermark_config.MARGIN}",
            "-c:v", watermark_config.VIDEO_CODEC,
            "-preset", watermark_config.VIDEO_PRESET,
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
        wm = create_watermark_image(text)
        x = img.width - wm.width - watermark_config.MARGIN
        y = img.height - wm.height - watermark_config.MARGIN
        img.paste(wm, (x, y), wm)
        img.convert("RGB").save(output_path, quality=95)
        return True
    except Exception as e:
        logger.error(e)
        return False

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
# ================= USER SETTINGS COMMANDS =================
# CRF setter
@app.on_message(filters.command("crf"))
async def set_crf_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        value = int(msg.text.split()[1])
        sess.crf = value
        await msg.reply_text(f"CRF set to {value}")
    except:
        await msg.reply_text("Usage: /crf 22")

# Font size
@app.on_message(filters.command("size"))
async def set_size_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        value = int(msg.text.split()[1])
        sess.font_size = value
        await msg.reply_text(f"Font size set to {value}")
    except:
        await msg.reply_text("Usage: /size 48")

# Color hex
@app.on_message(filters.command("color"))
async def set_color_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        hexcode = msg.text.split()[1].lstrip('#')
        r,g,b = tuple(int(hexcode[i:i+2],16) for i in (0,2,4))
        sess.font_color = (r,g,b,255)
        await msg.reply_text(f"Color set to #{hexcode.upper()}")
    except:
        await msg.reply_text("Usage: /color FF00FF")

# Speed
@app.on_message(filters.command("speed"))
async def set_speed_cmd(client, msg):
    sess = await session_manager.get(msg.from_user.id)
    try:
        value = int(msg.text.split()[1])
        sess.speed = value
        await msg.reply_text(f"Animation speed set to {value}")
    except:
        await msg.reply_text("Usage: /speed 60")

# ================= QUEUE SYSTEM =================
async def video_queue_worker(user_id):
    sess = await session_manager.get(user_id)
    if getattr(sess, "is_processing", False):
        return
    sess.is_processing = True

    while getattr(sess, "queue", []):
        job = sess.queue.pop(0)
        input_path, text = job
        out_path = input_path.replace('.mp4','_done.mp4')

        ok, err = process_video(
            input_path,
            text,
            out_path,
            crf=getattr(sess, "crf", watermark_config.CRF),
            move_speed=getattr(sess, "speed", 50),
            font_color=getattr(sess, "font_color", watermark_config.FONT_COLOR),
            font_size=getattr(sess, "font_size", watermark_config.FONT_SIZE)
        )

        if not ok:
            await app.send_message(user_id, f"❌ Failed: {err}")
        else:
            await app.send_video(user_id, out_path)
            try: os.remove(out_path)
            except: pass
        try: os.remove(input_path)
        except: pass

    sess.is_processing = False
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

    if sess.step != "waiting_media":
        return await msg.reply_text("Use /w first.")

    # Identify file
    if msg.photo:
        f = msg.photo
        ftype = "photo"
        ext
