

import os
from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class TelegramConfig:
    """Telegram API Configuration"""
    API_ID = int(os.environ.get("API_ID", "37360333")) 
    API_HASH = os.environ.get("API_HASH", "66ad4da58fcafc35e6eb6762cc562334")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "8026693881:AAGvD1AT4eQLlxV2bd7T5fUv13oHapxk0dU")


@dataclass
class WatermarkConfig:
    """Watermark Visual Settings"""
    # Font settings
    FONT_PATH: str = "/arial.ttf"
    FONT_SIZE: int = 32
    FONT_COLOR: Tuple[int, int, int, int] = (255, 255, 255, 255)  # White RGBA
    
    # Background box settings
    BOX_COLOR: Tuple[int, int, int, int] = (0, 0, 0, 160)  # Semi-transparent black
    BOX_PADDING: int = 20
    BOX_CORNER_RADIUS: int = 15
    
    # Positioning
    MARGIN: int = 30  # Margin from edges
    
    # Video specific settings
    VIDEO_INTERVAL: float = 5.0  # Seconds between position changes
    CROSSFADE_DURATION: float = 0.5  # Crossfade transition duration
    
    # Rendering settings
    VIDEO_CODEC: str = "libx264"
    AUDIO_CODEC: str = "aac"
    VIDEO_PRESET: str = "medium"
    VIDEO_CRF: int = 23


@dataclass
class BotConfig:
    """General Bot Settings"""
    # File limits
    MAX_FILE_SIZE: int = 2 * 1024 * 1024 * 1024  # 2GB in bytes
    MAX_FILE_SIZE_DISPLAY: str = "2GB"
    
    # Progress bar settings
    PROGRESS_UPDATE_INTERVAL: float = 5.0  # Seconds between progress updates
    
    # Temporary directories
    DOWNLOAD_DIR: str = "downloads"
    OUTPUT_DIR: str = "outputs"
    
    # Logging
    LOG_FILE: str = "bot.log"
    LOG_LEVEL: str = "INFO"


# Create config instances
telegram_config = TelegramConfig()
watermark_config = WatermarkConfig()
bot_config = BotConfig()


# Ensure directories exist
os.makedirs(bot_config.DOWNLOAD_DIR, exist_ok=True)
os.makedirs(bot_config.OUTPUT_DIR, exist_ok=True)
os.makedirs("fonts", exist_ok=True)