#!/usr/bin/env python3
# Async Watermark Bot – Final Corrected Version
# 1. Static Shape: Pill Box (Radius 20, Padding 40/20)
# 2. Static Text: Locked to "🦋Vaiᡣ𐭩Su" (Symbola Font)
# 3. Static Scaling: Resolution / 18

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

# ==================== RESOURCES (Symbola for 🦋 and 𐭩) ====================
FONT_URL = "https://github.com/shaunweingarten/fonts/raw/master/Symbola.ttf"
FONT_PATH = os.path.join(WORK_DIR, "Symbola.ttf")

def check_resources():
    if not os.path.exists(FONT_PATH):
        print(f"⏳ Downloading Symbola Font (for 🦋 and 𐭩)...")
        try:
            opener = urllib.request.build_opener()
            opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
            urllib.request.install_opener(opener)
            urllib.request.urlretrieve(FONT_URL, FONT_PATH)
            print("✅ Font Downloaded.")
        except Exception as e:
            print(f"❌ Font Download Failed: {e}")

# ==================== SESSION MANAGER ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    second_watermark_text: str = "" 
    watermark_mode: str = "static" 
    queue: List[Message] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 23
    resolution: int = 720
    custom_thumb_path: str = None 
    codec: str = "libx265"
    custom_size: Optional[Tuple[int, int]] = None
    speed_factor: float = 1.0
    watermark_scale: float = 1.0

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""
        self.second_watermark_text = ""
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
    except Exception as e: logger.error(f"Edit failed: {e}")

async def download_progress(current, total, status_msg, start_time, last_update_ref):
    if current == total: pass
    elif (time.time() - last_update_ref[0]) < UPDATE_INTERVAL: return
    text = f"⬇️ **Downloading...**\n{render_bar(current, total)}"
    await safe_edit(status_msg, text, last_update_ref)


# ==================== WATERMARK GENERATION (YOUR CODE) ====================
def create_watermark(text: str, style: str = "static"):
    # Generates a high-quality base image (Font Size 80)
    # This will be resized later based on resolution
    font_size = 80
    font = ImageFont.load_default()
    
    # Try loading custom fonts (Prioritize Symbola for 🦋 and 𐭩)
    for p in [FONT_PATH, "fonts/Symbola.ttf", "Symbola.ttf", "arialbd.ttf"]:
        if os.path.exists(p):
            try: 
                font = ImageFont.truetype(p, font_size)
                break
            except: continue

    # Determine Padding/Style
    if style == "static":
        # YOUR SPECIFIC STATIC LOGIC
        px, py = 40, 20
        # Calculate size based on text
        dummy = Image.new("RGBA", (1, 1))
        d = ImageDraw.Draw(dummy)
        bbox = d.textbbox((0, 0), text, font=font)
        
        w, h = bbox[2] - bbox[0] + px, bbox[3] - bbox[1] + py
        
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw pill shape (Radius 20, Black 180)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=20, fill=(0, 0, 0, 180))
        
        # Draw Text centered relative to padding
        # Note: bbox[0] and bbox[1] offsets are needed for precise centering
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_pos = (w - text_w) // 2
        y_pos = (h - text_h) // 2 - bbox[1] # Subtract offset to realign
        
        draw.text((x_pos, y_pos), text, font=font, fill=(255, 255, 255, 255))
        return img

    else:
        # MOVING STYLE (Red Text, Minimal Padding)
        px, py = 4, 4
        dummy = Image.new("RGBA", (1, 1))
        d = ImageDraw.Draw(dummy)
        bbox = d.textbbox((0, 0), text, font=font)
        
        w, h = bbox[2] - bbox[0] + px, bbox[3] - bbox[1] + py
        
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_pos = (w - text_w) // 2
        y_pos = (h - text_h) // 2 - bbox[1]
        
        draw.text((x_pos, y_pos), text, font=font, fill=(255, 0, 0, 255))
        return img

# ==================== VIDEO PROCESSING ====================
async def get_video_info(path):
    cmd = ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-of", "json", path]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    try:
        meta = json.loads(stdout)
        stream = meta["streams"][0]
        return int(stream.get("width", 0)), int(stream.get("height", 0)), float(stream.get("duration", 0))
    except: return 0, 0, 0

async def process_video(in_path, text, out_path, sess, status_msg):
    pid = os.getpid()
    ts = int(time.time())
    
    wm_path = f"{WORK_DIR}/wm_{ts}_{pid}.png"
    wm2_path = f"{WORK_DIR}/wm2_{ts}_{pid}.png"
    
    try:
        in_w, in_h, duration = await get_video_info(in_path)
        if duration == 0: duration = 1 
        
        # --- SIZE CALCULATION LOGIC ---
        def get_target_size(base_img, is_static_style=True):
            # 1. FIXED STATIC SIZE LOGIC (Resolution / 18)
            if is_static_style:
                target_height = int(sess.resolution / 18) 
                # Calculate width based on aspect ratio
                aspect = base_img.width / base_img.height
                target_width = int(target_height * aspect)
                return target_width, target_height
            
            # 2. MOVING SIZE LOGIC (Variable Scale)
            else:
                if sess.custom_size:
                    return sess.custom_size
                else:
                    base_height = sess.resolution / 22
                    scaled_height = base_height * sess.watermark_scale
                    t_h = int(scaled_height)
                    t_w = int(t_h * (base_img.width / base_img.height))
                    return t_w, t_h

        if sess.watermark_mode == "dual":
            # 1. Static Part (LOCKED TEXT)
            wm_static_raw = await asyncio.to_thread(create_watermark, FIXED_STATIC_TEXT, style="static")
            w1, h1 = get_target_size(wm_static_raw, is_static_style=True)
            wm_static = await asyncio.to_thread(wm_static_raw.resize, (w1, h1), RESAMPLE_MODE)
            await asyncio.to_thread(wm_static.save, wm_path)

            # 2. Moving Part (User Text - Red)
            wm_move_raw = await asyncio.to_thread(create_watermark, sess.second_watermark_text, style="moving")
            w2, h2 = get_target_size(wm_move_raw, is_static_style=False)
            wm_move = await asyncio.to_thread(wm_move_raw.resize, (w2, h2), RESAMPLE_MODE)
            await asyncio.to_thread(wm_move.save, wm2_path)
            
            div_x = 3.7 / sess.speed_factor
            div_y = 2.3 / sess.speed_factor
            
            filter_complex = (
                f"[0:v]scale=-2:{sess.resolution}[bg];"
                f"[bg][1:v]overlay=x=W-w:y=H-h[v1];"
                f"[v1][2:v]overlay=x='(W-w)/2+(W-w)/2*sin(t/{div_x})':y='(H-h)/2+(H-h)/2*cos(t/{div_y})'"
            )
            inputs = ["-i", in_path, "-i", wm_path, "-i", wm2_path]

        else:
            # Single Mode
            if sess.watermark_mode == "random":
                # Moving Mode -> Use User Text
                style = "moving"
                is_static = False
                wm_text_to_use = text
            elif sess.watermark_mode == "static":
                # Static Mode -> FORCE FIXED TEXT
                style = "static"
                is_static = True
                wm_text_to_use = FIXED_STATIC_TEXT
            else:
                # Animated/Slide Mode
                style = "static" 
                is_static = True
                wm_text_to_use = FIXED_STATIC_TEXT

            wm_full = await asyncio.to_thread(create_watermark, wm_text_to_use, style=style)
            w, h = get_target_size(wm_full, is_static_style=is_static)
            wm = await asyncio.to_thread(wm_full.resize, (w, h), RESAMPLE_MODE)
            await asyncio.to_thread(wm.save, wm_path)
            
            if sess.watermark_mode == "random":
                div_x = 3.7 / sess.speed_factor
                div_y = 2.3 / sess.speed_factor
                overlay_cmd = f"x='(W-w)/2+(W-w)/2*sin(t/{div_x})':y='(H-h)/2+(H-h)/2*cos(t/{div_y})'"
            elif sess.watermark_mode == "slide" or sess.watermark_mode == "animated":
                 cycle_duration = 30.0 / sess.speed_factor
                 overlay_cmd = f"x='-w+((W+w)*((mod(t,{cycle_duration}))/{cycle_duration}))':y=H-h"
            else:
                 # Static: Absolute Bottom Right
                 overlay_cmd = "x=W-w:y=H-h"
            
            filter_complex = f"[0:v]scale=-2:{sess.resolution}[bg];[bg][1:v]overlay={overlay_cmd}"
            inputs = ["-i", in_path, "-i", wm_path]

        cmd_args = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
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
        if os.path.exists(wm2_path): os.remove(wm2_path)

async def generate_thumbnail(video_path):
    thumb_path = f"{video_path}.jpg"
    cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:02", "-vframes", "1", "-vf", "scale=320:-1", thumb_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return thumb_path if os.path.exists(thumb_path) else None

# ==================== WORKER ====================
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
            
            status_msg = await app.send_message(uid, f"⬇️ **Downloading next video...**")
            dl_path = os.path.join(WORK_DIR, f"in_{uid}_{int(time.time())}.mp4")
            
            try:
                last_update_time = [0]
                in_path = await app.download_media(
                    message_to_process, file_name=dl_path, 
                    progress=download_progress, progress_args=(status_msg, time.time(), last_update_time)
                )
                
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

                    await app.send_video(
                        uid, out_path, caption=final_caption, thumb=thumb, 
                        file_name=final_filename, duration=int(out_duration)
                    )
                    
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

@app.on_message(filters.command("start"))
async def start_handler(_, m):
    if m.from_user.id not in AUTHORIZED_USERS:
        return await m.reply(f"⛔ **Access Denied**\nYour ID: `{m.from_user.id}`")
    await m.reply(
        "**👋 Watermark Bot vFinal (Symbola Fix)**\n"
        "**Modes:**\n"
        "1. `/dual` - Static (Locked) + Moving\n"
        "2. `/ws` - Static Mode (Locked Text)\n"
        "3. `/w` - Animated Mode (Locked Text)\n"
        "4. `/wr` - Random Red Mode (Custom Text)\n\n"
        "**Settings:**\n"
        "5. `/res <720>` - Resolution\n"
        "6. `/crf <23>` - Quality (0-51)\n"
        "7. `/speed <x>` - Speed\n"
        "8. `/scale <x>` - Size Scale (Moving Only)\n\n"
        "**Extras:**\n"
        "9. `/setthumb` - Set Thumbnail\n"
        "10. `/clearthumb` - Delete Thumbnail\n"
        "11. `/settings` - Check Config\n"
        "12. `/reset` - Reset Defaults"
    )

# --- THUMBNAIL COMMANDS ---

@app.on_message(filters.command("setthumb") & (filters.photo | filters.reply) & authorized_only)
async def set_thumb_handler(c, m):
    sess = await get_session(m.from_user.id)
    photo = m.photo
    if not photo and m.reply_to_message:
        photo = m.reply_to_message.photo
        
    if not photo:
        return await m.reply("❌ **Send a photo** or **Reply to a photo** with `/setthumb`.")
        
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        try: os.remove(sess.custom_thumb_path)
        except: pass
        
    path = await c.download_media(photo, file_name=os.path.join(WORK_DIR, f"thumb_{m.from_user.id}.jpg"))
    sess.custom_thumb_path = path
    await m.reply("✅ **Custom Thumbnail Saved!**")

@app.on_message(filters.command(["clearthumb", "removethumb"]) & authorized_only)
async def clear_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        try: os.remove(sess.custom_thumb_path)
        except: pass
    sess.custom_thumb_path = None
    await m.reply("🗑 **Thumbnail Deleted.**")

@app.on_message(filters.command("viewthumb") & authorized_only)
async def view_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        await m.reply_photo(sess.custom_thumb_path, caption="🖼 **Your Custom Thumbnail**")
    else:
        await m.reply("❌ **No custom thumbnail set.**")

# --------------------------

@app.on_message(filters.command("dual") & authorized_only)
async def set_dual(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "dual"
    await m.reply(
        "✨ **Dual Mode**\n"
        f"Static: Locked to `{FIXED_STATIC_TEXT}`\n"
        "Send the text for the **Moving (Red)** watermark:"
    )

@app.on_message(filters.command("w") & authorized_only)
async def set_animated(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "animated"
    await m.reply(f"✨ **Animated Slide Mode**\nLocked to: `{FIXED_STATIC_TEXT}`\nJust send video now.")
    sess.step = "waiting_media"

@app.on_message(filters.command("ws") & authorized_only)
async def set_static(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "static"
    await m.reply(f"📍 **Static Mode**\nLocked to: `{FIXED_STATIC_TEXT}`\nJust send video now.")
    sess.step = "waiting_media"

@app.on_message(filters.command("wr") & authorized_only)
async def set_random(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "random"
    await m.reply("🟥 **Red Moving Text Mode**\n(No BG, No Stroke)\nSend the watermark text:")

@app.on_message(filters.command("speed") & authorized_only)
async def set_speed(_, m):
    try:
        if len(m.command) != 2: return await m.reply("Usage: `/speed 1.5`")
        val = float(m.command[1])
        if val <= 0.1: return await m.reply("❌ Speed must be > 0.1")
        sess = await get_session(m.from_user.id)
        sess.speed_factor = val
        await m.reply(f"🚀 **Speed Factor set to: {val}x**")
    except ValueError:
        await m.reply("❌ Invalid number.")

@app.on_message(filters.command("res") & authorized_only)
async def set_res(_, m):
    sess = await get_session(m.from_user.id)
    try:
        if len(m.command) != 2: return await m.reply("Usage: `/res 720`")
        val = int(m.command[1])
        if val < 144 or val > 2160: return await m.reply("❌ Range: 144 - 2160")
        sess.resolution = val
        await m.reply(f"🖥 **Resolution set to: {val}p**")
    except: await m.reply("❌ Invalid number.")

@app.on_message(filters.command("crf") & authorized_only)
async def set_crf(_, m):
    sess = await get_session(m.from_user.id)
    try:
        if len(m.command) != 2: return await m.reply("Usage: `/crf 23`")
        val = int(m.command[1])
        if val < 0 or val > 51: return await m.reply("❌ Range: 0 (Lossless) - 51 (Worst)")
        sess.crf = val
        await m.reply(f"🎨 **Quality (CRF) set to: {val}**")
    except: await m.reply("❌ Invalid number.")

@app.on_message(filters.command("scale") & authorized_only)
async def set_scale(_, m):
    sess = await get_session(m.from_user.id)
    try:
        if len(m.command) != 2: return await m.reply("Usage: `/scale 1.2`")
        val = float(m.command[1])
        if val < 0.1 or val > 5.0: return await m.reply("❌ Range: 0.1 - 5.0")
        sess.watermark_scale = val
        await m.reply(f"🔍 **Watermark Scale set to: {val}x**")
    except: await m.reply("❌ Invalid number.")

@app.on_message(filters.command("reset") & authorized_only)
async def reset_settings(_, m):
    sess = await get_session(m.from_user.id)
    sess.custom_size = None
    sess.speed_factor = 1.0
    sess.watermark_scale = 1.0 
    sess.crf = 23
    sess.resolution = 720
    sess.codec = "libx265"
    sess.watermark_mode = "static" 
    await m.reply("🔄 **All Settings Reset to Defaults.**")

@app.on_message(filters.command("codec") & authorized_only)
async def set_codec(_, m):
    try:
        sess = await get_session(m.from_user.id)
        arg = m.command[1] if len(m.command) > 1 else ""
        if arg == "265":
            sess.codec = "libx265"
            await m.reply("✅ Codec: **H.265 (HEVC)**")
        elif arg == "264":
            sess.codec = "libx264"
            await m.reply("✅ Codec: **H.264 (AVC)**")
        else: await m.reply("Usage: `/codec 264` or `/codec 265`")
    except: pass

@app.on_message(filters.command("settings") & authorized_only)
async def settings_handler(_, m):
    sess = await get_session(m.from_user.id)
    sz = f"{sess.custom_size[0]}x{sess.custom_size[1]}" if sess.custom_size else "Auto"
    thumb_status = "✅ Set" if sess.custom_thumb_path else "❌ None"
    await m.reply(f"**Settings**\nMode: `{sess.watermark_mode}`\nRes: `{sess.resolution}p`\nCRF: `{sess.crf}`\nSpeed: `{sess.speed_factor}x`\nScale: `{sess.watermark_scale}x`\nSize: `{sz}`\nCodec: `{sess.codec}`\nThumb: {thumb_status}")

@app.on_message(filters.text & filters.private & authorized_only)
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        if sess.watermark_mode == "dual":
            sess.watermark_text = FIXED_STATIC_TEXT
            sess.second_watermark_text = m.text.strip()
            sess.step = "waiting_media"
            await m.reply(f"✅ **Dual Text Set!**\nStatic (Locked): `{sess.watermark_text}`\nMoving: `{sess.second_watermark_text}`\nNow send video.")
        elif sess.watermark_mode == "random":
            sess.watermark_text = m.text[:50]
            sess.step = "waiting_media"
            await m.reply(f"✅ Text Set: `{sess.watermark_text}`\nNow send video.")
        else:
            await m.reply(f"⚠️ Text is locked to `{FIXED_STATIC_TEXT}` for this mode. Send video.")

@app.on_message((filters.video | filters.document) & filters.private & authorized_only)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("⚠️ Use a mode command first (e.g., /dual).")
    if m.document and "video" not in m.document.mime_type: return await m.reply("❌ Not a video.")
    sess.queue.append(m)
    pos = len(sess.queue)
    msg = "✅ **Added to Queue** (Starting now...)" if (pos == 1 and not sess.is_processing) else f"✅ **Added to Queue** (Pos: {pos})"
    await m.reply(msg)
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
