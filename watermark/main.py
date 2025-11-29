"""
Telegram Watermark Bot
A robust bot for watermarking images and videos with custom text
"""

# ============================================================================
# PYTHON 3.10+ COMPATIBILITY PATCH FOR MOVIEPY
# Must be applied BEFORE importing MoviePy
# ============================================================================
import collections
import collections.abc

# Patch for Python 3.10+ where collections.Iterable was removed
if not hasattr(collections, 'Iterable'):
    collections.Iterable = collections.abc.Iterable
if not hasattr(collections, 'Mapping'):
    collections.Mapping = collections.abc.Mapping
if not hasattr(collections, 'MutableMapping'):
    collections.MutableMapping = collections.abc.MutableMapping
if not hasattr(collections, 'MutableSequence'):
    collections.MutableSequence = collections.abc.MutableSequence
if not hasattr(collections, 'Callable'):
    collections.Callable = collections.abc.Callable

# ============================================================================
# IMPORTS
# ============================================================================
import os
import sys
import time
import random
import asyncio
import logging
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

# Third-party imports
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# MoviePy imports (after patch)
from moviepy.editor import (
    VideoFileClip, ImageClip, CompositeVideoClip,
    concatenate_videoclips, vfx
)

# Pyrogram imports
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import FloodWait, MessageDeleteForbidden

# Local config
from config import telegram_config, watermark_config, bot_config

# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging() -> logging.Logger:
    """Configure logging to both file and console"""
    logger = logging.getLogger("WatermarkBot")
    logger.setLevel(getattr(logging, bot_config.LOG_LEVEL))
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler
    file_handler = logging.FileHandler(bot_config.LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ============================================================================
# USER SESSION MANAGEMENT
# ============================================================================
@dataclass
class UserSession:
    """Represents a user's watermarking session"""
    user_id: int
    step: str = "idle"  # idle, waiting_text, waiting_media
    watermark_text: str = ""
    downloaded_file_path: Optional[str] = None
    file_type: Optional[str] = None  # 'photo' or 'video'
    message_ids: List[int] = field(default_factory=list)
    user_message_ids: List[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    
    def add_bot_message(self, message_id: int):
        """Track bot message for later cleanup"""
        if message_id not in self.message_ids:
            self.message_ids.append(message_id)
    
    def add_user_message(self, message_id: int):
        """Track user message for later cleanup"""
        if message_id not in self.user_message_ids:
            self.user_message_ids.append(message_id)
    
    def get_all_message_ids(self) -> List[int]:
        """Get all tracked message IDs"""
        return self.message_ids + self.user_message_ids
    
    def reset(self, keep_file: bool = False):
        """Reset session state but keep watermark text for future use"""
    # After finishing a job, user can directly send new media
    # with the same watermark text without using /w again.
    self.step = "waiting_media"
    if not keep_file:
        # We only clear file-related fields, not the text itself
        self.downloaded_file_path = None
        self.file_type = None
    # Clear tracked message ids
    self.message_ids = []
    self.user_message_ids = []



class SessionManager:
    """Manages all user sessions"""
    
    def __init__(self):
        self._sessions: Dict[int, UserSession] = {}
        self._lock = asyncio.Lock()
    
    async def get_session(self, user_id: int) -> UserSession:
        """Get or create a session for user"""
        async with self._lock:
            if user_id not in self._sessions:
                self._sessions[user_id] = UserSession(user_id=user_id)
                logger.info(f"Created new session for user {user_id}")
            return self._sessions[user_id]
    
    async def has_unfinished_session(self, user_id: int) -> bool:
        """Check if user has an unfinished session with downloaded file"""
        session = await self.get_session(user_id)
        if session.downloaded_file_path and os.path.exists(session.downloaded_file_path):
            return True
        return False
    
    async def clear_session(self, user_id: int):
        """Clear session and cleanup files"""
        async with self._lock:
            if user_id in self._sessions:
                session = self._sessions[user_id]
                # Cleanup file if exists
                if session.downloaded_file_path and os.path.exists(session.downloaded_file_path):
                    try:
                        os.remove(session.downloaded_file_path)
                        logger.info(f"Cleaned up file: {session.downloaded_file_path}")
                    except Exception as e:
                        logger.error(f"Failed to cleanup file: {e}")
                
                self._sessions[user_id] = UserSession(user_id=user_id)


# Global session manager
session_manager = SessionManager()

# Thread pool for CPU-intensive tasks
executor = ThreadPoolExecutor(max_workers=4)

# ============================================================================
# PYROGRAM CLIENT SETUP
# ============================================================================
app = Client(
    "watermark_bot",
    api_id=telegram_config.API_ID,
    api_hash=telegram_config.API_HASH,
    bot_token=telegram_config.BOT_TOKEN
)

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def create_progress_bar(current: int, total: int, width: int = 20) -> str:
    """Create a text-based progress bar"""
    percentage = (current / total) * 100 if total > 0 else 0
    filled = int(width * current / total) if total > 0 else 0
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {percentage:.1f}%"


class ProgressCallback:
    """Progress callback handler with rate limiting"""
    
    def __init__(self, message: Message, action: str = "Downloading"):
        self.message = message
        self.action = action
        self.last_update = 0
        self.update_interval = bot_config.PROGRESS_UPDATE_INTERVAL
    
    async def __call__(self, current: int, total: int):
        """Called by Pyrogram during file transfer"""
        now = time.time()
        
        # Only update every N seconds to avoid FloodWait
        if now - self.last_update < self.update_interval:
            return
        
        self.last_update = now
        
        progress_bar = create_progress_bar(current, total)
        text = (
            f"📥 **{self.action}...**\n\n"
            f"{progress_bar}\n"
            f"📊 {format_size(current)} / {format_size(total)}"
        )
        
        try:
            await self.message.edit_text(text)
        except FloodWait as e:
            logger.warning(f"FloodWait: sleeping for {e.value} seconds")
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.debug(f"Progress update error: {e}")

# ============================================================================
# WATERMARK CREATION (PILLOW)
# ============================================================================
def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Get font with fallback options"""
    font_paths = [
        watermark_config.FONT_PATH,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    
    logger.warning("No TrueType font found, using default")
    return ImageFont.load_default()


def draw_rounded_rectangle(
    draw: ImageDraw.Draw,
    coords: Tuple[int, int, int, int],
    radius: int,
    fill: Tuple[int, int, int, int]
):
    """Draw a rounded rectangle"""
    x1, y1, x2, y2 = coords
    
    # Draw main rectangles
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    
    # Draw corners
    draw.ellipse([x1, y1, x1 + 2*radius, y1 + 2*radius], fill=fill)
    draw.ellipse([x2 - 2*radius, y1, x2, y1 + 2*radius], fill=fill)
    draw.ellipse([x1, y2 - 2*radius, x1 + 2*radius, y2], fill=fill)
    draw.ellipse([x2 - 2*radius, y2 - 2*radius, x2, y2], fill=fill)


def create_watermark_image(
    text: str,
    font_size: Optional[int] = None,
    padding: Optional[int] = None
) -> Image.Image:
    """
    Create a watermark image with text on a semi-transparent rounded rectangle
    Returns a PIL Image with RGBA mode
    """
    font_size = font_size or watermark_config.FONT_SIZE
    padding = padding or watermark_config.BOX_PADDING
    
    font = get_font(font_size)
    
    # Calculate text size
    dummy_img = Image.new('RGBA', (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    
    # Get text bounding box
    bbox = dummy_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Calculate total size with padding
    total_width = text_width + (padding * 2)
    total_height = text_height + (padding * 2)
    
    # Create watermark image
    watermark = Image.new('RGBA', (total_width, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark)
    
    # Draw rounded rectangle background
    draw_rounded_rectangle(
        draw,
        (0, 0, total_width - 1, total_height - 1),
        watermark_config.BOX_CORNER_RADIUS,
        watermark_config.BOX_COLOR
    )
    
    # Draw text centered
    text_x = padding
    text_y = padding - bbox[1]  # Adjust for text baseline
    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill=watermark_config.FONT_COLOR
    )
    
    return watermark

# ============================================================================
# IMAGE PROCESSING
# ============================================================================
def process_image(
    input_path: str,
    watermark_text: str,
    output_path: str
) -> bool:
    """
    Process an image and add watermark at bottom-right corner
    Returns True on success, False on failure
    """
    try:
        logger.info(f"Processing image: {input_path}")
        
        # Open the original image
        with Image.open(input_path) as img:
            # Convert to RGBA for processing
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # Create watermark
            watermark = create_watermark_image(watermark_text)
            
            # Calculate position (bottom-right with margin)
            margin = watermark_config.MARGIN
            x = img.width - watermark.width - margin
            y = img.height - watermark.height - margin
            
            # Ensure position is not negative
            x = max(0, x)
            y = max(0, y)
            
            # Paste watermark
            img.paste(watermark, (x, y), watermark)
            
            # Convert back to RGB for saving (if needed for JPEG)
            if output_path.lower().endswith(('.jpg', '.jpeg')):
                img = img.convert('RGB')
            
            # Save with high quality
            img.save(output_path, quality=95)
            
        logger.info(f"Image processed successfully: {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Image processing failed: {e}", exc_info=True)
        return False

# ============================================================================
# VIDEO PROCESSING (MOVIEPY)
# ============================================================================
def create_watermark_clip(
    watermark_img: Image.Image,
    duration: float,
    position: Tuple[int, int],
    video_size: Tuple[int, int],
    start_time: float,
    crossfade_in: float = 0.5,
    crossfade_out: float = 0.5
) -> ImageClip:
    """
    Create a watermark clip with crossfade effects
    """
    # Convert PIL Image to numpy array
    watermark_array = np.array(watermark_img)
    
    # Create ImageClip
    clip = (ImageClip(watermark_array, transparent=True)
            .set_duration(duration)
            .set_position(position)
            .set_start(start_time))
    
    # Apply crossfade effects
    actual_crossfade_in = min(crossfade_in, duration / 2)
    actual_crossfade_out = min(crossfade_out, duration / 2)
    
    if actual_crossfade_in > 0:
        clip = clip.crossfadein(actual_crossfade_in)
    if actual_crossfade_out > 0:
        clip = clip.crossfadeout(actual_crossfade_out)
    
    return clip


def get_random_position(
    video_width: int,
    video_height: int,
    watermark_width: int,
    watermark_height: int,
    margin: int
) -> Tuple[int, int]:
    """
    Get a random position for watermark ensuring it stays within bounds
    """
    max_x = max(margin, video_width - watermark_width - margin)
    max_y = max(margin, video_height - watermark_height - margin)
    
    x = random.randint(margin, max_x)
    y = random.randint(margin, max_y)
    
    return (x, y)


def process_video(
    input_path: str,
    watermark_text: str,
    output_path: str,
    progress_callback: Optional[callable] = None
) -> Tuple[bool, Optional[str]]:
    """Process a video using ffmpeg with a static text watermark.

    This version is optimized for speed:
    - Uses ffmpeg directly instead of MoviePy
    - Copies audio stream without re-encoding
    - Keeps watermark text support
    Returns (success, error_message).
    """
    try:
        logger.info(f"Processing video with ffmpeg: {input_path}")

        # Create watermark image from text (re-uses existing styling)
        watermark_img = create_watermark_image(watermark_text)
        wm_tmp_path = os.path.join(
            bot_config.OUTPUT_DIR,
            f"wm_overlay_{int(time.time())}.png"
        )
        watermark_img.save(wm_tmp_path, "PNG")

        # Build ffmpeg command
        # Bottom-right overlay with margin; video re-encoded with configured codec
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
            "-i", wm_tmp_path,
            "-filter_complex", f"overlay=W-w-{watermark_config.MARGIN}:H-h-{watermark_config.MARGIN}",
            "-c:v", watermark_config.VIDEO_CODEC,
            "-preset", watermark_config.VIDEO_PRESET,
            "-c:a", "copy",
            output_path,
        ]

        logger.debug("Running ffmpeg command: %s", " ".join(ffmpeg_cmd))
        result = subprocess.run(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Cleanup temporary watermark image
        try:
            if os.path.exists(wm_tmp_path):
                os.remove(wm_tmp_path)
        except Exception as e:
            logger.warning(f"Failed to remove temp watermark image: {e}")

        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr[-400:]} ")
            return (False, "Video processing failed at ffmpeg stage.")

        # Validate size
        if not os.path.exists(output_path):
            logger.error("Output file was not created by ffmpeg")
            return (False, "Output file was not created.")

        output_size = os.path.getsize(output_path)
        if output_size > bot_config.MAX_FILE_SIZE:
            logger.error(f"Output file too large: {format_size(output_size)}")
            return (
                False,
                f"Output file is too large ({format_size(output_size)}). Maximum allowed is {bot_config.MAX_FILE_SIZE_DISPLAY}.",
            )

        logger.info(f"Video processed successfully via ffmpeg: {output_path} ({format_size(output_size)})")
        return (True, None)

    except Exception as e:
        logger.error(f"ffmpeg processing error: {e}", exc_info=True)
        return (False, str(e))
    finally:
        # progress_callback is unused in this fast path, but kept for API compatibility
        pass


def (*file_paths: str):
    """Delete local files"""
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"Deleted local file: {path}")
            except Exception as e:
                logger.error(f"Failed to delete {path}: {e}")

# ============================================================================
# COMMAND HANDLERS
# ============================================================================
@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Handle /start command"""
    session = await session_manager.get_session(message.from_user.id)
    session.add_user_message(message.id)
    
    text = (
        "👋 **Welcome to Watermark Bot!**\n\n"
        "I can add text watermarks to your images and videos.\n\n"
        "**Commands:**\n"
        "• `/w` - Start watermarking process\n"
        "• `/cancel` - Cancel current operation\n\n"
        "**Features:**\n"
        "• 🖼 Images: Watermark at bottom-right\n"
        "• 🎬 Videos: Dynamic moving watermark with smooth transitions\n\n"
        "Use `/w` to get started!"
    )
    
    msg = await message.reply_text(text)
    session.add_bot_message(msg.id)
    logger.info(f"User {message.from_user.id} started the bot")


@app.on_message(filters.command("w"))
async def watermark_command(client: Client, message: Message):
    """Handle /w command - Entry point for watermarking"""
    user_id = message.from_user.id
    session = await session_manager.get_session(user_id)
    session.add_user_message(message.id)
    
    logger.info(f"User {user_id} started watermark command")
    
    # Check for unfinished session
    if await session_manager.has_unfinished_session(user_id):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📂 Resume Session", callback_data="resume_session"),
                InlineKeyboardButton("🔄 Start Fresh", callback_data="start_fresh")
            ]
        ])
        
        msg = await message.reply_text(
            "🔔 **Unfinished Session Found!**\n\n"
            "You have a previously downloaded file waiting.\n"
            "Would you like to resume or start fresh?",
            reply_markup=keyboard
        )
        session.add_bot_message(msg.id)
        return
    
    # Start fresh session
    await start_fresh_session(client, message, session)


async def start_fresh_session(client: Client, message: Message, session: UserSession):
    """Start a fresh watermarking session"""
    # Clear any existing files
    await session_manager.clear_session(session.user_id)
    session = await session_manager.get_session(session.user_id)
    
    session.step = "waiting_text"
    
    msg = await message.reply_text(
        "✏️ **Enter Watermark Text**\n\n"
        "Please send the text you want to use as watermark.\n\n"
        "Example: `© 2024 MyBrand`"
    )
    session.add_bot_message(msg.id)


@app.on_callback_query(filters.regex(r"^(resume_session|start_fresh)$"))
async def handle_session_choice(client: Client, callback: CallbackQuery):
    """Handle session choice callback"""
    user_id = callback.from_user.id
    session = await session_manager.get_session(user_id)
    
    await callback.answer()
    
    if callback.data == "resume_session":
        # Resume with existing file
        session.step = "waiting_text"
        
        msg = await callback.message.edit_text(
            "✏️ **Session Resumed!**\n\n"
            "Your previous file is ready.\n"
            "Please send the watermark text:"
        )
        session.add_bot_message(msg.id)
        logger.info(f"User {user_id} resumed session")
        
    else:  # start_fresh
        await start_fresh_session(client, callback.message, session)
        logger.info(f"User {user_id} started fresh session")


@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    """Handle /cancel command"""
    user_id = message.from_user.id
    session = await session_manager.get_session(user_id)
    
    # Cleanup
    await cleanup_session_messages(client, message.chat.id, session)
    await session_manager.clear_session(user_id)
    
    await message.reply_text(
        "❌ **Operation Cancelled**\n\n"
        "All session data has been cleared.\n"
        "Use `/w` to start again."
    )
    
    logger.info(f"User {user_id} cancelled operation")

# ============================================================================
# TEXT INPUT HANDLER
# ============================================================================
@app.on_message(filters.text & filters.private & ~filters.command(["start", "w", "cancel"]))
async def handle_text_input(client: Client, message: Message):
    """Handle text input for watermark"""
    user_id = message.from_user.id
    session = await session_manager.get_session(user_id)
    session.add_user_message(message.id)
    
    if session.step != "waiting_text":
        return
    
    watermark_text = message.text.strip()
    
    if not watermark_text:
        msg = await message.reply_text("❌ Watermark text cannot be empty. Please try again:")
        session.add_bot_message(msg.id)
        return
    
    if len(watermark_text) > 100:
        msg = await message.reply_text("❌ Watermark text is too long (max 100 characters). Please try again:")
        session.add_bot_message(msg.id)
        return
    
    session.watermark_text = watermark_text
    session.step = "waiting_media"
    
    msg = await message.reply_text(
        f"✅ **Watermark Text Saved:**\n`{watermark_text}`\n\n"
        "📤 **Now send me a Photo or Video**\n\n"
        f"⚠️ Maximum file size: {bot_config.MAX_FILE_SIZE_DISPLAY}"
    )
    session.add_bot_message(msg.id)
    
    logger.info(f"User {user_id} set watermark text: {watermark_text[:30]}...")

# ============================================================================
# MEDIA INPUT HANDLER
# ============================================================================
@app.on_message((filters.photo | filters.video | filters.document) & filters.private)
async def handle_media_input(client: Client, message: Message):
    """Handle photo/video input"""
    user_id = message.from_user.id
    session = await session_manager.get_session(user_id)
    session.add_user_message(message.id)
    
    if session.step != "waiting_media":
        msg = await message.reply_text(
            "⚠️ Please use `/w` command first to start the watermarking process."
        )
        session.add_bot_message(msg.id)
        return
    
    # Determine media type and get file
    if message.photo:
        file = message.photo
        file_size = file.file_size
        file_type = "photo"
        file_ext = ".jpg"
    elif message.video:
        file = message.video
        file_size = file.file_size
        file_type = "video"
        file_ext = ".mp4"
    elif message.document:
        file = message.document
        file_size = file.file_size
        mime = file.mime_type or ""
        
        if mime.startswith("image/"):
            file_type = "photo"
            file_ext = Path(file.file_name).suffix if file.file_name else ".jpg"
        elif mime.startswith("video/"):
            file_type = "video"
            file_ext = Path(file.file_name).suffix if file.file_name else ".mp4"
        else:
            msg = await message.reply_text(
                "❌ Unsupported file type. Please send an image or video."
            )
            session.add_bot_message(msg.id)
            return
    else:
        return
    
    # Check file size
    if file_size > bot_config.MAX_FILE_SIZE:
        msg = await message.reply_text(
            f"❌ **File Too Large!**\n\n"
            f"Your file: {format_size(file_size)}\n"
            f"Maximum allowed: {bot_config.MAX_FILE_SIZE_DISPLAY}\n\n"
            "Please send a smaller file."
        )
        session.add_bot_message(msg.id)
        logger.warning(f"User {user_id} sent file too large: {format_size(file_size)}")
        return
    
    logger.info(f"User {user_id} sent {file_type}: {format_size(file_size)}")
    
    # Send progress message
    progress_msg = await message.reply_text("📥 **Starting download...**")
    session.add_bot_message(progress_msg.id)
    
    # Download file
    timestamp = int(time.time())
    download_path = os.path.join(
        bot_config.DOWNLOAD_DIR, 
        f"{user_id}_{timestamp}{file_ext}"
    )
    
    try:
        progress_callback = ProgressCallback(progress_msg, "Downloading")
        
        downloaded_path = await message.download(
            file_name=download_path,
            progress=progress_callback
        )
        
        session.downloaded_file_path = downloaded_path
        session.file_type = file_type
        
        await progress_msg.edit_text("✅ **Download complete!** Processing...")
        
    except Exception as e:
        logger.error(f"Download failed for user {user_id}: {e}", exc_info=True)
        await progress_msg.edit_text(f"❌ **Download failed:** {str(e)}")
        session.step = "idle"
        return
    
    # Process the file
    await process_and_send(client, message, session, progress_msg)


async def process_and_send(
    client: Client,
    message: Message,
    session: UserSession,
    progress_msg: Message
):
    """Process the media and send result"""
    user_id = session.user_id
    input_path = session.downloaded_file_path
    file_type = session.file_type
    watermark_text = session.watermark_text
    
    # Prepare output path
    timestamp = int(time.time())
    ext = Path(input_path).suffix
    output_path = os.path.join(
        bot_config.OUTPUT_DIR,
        f"{user_id}_{timestamp}_watermarked{ext}"
    )
    
    try:
        if file_type == "photo":
            await progress_msg.edit_text("🎨 **Processing image...**")
            
            # Run image processing in executor
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                executor,
                process_image,
                input_path,
                watermark_text,
                output_path
            )
            
            if not success:
                await progress_msg.edit_text("❌ **Image processing failed.** Please try again.")
                return
            
            await progress_msg.edit_text("📤 **Uploading...**")
            
            # Send watermarked image
            result_msg = await message.reply_photo(
                photo=output_path,
                caption=f"✅ **Watermarked Image**\n\n💬 Watermark: `{watermark_text}`"
            )
            
        else:  # video
            await progress_msg.edit_text(
                "🎬 **Processing video...**\n\n"
                "⏳ This may take a while depending on video length.\n"
                "Please wait..."
            )
            
            # Run video processing in executor
            loop = asyncio.get_event_loop()
            success, error = await loop.run_in_executor(
                executor,
                process_video,
                input_path,
                watermark_text,
                output_path
            )
            
            if not success:
                await progress_msg.edit_text(f"❌ **Video processing failed:**\n{error}")
                return
            
            # Check output size before uploading
            output_size = os.path.getsize(output_path)
            if output_size > bot_config.MAX_FILE_SIZE:
                await progress_msg.edit_text(
                    f"❌ **Output file too large!**\n\n"
                    f"Processed file: {format_size(output_size)}\n"
                    f"Maximum: {bot_config.MAX_FILE_SIZE_DISPLAY}\n\n"
                    "Try with a shorter or lower resolution video."
                )
                await cleanup_local_files(output_path)
                return
            
            await progress_msg.edit_text("📤 **Uploading video...**")
            
            # Create upload progress callback
            upload_progress = ProgressCallback(progress_msg, "Uploading")
            
            # Send watermarked video
            result_msg = await message.reply_video(
                video=output_path,
                caption=f"✅ **Watermarked Video**\n\n💬 Watermark: `{watermark_text}`",
                progress=upload_progress
            )
        
        logger.info(f"User {user_id}: Successfully processed and sent {file_type}")
        
        # Cleanup session messages
        await cleanup_session_messages(client, message.chat.id, session)
        
        # Cleanup local files
        await cleanup_local_files(input_path, output_path)
        
        # Reset session
        session.reset()
        
    except FloodWait as e:
        logger.warning(f"FloodWait during upload: {e.value}s")
        await asyncio.sleep(e.value)
        await process_and_send(client, message, session, progress_msg)
        
    except Exception as e:
        logger.error(f"Processing error for user {user_id}: {e}", exc_info=True)
        await progress_msg.edit_text(
            f"❌ **An error occurred:**\n`{str(e)}`\n\n"
            "Please try again with `/w`"
        )
        
        # Cleanup on error
        await cleanup_local_files(input_path, output_path)
        session.reset()

# ============================================================================
# ERROR HANDLER
# ============================================================================
# @app.on_error()
# async def error_handler(client: Client, error: Exception):
#     """Global error handler"""
#     logger.error(f"Unhandled error: {error}", exc_info=True)

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================
def main():
    """Main entry point"""
    logger.info("=" * 50)
    logger.info("Starting Watermark Bot...")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Download directory: {bot_config.DOWNLOAD_DIR}")
    logger.info(f"Output directory: {bot_config.OUTPUT_DIR}")
    logger.info("=" * 50)
    
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
