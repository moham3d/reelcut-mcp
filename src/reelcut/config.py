"""Configuration management for ReelCut."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


WINDOWS_ARABIC_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\Cairo-Bold.ttf",
    r"C:\Windows\Fonts\Tajawal-Bold.ttf",
    r"C:\Windows\Fonts\tahomabd.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
]


def find_default_arabic_font() -> str:
    """Locate the best available bold Arabic-capable TTF font on Windows."""
    env_font = os.getenv("REELCUT_FONT_PATH", "").strip()
    if env_font and Path(env_font).is_file():
        return env_font

    for candidate in WINDOWS_ARABIC_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate

    raise FileNotFoundError(
        "No Arabic-capable TrueType font found. Set REELCUT_FONT_PATH in .env."
    )


@dataclass(slots=True)
class CaptionStyle:
    """Visual styling for 2-4 word dynamic Arabic Reels captions."""

    words_per_chunk: int = 3
    min_words_per_chunk: int = 2
    max_words_per_chunk: int = 4
    font_size: int = 66
    text_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    highlight_color: tuple[int, int, int, int] = (255, 214, 10, 255)
    stroke_color: tuple[int, int, int, int] = (12, 12, 16, 255)
    stroke_width: int = 6
    pill_bg_color: tuple[int, int, int, int] = (15, 15, 20, 145)
    pill_padding_x: int = 28
    pill_padding_y: int = 16
    pill_radius: int = 22
    overlay_y: int = 1290
    overlay_height: int = 320


@dataclass(slots=True)
class ReelcutConfig:
    """Runtime configuration for local processing and rendering."""

    whisper_model: str = "small"
    whisper_compute: str = "int8"
    language: str = "ar"
    target_width: int = 1080
    target_height: int = 1920
    fps: int = 30
    zoom_factor: float = 1.12
    brolls_dir: Path | None = None
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    ffmpeg_path: str = ""
    font_path: str = ""
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)

    @classmethod
    def load(cls, project_dir: str | Path | None = None) -> "ReelcutConfig":
        """Load configuration from environment variables and optional .env files."""
        repo_root = Path(__file__).resolve().parents[2]
        if (repo_root / ".env").is_file():
            load_dotenv(repo_root / ".env", override=False)
        if project_dir:
            proj_env = Path(project_dir) / ".env"
            if proj_env.is_file():
                load_dotenv(proj_env, override=True)

        brolls_raw = os.getenv("REELCUT_BROLLS_DIR", "").strip()
        brolls_dir = Path(brolls_raw).expanduser().resolve() if brolls_raw else None

        return cls(
            whisper_model=os.getenv("REELCUT_WHISPER_MODEL", "small").strip() or "small",
            whisper_compute=os.getenv("REELCUT_WHISPER_COMPUTE", "int8").strip() or "int8",
            language=os.getenv("REELCUT_LANGUAGE", "ar").strip() or "ar",
            brolls_dir=brolls_dir,
            pexels_api_key=os.getenv("PEXELS_API_KEY", "").strip(),
            pixabay_api_key=os.getenv("PIXABAY_API_KEY", "").strip(),
            ffmpeg_path=os.getenv("REELCUT_FFMPEG_PATH", "").strip(),
            font_path=find_default_arabic_font(),
        )
