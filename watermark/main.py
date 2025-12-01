#!/usr/bin/env python3
# Watermark Bot – Animated & Static Support
# library: pip install ffmpeg-python pyrogram tgcrypto pillow

import os, time, json, asyncio, logging, subprocess, random
from dataclasses import dataclass, field
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont
import ffmpeg # Wrapper library

from pyrogram import Client, filters

# ==================== CONFIG ====================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

os.makedirs("/tmp", exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== RESAMPLING ====================
RESAMPLE_MODE = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS

# ==================== SESSION ====================
@dataclass
class UserSession:
    user_id: int
    step: str = "idle"
    watermark_text: str = ""
    queue: List[Tuple[str,str,str,str]] = field(default_factory=list)
    is_processing: bool = False
    crf: int = 21
    resolution: int = 720
    wm_mode: str = "animated" 

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

# ==================== WATERMARK GEN ====================
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
    px,py = 20,12 
    w,h = bbox[2]-bbox[0]+px, bbox[3]-bbox[1]+py
    img = Image.new("RGBA",(w,h),(0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0,0,w-1,h-1),radius=10,fill=(0,0,0,180))
    draw.text((px//2,py//2),text,font=font,fill=(255,255,255,255))

    new_w,new_h = int(w*scale),int(h*scale)
    return img.resize((new_w,new_h),RESAMPLE_MODE)

# ==================== ANIMATION LOGIC ====================
def get_animated_expr():
    POSITIONS = 10
    INTERVAL = 10
    positions = [(random.randint(0, 200), random.randint(0, 200)) for _ in range(POSITIONS)]
    
    index_expr = f"mod(floor(t/{INTERVAL}),{POSITIONS})"
    
    x_expr = ""
    for i in range(POSITIONS): x_expr += f"if(eq({index_expr},{i}),{positions[i][0]},"
    x_expr += "0" + ")" * POSITIONS

    y_expr = ""
    for i in range(POSITIONS): y_expr += f"if(eq({index_expr},{i}),{positions[i][1]},"
    y_expr += "0" + ")" * POSITIONS
    
    # Safe fading expression (Fade In 1s, Hold 8s, Fade Out 1s)
    # Using 'if' logic instead of ternary colons to be safe with ffmpeg-python
    fade_expr = "if(lt(mod(t,10),1), mod(t,10), if(lt(mod(t,10),9), 1, 10-mod(t,10)))"
    
    return x_expr, y_expr, fade_expr

# ==================== VIDEO PROCESSING ====================
async def process_video(in_path, text, out_path, crf, resolution, wm_mode, status):
    progress_pipe = f"/tmp/progress_{os.getpid()}.txt"
    try:
        # 1. Create Watermark
        wm = create_watermark(text)
        wm_path = f"/tmp/wm_{os.getpid()}.png"
        wm.save(wm_path)

        # 2. Get Duration
        probe = ffmpeg.probe(in_path)
        duration = float(probe['format']['duration'])
        total_duration = max(1, int(duration))

        # 3. Build FFmpeg Graph (Using ffmpeg-python)
        input_stream = ffmpeg.input(in_path)
        wm_stream = ffmpeg.input(wm_path)
        
        # Scale video
        scaled = input_stream.video.filter('scale', -2, f'min(ih,{resolution})')

        if wm_mode == "animated":
            x_e, y_e, alpha_e = get_animated_expr()
            # Library handles complex escaping automatically
            overlay_layer = scaled.overlay(wm_stream, x=x_e, y=y_e, alpha=alpha_e)
        else:
            # Static: Bottom Right
            overlay_layer = scaled.overlay(wm_stream, x='W-w-20', y='H-h-20')

        # 4. Compile Command (Mix of Library + Subprocess for Progress Bar)
        # We compile the graph to arguments, then run it manually to watch the pipe
        out = ffmpeg.output(
            overlay_layer, 
            input_stream.audio, 
            out_path, 
            vcodec='libx264', 
            preset='fast', 
            crf=crf, 
            acodec='aac', 
            audio_bitrate='192k', 
            movflags='+faststart'
        ).overwrite_output()

        cmd = ffmpeg.compile(out)
        
        # Add progress flag manually
        if not os.path.exists(progress_pipe): os.mkfifo(progress_pipe)
        cmd.extend(["-progress", progress_pipe])

        # 5. Run Process
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Progress Loop
        last_pct = -1
        with open(progress_pipe, 'r') as pipe:
            while process.poll() is None:
                line = pipe.readline()
                if not line:
                    await asyncio.sleep(0.1)
                    continue
                
                if "out_time_ms" in line:
                    try:
                        ms = int(line.split('=')[1])
                        pct = min(100, int((ms / 1_000_000) * 100 / total_duration))
                        if pct > last_pct and (pct % 5 == 0 or pct == 100):
                            last_pct = pct
                            bar = "█"*(pct//5) + "░"*(20-pct//5)
                            await status.edit_text(f"Processing ({wm_mode})...\n[{bar}] {pct}%")
                    except: pass
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            # Report actual error to Telegram
            err_msg = stderr.decode()[-800:] # Last 800 chars
            await status.edit_text(f"❌ FFmpeg Error:\n`{err_msg}`")
            return False

        # Cleanup
        if os.path.exists(wm_path): os.remove(wm_path)
        if os.path.exists(progress_pipe): os.remove(progress_pipe)

        return os.path.exists(out_path)

    except ffmpeg.Error as e:
        await status.edit_text(f"❌ FFmpeg Graph Error:\n`{e.stderr.decode()}`")
        return False
    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(f"❌ Error: {str(e)}")
        return False

# ==================== HELPERS ====================
def make_thumb(path):
    t=f"/tmp/thumb_{int(time.time())}.jpg"
    try:
        (
            ffmpeg
            .input(path, ss=10)
            .filter('scale', 'min(640,iw)', -2)
            .output(t, vframes=1)
            .overwrite_output()
            .run(quiet=True)
        )
    except: pass
    return t if os.path.exists(t) else None

# ==================== WORKER ====================
async def worker(uid):
    sess = await get_session(uid)
    if sess.is_processing: return
    sess.is_processing = True

    while sess.queue:
        in_path, text, _, wm_mode = sess.queue.pop(0)
        out_path = f"/tmp/out_{uid}_{int(time.time())}.mp4"
        
        status = await app.send_message(uid,f"Starting ({wm_mode})...")
        
        success = await process_video(in_path, text, out_path, sess.crf, sess.resolution, wm_mode, status)
        
        if success:
            await status.delete()
            caption=f"Watermark: {text}\nMode: {wm_mode}\nCRF: {sess.crf}\nRes: {sess.resolution}p"
            thumb = make_thumb(out_path)
            try:
                d = float(ffmpeg.probe(out_path)['format']['duration'])
                await app.send_video(uid, out_path, caption=caption, duration=int(d), thumb=thumb)
                await app.send_message(uid,"Done ✔️")
            except Exception as e:
                await app.send_message(uid,f"Upload error: {e}")
            if thumb: os.remove(thumb)
        
        for p in (in_path, out_path):
            if os.path.exists(p): os.remove(p)

    sess.is_processing=False

# ==================== BOT ====================
app=Client("wm_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="/tmp")

@app.on_message(filters.command("start"))
async def start(_, m):
    await m.reply("**Watermark Bot**\n/w - Animated\n/sw - Static\n/crf 21\n/res 720")

@app.on_message(filters.command("w"))
async def w(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.wm_mode = "animated"
    await m.reply("Mode: **Animated**\nSend text:")

@app.on_message(filters.command("sw"))
async def sw(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    sess.wm_mode = "static"
    await m.reply("Mode: **Static**\nSend text:")

@app.on_message(filters.command("crf"))
async def crf_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try: sess.crf = int(m.text.split()[1]); await m.reply(f"CRF: {sess.crf}")
    except: pass

@app.on_message(filters.command("res"))
async def res_cmd(_, m):
    sess = await get_session(m.from_user.id)
    try: sess.resolution = int(m.text.split()[1]); await m.reply(f"Res: {sess.resolution}")
    except: pass

@app.on_message(filters.text & ~filters.command(["start","w","sw","crf","res","cancel"]))
async def text_msg(_, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_text": return
    sess.watermark_text = m.text.strip()
    sess.step = "waiting_media"
    await m.reply(f"Text: {sess.watermark_text}\nSend Video.")

@app.on_message(filters.video | filters.document)
async def media_msg(c, m):
    sess = await get_session(m.from_user.id)
    if sess.step != "waiting_media": return await m.reply("Use /w or /sw first.")
    
    msg = await m.reply("Downloading...")
    path = await c.download_media(m, progress=download_progress, progress_args=(msg,))
    await msg.delete()
    
    if not path: return await m.reply("Download failed")
    
    sess.queue.append((path, sess.watermark_text, "video", sess.wm_mode))
    asyncio.create_task(worker(m.from_user.id))
    await m.reply(f"Queued ({sess.wm_mode})")

@app.on_message(filters.command("cancel"))
async def cancel(_, m):
    sess = await get_session(m.from_user.id)
    sess.reset()
    await m.reply("Cancelled.")

if __name__=="__main__":
    print("Bot Running...")
    app.run()
