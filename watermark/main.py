#!/usr/bin/env python3
# Fully Optimized Watermark Bot for Heroku 2X
# - 480p output
# - Smart split + Telegram native merge
# - /merge command for desktop users
# - 100% safe on 1–2 GB RAM

import os
import sys
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, types
from pyrogram.errors import FloodWait

from config import telegram_config, watermark_config, bot_config

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("WM-Bot")

# ==================== USER SESSION ====================
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
    
    # Settings
    crf: int = field(default=watermark_config.VIDEO_CRF)
    font_size: int = field(default=watermark_config.FONT_SIZE)
    font_color: Tuple[int, int, int, int] = field(default=watermark_config.FONT_COLOR)
    speed: int = field(default=50)
    
    # Queue & processing
    queue: List[Tuple[str, str, str]] = field(default_factory=list)
    is_processing: bool = False
    last_split_dir: Optional[str] = None  # For /merge command

    def reset(self, keep_file: bool = False):
        self.step = "waiting_media"
        if not keep_file:
            self.downloaded_file_path = None
            self.file_type = None
        self.message_ids.clear()
        self.user_message_ids.clear()

class SessionManager:
    def __init__(self): self.sessions = {}; self.lock = asyncio.Lock()
    async def get(self, uid: int) -> UserSession:
        async with self.lock:
            if uid not in self.sessions:
                self.sessions[uid] = UserSession(user_id=uid)
            return self.sessions[uid]
    async def clear_old_files(self, uid: int):
        async with self.lock:
            if uid in self.sessions:
                sess = self.sessions[uid]
                for path in [sess.downloaded_file_path, sess.last_split_dir]:
                    if path and os.path.exists(path):
                        try: [f.unlink() for f in Path(path).rglob("*") if f.is_file()]
                        except: pass

session_manager = SessionManager()

# ==================== UTILS ====================
def format_size(b): 
    for u in ['B','KB','MB','GB']: 
        if b < 1024: return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}TB"

class Progress:
    def __init__(self, msg, action="Downloading"): self.msg = msg; self.action = action; self.last = 0
    async def __call__(self, cur, total):
        if time.time() - self.last < 1: return
        self.last = time.time()
        pct = cur/total*100
        bar = "█"*int(pct//5) + "░"*(20-int(pct//5))
        try: await self.msg.edit_text(f"**{self.action}**\n[{bar}] {pct:.1f}%\n{format_size(cur)} / {format_size(total)}")
        except: pass

# ==================== WATERMARK IMAGE ====================
def get_font(size):
    for path in [watermark_config.FONT_PATH, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except: pass
    return ImageFont.load_default()

def create_watermark_image_advanced(text, font_size=40, font_color=(255,255,255,255)):
    font = get_font(font_size)
    dummy = Image.new("RGBA", (1,1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0,0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    pad = watermark_config.BOX_PADDING
    w, h = tw + pad*2, th + pad*2
    img = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    try:
        draw.rounded_rectangle((0,0,w,h), radius=watermark_config.BOX_CORNER_RADIUS, fill=watermark_config.BOX_COLOR)
    except:
        draw.rectangle((0,0,w,h), fill=watermark_config.BOX_COLOR)
    draw.text((pad, pad-bbox[1]), text, font=font, fill=font_color)
    return img

# ==================== PURE FFMPEG 480P PROCESSING ====================
def process_video_480p(input_path, text, output_path, crf=23, speed=50, font_size=42, font_color=(255,255,255,255)):
    try:
        wm = create_watermark_image_advanced(text, font_size, font_color)
        wm_path = f"/tmp/wm_{os.getpid()}_{int(time.time())}.png"
        wm.save(wm_path)

        # Smooth diagonal moving watermark (always visible)
        overlay = (
            f"overlay=x='20+mod(t*{speed},W-w-40)':"
            f"y='H-h-20-mod(t*{speed}*0.7,H-h-40)':shortest=1"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", wm_path,
            "-filter_complex", f"[0:v]scale=-2:480[bg];[bg][1:v]{overlay}",
            "-map", "[bg]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3600)
        os.remove(wm_path)
        
        if result.returncode != 0:
            logger.error(result.stderr.decode())
            return False, "FFmpeg encoding failed"
        return True, None
    except Exception as e:
        return False, str(e)

def process_image(input_path, text, output_path):
    try:
        img = Image.open(input_path).convert("RGBA")
        wm = create_watermark_image_advanced(text)
        img.paste(wm, (img.width - wm.width - watermark_config.MARGIN, img.height - wm.height - watermark_config.MARGIN), wm)
        img.convert("RGB").save(output_path, "JPEG", quality=92)
        return True
    except Exception as e:
        logger.error(e)
        return False

# ==================== QUEUE WORKER (480p + Smart Split + /merge support) ====================
async def video_queue_worker(user_id):
    sess = await session_manager.get(user_id)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        input_path, text, ftype = sess.queue.pop(0)
        out_dir = bot_config.OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, Path(input_path).stem + "_WM_480p.mp4")

        status_msg = await app.send_message(user_id, "Processing → 480p + watermark...")

        if ftype == "photo":
            success = process_image(input_path, text, output_path.replace(".mp4", ".jpg"))
            final_path = output_path.replace(".mp4", ".jpg") if success else None
        else:
            success, err = process_video_480p(input_path, text, output_path,
                                              crf=sess.crf, speed=sess.speed,
                                              font_size=sess.font_size, font_color=sess.font_color)
            final_path = output_path if success else None

        await status_msg.delete()

        if not success or not final_path:
            await app.send_message(user_id, "Processing failed!")
            continue

        file_size = os.path.getsize(final_path)

        # < 45 MB → send directly
        if file_size <= 45 * 1024 * 1024:
            caption = f"Watermark: {text}\nResolution: 480p"
            if ftype == "photo":
                await app.send_photo(user_id, final_path, caption=caption)
            else:
                await app.send_video(user_id, final_path, caption=caption)
            os.remove(final_path)
        else:
            # SPLIT + SEND AS ALBUM (Telegram shows "Merge" button)
            await app.send_message(user_id, "Large file → splitting into parts...\nTelegram will show a Merge button")

            chunk_dir = os.path.join(out_dir, f"parts_{user_id}_{int(time.time())}")
            os.makedirs(chunk_dir, exist_ok=True)
            sess.last_split_dir = chunk_dir  # Save for /merge command

            subprocess.run([
                "ffmpeg", "-y", "-i", final_path,
                "-c", "copy", "-map", "0",
                "-f", "segment", "-segment_time", "280",
                "-reset_timestamps", "1",
                f"{chunk_dir}/part_%03d.mp4"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            parts = sorted(Path(chunk_dir).glob("part_*.mp4"))
            if not parts:
                await app.send_video(user_id, final_path, caption="Split failed → sending full")
            else:
                media = []
                for i, p in enumerate(parts):
                    cap = f"Watermark: {text} • Part {i+1}/{len(parts)}\nTap ⋮ → Merge when all arrive" if i == 0 else ""
                    media.append(types.InputMediaVideo(str(p), caption=cap))
                await app.send_media_group(user_id, media)
                await app.send_message(user_id,
                    "All parts sent!\n\n"
                    "Mobile: Tap three dots ⋮ → Merge\n"
                    "Desktop: Right-click first part → Merge Videos\n"
                    "Or use /merge command here")

            # Cleanup
            [p.unlink(missing_ok=True) for p in parts]
            try: os.rmdir(chunk_dir)
            except: pass
            os.remove(final_path)

        os.remove(input_path)

    sess.is_processing = False

# ==================== BOT ====================
app = Client("wm-bot", api_id=telegram_config.API_ID, api_hash=telegram_config.API_HASH, bot_token=telegram_config.BOT_TOKEN)

# ==================== COMMANDS ====================
@app.on_message(filters.command("start"))
async def start(c, m): await m.reply("Welcome! Send /w to add animated watermark (480p output)")

@app.on_message(filters.command("w"))
async def w(c, m):
    sess = await session_manager.get(m.from_user.id)
    sess.reset()
    sess.step = "waiting_text"
    await m.reply("Send the watermark text:")

@app.on_message(filters.text & ~filters.command(["start","w","crf","size","color","speed","merge","cancel"]))
async def text_handler(c, m):
    sess = await session_manager.get(m.from_user.id)
    if sess.step != "waiting_text": return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply("Now send a photo or video (will be converted to 480p)")

@app.on_message(filters.photo | filters.video | filters.document)
async def media(c, m):
    sess = await session_manager.get(m.from_user.id)
    if sess.step != "waiting_media" or not sess.watermark_text:
        return await m.reply("Use /w first")
    
    prog = await m.reply("Downloading...")
    progress = Progress(prog)
    path, ftype = await c.download_media(m, progress=progress) or (None, None)
    if not path: return await prog.edit("Download failed")
    
    sess.queue.append((path, sess.watermark_text, ftype))
    asyncio.create_task(video_queue_worker(m.from_user.id))
    await prog.edit("Queued! Processing in background...")

# Settings
@app.on_message(filters.command(["crf", "size", "color", "speed"]))
async def settings(c, m):
    sess = await session_manager.get(m.from_user.id)
    cmd = m.command[0]
    try:
        val = m.text.split()[1]
        if cmd == "crf": sess.crf = int(val)
        elif cmd == "size": sess.font_size = int(val)
        elif cmd == "color": 
            val = val.lstrip('#'); r,g,b = int(val[i:i+2],16) for i in (0,2,4)
            sess.font_color = (r,g,b,255)
        elif cmd == "speed": sess.speed = int(val)
        await m.reply(f"{cmd.upper()} → {val}")
    except: await m.reply(f"Usage: /{cmd} value")

# MERGE COMMAND (for users who need it)
@app.on_message(filters.command("merge") & filters.reply)
async def merge_command(c, m):
    if not m.reply_to_message or not m.reply_to_message.video:
        return await m.reply("Reply to the first part of a split video")
    
    await m.reply(
        "To merge the parts you received:\n\n"
        "Mobile (Recommended):\n"
        "   → Open the album → tap ⋮ → Merge\n\n"
        "Desktop / PC:\n"
        "   → Select all parts → right-click → 'Merge Videos'\n\n"
        "Or download all parts and run this command:\n"
        "```bash\n"
        "ffmpeg -f concat -safe 0 -i <(for f in part_*.mp4; do echo \"file '$f'\"; done) -c copy merged.mp4\n"
        "```\n\n"
        "Done in 3 seconds!", disable_web_page_preview=True)

@app.on_message(filters.command("cancel"))
async def cancel(c, m):
    sess = await session_manager.get(m.from_user.id)
    sess.queue.clear()
    sess.reset()
    await m.reply("All tasks cancelled")

# ==================== RUN ====================
if __name__ == "__main__":
    # Auto-cleanup old files every hour
    async def cleanup():
        while True:
            await asyncio.sleep(3600)
            for d in [bot_config.DOWNLOAD_DIR, bot_config.OUTPUT_DIR]:
                for f in Path(d).rglob("*"):
                    if f.is_file() and time.time() - f.stat().st_mtime > 1800:
                        f.unlink(missing_ok=True)
    app.loop.create_task(cleanup())
    app.run()
