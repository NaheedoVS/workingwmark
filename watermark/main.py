import os
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from config import bot_config

app = Client("wm", bot_token=bot_config.BOT_TOKEN)

def apply_watermark_ffmpeg(input_path, watermark_path, output_path):
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-i", watermark_path,
        "-filter_complex", "overlay=10:10",
        "-c:a", "copy",
        "-preset", "ultrafast",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

@app.on_message(filters.command("w") & filters.reply)
async def set_watermark(client, msg: Message):
    if not msg.reply_to_message.photo and not msg.reply_to_message.document:
        return await msg.reply("Reply to an image to set it as watermark.")
    path = await msg.reply_to_message.download()
    bot_config.save_watermark(path)
    await msg.reply("Watermark saved successfully. It will be used for ALL future videos.")

@app.on_message(filters.video)
async def process_video(client, msg: Message):
    if not os.path.exists(bot_config.SAVED_WATERMARK):
        return await msg.reply("No watermark set. Send `/w` with an image first.")
    video_path = await msg.download()
    os.makedirs(bot_config.OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(bot_config.OUTPUT_DIR, f"wm_{msg.video.file_id}.mp4")
    await msg.reply("Processing your video...")
    apply_watermark_ffmpeg(video_path, bot_config.SAVED_WATERMARK, output_path)
    await client.send_video(msg.chat.id, output_path, caption="Here's your watermarked video.")

app.run()
