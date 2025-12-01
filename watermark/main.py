#!/usr/bin/env python3
# Async Watermark Bot – Huge Text (3x) + Wider + Progress Bar

import os
import re
import time
import json
import asyncio
import logging
import random
import urllib.request
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
    """Downloads font using standard library."""
    if not os.path.exists(FONT_PATH):
        logger.info("Downloading bold font...")
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
    watermark_mode: str = "static" 
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

# ==================== HELPERS ====================
def time_to_seconds(time_str):
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except:
        return 0

def render_bar(current, total):
    if total == 0: return "[░░░░░░░░░░]"
    pct = int(current * 100 / total)
    pct = max(0, min(100, pct))
    filled = pct // 10
    return f"[{'█' * filled}{'░' * (10 - filled)}] {pct}%"

async def download_progress(current, total, status_msg, start_time):
    now = time.time()
    if (now - start_time) < 3 and current < total: 
        return 
    try:
        await status_msg.edit_text(f"⬇️ **Downloading...**\n{render_bar(current, total)}")
    except:
        pass

# ==================== HUGE WATERMARK GENERATION ====================
def create_watermark(text: str, target_video_height: int) -> str:
    # Supersampling Factor (High Quality)
    scale_factor = 3
    
    # === SIZE CHANGE: 3x BIGGER ===
    # Was: // 20 (Small) -> Now: // 7 (Huge)
    base_font_size = int((target_video_height // 7) * scale_factor)
    
    try:
        font = ImageFont.truetype(FONT_PATH, base_font_size)
    except:
        font = ImageFont.load_default()

    # 1. Text Layer
    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1,1)))
    bbox = dummy_draw.textbbox((0, 0), text, font=font, stroke_width=0)
    w_raw = bbox[2] - bbox[0]
    h_raw = bbox[3] - bbox[1]

    text_img = Image.new("RGBA", (w_raw, h_raw + (40 * scale_factor)), (0,0,0,0))
    d_text = ImageDraw.Draw(text_img)
    d_text.text((0, 0), text, font=font, fill="white", stroke_width=0)
    
    if text_img.getbbox():
        text_img = text_img.crop(text_img.getbbox())

    # 2. Distortion (Wider)
    cur_w, cur_h = text_img.size
    
    # === WIDTH CHANGE: WIDER ===
    # Was: 2.0 -> Now: 2.5
    distort_w = int(cur_w * 2.5) 
    distort_h = int(cur_h * 1.5)
    
    text_img = text_img.resize((distort_w, distort_h), Image.Resampling.LANCZOS)

    # 3. Background Box (Tight)
    # Reduced padding relative to the huge font so box isn't massive
    padding_x = int(base_font_size * 0.25) 
    padding_y = int(base_font_size * 0.15)
    
    box_w = distort_w + (padding_x * 2)
    box_h = distort_h + (padding_y * 2)

    bg_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bg_img)
    draw.rounded_rectangle((0, 0, box_w, box_h), radius=box_h // 2, fill=(0, 0, 0, 180))
    
    px = (box_w - distort_w) // 2
    py = (box_h - distort_h) // 2
    bg_img.paste(text_img, (px, py), text_img)

    # 4. Final Downscale
    final_w = int(box_w / scale_factor)
    final_h = int(box_h / scale_factor)
    final_img = bg_img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    wm_path = os.path.join(WORK_DIR, f"wm_{int(time.time())}_{random.randint(1,999)}.png")
    final_img.save(wm_path, "PNG")
    return wm_path

# ==================== VIDEO PROCESSING ====================
async def get_video_info(path):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-of", "json", path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    try:
        meta = json.loads(stdout)
        stream = meta["streams"][0]
        return (
            int(stream.get("width", 0)), 
            int(stream.get("height", 0)), 
            float(stream.get("duration", 0))
        )
    except:
        return 0, 0, 0

async def process_video(in_path, text, out_path, crf, resolution, status_msg, mode="static"):
    wm_path = None
    try:
        in_w, in_h, duration = await get_video_info(in_path)
        if duration == 0: duration = 1 

        wm_path = create_watermark(text, resolution)
        
        filter_complex = f"[0:v]scale=-2:{resolution}[bg];"
        last_stream = "[bg]"

        if mode == "static":
            margin = int(resolution * 0.02) # Slightly tighter margin for huge text
            filter_complex += f"{last_stream}[1:v]overlay=W-w-{margin}:H-h-{margin}"
        else:
            speed_x = resolution // 15
            speed_y = resolution // 20
            x_expr = f"abs(mod(t*{speed_x}, 2*(W-w)) - (W-w))"
            y_expr = f"abs(mod(t*{speed_y}, 2*(H-h)) - (H-h))"
            filter_complex += f"{last_stream}[1:v]overlay=x='{x_expr}':y='{y_expr}'"

        cmd_args = [
            "ffmpeg", "-y", "-i", in_path, "-i", wm_path,
            "-filter_complex", filter_complex,
            "-map", "0:a?", "-c:v", "libx264", "-preset", "faster",
            "-crf", str(crf), "-c:a", "aac", "-b:a", "192k", 
            "-movflags", "+faststart", out_path
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd_args, 
            stdout=asyncio.subprocess.PIPE, 
            stderr=asyncio.subprocess.PIPE
        )

        last_update_time = time.time()
        
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            
            line_str = line.decode('utf-8', errors='ignore')
            
            if "time=" in line_str:
                time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", line_str)
                if time_match:
                    current_time_str = time_match.group(1)
                    current_seconds = time_to_seconds(current_time_str)
                    
                    if time.time() - last_update_time > 4:
                        bar = render_bar(current_seconds, duration)
                        try:
                            await status_msg.edit_text(
                                f"⚙️ **Processing...**\n{bar}\n\n"
                                f"Mode: {mode.title()} | CRF: {crf}"
                            )
                        except:
                            pass
                        last_update_time = time.time()

        await process.wait()
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
            
            status_msg = await app.send_message(uid, f"⏳ **Starting FFmpeg...**")
            start_t = time.time()
            
            success = await process_video(
                in_path, text, out_path, 
                sess.crf, sess.resolution, 
                status_msg,
                mode=sess.watermark_mode
            )
            
            if success:
                dur = int(time.time() - start_t)
                thumb = await generate_thumbnail(out_path)
                caption = (
                    f"✅ **Done ({sess.watermark_mode.title()})**\n"
                    f"⏱ Total Time: {dur}s | CRF: {sess.crf}\n"
                    f"📝 `{text}`"
                )
                await status_msg.edit_text(f"📤 **Uploading...**\n{render_bar(0, 100)}")
                
                start_up = time.time()
                await app.send_video(
                    uid, out_path, caption=caption, thumb=thumb, supports_streaming=True,
                    progress=download_progress, progress_args=(status_msg, start_up)
                )
                if thumb: os.remove(thumb)
            else:
                await status_msg.edit_text("❌ Processing Failed.")

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
        "**👋 Watermark Bot v3.1**\n\n"
        "1. **/ws** - Static Watermark (Bottom Right)\n"
        "2. **/w** - Animated Watermark (Smooth Bounce)\n"
        "3. **/settings** - Check config\n"
    )

@app.on_message(filters.command("w"))
async def set_animated(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "animated"
    await m.reply("✨ **Animated Mode Selected**\n(Smooth Floating)\n\nSend the watermark text:")

@app.on_message(filters.command("ws"))
async def set_static(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "static"
    await m.reply("📍 **Static Mode Selected**\n(Bottom Right)\n\nSend the watermark text:")

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
        return await m.reply("⚠️ Use /ws or /w first.")
    
    file = m.video or m.document
    if m.document and "video" not in m.document.mime_type:
        return await m.reply("❌ Not a video.")

    status = await m.reply("⬇️ **Downloading...**")
    path = await c.download_media(
        file, file_name=os.path.join(WORK_DIR, f"in_{m.from_user.id}_{int(time.time())}.mp4"),
        progress=download_progress, progress_args=(status, time.time())
    )
    if path:
        sess.queue.append((path, sess.watermark_text, "video"))
        await status.edit("✅ **Queued**")
        asyncio.create_task(worker(m.from_user.id))

if __name__ == "__main__":
    check_resources()
    print("Bot Started...")
    app.run()
