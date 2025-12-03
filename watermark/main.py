#!/usr/bin/env python3
# Async Watermark Bot – Fixed FloodWait + Strict Queue + Codec Select

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

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID", "0")) 
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
WORK_DIR = "downloads"
AUTH_FILE = "auth_users.json"

FILENAME_SUFFIX = " 🦋Vaiᡣ𐭩Su×@pglinsan"

os.makedirs(WORK_DIR, exist_ok=True)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

def get_font(size):
    try: return ImageFont.truetype(FONT_PATH, size)
    except: return ImageFont.load_default()

# ==================== SESSION MANAGER ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    watermark_mode: str = "static" 
    queue: List[Message] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 23
    resolution: int = 720
    custom_thumb_path: str = None 
    codec: str = "libx265"

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

# --- FIXED: DOWNLOAD PROGRESS WITH ANTI-FLOOD ---
async def download_progress(current, total, status_msg, start_time, last_update_ref):
    now = time.time()
    
    # Wait 5 seconds between updates to avoid 420 FLOOD_WAIT
    if (now - last_update_ref[0]) < 5 and current < total:
        return 
    
    last_update_ref[0] = now
    
    try:
        await status_msg.edit_text(f"⬇️ **Downloading...**\n{render_bar(current, total)}")
    except Exception: pass

# ==================== WATERMARK LOGIC ====================
def create_watermark(text: str, target_video_height: int) -> str:
    scale_factor = 3
    base_font_size = int((target_video_height // 12) * scale_factor)
    font = get_font(base_font_size)

    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1,1)))
    bbox = dummy_draw.textbbox((0, 0), text, font=font, stroke_width=0)
    w_raw, h_raw = bbox[2] - bbox[0], bbox[3] - bbox[1]

    text_img = Image.new("RGBA", (w_raw, h_raw + (40 * scale_factor)), (0,0,0,0))
    d_text = ImageDraw.Draw(text_img)
    d_text.text((0, 0), text, font=font, fill="white", stroke_width=0)
    if text_img.getbbox(): text_img = text_img.crop(text_img.getbbox())

    cur_w, cur_h = text_img.size
    distort_w, distort_h = int(cur_w * 1.0), int(cur_h * 1.0)
    text_img = text_img.resize((distort_w, distort_h), Image.Resampling.LANCZOS)

    padding_x, padding_y = int(base_font_size * 0.4), int(base_font_size * 0.2)
    box_w, box_h = distort_w + (padding_x * 2), distort_h + (padding_y * 2)

    bg_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bg_img)
    draw.rounded_rectangle((0, 0, box_w, box_h), radius=box_h // 2, fill=(0, 0, 0, 180))
    
    px, py = (box_w - distort_w) // 2, (box_h - distort_h) // 2
    bg_img.paste(text_img, (px, py), text_img)

    final_w, final_h = int(box_w / scale_factor), int(box_h / scale_factor)
    final_img = bg_img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    wm_path = os.path.join(WORK_DIR, f"wm_{int(time.time())}_{random.randint(1,999)}.png")
    final_img.save(wm_path, "PNG")
    return wm_path

# ==================== PROCESSOR ====================
async def get_video_info(path):
    cmd = ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-of", "json", path]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    try:
        meta = json.loads(stdout)
        stream = meta["streams"][0]
        return int(stream.get("width", 0)), int(stream.get("height", 0)), float(stream.get("duration", 0))
    except: return 0, 0, 0

async def process_video(in_path, text, out_path, crf, resolution, codec, status_msg, mode="static"):
    wm_path = None
    try:
        in_w, in_h, duration = await get_video_info(in_path)
        if duration == 0: duration = 1 
        wm_path = create_watermark(text, resolution)
        
        filter_complex = f"[0:v]scale=-2:{resolution}[bg];"
        last_stream = "[bg]"

        if mode == "static":
            margin = int(resolution * 0.03)
            filter_complex += f"{last_stream}[1:v]overlay=W-w-{margin}:H-h-{margin}"
        else:
            speed_x, speed_y = resolution // 15, resolution // 20
            x_expr = f"abs(mod(t*{speed_x}, 2*(W-w)) - (W-w))"
            y_expr = f"abs(mod(t*{speed_y}, 2*(H-h)) - (H-h))"
            filter_complex += f"{last_stream}[1:v]overlay=x='{x_expr}':y='{y_expr}'"
        
        cmd_args = [
            "ffmpeg", "-y", "-i", in_path, "-i", wm_path, "-filter_complex", filter_complex,
            "-map", "0:a?", 
            "-c:v", codec,             
            "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart"
        ]

        if codec == "libx265":
            hevc_crf = int(crf) + 4
            cmd_args.extend(["-crf", str(hevc_crf)])
            cmd_args.extend(["-tag:v", "hvc1"]) 
        else:
            cmd_args.extend(["-crf", str(crf)])
            cmd_args.extend(["-pix_fmt", "yuv420p"]) 

        cmd_args.append(out_path)

        process = await asyncio.create_subprocess_exec(*cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        last_update_time = time.time()
        
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk: break
            
            chunk_str = chunk.decode('utf-8', errors='ignore')
            
            if "time=" in chunk_str:
                time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", chunk_str)
                if time_match:
                    # Increased to 5 seconds to be safe against FloodWait
                    if time.time() - last_update_time > 5:
                        try:
                            codec_name = "HEVC" if codec == "libx265" else "AVC"
                            await status_msg.edit_text(f"⚙️ **Processing ({codec_name})...**\n{render_bar(time_to_seconds(time_match.group(1)), duration)}")
                            last_update_time = time.time()
                        except: pass
        
        await process.wait()
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception as e:
        logger.error(f"FFmpeg Error: {e}")
        return False
    finally:
        if wm_path and os.path.exists(wm_path): os.remove(wm_path)

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
            
            status_msg = await app.send_message(uid, f"⬇️ **Downloading next video...**")
            dl_path = os.path.join(WORK_DIR, f"in_{uid}_{int(time.time())}.mp4")
            
            try:
                # --- FIXED: USING MUTABLE LIST FOR TIME TRACKING ---
                last_update_time = [0]

                in_path = await app.download_media(
                    message_to_process, 
                    file_name=dl_path, 
                    progress=download_progress, 
                    progress_args=(status_msg, time.time(), last_update_time) 
                )
                
                if not in_path:
                    await status_msg.edit("❌ Download Failed.")
                    continue

                out_path = os.path.join(WORK_DIR, f"out_{uid}_{int(time.time())}_{random.randint(100,999)}.mp4")
                
                await status_msg.edit(f"⏳ **Starting FFmpeg...**")
                
                success = await process_video(
                    in_path, sess.watermark_text, out_path, sess.crf, sess.resolution, 
                    sess.codec, status_msg, mode=sess.watermark_mode
                )
                
                if success:
                    _, _, out_duration = await get_video_info(out_path)
                    
                    thumb = None
                    is_custom_thumb = False
                    
                    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
                        thumb = sess.custom_thumb_path
                        is_custom_thumb = True
                    else:
                        thumb = await generate_thumbnail(out_path)
                        is_custom_thumb = False

                    await status_msg.edit_text(f"📤 **Uploading...**\n{render_bar(0, 100)}")
                    
                    name_root, ext = os.path.splitext(original_name)
                    final_filename = f"{name_root}{FILENAME_SUFFIX}{ext}"
                    final_caption = original_caption if original_caption else f"✅ **Done**"

                    await app.send_video(
                        uid, 
                        out_path, 
                        caption=final_caption, 
                        thumb=thumb, 
                        file_name=final_filename,
                        duration=int(out_duration)
                    )
                    
                    if thumb and not is_custom_thumb:
                        os.remove(thumb)
                else:
                    await status_msg.edit_text("❌ Processing Failed.")
                
                await status_msg.delete()
                
                if os.path.exists(in_path): os.remove(in_path)
                if os.path.exists(out_path): os.remove(out_path)

            except Exception as e:
                logger.error(f"Worker Error: {e}")
                await status_msg.edit(f"❌ Error: {e}")
                
    finally: 
        sess.is_processing = False

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

@app.on_message(filters.command("users") & filters.user(OWNER_ID))
async def list_users(_, m):
    await m.reply(f"**Authorized Users:**\n" + "\n".join([f"`{uid}`" for uid in AUTHORIZED_USERS]))

@app.on_message(filters.command("start"))
async def start_handler(_, m):
    if m.from_user.id not in AUTHORIZED_USERS:
        return await m.reply(f"⛔ **Access Denied**\nYour ID: `{m.from_user.id}`")
    await m.reply(
        "**👋 Watermark Bot v5.1 (Fix FloodWait)**\n"
        "1. /ws - Static Watermark\n"
        "2. /w - Animated Watermark\n"
        "3. /codec 264 - Fast Mode\n"
        "4. /codec 265 - High Compress Mode\n"
        "5. /settings - Check current settings"
    )

@app.on_message(filters.command("setthumb") & (filters.photo | filters.reply) & authorized_only)
async def set_thumb_handler(c, m):
    sess = await get_session(m.from_user.id)
    photo = None
    if m.photo:
        photo = m.photo
    elif m.reply_to_message and m.reply_to_message.photo:
        photo = m.reply_to_message.photo
    else:
        return await m.reply("❌ Send a photo with `/setthumb` or reply to a photo.")
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        os.remove(sess.custom_thumb_path)
    path = await c.download_media(photo, file_name=os.path.join(WORK_DIR, f"thumb_{m.from_user.id}.jpg"))
    sess.custom_thumb_path = path
    await m.reply("✅ **Custom Thumbnail Saved!**")

@app.on_message(filters.command("clearthumb") & authorized_only)
async def clear_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        os.remove(sess.custom_thumb_path)
    sess.custom_thumb_path = None
    await m.reply("🗑 **Thumbnail Deleted.**")

@app.on_message(filters.command("viewthumb") & authorized_only)
async def view_thumb_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.custom_thumb_path and os.path.exists(sess.custom_thumb_path):
        await m.reply_photo(sess.custom_thumb_path, caption="🖼 **Your Custom Thumbnail**")
    else:
        await m.reply("❌ You don't have a custom thumbnail set.")

@app.on_message(filters.command("w") & authorized_only)
async def set_animated(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "animated"
    await m.reply("✨ **Animated Mode**\nSend the watermark text:")

@app.on_message(filters.command("ws") & authorized_only)
async def set_static(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.watermark_mode = "static"
    await m.reply("📍 **Static Mode**\nSend the watermark text:")

@app.on_message(filters.command("codec") & authorized_only)
async def set_codec(_, m):
    try:
        sess = await get_session(m.from_user.id)
        arg = m.command[1] if len(m.command) > 1 else ""
        
        if arg == "265":
            sess.codec = "libx265"
            await m.reply("✅ Codec set to **H.265 (HEVC)**.\nSmaller files, slower speed.")
        elif arg == "264":
            sess.codec = "libx264"
            await m.reply("✅ Codec set to **H.264 (AVC)**.\nFaster speed, standard compatibility.")
        else:
            await m.reply("Usage:\n`/codec 264` (Fast)\n`/codec 265` (Small Size)")
    except: await m.reply("Error setting codec.")

@app.on_message(filters.command("settings") & authorized_only)
async def settings_handler(_, m):
    sess = await get_session(m.from_user.id)
    thumb_status = "✅ Set" if sess.custom_thumb_path else "❌ Auto"
    c_name = "HEVC (H.265)" if sess.codec == "libx265" else "AVC (H.264)"
    await m.reply(f"**Settings**\nMode: `{sess.watermark_mode}`\nCodec: `{c_name}`\nCRF: {sess.crf}\nRes: {sess.resolution}p\nThumb: {thumb_status}")

@app.on_message(filters.command("crf") & authorized_only)
async def set_crf(_, m):
    try:
        sess = await get_session(m.from_user.id)
        sess.crf = max(0, min(int(m.command[1]), 51))
        await m.reply(f"✅ CRF: {sess.crf}")
    except: await m.reply("Usage: /crf 23")

@app.on_message(filters.command("res") & authorized_only)
async def set_res(_, m):
    try:
        sess = await get_session(m.from_user.id)
        sess.resolution = int(m.command[1])
        await m.reply(f"✅ Res: {sess.resolution}p")
    except: await m.reply("Usage: /res 720")

@app.on_message(filters.text & filters.private & authorized_only)
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        sess.watermark_text = m.text[:50]
        sess.step = "waiting_media"
        await m.reply(f"✅ Text Set: `{sess.watermark_text}`\nNow send video.")

@app.on_message((filters.video | filters.document) & filters.private & authorized_only)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("⚠️ Use /ws or /w first.")
    
    if m.document and "video" not in m.document.mime_type: return await m.reply("❌ Not a video.")

    sess.queue.append(m)
    
    position = len(sess.queue)
    if position == 1 and not sess.is_processing:
        await m.reply("✅ **Added to Queue** (Starting now...)")
    else:
        await m.reply(f"✅ **Added to Queue** (Position: {position})")
        
    asyncio.create_task(worker(m.from_user.id))

if __name__ == "__main__":
    check_resources()
    print("Bot is starting...")
    app.start()
    
    try:
        app.send_message(OWNER_ID, "Hey Vaisu welcome back")
    except Exception as e:
        print(f"Startup message failed: {e}")
    
    print("Bot is now running.")
    idle()
    app.stop()
