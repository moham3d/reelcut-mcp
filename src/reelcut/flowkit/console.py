"""Windows-safe console output.

The libraries are Arabic (`Islamic_Reminder_Variations.md`) and the run log
prints selected titles and quotes verbatim. On Windows a stdout that is
redirected, piped, or attached to a legacy code page is cp1252/cp437, and
`print()` of an Arabic title raises `UnicodeEncodeError: 'charmap' codec ...`.

That is not a cosmetic problem: it killed a rehearsal at `select_content`
(2026-11-05) - the FIRST step, before any browser work - because
`RunReport.log` printed the Islamic title. Under `schtasks` there is no
console at all and the encoding is whatever the system code page happens to
be, so the same crash would hit every unattended Islamic run while every
interactive one looked fine. Logging must never be able to take down a spend.

Two layers, cheapest first:

* `ensure_utf8()` re-points stdout/stderr at UTF-8 (errors='replace'). Called
  once at the start of every CLI entry point.
* `emit()` is a `print` that degrades to the stream's own encoding with
  replacement characters instead of raising. Used by every logger, because a
  stream may not be reconfigurable (a test harness, a pipe wrapper) and the
  log must still survive.
"""

from __future__ import annotations

import sys


def ensure_utf8() -> None:
    """Best-effort: make stdout/stderr able to carry any content we log."""
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, 'reconfigure'):
            continue
        try:
            enc = (getattr(stream, 'encoding', '') or '')
            flat = enc.lower().replace('-', '').replace('_', '')
            if flat != 'utf8':
                stream.reconfigure(encoding='utf-8', errors='replace')
            else:
                stream.reconfigure(errors='replace')
        except (AttributeError, ValueError, OSError):
            # A stream that cannot be reconfigured still gets emit()'s
            # fallback; nothing to do here.
            pass


def emit(text: str) -> None:
    """`print(text, flush=True)` that cannot raise on an unencodable char."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        stream = sys.stdout
        enc = getattr(stream, 'encoding', None) or 'ascii'
        try:
            safe = text.encode(enc, 'replace').decode(enc, 'replace')
        except (LookupError, UnicodeError):
            safe = text.encode('ascii', 'replace').decode('ascii')
        try:
            stream.write(safe + '\n')
            stream.flush()
        except Exception:
            # Last resort: the run continues even if the console is gone.
            pass
