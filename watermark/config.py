import os
from dataclasses import dataclass, field
from typing import Tuple

@dataclass
class TelegramConfig:
    """Telegram API Configuration"""
    API_ID: int = field(default_factory=lambda: int(os.environ.get("API_ID", "0")))
    API_HASH: str = field(default_factory=lambda: os.environ.get("API_HASH", ""))
    BOT_TOKEN: str = field(default_factory=lambda: os.environ.get("BOT_TOKEN", ""))

@dataclass
class WatermarkConfig:
    """Watermark Visual Settings"""
    # Font settings
    FONT_PATH: str = field(default="fonts/arial.ttf")  # Relative; fallbacks in code handle
    FONT_SIZE: int = field(default=32)
    FONT_COLOR: Tuple[int, int, int, int] = field(default=(255, 255, 255, 255))  # White RGBA
    
    # Background box settings
    BOX_COLOR: Tuple[int, int, int, int] = field(default=(0, 0, 0, 160))  # Semi-transparent black
    BOX_PADDING: int = field(default=20)
    BOX_CORNER_RADIUS: int = field(default=15)
    
    # Positioning
    MARGIN: int = field(default=30)  # Margin from edges
    
    # Video specific settings
    VIDEO_INTERVAL: float = field(default=5.0)  # Seconds between position changes (unused)
    CROSSFADE_DURATION: float = field(default=0.5)  # Crossfade transition duration (unused)
    
    # Rendering settings
    VIDEO_CODEC: str = field(default="libx264")
    AUDIO_CODEC: str = field(default="aac")  # Unused (-c:a copy)
    VIDEO_PRESET: str = field(default="medium")
    VIDEO_CRF: int = field(default=23)

@dataclass
class BotConfig:
    """General Bot Settings"""
    # File limits
    MAX_FILE_SIZE: int = field(default=2 * 1024 * 1024 * 1024)  # 2GB in bytes
    MAX_FILE_SIZE_DISPLAY: str = field(default="2GB")
    
    # Progress bar settings
    PROGRESS_UPDATE_INTERVAL: float = field(default=5.0)  # Seconds between progress updates
    
    # Temporary directories (Heroku: use /tmp for writability)
    DOWNLOAD_DIR: str = field(default="/tmp/downloads")
    OUTPUT_DIR: str = field(default="/tmp/outputs")
    
    # Logging
    LOG_FILE: str = field(default="bot.log")
    LOG_LEVEL: str = field(default="INFO")

# Create config instances
telegram_config = TelegramConfig()
watermark_config = WatermarkConfig()
bot_config = BotConfig()

# Ensure directories exist (skip for /tmp on Heroku; it's auto-managed)
try:
    os.makedirs("fonts", exist_ok=True)
except:
    pass  # Ignore on Heroku if non-writable
