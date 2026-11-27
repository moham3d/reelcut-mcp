"""Friendly, forgiving argument normalisation.

The CLI is meant to be driven by another agent, which will not remember that
the option is spelled "9:16" rather than "9x16", or that duration is "10s"
rather than "10". Normalise here, once, and fail with a message that names the
accepted values - never with a raw Playwright or argparse error.

Veo models are deliberately NOT supported: this CLI must never select one, so
`model("veo…")` raises instead of being passed through.
"""

from __future__ import annotations

from .errors import Blocked

ASPECTS = {
    '9:16': '9:16', '9x16': '9:16', '916': '9:16', 'portrait': '9:16',
    'vertical': '9:16', 'shorts': '9:16',
    '16:9': '16:9', '16x9': '16:9', '169': '16:9', 'landscape': '16:9',
    'wide': '16:9',
    '1:1': '1:1', '1x1': '1:1', 'square': '1:1',
    '4:3': '4:3', '4x3': '4:3',
    '3:4': '3:4', '3x4': '3:4',
}

DURATIONS = {'4': '4s', '4s': '4s', '6': '6s', '6s': '6s', '8': '8s',
             '8s': '8s', '10': '10s', '10s': '10s'}

RESOLUTIONS = {'360': '360p', '360p': '360p', 'sd': '360p',
               '720': '720p', '720p': '720p', 'hd': '720p'}

MODELS = {
    'nano': 'Nano Banana 2.1', 'banana': 'Nano Banana 2.1',
    'nano banana': 'Nano Banana 2.1', 'nano banana 2.1': 'Nano Banana 2.1',
    'image': 'Nano Banana 2.1',
    'omni': 'Omni 1.1 Flash', 'omni 1.1': 'Omni 1.1 Flash',
    'omni flash': 'Omni 1.1 Flash', 'omni 1.1 flash': 'Omni 1.1 Flash',
    'flash': 'Omni 1.1 Flash',
    'omni 1.2': 'Omni 1.2 Flash', 'omni12': 'Omni 1.2 Flash',
    'omni 1.2 flash': 'Omni 1.2 Flash', 'omni12flash': 'Omni 1.2 Flash',
    '1.2': 'Omni 1.2 Flash',
}

# Which aspect ratios each mode actually offers in the composer (measured
# 2026-11-08): images get five, video only landscape and portrait, on every
# video model including Omni 1.2.
IMAGE_ASPECTS = ('16:9', '4:3', '1:1', '3:4', '9:16')
VIDEO_ASPECTS = ('16:9', '9:16')

# Omni 1.1 offers fixed stops; Omni 1.2 offers a 3..15 second slider.
DURATION_STOPS = {'Omni 1.1 Flash': (4, 6, 8, 10),
                  'Omni 1.2 Flash': tuple(range(3, 16))}

THINKING = {'low': 'Low', 'medium': 'Medium', 'med': 'Medium', 'high': 'High',
            'extra': 'Extra thorough', 'extra thorough': 'Extra thorough'}

MODES = {'image': 'Image', 'img': 'Image', 'video': 'Video', 'vid': 'Video'}
SUBMODES = {'frames': 'Frames', 'frame': 'Frames',
            'ingredients': 'Ingredients', 'ingredient': 'Ingredients',
            'refs': 'Ingredients', 'references': 'Ingredients'}

DOWNLOAD_VARIANTS = {
    '2k': '2K', '2K': '2K', 'original': '720p', 'orig': '720p',
    '720p': '720p', '1080p': '1080p', '1080': '1080p', 'hd': '1080p',
    '4k': '4K', '4K': '4K', 'gif': '270p',
}


def _pick(value: str | None, table: dict[str, str], what: str,
          allowed: str) -> str | None:
    if value is None or value == '':
        return None
    key = str(value).strip().lower()
    if key in table:
        return table[key]
    raise Blocked(f'unknown {what} {value!r}; accepted: {allowed}')


def aspect(value: str | None) -> str | None:
    return _pick(value, ASPECTS, 'aspect ratio',
                 '9:16, 16:9, 1:1, 4:3, 3:4 (or portrait/landscape/square)')


def duration(value: str | None) -> str | None:
    """`4`, `10s`, `15 sec` -> `4s`/`10s`/`15s`.

    Range is 3..15 s because that is what Omni 1.2's slider offers; whether the
    SELECTED model supports the value is checked against the composer (Omni 1.1
    only has 4/6/8/10). A model/duration mismatch is reported there, with the
    price, rather than guessed here.
    """
    if value is None or value == '':
        return None
    v = str(value).strip().lower().replace(' ', '')
    if v.endswith('sec'):
        v = v[:-3] + 's'
    if v.endswith('s'):
        v = v[:-1]
    if not v.isdigit():
        raise Blocked(f'unknown duration {value!r}; use seconds or e.g. 10s')
    n = int(v)
    if n < 3 or n > 15:
        raise Blocked(f'duration must be 3..15 seconds, not {n}; '
                      f'Omni 1.1 accepts 4/6/8/10, Omni 1.2 accepts 3..15')
    return f'{n}s'


def duration_seconds(value: str | None) -> int | None:
    d = duration(value)
    return int(d[:-1]) if d else None


def resolution(value: str | None) -> str | None:
    return _pick(value, RESOLUTIONS, 'resolution', '360p or 720p')


def model(value: str | None) -> str | None:
    if value is None or value == '':
        return None
    key = str(value).strip().lower()
    if 'veo' in key:
        raise Blocked('Veo models are disabled in this CLI by policy; use '
                      '"Omni 1.1 Flash" for video or "Nano Banana 2.1" for '
                      'images')
    if key in MODELS:
        return MODELS[key]
    # Pass through a full, correctly-cased name that Flow itself offers.
    if str(value).strip() in ('Omni 1.1 Flash', 'Nano Banana 2.1'):
        return str(value).strip()
    raise Blocked(f'unknown model {value!r}; accepted: "Omni 1.1 Flash" '
                  f'(video), "Nano Banana 2.1" (images)')


def thinking(value: str | None) -> str | None:
    return _pick(value, THINKING, 'thinking level',
                 'Low, Medium, High, Extra thorough')


def mode(value: str | None) -> str | None:
    return _pick(value, MODES, 'mode', 'Image or Video')


def submode(value: str | None) -> str | None:
    return _pick(value, SUBMODES, 'video sub-mode', 'Frames or Ingredients')


def download(value: str | None) -> str | None:
    return _pick(value, DOWNLOAD_VARIANTS, 'download variant',
                 '2K, 1080p, 720p/Original, 4K, gif')


def count(value: int | None, *, maximum: int = 1) -> int:
    """Outputs per click.

    Flow gives every output of one click the SAME title (two x2 images are both
    "Copper kettle on stone counter"), so they cannot be told apart by name -
    and this CLI never acts by position. Multi-output is therefore refused with
    a pointer to the agent batch path, which produces uniquely named tiles.
    """
    if value is None:
        return 1
    v = int(value)
    if v < 1:
        raise Blocked('--count must be at least 1')
    if v > maximum:
        raise Blocked(
            f'--count {v} is not supported: Flow gives every output of one '
            f'click the same title, so duplicate outputs cannot be resolved by '
            f'name. Use `flow agent --expect {v}` instead - it asks the agent '
            f'for {v} uniquely-named images in one submit.')
    return v
