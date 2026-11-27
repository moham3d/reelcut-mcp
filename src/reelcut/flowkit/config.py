"""Where the standalone CLI points itself.

One Chrome profile, one debug port, one Google `/u/<n>` index — that is the
whole identity model for a personal tool. Env-overridable, and every flag has a
default that matches the machine this repo was built on.

    FLOW_PROFILE   Chrome profile directory   (default ~/chrome-automation)
    FLOW_PORT      CDP port                   (default 9223)
    FLOW_U_INDEX   Google account index       (default 0)
    FLOW_CHROME_EXE  path to chrome.exe

Deliberately no accounts.py: the CLI must not inherit YT-Agent's channel and
render-account assumptions. Copy, don't import - the pipeline has its own.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_PROFILE = os.environ.get('FLOW_PROFILE') or str(
    Path.home() / 'chrome-automation')
DEFAULT_PORT = int(os.environ.get('FLOW_PORT') or 9223)
DEFAULT_U = int(os.environ.get('FLOW_U_INDEX') or 0)

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')


def chrome_exe() -> str:
    """The Chrome binary, preferring an explicit path over a PATH lookup."""
    cands = [
        os.environ.get('FLOW_CHROME_EXE'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        str(Path.home() / 'AppData/Local/Google/Chrome/Application/chrome.exe'),
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    return os.environ.get('FLOW_CHROME_EXE') or 'chrome'


def email_from_labels(labels: list[str]) -> str:
    """The signed-in email out of Google's "Google Account: Name (email)" label."""
    for label in labels or []:
        m = _EMAIL_RE.search(label or '')
        if m:
            return m.group(0)
    return ''


def cdp_url(port: int) -> str:
    return f'http://127.0.0.1:{port}'
