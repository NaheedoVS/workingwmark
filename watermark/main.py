#!/usr/bin/env python3
# Async Watermark Bot – Dual Mode & Lissajous
# 1. Static: Preserved (Res/18, Pill Shape, Black Box)
# 2. Moving: Red Text Only, Lissajous Animation
# 3. Dual: Both active simultaneously

import os
import time
import math
import json
import asyncio
import logging
import random
import re
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
# Expects a font file in the root directory or downloads a fallback
FONT_PATH = "arial.ttf" 

def check_resources():
    if not os.path.exists(FONT_PATH):
        print(f"⚠️ Local font '{FONT_PATH}' not found. Watermarks might look generic.")
    else:
        print(f"✅ Found font: {FONT_PATH}")

# ==================== SESSION MANAGER ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    
    # Watermark Data
    watermark_text: str = ""        # For Moving
    watermark_mode: str = "static"  # static, moving, dual
    
    # Video Settings
    crf: int = 23
    resolution: int = 720
    codec: str = "libx265"
    custom_thumb_path: str = None 
    
    # Animation Settings
    speed: float = 1.0              # Speed multiplier
    scale: float = 1.0              # Size multiplier for moving text
    custom_size: Optional[Tuple[int, int]] = None # (W, H) override
    
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

# ==================== PROGRESS HELPERS ====================
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
    
    t_w = bbox[2] - bbox[0]
    t_h = bbox[3] - bbox[1]
    y_off = bbox[1]

    if style == "static":
        # === 1. STATIC: PILL SHAPE, BLACK BOX (Unchanged) ===
        px, py = 40, 20
        w, h = t_w + px, t_h + py
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Black Pill Box
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=20, fill=(0, 0, 0, 180))
        
        # White Text
        draw.text(((w - t_w)//2, (h - t_h)//2 - y_off), text, font=font, fill="white")
        return img

    else:
        # === 2. MOVING: RED TEXT, NO BOX (New) ===
        px, py = 10, 10 # Minimal padding
        w, h = t_w + px, t_h + py
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Red Text Only (#FF0000)
        draw.text(((w - t_w)//2, (h - t_h)//2 - y_off), text, font=font, fill=(255, 0, 0, 255))
        return img

# ==================== VIDEO PROCESSING (FIXED) ====================
async def get_video_info(path):
    # Probing the video to get width/height/duration
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
        
        # SAFETY CHECK: If video analysis failed, stop here
        if vw == 0 or vh == 0:
            logger.error("❌ FFprobe failed to detect video dimensions.")
            return False
        if dur == 0: dur = 1
        
        inputs = ["-i", in_path]
        filter_parts = []
        
        # Scale Base Video to output resolution (must be even)
        res = make_even(sess.resolution)
        filter_parts.append(f"[0:v]scale=-2:{res}[bg]")
        curr = "[bg]"
        
        # --- PREPARE IMAGES ---
        
        # 1. Static Image Logic
        if sess.watermark_mode in ["static", "dual"]:
            raw_s = await asyncio.to_thread(create_watermark, FIXED_STATIC_TEXT, "static")
            h_s = make_even(res / 18)
            # Prevent 0px height crash
            h_s = max(2, h_s) 
            w_s = make_even(h_s * (raw_s.width / raw_s.height))
            img_s = await asyncio.to_thread(raw_s.resize, (w_s, h_s), RESAMPLE_MODE)
            await asyncio.to_thread(img_s.save, wm_s_path)
            inputs.extend(["-i", wm_s_path])

        # 2. Moving Image Logic (Handles 'moving' AND 'lissajous')
        if sess.watermark_mode in ["moving", "lissajous", "dual"]:
            txt = sess.watermark_text if sess.watermark_text else "Watermark"
            raw_m = await asyncio.to_thread(create_watermark, txt, "moving")
            
            if sess.custom_size:
                w_m, h_m = sess.custom_size
            else:
                h_m = make_even((res / 25) * sess.scale)
                h_m = max(2, h_m) # Safety
                w_m = make_even(h_m * (raw_m.width / raw_m.height))
            
            img_m = await asyncio.to_thread(raw_m.resize, (w_m, h_m), RESAMPLE_MODE)
            await asyncio.to_thread(img_m.save, wm_m_path)
            inputs.extend(["-i", wm_m_path])

        # --- BUILD FILTER CHAIN ---
        sp = sess.speed
        lissa_cmd = f"x='(W-w)/2 + (W-w)/3*sin(t*{sp})':y='(H-h)/2 + (H-h)/3*cos(t*{sp}*2.2)'"
        static_cmd = "x=W-w-20:y=H-h-20"

        if sess.watermark_mode == "static":
            filter_parts.append(f"{curr}[1:v]overlay={static_cmd}")
            
        elif sess.watermark_mode in ["moving", "lissajous"]:
            filter_parts.append(f"{curr}[1:v]overlay={lissa_cmd}")
            
        elif sess.watermark_mode == "dual":
            # Input 1 (Static) -> [v1]
            filter_parts.append(f"{curr}[1:v]overlay={static_cmd}[v1]")
            # Input 2 (Moving) on [v1] -> Out
            filter_parts.append(f"[v1][2:v]overlay={lissa_cmd}")

        # --- EXECUTE FFMPEG ---
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
            # Capture Progress
            try:
                line = chunk.decode('utf-8', 'ignore')
                # Print errors to logs for debugging
                if "Error" in line or "Invalid" in line:
                    logger.error(f"FFmpeg Output: {line.strip()}")
                    
                if "time=" in line:
                    tm = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", line)
                    if tm:
                        await safe_edit(status_msg, f"⚙️ **Encoding...**\n{render_bar(time_to_seconds(tm.group(1)), dur)}", last_upd)
            except: pass
        
        await proc.wait()
        
        # Verification
        if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
            return True
        else:
            logger.error("❌ Output file not found or empty.")
            return False
        
    except Exception as e:
        logger.error(f"Process Exception: {e}")
        return False
    finally:
        if os.path.exists(wm_s_path): os.remove(wm_s_path)
        if os.path.exists(wm_m_path): os.remove(wm_m_path)

# ==================== HANDLERS & MAIN ====================
app = Client("WatermarkBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- CONFIG COMMANDS ---
@app.on_message(filters.command("setthumb") & (filters.photo | filters.reply) & authorized_only)
async def set_thumb(c, m):
    sess = await get_session(m.from_user.id)
    photo = m.photo or (m.reply_to_message.photo if m.reply_to_message else None)
    if not photo: return await m.reply("❌ Send/Reply to photo")
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        os.remove(sess.custom_thumb_path)
    path = await c.download_media(photo, file_name=f"{WORK_DIR}/thumb_{m.from_user.id}.jpg")
    sess.custom_thumb_path = path
    await m.reply("✅ Thumbnail Saved")

@app.on_message(filters.command("clearthumb") & authorized_only)
async def clear_thumb(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path: os.remove(sess.custom_thumb_path)
    sess.custom_thumb_path = None
    await m.reply("🗑 Thumbnail Removed")

@app.on_message(filters.command("res") & authorized_only)
async def set_res(_, m):
    try: (await get_session(m.from_user.id)).resolution = int(m.command[1])
    except: return await m.reply("Usage: `/res 720`")
    await m.reply(f"📺 Res: {m.command[1]}p")

@app.on_message(filters.command("crf") & authorized_only)
async def set_crf(_, m):
    try: (await get_session(m.from_user.id)).crf = int(m.command[1])
    except: return await m.reply("Usage: `/crf 23`")
    await m.reply(f"🎨 CRF: {m.command[1]}")

@app.on_message(filters.command("codec") & authorized_only)
async def set_codec(_, m):
    try:
        c = "libx265" if m.command[1] == "265" else "libx264"
        (await get_session(m.from_user.id)).codec = c
        await m.reply(f"💿 Codec: {c}")
    except: await m.reply("Usage: `/codec 264` or `265`")

@app.on_message(filters.command("speed") & authorized_only)
async def set_speed(_, m):
    try:
        (await get_session(m.from_user.id)).speed = float(m.command[1])
        await m.reply(f"🚀 Speed: {m.command[1]}")
    except: await m.reply("Usage: `/speed 1.5`")

@app.on_message(filters.command("scale") & authorized_only)
async def set_scale(_, m):
    try:
        (await get_session(m.from_user.id)).scale = float(m.command[1])
        await m.reply(f"🔍 Scale: {m.command[1]}")
    except: await m.reply("Usage: `/scale 1.5`")

@app.on_message(filters.command("size") & authorized_only)
async def set_size(_, m):
    try:
        w, h = int(m.command[1]), int(m.command[2])
        (await get_session(m.from_user.id)).custom_size = (w, h)
        await m.reply(f"📏 Fixed Size: {w}x{h}")
    except: await m.reply("Usage: `/size 200 100`")

# --- MODE COMMANDS ---
@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply(
        "**Watermark Bot v7.1 (Fix)**\n"
        "1. `/dual` - Static + Moving\n"
        "2. `/moving` - Moving Only (Red, Lissajous)\n"
        "3. `/ws` - Static Only\n"
        "**Config:** `/res`, `/crf`, `/codec`, `/speed`, `/scale`, `/setthumb`"
    )

@app.on_message(filters.command("dual") & authorized_only)
async def dual_mode(c, m):
    s = await get_session(m.from_user.id)
    s.reset()
    s.watermark_mode = "dual"
    await m.reply("✨ **Dual Mode Activated**\nStatic: Locked\nSend text for the **Moving Red Watermark**:")

@app.on_message(filters.command(["lissajous", "moving"]) & authorized_only)
async def moving_mode(c, m):
    s = await get_session(m.from_user.id)
    s.reset()
    # Normalize to 'moving' so the processor understands it
    s.watermark_mode = "moving" 
    await m.reply("🔴 **Moving Mode** (Lissajous)\nSend text:")

@app.on_message(filters.command("ws") & authorized_only)
async def static_mode(c, m):
    s = await get_session(m.from_user.id)
    s.reset()
    s.watermark_mode = "static"
    await m.reply(f"📍 **Static Mode**\nLocked to `{FIXED_STATIC_TEXT}`\nSend video now.")
    s.step = "waiting_media"

@app.on_message(filters.command("settings") & authorized_only)
async def view_settings(_, m):
    s = await get_session(m.from_user.id)
    await m.reply(f"**Config**\nMode: `{s.watermark_mode}`\nSpeed: `{s.speed}`\nScale: `{s.scale}`\nRes: `{s.resolution}p`")

@app.on_message(filters.text & filters.private & authorized_only)
async def get_text(_, m):
    s = await get_session(m.from_user.id)
    if s.step == "waiting_text":
        s.watermark_text = m.text
        s.step = "waiting_media"
        await m.reply(f"✅ Text: `{m.text}`\nNow send video.")

@app.on_message((filters.video | filters.document) & filters.private & authorized_only)
async def get_video(_, m):
    s = await get_session(m.from_user.id)
    if s.step != "waiting_media": return await m.reply("⚠️ Select mode first")
    s.queue.append(m)
    await m.reply(f"✅ Added to Queue (Pos: {len(s.queue)})")
    asyncio.create_task(worker(m.from_user.id))

async def worker(uid):
    s = await get_session(uid)
    if s.is_processing: return
    s.is_processing = True
    try:
        while s.queue:
            msg = s.queue.pop(0)
            st = await app.send_message(uid, "⬇️ Downloading...")
            dl = f"{WORK_DIR}/in_{uid}_{time.time()}.mp4"
            out = f"{WORK_DIR}/out_{uid}_{time.time()}.mp4"
            try:
                if not await app.download_media(msg, file_name=dl): continue
                await st.edit("⏳ Encoding...")
                
                # Run Processing
                success = await process_video(dl, out, s, st)
                
                if success:
                    th = s.custom_thumb_path or f"{dl}.jpg"
                    if not s.custom_thumb_path:
                        await asyncio.create_subprocess_exec("ffmpeg","-i",out,"-ss","2","-vframes","1",th)
                    await st.edit("📤 Uploading...")
                    await app.send_video(uid, out, caption="✅ Done", thumb=th if os.path.exists(th) else None)
                    if not s.custom_thumb_path and os.path.exists(th): os.remove(th)
                else:
                    await st.edit("❌ Encoding Failed\n(Check Logs)")
            finally:
                if os.path.exists(dl): os.remove(dl)
                if os.path.exists(out): os.remove(out)
                await st.delete()
    finally: s.is_processing = False

if __name__ == "__main__":
    check_resources()
    app.start()
    print("Bot Running")
    idle()
    app.stop()
