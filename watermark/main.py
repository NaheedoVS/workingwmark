#!/usr/bin/env python3
# Async Watermark Bot – v7.0 (Static Preserved + Red Lissajous)

import os
import re
import time
import json
import asyncio
import logging
import random
import urllib.request
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import FloodWait, MessageNotModified

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID", "0")) 
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
WORK_DIR = "downloads"
AUTH_FILE = "auth_users.json"

# === TUNING ===
UPDATE_INTERVAL = 120 
FILENAME_SUFFIX = " 🦋Vaiᡣ𐭩Su×@pglinsan2"

os.makedirs(WORK_DIR, exist_ok=True)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== GLOBAL CONSTANTS ====================
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS

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

# ==================== RESOURCES ====================
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
    
    # Animated Watermark Settings (New)
    speed: float = 1.0
    scale: float = 1.0

    # System
    queue: List[Message] = field(default_factory=list)
    is_processing: bool = False

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""
        self.queue.clear()

session_manager = {}

async def get_session(uid: int) -> UserSession:
    return session_manager.setdefault(uid, UserSession(uid))

# ==================== HELPERS ====================
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
    # We do nothing here. This disables the progress bar updates.
    # The bot will still show "Downloading", "Processing", and "Uploading" 
    # via the main worker function, but it won't flood Telegram with % updates.
    return

async def download_progress(current, total, status_msg, start_time, last_update_ref):
    if current == total: pass
    elif (time.time() - last_update_ref[0]) < UPDATE_INTERVAL: return
    text = f"⬇️ **Downloading...**\n{render_bar(current, total)}"
    await safe_edit(status_msg, text, last_update_ref)


# ==================== WATERMARK GENERATION ====================
def create_watermark(text: str, style: str = "static"):
    font_size = 80
    font = ImageFont.load_default()
    
    # === FONT SELECTION ===
    if style == "static":
        # Static: Full Bold
        search_paths = [FONT_PATH, "fonts/Roboto-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    else:
        # Moving: Standard/Thin
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
        # === STATIC ===
        px, py = 40, 20
        w, h = text_w + px, text_h + py
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=20, fill=(0, 0, 0, 180))
        draw.text((w / 2, h / 2), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
        return img

    else:
        # === MOVING (Fake Semi-Bold) ===
        px, py = 10, 10
        # Add a tiny bit of extra padding for the stroke
        w, h = text_w + px + 2, text_h + py + 2
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # TRICK: Stroke width 1 with the SAME Red color.
        # This makes it slightly bolder than 'Thin', but much thinner than 'Bold'.
        # It fixes the pixelation without making the text huge.
        draw.text((w / 2, h / 2), text, font=font, fill=(255, 0, 0, 255), anchor="mm", stroke_width=1, stroke_fill=(255, 0, 0, 255))
        return img
    
    

# ==================== PROCESSOR ====================
async def get_video_info(path):
    # We ask ffprobe for both stream info AND format (container) info
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", 
        "-show_entries", "stream=width,height,duration:format=duration", 
        "-of", "json", path
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            return 0, 0, 0
            
        meta = json.loads(stdout)
        stream = meta["streams"][0]
        
        # 1. Try to get duration from the VIDEO STREAM
        dur = float(stream.get("duration", 0))
        
        # 2. If Stream fails (is 0), get it from the CONTAINER (Format)
        if dur == 0:
            dur = float(meta["format"].get("duration", 0))

        return int(stream.get("width", 0)), int(stream.get("height", 0)), dur
    except: 
        return 0, 0, 0


async def process_video(in_path, text, out_path, sess, status_msg):
    wm_path = f"{WORK_DIR}/wm_{int(time.time())}_{os.getpid()}.png"
    
    try:
        in_w, in_h, duration = await get_video_info(in_path)
        if duration == 0: duration = 1 
        
        # --- WATERMARK SIZING LOGIC ---
        
        if sess.watermark_mode == "static":
            # 1. Generate Static (Box + White)
            wm_full = await asyncio.to_thread(create_watermark, text, style="static")
            # 2. Logic: Resolution / 18 (Original)
            t_h = int(sess.resolution / 18)
            t_w = int(t_h * (wm_full.width / wm_full.height))
            # 3. Static Position: Bottom Right
            overlay_cmd = "x=W-w-20:y=H-h-20"
            
        else:
            # 1. Generate Moving (Red Text Only)
            wm_full = await asyncio.to_thread(create_watermark, text, style="moving")
            
            # 2. Logic: (Resolution / 25) * Scale Factor
            # Base size is slightly smaller than static, then multiplied by user scale
            base_h = int(sess.resolution / 25) 
            t_h = int(base_h * sess.scale)
            t_w = int(t_h * (wm_full.width / wm_full.height))
            
            # 3. Moving Position: Lissajous Animation
            # x = Center + WidthAmp * sin(t * speed)
            # y = Center + HeightAmp * cos(t * speed * 2.2)
            sp = sess.speed
            overlay_cmd = f"x='(W-w)/2 + (W-w)/3*sin(t*{sp})':y='(H-h)/2 + (H-h)/3*cos(t*{sp}*2.2)'"

        # Resize Image
        wm = await asyncio.to_thread(wm_full.resize, (t_w, t_h), RESAMPLE_MODE)
        await asyncio.to_thread(wm.save, wm_path)
        
        filter_complex = f"[0:v]scale=-2:{sess.resolution}[bg];[bg][1:v]overlay={overlay_cmd}"
        
        cmd_args = [
            "ffmpeg", "-y", "-i", in_path, "-i", wm_path, "-filter_complex", filter_complex,
            "-map", "0:a?", "-c:v", sess.codec, "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"
        ]

        if sess.codec == "libx265":
            hevc_crf = int(sess.crf) + 4
            cmd_args.extend(["-crf", str(hevc_crf), "-tag:v", "hvc1"]) 
        else:
            cmd_args.extend(["-crf", str(sess.crf), "-pix_fmt", "yuv420p"]) 

        cmd_args.append(out_path)

        process = await asyncio.create_subprocess_exec(*cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        last_update_time = [0]
        
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk: break
            chunk_str = chunk.decode('utf-8', errors='ignore')
            if "time=" in chunk_str:
                time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", chunk_str)
                if time_match:
                    codec_name = "HEVC" if sess.codec == "libx265" else "AVC"
                    text = f"⚙️ **Processing ({codec_name})...**\n{render_bar(time_to_seconds(time_match.group(1)), duration)}"
                    await safe_edit(status_msg, text, last_update_time)
        
        await process.wait()
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception as e:
        logger.error(f"FFmpeg Error: {e}")
        return False
    finally:
        if os.path.exists(wm_path): os.remove(wm_path)

async def generate_thumbnail(video_path):
    thumb_path = f"{video_path}.jpg"
    cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:02", "-vframes", "1", "-vf", "scale=320:-1", thumb_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return thumb_path if os.path.exists(thumb_path) else None

async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True
    try:
        while sess.queue:
            message_to_process = sess.queue.pop(0)
            file = message_to_process.video or message_to_process.document
            original_caption = message_to_process.caption.html if message_to_process.caption else ""
            original_name = file.file_name if file.file_name else "video.mp4"
            
            status_msg = await app.send_message(uid, f"⬇️ **Downloading...**")
            dl_path = os.path.join(WORK_DIR, f"in_{uid}_{int(time.time())}.mp4")
            
            try:
                last_update_time = [0]
                in_path = await app.download_media(message_to_process, file_name=dl_path, progress=download_progress, progress_args=(status_msg, time.time(), last_update_time))
                
                if not in_path:
                    await status_msg.edit("❌ Download Failed.")
                    continue

                out_path = os.path.join(WORK_DIR, f"out_{uid}_{int(time.time())}_{random.randint(100,999)}.mp4")
                await status_msg.edit(f"⏳ **Starting FFmpeg...**")
                
                success = await process_video(in_path, sess.watermark_text, out_path, sess, status_msg)
                
                if success:
                    _, _, out_duration = await get_video_info(out_path)
                    thumb = sess.custom_thumb_path if (sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path)) else await generate_thumbnail(out_path)
                    is_custom_thumb = (thumb == sess.custom_thumb_path)

                    await status_msg.edit_text(f"📤 **Uploading...**")
                    name_root, ext = os.path.splitext(original_name)
                    final_filename = f"{name_root}{FILENAME_SUFFIX}{ext}"
                    final_caption = original_caption if original_caption else f"✅ **Done**"

                    await app.send_video(uid, out_path, caption=final_caption, thumb=thumb, file_name=final_filename, duration=int(out_duration))
                    if thumb and not is_custom_thumb: os.remove(thumb)
                else:
                    await status_msg.edit_text("❌ Processing Failed.")
                
                await status_msg.delete()
                if os.path.exists(in_path): os.remove(in_path)
                if os.path.exists(out_path): os.remove(out_path)

            except Exception as e:
                logger.error(f"Worker Error: {e}")
                await status_msg.edit(f"❌ Error: {e}")
    finally: sess.is_processing = False


# ==================== HANDLERS ====================
app = Client("WatermarkBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- AUTH ---
@app.on_message(filters.command("auth") & filters.user(OWNER_ID))
async def auth_handler(_, m):
    if len(m.command) != 2: return await m.reply("Usage: `/auth 123456789`")
    try:
        new_id = int(m.command[1])
        AUTHORIZED_USERS.add(new_id)
        save_auth_users()
        await m.reply(f"✅ User `{new_id}` added.")
    except: await m.reply("❌ Invalid ID.")

@app.on_message(filters.command("unauth") & filters.user(OWNER_ID))
async def unauth_handler(_, m):
    if len(m.command) != 2: return await m.reply("Usage: `/unauth 123456789`")
    try:
        rem_id = int(m.command[1])
        if rem_id == OWNER_ID: return await m.reply("❌ Cannot ban owner.")
        if rem_id in AUTHORIZED_USERS:
            AUTHORIZED_USERS.remove(rem_id)
            save_auth_users()
            await m.reply(f"🚫 User `{rem_id}` removed.")
        else: await m.reply("⚠️ User not in list.")
    except: await m.reply("❌ Invalid ID.")

# --- SETTINGS COMMANDS ---
@app.on_message(filters.command("start"))
async def start_handler(_, m):
    if m.from_user.id not in AUTHORIZED_USERS:
        return await m.reply(f"⛔ **Access Denied**\nYour ID: `{m.from_user.id}`")
    await m.reply(
        "**👋 Watermark Bot v7.0 (Red Animation)**\n"
        "1. `/ws` - Static Watermark\n"
        "2. `/w` - Animated Watermark (Red, No Box)\n"
        "3. `/dual` - Both (Static + Animated)\n"
        "**Settings:**\n"
        "• `/speed 1.5` - Set Animation Speed\n"
        "• `/scale 1.2` - Set Animation Size\n"
        "• `/setthumb` - Save Thumbnail\n"
        "• `/codec 265` - Video Codec"
    )

@app.on_message(filters.command("setthumb") & (filters.photo | filters.reply) & authorized_only)
async def set_thumb_handler(c, m):
    sess = await get_session(m.from_user.id)
    photo = m.photo or (m.reply_to_message.photo if m.reply_to_message else None)
    if not photo: return await m.reply("❌ Send a photo with `/setthumb`")
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path): os.remove(sess.custom_thumb_path)
    path = await c.download_media(photo, file_name=os.path.join(WORK_DIR, f"thumb_{m.from_user.id}.jpg"))
    sess.custom_thumb_path = path
    await m.reply("✅ **Custom Thumbnail Saved!**")

@app.on_message(filters.command("clearthumb") & authorized_only)
async def clear_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path: os.remove(sess.custom_thumb_path)
    sess.custom_thumb_path = None
    await m.reply("🗑 **Thumbnail Deleted.**")

@app.on_message(filters.command("speed") & authorized_only)
async def set_speed(_, m):
    try:
        val = float(m.command[1])
        (await get_session(m.from_user.id)).speed = val
        await m.reply(f"🚀 Speed set to **{val}**")
    except: await m.reply("Usage: `/speed 1.5` (1.0 is default)")

@app.on_message(filters.command("scale") & authorized_only)
async def set_scale(_, m):
    try:
        val = float(m.command[1])
        (await get_session(m.from_user.id)).scale = val
        await m.reply(f"🔍 Scale set to **{val}**")
    except: await m.reply("Usage: `/scale 1.5` (1.0 is default)")

# --- MODES ---
@app.on_message(filters.command(["w", "moving"]) & authorized_only)
async def set_animated(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "moving"
    await m.reply("🔴 **Animated Mode** (Red Text, Lissajous)\nSend the watermark text:")

@app.on_message(filters.command("ws") & authorized_only)
async def set_static(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "static"
    await m.reply("📍 **Static Mode** (Black Box)\nSend the watermark text:")

@app.on_message(filters.command("dual") & authorized_only)
async def set_dual(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "dual"
    await m.reply("✨ **Dual Mode** (Both)\nSend the watermark text:")

# --- CONFIG ---
@app.on_message(filters.command("codec") & authorized_only)
async def set_codec(_, m):
    try:
        sess = await get_session(m.from_user.id)
        arg = m.command[1] if len(m.command) > 1 else ""
        if arg == "265":
            sess.codec = "libx265"
            await m.reply("✅ **H.265 (HEVC)** Active")
        elif arg == "264":
            sess.codec = "libx264"
            await m.reply("✅ **H.264 (AVC)** Active")
        else: await m.reply("Usage: `/codec 264` or `265`")
    except: pass

@app.on_message(filters.command("crf") & authorized_only)
async def set_crf(_, m):
    try:
        val = int(m.command[1])
        (await get_session(m.from_user.id)).crf = val
        await m.reply(f"✅ CRF: {val}")
    except: await m.reply("Usage: `/crf 23`")

@app.on_message(filters.command("res") & authorized_only)
async def set_res(_, m):
    try:
        val = int(m.command[1])
        (await get_session(m.from_user.id)).resolution = val
        await m.reply(f"✅ Res: {val}p")
    except: await m.reply("Usage: `/res 720`")

@app.on_message(filters.command("settings") & authorized_only)
async def settings_handler(_, m):
    s = await get_session(m.from_user.id)
    await m.reply(f"**Settings**\nMode: `{s.watermark_mode}`\nCodec: `{s.codec}`\nSpeed: `{s.speed}`\nScale: `{s.scale}`\nThumb: {'✅' if s.custom_thumb_path else '❌'}")

@app.on_message(filters.text & filters.private & authorized_only)
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        sess.watermark_text = m.text
        sess.step = "waiting_media"
        await m.reply(f"✅ Text Set: `{sess.watermark_text}`\nNow send video.")

@app.on_message((filters.video | filters.document) & filters.private & authorized_only)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("⚠️ Use /ws, /w, or /dual first.")
    if m.document and "video" not in m.document.mime_type: return await m.reply("❌ Not a video.")
    sess.queue.append(m)
    await m.reply(f"✅ **Added to Queue** (Pos: {len(sess.queue)})")
    asyncio.create_task(worker(m.from_user.id))

# --- NEW CANCEL COMMAND ---
@app.on_message(filters.command("cancel") & authorized_only)
async def cancel_handler(_, m):
    sess = await get_session(m.from_user.id)
    if not sess.queue:
        return await m.reply("❌ **Queue is empty.**")
    
    count = len(sess.queue)
    sess.queue.clear()
    await m.reply(f"🛑 **Cancelled!**\nCleared {count} item(s) from the queue.\nCurrent task will finish shortly.")

if __name__ == "__main__":
    check_resources()
    print("Bot is starting...")
    app.start()
    try: app.send_message(OWNER_ID, "Hey Vaisu welcome back")
    except: pass
    print("Bot is now running.")
    idle()
    app.stop()
