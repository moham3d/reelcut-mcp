"""Configuration management for ReelCut."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


FILMCRAFT_CANDIDATES = [
    r"C:\Program Files\ArtCraft\FilmCraft\filmcraft-cli.exe",
    "/usr/local/bin/filmcraft-cli",
    "/opt/artcraft/filmcraft-cli",
    "/usr/bin/filmcraft-cli",
]


def find_filmcraft_cli() -> str | None:
    """Locate FilmCraft headless CLI (env -> portable bin/ -> PATH -> OS defaults)."""
    env_fc = os.getenv("REELCUT_FILMCRAFT_PATH", "").strip()
    if env_fc and Path(env_fc).is_file():
        return env_fc

    repo_bin = Path(__file__).resolve().parents[2] / "bin"
    for bin_name in ("filmcraft-cli.exe", "filmcraft-cli"):
        local_bin = repo_bin / bin_name
        if local_bin.is_file():
            return str(local_bin)

    on_path = shutil.which("filmcraft-cli")
    if on_path:
        return on_path

    for candidate in FILMCRAFT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


WINDOWS_ARABIC_FONT_NAMES = [
    "PlaypenSansArabic[wght].ttf",
    "MarkaziText[wght].ttf",
    "PlaypenSansArabic-Bold.ttf",
    "Cairo-Bold.ttf",
    "Tajawal-Bold.ttf",
    "tahomabd.ttf",
    "arialbd.ttf",
    "segoeuib.ttf",
    "tahoma.ttf",
]


def find_default_arabic_font() -> str:
    """Locate the best available Arabic-capable TTF font in bundled or Windows fonts."""
    env_font = os.getenv("REELCUT_FONT_PATH", "").strip()
    if env_font and Path(env_font).is_file():
        return env_font

    repo_fonts = Path(__file__).resolve().parents[2] / "assets" / "fonts"
    font_dirs = [
        repo_fonts,
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
        Path(r"C:\Windows\Fonts"),
        Path("/usr/share/fonts/truetype"),
        Path("/usr/share/fonts"),
        Path.home() / ".local" / "share" / "fonts",
    ]
    for name in WINDOWS_ARABIC_FONT_NAMES:
        for folder in font_dirs:
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    # Linux fallback: common Noto Sans Arabic / DejaVu Sans
    for linux_font in (
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(linux_font).is_file():
            return linux_font

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
    filmcraft_path: str | None = None
    render_backend: str = "auto"
    color_preset: str = "none"
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
            filmcraft_path=find_filmcraft_cli(),
            render_backend=os.getenv("REELCUT_RENDER_BACKEND", "auto").strip().lower() or "auto",
            color_preset=os.getenv("REELCUT_COLOR_PRESET", "none").strip().lower() or "none",
            font_path=find_default_arabic_font(),
        )
