#!/usr/bin/env python3
# Watermark Bot – Smart Queue (Download-Process-Delete Cycle)

import os, time, json, asyncio, logging, shutil, re
from dataclasses import dataclass, field
from typing import List
from PIL import Image, ImageDraw, ImageFont

from pyrogram import Client, filters, errors, types

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

WORK_DIR = "downloads"
if os.path.exists(WORK_DIR): shutil.rmtree(WORK_DIR)
os.makedirs(WORK_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== SYSTEM ====================
def get_ffmpeg_path():
    if os.path.exists("/usr/bin/ffmpeg"): return "/usr/bin/ffmpeg"
    path = shutil.which("ffmpeg")
    if not path: raise FileNotFoundError("FFmpeg not found!")
    return path

FFMPEG_BIN = get_ffmpeg_path()
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS

def check_disk_space(required_gb=1.5):
    total, used, free = shutil.disk_usage(".")
    return (free / (2**30)) > required_gb

# ==================== SESSION ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    # Queue now stores the Message object, not the file path
    queue: List[types.Message] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 23
    resolution: int = 720
    style: str = "static"

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""
        self.queue.clear()

session_manager = {}
session_lock = asyncio.Lock()

async def get_session(uid):
    async with session_lock:
        return session_manager.setdefault(uid, UserSession(uid))

# ==================== PROGRESS ====================
async def progress_bar(current, total, status_msg, action_desc):
    if total == 0: return
    pct = int(current * 100 / total)
    if getattr(progress_bar, "last_pct", 0) // 5 == pct // 5 and pct != 100: return
    progress_bar.last_pct = pct
    bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
    try: await status_msg.edit_text(f"{action_desc}...\n[{bar}] {pct}%")
    except: pass

# ==================== WATERMARK ENGINE ====================
def create_watermark(text: str):
    font_size = 80
    font = ImageFont.load_default()
    for p in ["fonts/Roboto-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            try: font = ImageFont.truetype(p, font_size); break
            except: continue

    dummy = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)
    px, py = 40, 20
    w, h = bbox[2] - bbox[0] + px, bbox[3] - bbox[1] + py
    
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=20, fill=(0, 0, 0, 180))
    draw.text((px // 2, py // 2), text, font=font, fill=(255, 255, 255, 255))
    return img

# ==================== HELPERS ====================
def time_to_seconds(t):
    try:
        h, m, s = t.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except: return 0

async def get_duration(path):
    cmd = [FFMPEG_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try: return float(out.decode().strip())
    except: return 0

async def generate_thumbnail(in_path):
    out = f"{in_path}.jpg"
    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", "-i", in_path, "-ss", "00:00:02", "-vframes", "1", "-vf", "scale=320:-1", out]
    await (await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).wait()
    return out if os.path.exists(out) else None

# ==================== PROCESSOR ====================
async def process_video(in_path, text, out_path, crf, resolution, style, status_msg, total_duration):
    wm_path = f"{WORK_DIR}/wm_{int(time.time())}_{os.getpid()}.png"
    try:
        wm_full = await asyncio.to_thread(create_watermark, text)
        target_wm_height = int(resolution / 18)
        target_wm_width = int(target_wm_height * (wm_full.width / wm_full.height))
        wm = await asyncio.to_thread(wm_full.resize, (target_wm_width, target_wm_height), RESAMPLE_MODE)
        await asyncio.to_thread(wm.save, wm_path)

        overlay = "x='-w+((W+w)*((mod(t,30))/30))':y=H-h-20" if style == "slide" else "x=W-w-20:y=H-h-20"
        
        cmd = [
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", "-stats",
            "-i", in_path, "-i", wm_path,
            "-filter_complex", f"[0:v]scale=-2:{resolution}[bg];[bg][1:v]overlay={overlay}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k", "-max_muxing_queue_size", "1024",
            "-movflags", "+faststart", out_path
        ]

        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        
        while True:
            try:
                line = await process.stderr.readline()
                if not line: break
                line_str = line.decode('utf-8', errors='ignore')
                if "time=" in line_str:
                    match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line_str)
                    if match and total_duration > 0:
                        await progress_bar(time_to_seconds(match.group(1)), total_duration, status_msg, "Processing")
                await asyncio.sleep(0.05)
            except: continue

        await process.wait()
        return process.returncode == 0 and os.path.exists(out_path)
    except Exception as e:
        logger.error(f"Err: {e}")
        return False
    finally:
        if os.path.exists(wm_path): os.remove(wm_path)

# ==================== SMART QUEUE WORKER ====================
async def queue_worker(uid, client):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True

    try:
        while sess.queue:
            # 1. Get next message from queue (Don't pop yet, just peek or pop)
            msg = sess.queue.pop(0)
            
            # 2. Check Disk Space
            if not check_disk_space(required_gb=1):
                await client.send_message(uid, "⚠️ Disk full. Clearing queue.")
                sess.queue.clear()
                break

            # 3. Download (Lazy Loading)
            status = await msg.reply("⬇️ Queue Active: Downloading...")
            in_path = f"{WORK_DIR}/{uid}_{int(time.time())}.mp4"
            out_path = f"{WORK_DIR}/out_{uid}_{int(time.time())}.mp4"

            try:
                path = await client.download_media(
                    msg, file_name=in_path,
                    progress=progress_bar, progress_args=(status, "Downloading")
                )
                
                if not path:
                    await status.edit_text("❌ Download Failed.")
                    continue

                # 4. Process
                dur = await get_duration(path)
                success = await process_video(path, sess.watermark_text, out_path, sess.crf, sess.resolution, sess.style, status, dur)

                # 5. Upload
                if success:
                    thumb = await generate_thumbnail(out_path)
                    caption = f"✅ Done\nMode: {sess.style.title()} | Res: {sess.resolution}p"
                    await progress_bar(0, 100, status, "Uploading")
                    try:
                        await client.send_video(
                            uid, out_path, caption=caption, duration=int(dur), thumb=thumb,
                            progress=progress_bar, progress_args=(status, "Uploading")
                        )
                        await status.delete()
                    except errors.EntityTooLarge:
                        await status.edit_text("❌ Video too large for Telegram (2GB Limit).")
                    except Exception as e:
                        await status.edit_text(f"Upload Error: {e}")
                    if thumb and os.path.exists(thumb): os.remove(thumb)
                else:
                    await status.edit_text("❌ Processing Failed.")

            except Exception as e:
                logger.error(f"Worker Error: {e}")
                await status.edit_text("❌ Error occurred.")
            
            # 6. CLEANUP (Crucial for multi-video support)
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)

    finally:
        sess.is_processing = False

# ==================== HANDLERS ====================
app = Client("wm_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=WORK_DIR)

@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    await m.reply("**Smart Watermark Bot** 🧠\nBulk processing supported! Send 10 videos, I'll process them one by one.\n\n/w - Set Text\n/mode static|slide\n/crf 23\n/res 720")

@app.on_message(filters.command("w"))
async def w_cmd(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    await m.reply("Send watermark text:")

@app.on_message(filters.command("mode"))
async def mode_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.style = m.text.split()[1].lower()
        await m.reply(f"✅ Mode: {sess.style}")
    except: await m.reply(f"Current: {sess.style}. Use /mode static or /mode slide")

@app.on_message(filters.command(["crf", "res"]))
async def settings_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try:
        val = int(m.text.split()[1])
        if "crf" in m.command: sess.crf = val
        else: sess.resolution = val
        await m.reply(f"Updated to {val}")
    except: await m.reply("Invalid value.")

@app.on_message(filters.text & ~filters.command(["start", "w", "mode", "crf", "res"]))
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        sess.watermark_text = m.text
        sess.step = "waiting_media"
        await m.reply(f"Text set: `{m.text}`. Send videos! (You can send multiple)")

@app.on_message(filters.video | filters.document)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("Set text with /w first.")
    
    # Add message to queue (do NOT download yet)
    sess.queue.append(m)
    
    # Notify user
    pos = len(sess.queue)
    if sess.is_processing:
        await m.reply(f"🕒 Added to queue (Position: {pos})")
    else:
        await m.reply(f"🚀 Starting processing...")

    # Start worker
    asyncio.create_task(queue_worker(m.from_user.id, c))

if __name__ == "__main__":
    print("Bot Started...")
    app.run()
