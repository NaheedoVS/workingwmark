#!/usr/bin/env python3

# Watermark Bot – Pop-up Animated Watermark + Original Dimensions + Reduced Background + Resolution Support

import os, time, json, asyncio, logging, subprocess, random
from dataclasses import dataclass, field
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters

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
    resolution: int = 720   # default resolution

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
    if getattr(download_progress, "last", -1) == pct:
        return
    download_progress.last = pct
    bar = "█" * (pct//5) + "░" * (20-pct//5)
    try:
        await msg.edit_text(f"Downloading...\n[{bar}] {pct}%")
    except:
        pass

# ==================== WATERMARK CREATION ====================

def create_watermark(text: str, scale=0.7):
    font = ImageFont.load_default()
    for p in ["fonts/Roboto-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, 36)
                break
            except:
                pass

    dummy = Image.new("RGBA", (1,1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0,0), text, font=font)

    padding_x = 20
    padding_y = 10
    w = bbox[2]-bbox[0] + padding_x
    h = bbox[3]-bbox[1] + padding_y

    img = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0,0,w-1,h-1), radius=10, fill=(0,0,0,180))
    draw.text((padding_x//2, padding_y//2), text, font=font, fill=(255,255,255,255))

    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img = img.resize((new_w, new_h), Image.ANTIALIAS)
    return img

# ==================== VIDEO PROCESSING ====================

def process_video(in_path, text, out_path, crf=21, resolution=720):
    try:
        wm = create_watermark(text, scale=0.7)
        wm_path = f"/tmp/wm_{os.getpid()}.png"
        wm.save(wm_path)

        # Generate 10 random watermark positions
        positions = []
        for _ in range(10):
            positions.append((random.randint(0, 1280 - wm.width),
                              random.randint(0, 720 - wm.height)))

        # Build proper FFmpeg filter chain
        filter_parts = [f"[0:v]scale=-2:{resolution}[scaled]"]

        prev = "[scaled]"
        for i, (x, y) in enumerate(positions):
            out = f"[wm{i}]"
            start = i * 5
            end = start + 5
            filter_parts.append(
                f"{prev}[1:v]overlay=x={x}:y={y}:enable='between(t,{start},{end})'{out}"
            )
            prev = out

        # Final label
        filter_parts.append(f"{prev}final")
        filter_complex = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-i", wm_path,
            "-filter_complex", filter_complex,
            "-map", "[final]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(crf),
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            out_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)
        os.remove(wm_path)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False

        return os.path.getsize(out_path) > 200000

    except Exception as e:
        logger.error(f"Processing error: {e}")
        return False

# ==================== DURATION & THUMB ====================

def get_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe","-v","quiet","-show_entries","format=duration","-of","json",path],
            capture_output=True, text=True, timeout=15
        )
        return round(float(json.loads(r.stdout)["format"]["duration"]))
    except:
        return 0

def make_thumb(path):
    t = f"/tmp/thumb_{int(time.time())}.jpg"
    subprocess.run(
        ["ffmpeg","-y","-i",path,"-ss","10","-vframes","1","-vf","scale=640:-2",t],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
    )
    return t if os.path.exists(t) else None

# ==================== WORKER ====================

async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing:
        return
    sess.is_processing = True

    while sess.queue:
        in_path, text, typ = sess.queue.pop(0)
        out_path = f"/tmp/out_{uid}_{int(time.time())}.mp4"

        status = await app.send_message(uid, f"Processing video + watermark...")

        success = process_video(in_path, text, out_path, sess.crf, sess.resolution)

        await status.delete()

        if not success or not os.path.exists(out_path):
            await app.send_message(uid, "Processing failed ❌")
            if os.path.exists(in_path): os.remove(in_path)
            continue

        caption = (
            f"Watermark: {text}\n"
            f"CRF: {sess.crf}\n"
            f"Resolution: {sess.resolution}p"
        )

        try:
            thumb = make_thumb(out_path)
            duration = get_duration(out_path)

            await app.send_video(
                uid, out_path,
                caption=caption,
                duration=duration,
                thumb=thumb,
                supports_streaming=True,
                file_name=f"wm_{int(time.time())}.mp4"
            )
            if thumb: os.remove(thumb)
            await app.send_message(uid, "Done ✔️")
        except Exception as e:
            await app.send_message(uid, f"Upload error: {e}")

        for p in (in_path, out_path):
            if os.path.exists(p): os.remove(p)

    sess.is_processing = False

# ==================== BOT ====================

app = Client("wm_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="/tmp")

@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply(
        "**Watermark Bot**\n"
        "• Moving watermark every 5s\n"
        "• Set CRF → /crf 21\n"
        "• Set Resolution → /res 720\n"
        "Start with /w"
    )

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
        await m.reply(f"CRF updated → {sess.crf}")
    except:
        await m.reply("Usage: /crf 21")

@app.on_message(filters.command("res"))
async def set_res(_, m):
    sess = await get_session(m.from_user.id)

    try:
        value = int(m.text.split()[1])
        if value not in (480, 720, 1080):
            return await m.reply("❌ Invalid resolution.\nUse: 480 / 720 / 1080")

        sess.resolution = value
        await m.reply(f"Resolution set to **{value}p** ✔️")

    except:
        await m.reply("Usage: `/res 720`", parse_mode="markdown")

@app.on_message(filters.text & ~filters.command(["start", "w", "crf", "res", "cancel"]))
async def text(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_text":
        return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply("Now send video 🎥")

@app.on_message(filters.video | filters.document)
async def media(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media":
        return await m.reply("Use /w first")

    prog = await m.reply("Downloading...")
    path = await c.download_media(m, progress=download_progress, progress_args=(prog,))
    await prog.delete()

    if not path:
        return await m.reply("Download failed ❌")

    sess.queue.append((path, sess.watermark_text, "video"))
    asyncio.create_task(worker(m.from_user.id))
    await m.reply(f"Queued | CRF {sess.crf} | {sess.resolution}p")

@app.on_message(filters.command("cancel"))
async def cancel(_, m):
    sess = await get_session(m.from_user.id)
    sess.queue.clear()
    sess.reset()
    await m.reply("Cancelled ✔")

# ==================== RUN ====================

if __name__ == "__main__":
    print("Watermark Bot Running…")
    app.run()
