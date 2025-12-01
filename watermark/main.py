#!/usr/bin/env python3
# Async Watermark Bot – Standard Library Only

import os
import time
import json
import asyncio
import logging
import random
import urllib.request  # <--- Changed from aiohttp to standard library
from dataclasses import dataclass, field
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID", "0")) 
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WORK_DIR = "downloads"

os.makedirs(WORK_DIR, exist_ok=True)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== RESOURCES ====================
FONT_URL = "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Bold.ttf"
FONT_PATH = os.path.join(WORK_DIR, "Roboto-Bold.ttf")

def check_resources():
    """Downloads font using standard library (no pip install needed)."""
    if not os.path.exists(FONT_PATH):
        logger.info("Downloading font...")
        try:
            urllib.request.urlretrieve(FONT_URL, FONT_PATH)
            logger.info("Font downloaded successfully.")
        except Exception as e:
            logger.error(f"Could not download font: {e}")

# ==================== SESSION MANAGER ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    watermark_mode: str = "animated"
    queue: List[Tuple[str, str, str]] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 23
    resolution: int = 720

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""
        self.queue.clear()

session_manager = {}
lock = asyncio.Lock()

async def get_session(uid: int) -> UserSession:
    async with lock:
        return session_manager.setdefault(uid, UserSession(uid))

# ==================== PROGRESS BAR ====================
async def progress_bar(current, total, status_msg, start_time):
    now = time.time()
    if (now - start_time) < 3 and current < total: 
        return 
        
    pct = int(current * 100 / total)
    bar = "█" * (pct // 10) + "▒" * (10 - (pct // 10))
    try:
        await status_msg.edit_text(f"**Downloading...**\n[{bar}] {pct}%")
    except Exception:
        pass

# ==================== IMAGE PROCESSING ====================
def create_watermark(text: str, scale=0.6) -> str:
    # Try to load the downloaded font, fall back to default if failed
    try:
        font = ImageFont.truetype(FONT_PATH, 40)
    except:
        font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0] + 40, bbox[3] - bbox[1] + 20

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w, h), radius=10, fill=(0, 0, 0, 160))
    draw.text((20, 10), text, font=font, fill=(255, 255, 255, 230))

    wm_path = os.path.join(WORK_DIR, f"wm_{int(time.time())}_{random.randint(1,999)}.png")
    
    final_w, final_h = int(w * scale), int(h * scale)
    img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)
    img.save(wm_path)
    return wm_path

# ==================== VIDEO PROCESSING ====================
async def get_video_meta(path):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    try:
        meta = json.loads(stdout)
        return meta["streams"][0]["width"], meta["streams"][0]["height"]
    except:
        return 0, 0

async def process_video(in_path, text, out_path, crf, resolution, mode="animated"):
    wm_path = None
    try:
        wm_path = create_watermark(text)
        
        filter_complex = f"[0:v]scale=-2:{resolution}[bg];"
        last_stream = "[bg]"

        if mode == "static":
            # Static: Bottom Left with 15px padding
            filter_complex += f"{last_stream}[1:v]overlay=15:H-h-15"
        else:
            # Animated: Random pop-up
            wm_img = Image.open(wm_path)
            wm_w, wm_h = wm_img.size
            in_w, in_h = await get_video_meta(in_path)
            
            scaled_w = int(in_w * (resolution / in_h))
            scaled_h = resolution
            max_x = max(0, scaled_w - wm_w - 20)
            max_y = max(0, scaled_h - wm_h - 20)

            interval = 5 
            hops = 8     

            for i in range(hops):
                x = random.randint(10, max_x)
                y = random.randint(10, max_y)
                enable_expr = f"between(t,{i*interval},{(i+1)*interval})"
                out_node = f"[v{i}]"
                filter_complex += f"{last_stream}[1:v]overlay={x}:{y}:enable='{enable_expr}'{out_node};"
                last_stream = out_node
            
            filter_complex = filter_complex.rstrip(";")

        # Construct Command
        cmd_args = [
            "ffmpeg", "-y", "-i", in_path, "-i", wm_path,
            "-filter_complex", filter_complex,
        ]

        if mode == "animated":
            cmd_args.extend(["-map", last_stream])
        # If static, we implicitly map the result of the single complex filter
        
        cmd_args.extend([
            "-map", "0:a?", "-c:v", "libx264", "-preset", "superfast",
            "-crf", str(crf), "-c:a", "aac", "-b:a", "128k", 
            "-movflags", "+faststart", out_path
        ])

        process = await asyncio.create_subprocess_exec(
            *cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()

        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024

    except Exception as e:
        logger.error(f"FFmpeg Error: {e}")
        return False
    finally:
        if wm_path and os.path.exists(wm_path):
            os.remove(wm_path)

async def generate_thumbnail(video_path):
    thumb_path = f"{video_path}.jpg"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", "00:00:02", "-vframes", "1",
        "-vf", "scale=320:-1", thumb_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return thumb_path if os.path.exists(thumb_path) else None

# ==================== WORKER QUEUE ====================
async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True

    try:
        while sess.queue:
            in_path, text, _ = sess.queue.pop(0)
            out_path = os.path.join(WORK_DIR, f"out_{uid}_{int(time.time())}.mp4")
            status_msg = await app.send_message(uid, f"⏳ **Processing ({sess.watermark_mode})...**")

            start_t = time.time()
            success = await process_video(
                in_path, text, out_path, 
                sess.crf, sess.resolution, 
                mode=sess.watermark_mode
            )
            
            if success:
                dur = int(time.time() - start_t)
                thumb = await generate_thumbnail(out_path)
                caption = (
                    f"✅ **Done ({sess.watermark_mode.title()})**\n"
                    f"⏱ Time: {dur}s | CRF: {sess.crf} | Res: {sess.resolution}p\n"
                    f"📝 `{text}`"
                )
                await status_msg.edit_text("📤 **Uploading...**")
                await app.send_video(uid, out_path, caption=caption, thumb=thumb, supports_streaming=True)
                if thumb: os.remove(thumb)
            else:
                await app.send_message(uid, "❌ Processing Failed.")

            await status_msg.delete()
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)
            
    finally:
        sess.is_processing = False

# ==================== HANDLERS ====================
app = Client("WatermarkBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_handler(_, m):
    await m.reply(
        "**👋 Watermark Bot**\n\n"
        "1. **/w** - Animated Watermark (Pop-up)\n"
        "2. **/ws** - Static Watermark (Bottom Left)\n"
        "3. **/settings** - Check config\n"
    )

@app.on_message(filters.command("w"))
async def set_animated(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "animated"
    await m.reply("✨ **Animated Mode Selected**\nSend the watermark text:")

@app.on_message(filters.command("ws"))
async def set_static(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "static"
    await m.reply("📍 **Static Mode Selected**\n(Bottom Left)\n\nSend the watermark text:")

@app.on_message(filters.command("settings"))
async def settings_handler(_, m):
    sess = await get_session(m.from_user.id)
    await m.reply(f"**Settings**\nMode: `{sess.watermark_mode}`\nCRF: {sess.crf}\nRes: {sess.resolution}p")

@app.on_message(filters.command("crf"))
async def set_crf(_, m):
    try:
        sess = await get_session(m.from_user.id)
        sess.crf = max(0, min(int(m.command[1]), 51))
        await m.reply(f"✅ CRF: {sess.crf}")
    except: await m.reply("Usage: /crf 23")

@app.on_message(filters.command("res"))
async def set_res(_, m):
    try:
        sess = await get_session(m.from_user.id)
        sess.resolution = int(m.command[1])
        await m.reply(f"✅ Res: {sess.resolution}p")
    except: await m.reply("Usage: /res 720")

@app.on_message(filters.text & filters.private)
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        sess.watermark_text = m.text[:50]
        sess.step = "waiting_media"
        await m.reply(f"✅ Text Set: `{sess.watermark_text}`\nNow send video.")

@app.on_message((filters.video | filters.document) & filters.private)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media":
        return await m.reply("⚠️ Use /w or /ws first.")
    
    if m.document and "video" not in m.document.mime_type:
        return await m.reply("❌ Not a video.")

    status = await m.reply("⬇️ **Downloading...**")
    path = await c.download_media(
        m, file_name=os.path.join(WORK_DIR, f"in_{m.from_user.id}_{int(time.time())}.mp4"),
        progress=progress_bar, progress_args=(status, time.time())
    )
    if path:
        sess.queue.append((path, sess.watermark_text, "video"))
        await status.edit("✅ **Queued**")
        asyncio.create_task(worker(m.from_user.id))

if __name__ == "__main__":
    check_resources() # Runs synchronously before bot start
    print("Bot Started...")
    app.run()
