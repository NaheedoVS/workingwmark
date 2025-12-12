#!/usr/bin/env python3
# Async Watermark Bot – Lissajous & Dual Mode
# 1. Static: Fixed Pill Shape, Res/18 Size, Text "🦋Vaiᡣ𐭩Su"
# 2. Moving: Lissajous Animation, Red Text, No Box
# 3. Dual Mode: Both at once

import os
import time
import asyncio
import logging
import random
import math
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
FIXED_STATIC_TEXT = "🦋Vaiᡣ𐭩Su" 

os.makedirs(WORK_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== GLOBAL CONSTANTS ====================
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS

# ==================== AUTH MANAGER ====================
AUTHORIZED_USERS = {OWNER_ID}
if os.path.exists(AUTH_FILE):
    try:
        with open(AUTH_FILE, "r") as f:
            AUTHORIZED_USERS.update(set(json.load(f)))
    except: pass

async def check_auth_func(_, __, message: Message):
    if not message.from_user: return False
    return message.from_user.id in AUTHORIZED_USERS

authorized_only = filters.create(check_auth_func)

# ==================== RESOURCES (LOCAL FONT) ====================
FONT_PATH = "arial.ttf" # Must be in same folder

def check_resources():
    if not os.path.exists(FONT_PATH):
        print(f"⚠️ WARNING: {FONT_PATH} not found!")
    else:
        print(f"✅ Found local font: {FONT_PATH}")

# ==================== SESSION MANAGER ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""        # For Moving/Lissajous
    watermark_mode: str = "static"  # static, moving, dual
    
    # Settings
    crf: int = 23
    resolution: int = 720
    codec: str = "libx265"
    speed: float = 1.0              # Animation speed
    scale: float = 1.0              # Moving watermark size multiplier
    custom_size: Optional[Tuple[int, int]] = None
    
    custom_thumb_path: str = None 
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
    now = time.time()
    if (now - timer_ref[0]) < UPDATE_INTERVAL: return 
    try:
        await msg.edit_text(text)
        timer_ref[0] = now
    except FloodWait as e:
        timer_ref[0] = now + e.value + 10
    except MessageNotModified: pass
    except Exception: pass

async def download_progress(current, total, status_msg, start_time, last_update_ref):
    if current == total: pass
    elif (time.time() - last_update_ref[0]) < UPDATE_INTERVAL: return
    text = f"⬇️ **Downloading...**\n{render_bar(current, total)}"
    await safe_edit(status_msg, text, last_update_ref)


# ==================== WATERMARK GENERATION ====================
def create_watermark(text: str, style: str = "static"):
    font_size = 80
    font = ImageFont.load_default()
    if os.path.exists(FONT_PATH):
        try: font = ImageFont.truetype(FONT_PATH, font_size)
        except: pass

    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    
    # Text Dimensions
    t_w = bbox[2] - bbox[0]
    t_h = bbox[3] - bbox[1]
    y_offset = bbox[1]

    if style == "static":
        # === PILL SHAPE (Original Logic) ===
        px, py = 40, 20
        w, h = t_w + px, t_h + py
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Black Box + White Text
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=20, fill=(0, 0, 0, 180))
        draw.text(((w - t_w)//2, (h - t_h)//2 - y_offset), text, font=font, fill="white")
        return img

    else:
        # === MOVING STYLE (Red, No Box) ===
        # Minimal padding to prevent clipping
        px, py = 10, 10
        w, h = t_w + px, t_h + py
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Red Text Only (#FF0000)
        draw.text(((w - t_w)//2, (h - t_h)//2 - y_offset), text, font=font, fill=(255, 0, 0, 255))
        return img

# ==================== VIDEO PROCESSING ====================
async def get_video_info(path):
    cmd = ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-of", "json", path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        m = json.loads(out)["streams"][0]
        return int(m["width"]), int(m["height"]), float(m["duration"])
    except: return 0, 0, 0

async def process_video(in_path, out_path, sess, status_msg):
    pid = os.getpid()
    ts = int(time.time())
    wm_s_path = f"{WORK_DIR}/wm_static_{ts}_{pid}.png"
    wm_m_path = f"{WORK_DIR}/wm_moving_{ts}_{pid}.png"
    
    try:
        vw, vh, dur = await get_video_info(in_path)
        if dur == 0: dur = 1
        
        # === PREPARE INPUTS ===
        inputs = ["-i", in_path]
        filter_parts = []
        
        # 1. GENERATE STATIC WM (If needed)
        if sess.watermark_mode in ["static", "dual"]:
            # Logic: Resolution / 18 (Keep strictly same as before)
            raw_s = await asyncio.to_thread(create_watermark, FIXED_STATIC_TEXT, "static")
            th_s = int(sess.resolution / 18)
            tw_s = int(th_s * (raw_s.width / raw_s.height))
            img_s = await asyncio.to_thread(raw_s.resize, (tw_s, th_s), RESAMPLE_MODE)
            await asyncio.to_thread(img_s.save, wm_s_path)
            inputs.extend(["-i", wm_s_path])

        # 2. GENERATE MOVING WM (If needed)
        if sess.watermark_mode in ["moving", "lissajous", "dual"]:
            # Logic: (Resolution / 25) * Scale Factor
            raw_m = await asyncio.to_thread(create_watermark, sess.watermark_text, "moving")
            
            if sess.custom_size:
                tw_m, th_m = sess.custom_size
            else:
                th_m = int((sess.resolution / 25) * sess.scale)
                tw_m = int(th_m * (raw_m.width / raw_m.height))
            
            img_m = await asyncio.to_thread(raw_m.resize, (tw_m, th_m), RESAMPLE_MODE)
            await asyncio.to_thread(img_m.save, wm_m_path)
            inputs.extend(["-i", wm_m_path])

        # === BUILD FILTERS ===
        # Scale Base Video
        filter_parts.append(f"[0:v]scale=-2:{sess.resolution}[bg]")
        current_stream = "[bg]"
        
        # Calculate Speed
        sp = sess.speed
        
        # A. Apply Static (Bottom Right)
        if sess.watermark_mode == "static":
            filter_parts.append(f"{current_stream}[1:v]overlay=x=W-w-20:y=H-h-20")
            
        # B. Apply Moving (Lissajous Curve)
        elif sess.watermark_mode in ["moving", "lissajous"]:
            # Lissajous Math: X = A*sin(at), Y = B*cos(bt)
            lissa = f"x='(W-w)/2 + (W-w)/3*sin(t*{sp})':y='(H-h)/2 + (H-h)/3*cos(t*{sp}*2.2)'"
            filter_parts.append(f"{current_stream}[1:v]overlay={lissa}")
            
        # C. Apply DUAL (Static First, Then Moving)
        elif sess.watermark_mode == "dual":
            # 1. Overlay Static (Input 1) on BG -> [v1]
            filter_parts.append(f"{current_stream}[1:v]overlay=x=W-w-20:y=H-h-20[v1]")
            
            # 2. Overlay Moving (Input 2) on [v1] -> Out
            lissa = f"x='(W-w)/2 + (W-w)/3*sin(t*{sp})':y='(H-h)/2 + (H-h)/3*cos(t*{sp}*2.2)'"
            filter_parts.append(f"[v1][2:v]overlay={lissa}")

        # === FFMPEG EXECUTION ===
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "0:a?", "-c:v", sess.codec, "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"
        ]
        
        if sess.codec == "libx265":
            cmd.extend(["-crf", str(sess.crf + 4), "-tag:v", "hvc1"])
        else:
            cmd.extend(["-crf", str(sess.crf), "-pix_fmt", "yuv420p"])
            
        cmd.append(out_path)
        
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        
        last_upd = [0]
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk: break
            if "time=" in chunk.decode('utf-8', 'ignore'):
                t_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", chunk.decode('utf-8','ignore'))
                if t_match:
                    await safe_edit(status_msg, f"⚙️ **Encoding...**\n{render_bar(time_to_seconds(t_match.group(1)), dur)}", last_upd)
        
        await proc.wait()
        return os.path.exists(out_path) and os.path.getsize(out_path) > 100
        
    except Exception as e:
        logger.error(f"Err: {e}")
        return False
    finally:
        if os.path.exists(wm_s_path): os.remove(wm_s_path)
        if os.path.exists(wm_m_path): os.remove(wm_m_path)


# ==================== HANDLERS ====================
app = Client("WatermarkBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True
    try:
        while sess.queue:
            msg = sess.queue.pop(0)
            status = await app.send_message(uid, "⬇️ **Downloading...**")
            dl = f"{WORK_DIR}/in_{uid}_{int(time.time())}.mp4"
            out = f"{WORK_DIR}/out_{uid}_{int(time.time())}.mp4"
            
            try:
                if not await app.download_media(msg, file_name=dl):
                    await status.edit("❌ Download Failed")
                    continue
                
                await status.edit("⏳ **Starting FFmpeg...**")
                if await process_video(dl, out, sess, status):
                    thumb = sess.custom_thumb_path or dl + ".jpg"
                    if not sess.custom_thumb_path:
                        await asyncio.create_subprocess_exec("ffmpeg","-i",out,"-ss","2","-vframes","1",thumb)
                    
                    await status.edit("📤 **Uploading...**")
                    await app.send_video(uid, out, caption="✅ **Done**", thumb=thumb if os.path.exists(thumb) else None)
                    if not sess.custom_thumb_path and os.path.exists(thumb): os.remove(thumb)
                else:
                    await status.edit("❌ Encoding Failed")
            except Exception as e:
                await status.edit(f"Error: {e}")
            finally:
                if os.path.exists(dl): os.remove(dl)
                if os.path.exists(out): os.remove(out)
                await status.delete()
    finally: sess.is_processing = False

@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    await m.reply(
        "**👋 Watermark Bot v7.0 (Dual + Lissajous)**\n\n"
        "**Modes:**\n"
        "1. `/dual` - Static Box + Moving Red Text\n"
        "2. `/lissajous` - Moving Red Text Only\n"
        "3. `/ws` - Static Box Only\n\n"
        "**Moving Settings:**\n"
        "• `/speed 1.5` - Set Speed (Default 1.0)\n"
        "• `/scale 2.0` - Set Size Multiplier (Default 1.0)\n"
        "• `/size 200 100` - Set Exact Size (WxH)\n\n"
        "**General:**\n"
        "• `/res 720` - Resolution\n"
        "• `/crf 23` - Quality\n"
        "• `/settings` - View Config"
    )

@app.on_message(filters.command("dual") & authorized_only)
async def set_dual(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "dual"
    await m.reply("✨ **Dual Mode Activated**\nStatic: Locked\nSend the text for the **Moving Red Watermark**:")

@app.on_message(filters.command(["lissajous", "moving"]) & authorized_only)
async def set_lissa(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "lissajous"
    await m.reply("🔴 **Lissajous Mode** (Red Text, No Box)\nSend the watermark text:")

@app.on_message(filters.command("ws") & authorized_only)
async def set_static(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "static"
    await m.reply(f"📍 **Static Mode**\nText is locked to `{FIXED_STATIC_TEXT}`.\nJust send video now.")
    sess.step = "waiting_media"

@app.on_message(filters.command("speed") & authorized_only)
async def set_speed(_, m):
    try:
        val = float(m.command[1])
        (await get_session(m.from_user.id)).speed = val
        await m.reply(f"🚀 Speed set to **{val}**")
    except: await m.reply("Usage: `/speed 1.5`")

@app.on_message(filters.command("scale") & authorized_only)
async def set_scale(_, m):
    try:
        val = float(m.command[1])
        (await get_session(m.from_user.id)).scale = val
        await m.reply(f"🔍 Scale set to **{val}**")
    except: await m.reply("Usage: `/scale 1.5`")

@app.on_message(filters.command("settings") & authorized_only)
async def view_settings(_, m):
    s = await get_session(m.from_user.id)
    await m.reply(f"**Config**\nMode: `{s.watermark_mode}`\nSpeed: `{s.speed}`\nScale: `{s.scale}`\nRes: `{s.resolution}p`")

# --- THUMBNAIL COMMANDS ---
@app.on_message(filters.command("setthumb") & (filters.photo | filters.reply) & authorized_only)
async def set_thumb_handler(c, m):
    sess = await get_session(m.from_user.id)
    
    # Check if message is a photo or reply to a photo
    photo = m.photo or (m.reply_to_message.photo if m.reply_to_message else None)
    if not photo:
        return await m.reply("❌ Send a photo or reply to one with `/setthumb`")

    # Delete old thumb if exists
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        try: os.remove(sess.custom_thumb_path)
        except: pass
        
    # Download new thumb
    path = await c.download_media(photo, file_name=os.path.join(WORK_DIR, f"thumb_{m.from_user.id}.jpg"))
    sess.custom_thumb_path = path
    await m.reply("✅ **Custom Thumbnail Saved!**")

@app.on_message(filters.command("clearthumb") & authorized_only)
async def clear_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        try: os.remove(sess.custom_thumb_path)
        except: pass
    sess.custom_thumb_path = None
    await m.reply("🗑 **Thumbnail Deleted**")

@app.on_message(filters.command("viewthumb") & authorized_only)
async def view_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        await m.reply_photo(sess.custom_thumb_path, caption="🖼 **Your Current Thumbnail**")
    else:
        await m.reply("❌ No custom thumbnail set.")

# --- ENCODING SETTINGS ---
@app.on_message(filters.command("res") & authorized_only)
async def set_res(_, m):
    try:
        val = int(m.command[1])
        if val < 144 or val > 2160: return await m.reply("❌ Invalid Resolution (144-2160)")
        (await get_session(m.from_user.id)).resolution = val
        await m.reply(f"📺 Resolution set to **{val}p**")
    except: await m.reply("Usage: `/res 720`")

@app.on_message(filters.command("crf") & authorized_only)
async def set_crf(_, m):
    try:
        val = int(m.command[1])
        if val < 0 or val > 51: return await m.reply("❌ Range 0-51")
        (await get_session(m.from_user.id)).crf = val
        await m.reply(f"🎨 Quality (CRF) set to **{val}**\n(Lower = Better Quality)")
    except: await m.reply("Usage: `/crf 23`")

@app.on_message(filters.command("codec") & authorized_only)
async def set_codec(_, m):
    sess = await get_session(m.from_user.id)
    try:
        arg = m.command[1]
        if arg == "265":
            sess.codec = "libx265"
            await m.reply("✅ Codec: **H.265 (HEVC)**\n(Small size, slow)")
        elif arg == "264":
            sess.codec = "libx264"
            await m.reply("✅ Codec: **H.264 (AVC)**\n(Fast, compatible)")
        else: await m.reply("Usage: `/codec 264` or `/codec 265`")
    except: await m.reply("Usage: `/codec 264` or `/codec 265`")
        
@app.on_message(filters.text & filters.private & authorized_only)
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        sess.watermark_text = m.text
        sess.step = "waiting_media"
        await m.reply(f"✅ Text Set: `{sess.watermark_text}`\nNow send video.")

@app.on_message((filters.video | filters.document) & filters.private & authorized_only)
async def media_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("⚠️ Set mode first (e.g. /dual)")
    sess.queue.append(m)
    await m.reply("✅ Added to Queue")
    asyncio.create_task(worker(m.from_user.id))

if __name__ == "__main__":
    check_resources()
    app.start()
    print("Bot Started")
    idle()
    app.stop()
