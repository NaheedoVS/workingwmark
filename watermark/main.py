#!/usr/bin/env python3
# Watermark Bot – Animated Watermark for full video

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

# ==================== RESAMPLING ====================
# Use Resampling.LANCZOS for better quality resizing if available
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS

# ==================== SESSION ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    queue: List[Tuple[str,str,str]] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 21
    resolution: int = 720

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
    pct = int(cur*100/tot)
    if getattr(download_progress,"last",-1)==pct: return
    download_progress.last = pct
    bar = "█"*(pct//5) + "░"*(20-pct//5)
    try: await msg.edit_text(f"Downloading...\n[{bar}] {pct}%")
    except: pass

# ==================== WATERMARK ====================
def create_watermark(text:str, scale=0.595):
    font = ImageFont.load_default()
    for p in ["fonts/Roboto-Bold.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p,36)
                break
            except:
                pass

    dummy = Image.new("RGBA",(1,1))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0,0),text,font=font)
    px,py = 20,12    # background increased a little
    w,h = bbox[2]-bbox[0]+px, bbox[3]-bbox[1]+py
    img = Image.new("RGBA",(w,h),(0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0,0,w-1,h-1),radius=10,fill=(0,0,0,180))
    draw.text((px//2,py//2),text,font=font,fill=(255,255,255,255))

    new_w,new_h = int(w*scale),int(h*scale)
    return img.resize((new_w,new_h),RESAMPLE_MODE)

# ==================== VIDEO PROCESSING (FIX APPLIED HERE) ====================
def process_video(in_path, text, out_path, crf=21, resolution=720):
    try:
        # Create watermark PNG
        wm = create_watermark(text)
        wm_path = f"/tmp/wm_{os.getpid()}.png"
        wm.save(wm_path)

        # Get video duration
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "json", in_path],
            capture_output=True, text=True
        )
        duration = float(json.loads(r.stdout)["format"]["duration"])
        duration = max(1, int(duration))

        # ================================
        # RANDOM POSITIONS + 10 SEC BLOCKS
        # ================================
        POSITIONS = 10
        INTERVAL = 10

        positions = [
            (
                random.randint(0, 200),
                random.randint(0, 200)
            )
            for _ in range(POSITIONS)
        ]

        index_expr = f"mod(floor(t/{INTERVAL}),{POSITIONS})"

        x_expr = ""
        for i in range(POSITIONS):
            x_expr += f"if(eq({index_expr},{i}),{positions[i][0]},"
        x_expr += "0" + ")" * POSITIONS

        y_expr = ""
        for i in range(POSITIONS):
            y_expr += f"if(eq({index_expr},{i}),{positions[i][1]},"
        y_expr += "0" + ")" * POSITIONS

        # fade in/out inside each 10-second block (using single backslash for mod(t\,10) escape)
        # fade in/out inside each 10-second block. Escaping both commas now.
        # Python string: "min(A\\, B)" -> Shell command: min(A\, B)
        fade_expr = "min(mod(t\\,10)/1\\, (10-mod(t\\,10))/1)"

        # ============================================
        # FIXED OVERLAY: Using 'alpha' parameter in overlay filter (Fix for Invalid argument error)
        # ============================================
        # Removed colorchannelmixer. Using [0:v] (video) and [1:v] (watermark) directly
        overlay_filter = (
            f"[0:v][1:v]overlay=x='{x_expr}':y='{y_expr}':" 
            f"alpha='{fade_expr}':" # Apply fade expression to the overlay's alpha
            f"format=auto[v]"
        )

        # ============================================
        # FFmpeg command
        # ============================================
        cmd = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-i", wm_path,
            "-filter_complex", overlay_filter,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(crf),
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-vf", f"scale=-2:'min(ih,{resolution})'", # Add resolution scaling filter
            out_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)
        logger.info(f"FFmpeg CMD: {' '.join(cmd)}")
        logger.info(f"FFmpeg STDOUT: {result.stdout}")
        logger.error(f"FFmpeg STDERR: {result.stderr}")

        os.remove(wm_path)

        # Check for success and minimum output size
        return result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 200000

    except Exception as e:
        logger.error(f"Processing error: {e}")
        return False


# ==================== THUMB & DURATION ====================
def get_duration(path):
    try:
        r=subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration","-of","json",path],
                         capture_output=True,text=True,timeout=15)
        return round(float(json.loads(r.stdout)["format"]["duration"]))
    except: return 0

def make_thumb(path):
    t=f"/tmp/thumb_{int(time.time())}.jpg"
    # Added aspect ratio correction for thumbnail scaling
    subprocess.run(["ffmpeg","-y","-i",path,"-ss","10","-vframes","1","-vf","scale='min(640,iw)':-2",t],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    return t if os.path.exists(t) else None

# ==================== WORKER ====================
async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        in_path, text, _ = sess.queue.pop(0)
        out_path = f"/tmp/out_{uid}_{int(time.time())}.mp4"
        status = await app.send_message(uid,"Processing video + watermark...")

        # Pass crf and resolution to the processing function
        success = process_video(in_path, text, out_path, sess.crf, sess.resolution)
        await status.delete()

        if not success or not os.path.exists(out_path):
            await app.send_message(uid,"Processing failed ❌")
            if os.path.exists(in_path): os.remove(in_path)
            continue

        caption=f"Watermark: {text}\nCRF: {sess.crf}\nResolution: {sess.resolution}p"
        try:
            thumb = make_thumb(out_path)
            duration = get_duration(out_path)
            await app.send_video(uid, out_path, caption=caption, duration=duration,
                                 thumb=thumb, supports_streaming=True,
                                 file_name=f"wm_{int(time.time())}.mp4")
            if thumb: os.remove(thumb)
            await app.send_message(uid,"Done ✔️")
        except Exception as e:
            await app.send_message(uid,f"Upload error: {e}")

        for p in (in_path, out_path):
            if os.path.exists(p): os.remove(p)

    sess.is_processing=False

# ==================== BOT ====================
app=Client("wm_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="/tmp")

@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply("**Watermark Bot**\n• Animated watermark every 10s\n• Set CRF → /crf 21\n• Set Resolution → /res 480|720|1080\nStart with /w")

@app.on_message(filters.command("w"))
async def w(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    await m.reply("Send watermark text:")

@app.on_message(filters.command("crf"))
async def crf_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try:
        sess.crf = int(m.text.split()[1])
        # Add boundary check for common CRF range
        if not 18 <= sess.crf <= 28: raise ValueError("CRF out of range")
        await m.reply(f"CRF updated → **{sess.crf}** (Lower is higher quality)")
    except:
        await m.reply("Usage: `/crf 21` (Value must be between 18 and 28)")

@app.on_message(filters.command("res"))
async def res_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try:
        r = int(m.text.split()[1])
        if r not in [480,720,1080]: raise ValueError
        sess.resolution = r
        await m.reply(f"Resolution set → **{r}p**")
    except:
        await m.reply("Usage: `/res 480|720|1080`")

@app.on_message(filters.text & ~filters.command(["start","w","crf","res","cancel"]))
async def text_msg(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_text": return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply(f"Watermark set to: **{sess.watermark_text}**\nNow send the video 🎥")

@app.on_message(filters.video | filters.document)
async def media_msg(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("Use `/w` first to set the watermark text.")
    prog = await m.reply("Downloading...")
    # Get media file size for better path management
    media_path = m.video.file_id if m.video else m.document.file_id
    file_id = media_path if isinstance(media_path, str) else media_path.file_id
    
    # Use a predictable path name based on file_id
    path = f"/tmp/{file_id}.mp4" 
    try:
        path = await c.download_media(m, file_name=path, progress=download_progress, progress_args=(prog,))
    except Exception as e:
        logger.error(f"Download failed: {e}")
        await prog.edit_text("Download failed ❌")
        return

    await prog.delete()
    if not path or not os.path.exists(path): return await m.reply("Download failed ❌")
    
    sess.queue.append((path, sess.watermark_text, "video"))
    # Start the worker task
    asyncio.create_task(worker(m.from_user.id))
    await m.reply(f"Video Queued! Current settings:\n• **CRF**: {sess.crf}\n• **Resolution**: {sess.resolution}p\n• **Queue size**: {len(sess.queue)}")

@app.on_message(filters.command("cancel"))
async def cancel(_, m):
    sess = await get_session(m.from_user.id)
    sess.queue.clear()
    sess.reset()
    await m.reply("Processing queue and current state cancelled ✔")

# ==================== RUN ====================
if __name__=="__main__":
    print("Watermark Bot Running…")
    try:
        app.run()
    except Exception as e:
        import traceback
        print("Bot crashed:", e)
        traceback.print_exc()
