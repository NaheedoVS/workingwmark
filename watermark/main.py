#!/usr/bin/env python3
# Async Watermark Bot – v9.0 (Stable + Cancel + Debug Mode)

import os
import re
import time
import json
import asyncio
import logging
import random
import urllib.request
from dataclasses import dataclass, field
from typing import List, Set
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID", "0")) 
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
WORK_DIR = "downloads"
AUTH_FILE = "auth_users.json"

# === TUNING ===
UPDATE_INTERVAL = 5 
FILENAME_SUFFIX = " 🦋Vaiᡣ𐭩Su×@pglinsan2"

os.makedirs(WORK_DIR, exist_ok=True)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONSTANTS & RESOURCES ====================
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS
FONT_URL = "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Bold.ttf"
FONT_PATH = os.path.join(WORK_DIR, "Roboto-Bold.ttf")

def check_resources():
    if not os.path.exists(FONT_PATH):
        try:
            opener = urllib.request.build_opener()
            opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
            urllib.request.install_opener(opener)
            urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        except: pass

# ==================== AUTH MANAGER ====================
def load_auth_users() -> Set[int]:
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r") as f:
                return set(json.load(f))
        except: pass
    return set()

def save_auth_users():
    try:
        with open(AUTH_FILE, "w") as f:
            json.dump(list(AUTHORIZED_USERS), f)
    except: pass

AUTHORIZED_USERS = load_auth_users()
AUTHORIZED_USERS.add(OWNER_ID)

async def check_auth_func(_, __, message: Message):
    if not message.from_user: return False
    return message.from_user.id in AUTHORIZED_USERS

authorized_only = filters.create(check_auth_func)



# ==================== SESSION MANAGER ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    
    # Watermark Data
    watermark_text: str = ""
    watermark_mode: str = "static" 
    
    # Video Settings
    crf: int = 23
    resolution: int = 720
    codec: str = "libx265"
    custom_thumb_path: str = None 
    
    # Animated Settings
    speed: float = 1.0
    scale: float = 1.0

    # System
    queue: List[Message] = field(default_factory=list)
    is_processing: bool = False
    current_process: asyncio.subprocess.Process = None # Added for Cancel

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""
        self.queue.clear()
        self.is_processing = False
        self.current_process = None

session_manager = {}

async def get_session(uid: int) -> UserSession:
    return session_manager.setdefault(uid, UserSession(uid))

# ==================== WATERMARK GENERATION ====================
def create_watermark(text: str, style: str = "static"):
    font_size = 80
    font = ImageFont.load_default()
    
    if style == "static":
        search_paths = [FONT_PATH, "fonts/Roboto-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    else:
        search_paths = [FONT_PATH, "fonts/Roboto-Regular.ttf", "Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]

    for p in search_paths:
        if os.path.exists(p):
            try: 
                font = ImageFont.truetype(p, font_size)
                break
            except: continue

    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    if style == "static":
        px, py = 40, 20
        w, h = text_w + px, text_h + py
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=20, fill=(0, 0, 0, 180))
        draw.text((w / 2, h / 2), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
        return img
    else:
        px, py = 10, 10
        w, h = text_w + px + 2, text_h + py + 2
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((w / 2, h / 2), text, font=font, fill=(255, 0, 0, 255), anchor="mm", stroke_width=1, stroke_fill=(255, 0, 0, 255))
        return img

# ==================== HELPERS & FFmpeg ====================
def time_to_seconds(time_str):
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except: return 0

def render_bar(current, total):
    if total == 0: return "[░░░░░░░░░░]"
    pct = int(current * 100 / total)
    pct = max(0, min(100, pct))
    filled = pct // 10
    return f"[{'█' * filled}{'░' * (10 - filled)}] {pct}%"

async def safe_edit(msg, text, timer_ref):
    now = time.time()
    if (now - timer_ref[0]) < UPDATE_INTERVAL: return 
    try:
        await msg.edit_text(text)
        timer_ref[0] = now
    except: pass

async def get_video_info(path):
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-show_entries", "format=duration", "-of", "json", path]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        meta = json.loads(stdout)
        duration = 0.0
        width = 0
        height = 0
        if "streams" in meta and meta["streams"]:
            stream = meta["streams"][0]
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            try: duration = float(stream.get("duration", 0))
            except: pass
        if duration == 0 and "format" in meta:
            try: duration = float(meta["format"].get("duration", 0))
            except: pass
        return width, height, duration
    except: return 0, 0, 0

async def process_video(in_path, text, out_path, sess, status_msg):
    wm_path = f"{WORK_DIR}/wm_{int(time.time())}_{os.getpid()}.png"
    try:
        in_w, in_h, duration = await get_video_info(in_path)
        if duration == 0: duration = 10 
        
        if sess.watermark_mode == "static":
            wm_full = await asyncio.to_thread(create_watermark, text, style="static")
            t_h = int(sess.resolution / 18)
            t_w = int(t_h * (wm_full.width / wm_full.height))
            overlay_cmd = "x=W-w-20:y=H-h-20"
        else:
            wm_full = await asyncio.to_thread(create_watermark, text, style="moving")
            base_h = int(sess.resolution / 25) 
            t_h = int(base_h * sess.scale)
            t_w = int(t_h * (wm_full.width / wm_full.height))
            sp = sess.speed
            overlay_cmd = f"x='(W-w)/2 + (W-w)/3*sin(t*{sp})':y='(H-h)/2 + (H-h)/3*cos(t*{sp}*2.2)'"

        wm = await asyncio.to_thread(wm_full.resize, (t_w, t_h), RESAMPLE_MODE)
        await asyncio.to_thread(wm.save, wm_path)
        
        filter_complex = f"[0:v]scale=-2:{sess.resolution}[bg];[bg][1:v]overlay={overlay_cmd}"
        cmd_args = ["ffmpeg", "-y", "-i", in_path, "-i", wm_path, "-filter_complex", filter_complex, "-map", "0:a?", "-c:v", sess.codec, "-preset", "fast", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]

        if sess.codec == "libx265":
            hevc_crf = int(sess.crf) + 4
            cmd_args.extend(["-crf", str(hevc_crf), "-tag:v", "hvc1"]) 
        else:
            cmd_args.extend(["-crf", str(sess.crf), "-pix_fmt", "yuv420p"]) 
        cmd_args.append(out_path)

        # FIX: stdout=DEVNULL prevents freeze
        process = await asyncio.create_subprocess_exec(*cmd_args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        sess.current_process = process # Link for Cancel
        
        last_update = [0]
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk: break
            chunk_str = chunk.decode('utf-8', errors='ignore')
            
            if "Duration:" in chunk_str and duration == 10:
                try:
                    dur_match = re.search(r"Duration: (\d{2}:\d{2}:\d{2}\.\d+)", chunk_str)
                    if dur_match: duration = time_to_seconds(dur_match.group(1))
                except: pass

            if "time=" in chunk_str:
                time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", chunk_str)
                if time_match:
                    current = time_to_seconds(time_match.group(1))
                    await safe_edit(status_msg, f"⚙️ **Processing...**\n{render_bar(current, duration)}", last_update)
        
        await process.wait()
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    finally:
        sess.current_process = None
        if os.path.exists(wm_path): os.remove(wm_path)


# ==================== HANDLERS ====================
app = Client("WatermarkBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def download_progress(current, total, status_msg, start, ref):
    if (time.time() - ref[0]) > UPDATE_INTERVAL:
        await safe_edit(status_msg, f"⬇️ **Downloading...**\n{render_bar(current, total)}", ref)

async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True
    try:
        while sess.queue:
            message = sess.queue.pop(0)
            file = message.video or message.document
            original_caption = message.caption.html if message.caption else ""
            
            status_msg = await app.send_message(uid, f"⬇️ **Downloading...**")
            dl_path = os.path.join(WORK_DIR, f"in_{uid}_{int(time.time())}.mp4")
            
            try:
                in_path = await app.download_media(message, file_name=dl_path, progress=download_progress, progress_args=(status_msg, time.time(), [0]))
                if not in_path: 
                    await status_msg.edit("❌ Download Failed.")
                    continue

                out_path = os.path.join(WORK_DIR, f"out_{uid}_{int(time.time())}_{random.randint(100,999)}.mp4")
                await status_msg.edit(f"⏳ **Starting Processing...**")
                
                success = await process_video(in_path, sess.watermark_text, out_path, sess, status_msg)
                
                if success:
                    thumb = sess.custom_thumb_path 
                    if not thumb or not os.path.exists(thumb):
                        cmd = ["ffmpeg", "-y", "-i", out_path, "-ss", "00:00:02", "-vframes", "1", "-vf", "scale=320:-1", f"{out_path}.jpg"]
                        await (await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).wait()
                        thumb = f"{out_path}.jpg"

                    name_root, ext = os.path.splitext(file.file_name or "video.mp4")
                    await status_msg.edit("📤 **Uploading...**")
                    
                    # Original Filename Logic + Suffix
                    await app.send_video(
                        uid, out_path, 
                        caption=original_caption or "✅ Done", 
                        thumb=thumb, 
                        file_name=f"{name_root}{FILENAME_SUFFIX}{ext}"
                    )
                    
                    if thumb and thumb != sess.custom_thumb_path: os.remove(thumb)
                    await status_msg.delete() # Only delete on Success
                else:
                    # Keep message on failure for debugging
                    await status_msg.edit("❌ **Processing Failed.**\n(Check logs or try different video)")
                
                if os.path.exists(in_path): os.remove(in_path)
                if os.path.exists(out_path): os.remove(out_path)

            except Exception as e:
                logger.error(f"Worker Error: {e}")
                # Keep message on crash
                await status_msg.edit(f"❌ **Critical Error:**\n`{str(e)}`")
    finally: sess.is_processing = False

@app.on_message(filters.command("cancel") & authorized_only)
async def cancel_handler(_, m):
    sess = await get_session(m.from_user.id)
    cancelled = False
    
    if sess.current_process:
        try: 
            sess.current_process.terminate()
            cancelled = True
        except: pass
    if sess.queue:
        sess.queue.clear()
        cancelled = True
    if sess.is_processing:
        sess.is_processing = False
        cancelled = True
        
    if cancelled: await m.reply("🛑 **Cancelled.**")
    else: await m.reply("💤 Nothing was processing.")

@app.on_message(filters.command("start"))
async def start_handler(_, m):
    if m.from_user.id not in AUTHORIZED_USERS:
        return await m.reply(f"⛔ **Access Denied**\nYour ID: `{m.from_user.id}`")
    await m.reply(
        "**👋 Watermark Bot v9.0**\n\n"
        "**Set Mode:**\n"
        "• `/ws` - Static (Box + Bold)\n"
        "• `/w` - Animated (Red Text)\n"
        "• `/dual` - Both Styles\n\n"
        "**Settings:**\n"
        "• `/speed 1.5` - Anim Speed\n"
        "• `/scale 1.2` - Anim Size\n"
        "• `/res 720` - Output Res\n"
        "• `/codec 265` - Video Codec\n"
        "• `/crf 23` - Quality (Lower=Better)\n"
        "• `/setthumb` - Reply to photo\n"
        "• `/cancel` - Stop Processing\n\n"
        "**System:**\n"
        "• `/settings` - View Config\n"
        "• `/auth` & `/unauth` (Owner)"
    )

@app.on_message(filters.command("settings") & authorized_only)
async def settings_handler(_, m):
    s = await get_session(m.from_user.id)
    await m.reply(f"**Settings**\nMode: `{s.watermark_mode}`\nCodec: `{s.codec}`\nSpeed: `{s.speed}`\nThumb: {'✅' if s.custom_thumb_path else '❌'}")

@app.on_message(filters.command("auth") & filters.user(OWNER_ID))
async def auth_handler(_, m):
    try:
        AUTHORIZED_USERS.add(int(m.command[1]))
        save_auth_users()
        await m.reply("✅ Added.")
    except: pass

@app.on_message(filters.command("unauth") & filters.user(OWNER_ID))
async def unauth_handler(_, m):
    try:
        AUTHORIZED_USERS.remove(int(m.command[1]))
        save_auth_users()
        await m.reply("🚫 Removed.")
    except: pass

@app.on_message(filters.command(["w", "moving"]) & authorized_only)
async def set_animated(_, m):
    (await get_session(m.from_user.id)).watermark_mode = "moving"
    await m.reply("🔴 **Animated Mode**\nSend Text:")

@app.on_message(filters.command("ws") & authorized_only)
async def set_static(_, m):
    (await get_session(m.from_user.id)).watermark_mode = "static"
    await m.reply("📍 **Static Mode**\nSend Text:")

@app.on_message(filters.command("dual") & authorized_only)
async def set_dual(_, m):
    (await get_session(m.from_user.id)).watermark_mode = "dual"
    await m.reply("✨ **Dual Mode**\nSend Text:")

@app.on_message(filters.command("setthumb") & (filters.photo | filters.reply) & authorized_only)
async def set_thumb_handler(c, m):
    photo = m.photo or (m.reply_to_message.photo if m.reply_to_message else None)
    if photo:
        path = await c.download_media(photo, file_name=os.path.join(WORK_DIR, f"thumb_{m.from_user.id}.jpg"))
        (await get_session(m.from_user.id)).custom_thumb_path = path
        await m.reply("✅ Thumbnail Saved.")

@app.on_message(filters.command(["speed", "scale", "crf", "res"]) & authorized_only)
async def quick_settings(_, m):
    try:
        cmd, val = m.command[0], float(m.command[1])
        sess = await get_session(m.from_user.id)
        if cmd == "speed": sess.speed = val
        elif cmd == "scale": sess.scale = val
        elif cmd == "crf": sess.crf = int(val)
        elif cmd == "res": sess.resolution = int(val)
        await m.reply(f"✅ {cmd} set to {val}")
    except: await m.reply("Usage: `/speed 1.5`")

@app.on_message(filters.command("codec") & authorized_only)
async def set_codec(_, m):
    sess = await get_session(m.from_user.id)
    if "265" in m.text: sess.codec = "libx265"
    else: sess.codec = "libx264"
    await m.reply(f"✅ Codec: {sess.codec}")

@app.on_message(filters.text & filters.private & authorized_only)
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    sess.watermark_text = m.text
    sess.step = "waiting_media"
    await m.reply(f"✅ Text Set: `{m.text}`\nNow send video.")

@app.on_message((filters.video | filters.document) & filters.private & authorized_only)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("⚠️ Set text first (/ws, /w).")
    
    # === FIX: PREVENT SCREENSHOT CRASH ===
    if m.document and "video" not in (m.document.mime_type or ""):
        return await m.reply("❌ **This is not a video file.**")
        
    sess.queue.append(m)
    # RESTORED: Queue Position Counter
    await m.reply(f"✅ **Added to Queue** (Pos: {len(sess.queue)})")
    asyncio.create_task(worker(m.from_user.id))

if __name__ == "__main__":
    check_resources()
    print("Bot is starting...")
    app.start()
    try: app.send_message(OWNER_ID, "Hey Vaisu welcome back")
    except: pass
    print("Bot is now running.")
    idle()
    app.stop()
