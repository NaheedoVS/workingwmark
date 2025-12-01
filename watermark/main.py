#!/usr/bin/env python3
# Watermark Bot – Dual Mode (Static + Slide) + Progress Bars

import os, time, json, asyncio, logging, shutil, re
from dataclasses import dataclass, field
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont

from pyrogram import Client, filters

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

WORK_DIR = "downloads"
os.makedirs(WORK_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== BINARY DETECTION ====================
def get_ffmpeg_path():
    if os.path.exists("/usr/bin/ffmpeg"): return "/usr/bin/ffmpeg"
    path = shutil.which("ffmpeg")
    if not path: raise FileNotFoundError("FFmpeg not found!")
    return path

FFMPEG_BIN = get_ffmpeg_path()
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS

# ==================== SESSION MANAGEMENT ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    queue: List[Tuple[str, str]] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 21
    resolution: int = 720
    # New: 'static' or 'slide'
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

# ==================== PROGRESS BAR ====================
async def progress_bar(current, total, status_msg, action_desc):
    if total == 0: return
    pct = int(current * 100 / total)
    if getattr(progress_bar, "last_pct", 0) // 5 == pct // 5 and pct != 100: return
    progress_bar.last_pct = pct
    
    bar_len = 10
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    
    try: await status_msg.edit_text(f"{action_desc}...\n[{bar}] {pct}%")
    except: pass

# ==================== WATERMARK GENERATION ====================
def create_watermark(text: str):
    font_size = 80
    font = ImageFont.load_default()
    possible = ["fonts/Roboto-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"]
    for p in possible:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, font_size); break
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

# ==================== UTILS ====================
def time_to_seconds(time_str):
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except: return 0

async def get_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try: return float(out.decode().strip())
    except: return 0

async def generate_thumbnail(in_path):
    out = f"{in_path}.jpg"
    cmd = [FFMPEG_BIN, "-y", "-i", in_path, "-ss", "00:00:02", "-vframes", "1", "-vf", "scale=320:-1", out]
    await (await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)).wait()
    return out if os.path.exists(out) else None

# ==================== PROCESSING ====================
async def process_video(in_path, text, out_path, crf, resolution, style, status_msg, total_duration):
    wm_path = f"{WORK_DIR}/wm_{int(time.time())}_{os.getpid()}.png"
    
    try:
        # 1. Prepare Watermark
        wm_full = await asyncio.to_thread(create_watermark, text)
        target_wm_height = int(resolution / 18)
        aspect = wm_full.width / wm_full.height
        target_wm_width = int(target_wm_height * aspect)
        wm = await asyncio.to_thread(wm_full.resize, (target_wm_width, target_wm_height), RESAMPLE_MODE)
        await asyncio.to_thread(wm.save, wm_path)

        # 2. Determine Style
        if style == "slide":
            # Left to Right Animation over 30 seconds
            overlay_cmd = "x='-w+((W+w)*((mod(t,30))/30))':y=H-h-20"
        else:
            # Static Bottom Right
            overlay_cmd = "x=W-w-20:y=H-h-20"

        # 3. Build Filter
        filter_chain = f"[0:v]scale=-2:{resolution}[bg];[bg][1:v]overlay={overlay_cmd}"

        cmd = [
            FFMPEG_BIN, "-y", "-i", in_path, "-i", wm_path,
            "-filter_complex", filter_chain,
            "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            out_path
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        # 4. Monitor Progress
        while True:
            line = await process.stderr.readline()
            if not line: break
            line_str = line.decode('utf-8', errors='ignore')
            match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line_str)
            if match and total_duration > 0:
                cur = time_to_seconds(match.group(1))
                await progress_bar(cur, total_duration, status_msg, "Processing")

        await process.wait()
        return process.returncode == 0 and os.path.exists(out_path)

    except Exception as e:
        logger.error(f"Err: {e}")
        return False
    finally:
        if os.path.exists(wm_path): os.remove(wm_path)

# ==================== WORKER ====================
async def queue_worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True

    try:
        while sess.queue:
            in_path, _ = sess.queue.pop(0)
            out_path = f"{WORK_DIR}/out_{uid}_{int(time.time())}.mp4"
            status = await app.send_message(uid, "⏳ Initializing...")
            
            dur = await get_duration(in_path)
            
            # Pass 'sess.style' to processor
            success = await process_video(in_path, sess.watermark_text, out_path, sess.crf, sess.resolution, sess.style, status, dur)
            
            if success:
                thumb = await generate_thumbnail(out_path)
                capt = f"✅ Done\nMode: {sess.style.title()} | Res: {sess.resolution}p"
                
                await progress_bar(0, 100, status, "Uploading")
                try:
                    await app.send_video(uid, out_path, caption=capt, duration=int(dur), thumb=thumb,
                                         progress=progress_bar, progress_args=(status, "Uploading"))
                    await status.delete()
                except Exception as e: await status.edit_text(f"Upload Error: {e}")
                
                if thumb and os.path.exists(thumb): os.remove(thumb)
            else:
                await status.edit_text("❌ Processing Failed.")
            
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)
    finally:
        sess.is_processing = False

# ==================== HANDLERS ====================
app = Client("wm_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=WORK_DIR)

@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    await m.reply(
        "**Watermark Bot** 🎥\n"
        "1. /w - Set Text\n"
        "2. /mode static - Bottom Right (Default)\n"
        "3. /mode slide - Left to Right Animation\n"
        "4. /crf 21 - Set Quality\n"
        "5. /res 720 - Set Resolution"
    )

@app.on_message(filters.command("w"))
async def w_cmd(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    await m.reply("Send watermark text:")

@app.on_message(filters.command("mode"))
async def mode_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try:
        # Clean input to match expected values
        mode = m.text.split()[1].lower()
        if mode in ["static", "slide"]:
            sess.style = mode
            await m.reply(f"✅ Mode set to: **{mode.title()}**")
        else:
            await m.reply("Unknown mode. Use:\n`/mode static`\n`/mode slide`")
    except:
        await m.reply(f"Current mode: **{sess.style.title()}**\nChange with: `/mode static` or `/mode slide`")

@app.on_message(filters.command("crf"))
async def crf_cmd(_, m):
    try:
        v = int(m.text.split()[1])
        sess = await get_session(m.from_user.id)
        sess.crf = v
        await m.reply(f"CRF set: {v}")
    except: await m.reply("Usage: /crf 21")

@app.on_message(filters.command("res"))
async def res_cmd(_, m):
    try:
        v = int(m.text.split()[1])
        if v in [480, 720, 1080]:
            sess = await get_session(m.from_user.id)
            sess.resolution = v
            await m.reply(f"Resolution set: {v}p")
        else: raise ValueError
    except: await m.reply("Usage: /res 480|720|1080")

@app.on_message(filters.text & ~filters.command(["start", "w", "mode", "crf", "res"]))
async def text_handler(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step == "waiting_text":
        sess.watermark_text = m.text
        sess.step = "waiting_media"
        await m.reply(f"Text set: `{m.text}`\nMode: **{sess.style.title()}**\nNow send video!")

@app.on_message(filters.video | filters.document)
async def media_handler(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("Use /w to set text first.")
    
    st = await m.reply("⬇️ Downloading...")
    path = await c.download_media(
        m, file_name=f"{WORK_DIR}/{m.from_user.id}_{int(time.time())}.mp4",
        progress=progress_bar, progress_args=(st, "Downloading")
    )
    sess.queue.append((path, "video"))
    asyncio.create_task(queue_worker(m.from_user.id))

if __name__ == "__main__":
    print("Bot Started...")
    app.run()
