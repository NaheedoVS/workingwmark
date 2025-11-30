#!/usr/bin/env python3
# FINAL 720p Watermark Bot – SMALL WATERMARK + NO FREEZE + NO CRASH

import os, time, json, asyncio, logging, subprocess
from dataclasses import dataclass, field
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

os.makedirs("/tmp", exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== SESSION ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    queue: List[Tuple[str, str, str]] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 21
    speed: int = 70

    def reset(self):
        self.step = "waiting_text"
        self.watermark_text = ""
        self.queue.clear()

session_manager = {}
lock = asyncio.Lock()

async def get_session(uid):
    async with lock:
        return session_manager.setdefault(uid, UserSession(uid))

# ==================== DOWNLOAD PROGRESS ====================
async def download_progress(cur, tot, msg):
    pct = int(cur * 100 / tot)
    if getattr(download_progress, "last", -1) == pct: return
    download_progress.last = pct
    bar = "█" * (pct//5) + "░" * (20-pct//5)
    try: await msg.edit_text(f"Downloading...\n[{bar}] {pct}%")
    except: pass

# ==================== SMALL WATERMARK ====================
def create_watermark(text: str):
    font = ImageFont.load_default()
    for p in ["fonts/Roboto-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            try: font = ImageFont.truetype(p, 32); break
            except: pass
    dummy = Image.new("RGBA", (1,1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0,0), text, font=font)
    w = bbox[2]-bbox[0] + 60
    h = bbox[3]-bbox[1] + 28
    img = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0,0,w-1,h-1), radius=12, fill=(0,0,0,170))
    draw.text((30, 10), text, font=font, fill=(255,255,255,255))
    return img

# ==================== FIXED PROCESSOR – NO FREEZE ====================
def process_video(in_path, text, out_path, crf=21, speed=70):
    try:
        wm = create_watermark(text)
        wm_path = f"/tmp/wm_{os.getpid()}.png"
        wm.save(wm_path)

        # ⭐ FIXED OVERLAY – no freeze
        filter_complex = (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,setsar=1[bg];"
            "[1:v]format=yuva444p,colorchannelmixer=aa=0.78[wm];"
            "[bg][wm]overlay="
            "x='40+mod(t*{speed},(main_w-overlay_w-80))':"
            "y='main_h-overlay_h-30-mod(t*{speed}*0.6,(main_h-overlay_h-60))':"
            "format=auto:repeatlast=0"
        ).format(speed=speed)

        cmd = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-i", wm_path,
            "-filter_complex", filter_complex,
            "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
            "-maxrate", "5000k", "-bufsize", "10000k",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",

            # ⭐ FIXED MAPPING – prevents first-frame freeze
            "-map", "0:v:0",
            "-map", "0:a?",
            out_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)
        os.remove(wm_path)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False

        return os.path.getsize(out_path) > 200000

    except Exception as e:
        logger.error(f"Error: {e}")
        return False

# ==================== DURATION & THUMB ====================
def get_duration(path):
    try:
        r = subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration","-of","json",path],
                          capture_output=True, text=True, timeout=15)
        return round(float(json.loads(r.stdout)["format"]["duration"]))
    except: return 0

def make_thumb(path):
    t = f"/tmp/thumb_{int(time.time())}.jpg"
    subprocess.run(["ffmpeg","-y","-i",path,"-ss","10","-vframes","1","-vf","scale=640:-2",t],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    return t if os.path.exists(t) else None

# ==================== WORKER ====================
async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        in_path, text, typ = sess.queue.pop(0)
        out_path = f"/tmp/out_{uid}_{int(time.time())}.mp4"

        status = await app.send_message(uid, "Processing 720p + small moving watermark...")

        success = process_video(in_path, text, out_path, sess.crf, sess.speed)

        await status.delete()

        if not success or not os.path.exists(out_path):
            await app.send_message(uid, "Processing failed!")
            if os.path.exists(in_path): os.remove(in_path)
            continue

        caption = f"Watermark: {text}\n720p • CRF {sess.crf}"

        try:
            thumb = make_thumb(out_path)
            duration = get_duration(out_path)

            await app.send_video(
                uid, out_path,
                caption=caption,
                duration=duration,
                width=1280, height=720,
                thumb=thumb,
                supports_streaming=True,
                file_name=f"『{text}』 720p.mp4"
            )
            if thumb: os.remove(thumb)
            await app.send_message(uid, "Done! Small watermark + perfect video")
        except Exception as e:
            await app.send_message(uid, f"Upload error: {e}")

        for p in (in_path, out_path):
            if os.path.exists(p): os.remove(p)

    sess.is_processing = False

# ==================== BOT ====================
app = Client("wm_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="/tmp")

@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply("720p Watermark Bot\nSmall moving watermark\nCustom CRF → /crf 21\nSend /w")

@app.on_message(filters.command("w"))
async def w(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    await m.reply("Send watermark text:")

@app.on_message(filters.command("crf"))
async def crf(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.crf = int(m.text.split()[1])
        await m.reply(f"CRF set to {sess.crf}\nLower = Better quality")
    except:
        await m.reply("Usage: /crf 21")

# Accept all text except commands
@app.on_message(filters.text & ~filters.command(["start", "w", "crf", "cancel"]))
async def text(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_text": return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply("Send video → 720p + small moving watermark")

@app.on_message(filters.video | filters.document)
async def media(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media":
        return await m.reply("Use /w first")

    prog = await m.reply("Downloading...")
    path = await c.download_media(m, progress=download_progress, progress_args=(prog,))
    await prog.delete()
    if not path:
        return await m.reply("Download failed")

    sess.queue.append((path, sess.watermark_text, "video"))
    asyncio.create_task(worker(m.from_user.id))
    await m.reply(f"Queued! CRF {sess.crf}")

@app.on_message(filters.command("cancel"))
async def cancel(_, m):
    sess = await get_session(m.from_user.id)
    sess.queue.clear()
    sess.reset()
    await m.reply("Cancelled")

# ==================== RUN ====================
if __name__ == "__main__":
    print("FINAL Watermark Bot Started – 100% Working!")
    app.run()
