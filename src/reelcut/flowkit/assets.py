"""Getting files out of Flow: wait for tiles, rename, download, manifest.

The download mechanism is the one `scripts/probe_download.py` settled in
YT-Agent on 2026-10-08 and the arc pipeline has used since: the tile menu's
"Download" only opens a resolution sub-menu; the download itself starts at the
RESOLUTION click, and `page.expect_download()` around THAT click works over an
attached CDP context. Never mix in Browser.setDownloadBehavior - the two fight
over the same browser-level setting and the loser is a paid render.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import Page

from . import pages
from .errors import Blocked

# Preference order per media type; the first token that appears in a sub-menu
# item wins. Images are offered in 2K on the image path, video in the upscale
# ladder; both lists are harmless to each other.
_PREFER = {
    'image': ('2K', '1080p', '720p', '4K', 'Original'),
    'video': ('1080p', '720p', '4K', '270p', 'Original'),
}


def _norm(s: str) -> str:
    return ' '.join((s or '').split())


def _looks_like_prompt(name: str, prompt: str) -> bool:
    """Flow labels a generating tile with the PROMPT and a finished one with a
    short title ("Old brass compass on map"). The prompt-labelled node never
    grows a Download action; it is a placeholder, not the asset."""
    p = ' '.join((prompt or '').split()).lower()
    n = ' '.join((name or '').split()).lower()
    if not p or not n:
        return False
    return n == p or (n.startswith(p[:32]) and len(n) > 45)


def wait_new_tiles(page: Page, before: set[str], want: int = 1,
                   timeout_s: int = 300, poll_s: int = 4,
                   prompt: str = '', log=print) -> list[dict]:
    """Wait for `want` new IMAGE/VIDEO tiles that are actually rendered.

    Three signals, all needed (2026-11-06):
    * the tile is IMAGE/VIDEO, not a collection or a prompt placeholder;
    * an image tile has a real <img> (naturalWidth > 0) - the placeholder does
      not, and its context menu offers only `delete`;
    * the name has SETTLED across two polls, because Flow renames the tile
      from the prompt to a short title as the render finishes.
    """
    # The grid virtualizes: only visible rows exist, and the newest tiles are in
    # the top row. Every other grid read scrolls to top first; this one used to
    # skip it, so a large or backgrounded project could read zero new tiles and
    # raise "did not settle" - the mid-chunk death the consumer tries to salvage.
    pages.bring_to_front(page)
    pages.scroll_grid_to_top(page)
    deadline = time.time() + timeout_s
    prev: list[str] = []
    polls = 0
    while time.time() < deadline:
        ts = pages.tiles(page)
        # The prompt-name filter only applies to tiles WITHOUT a rendered
        # image: placeholders are prompt-named and never render, but a FINISHED
        # tile can also carry a prompt-derived name when the agent ignores the
        # naming directive (measured 2026-11-12: a fresh project produced
        # 'Cat battling cucumber' finished tiles; filtering them by name made
        # every harvest falsely time out and re-submit the whole batch).
        new = [t for t in ts
               if t['kind'] in ('image', 'video')
               and (t['name'] or '').strip()
               and t['name'] not in before
               and (t['kind'] == 'video' or t.get('img_w', 0) > 0
                    or not _looks_like_prompt(t['name'], prompt))]
        names = [t['name'] for t in new]
        done = [t for t in new
                if not t['busy']
                and (t['kind'] != 'image' or t.get('img_w', 0) > 0)]
        polls += 1
        if len(done) >= want and names == prev:
            return done[:want]
        if names != prev or polls % 5 == 0:
            log(f'  waiting for {want}: {len(new)} candidate(s), '
                f'{len(done)} rendered')
        prev = names
        if len(done) < want and polls % 5 == 0:
            pages.scroll_grid_to_top(page)  # keep the newest row in the DOM
        page.wait_for_timeout(poll_s * 1000)
    raise Blocked(f'{want} rendered tile(s) did not settle within {timeout_s}s; '
                  f'saw {prev}')

def wait_one_new_tile(page: Page, before_count: int, timeout_s: int = 600,
                      poll_s: int = 4, log=print) -> dict:
    """Wait for exactly ONE new tile, rename it to a unique handle, return it.

    Signals (the grid is virtualized and agents may reuse old titles, so a
    plain count or name diff alone is unreliable - measured 2026-11-12 where a
    new row pushed an old row out of the DOM and the count stayed flat):
    * a rendered top-left tile whose name was not there before, OR
    * the per-name rendered histogram grew (a finished duplicate of an old
      title is still a NEW tile).
    The winning tile is renamed by ELEMENT to a unique stamp so the download
    can never be ambiguous.
    """
    from collections import Counter

    def _snapshot():
        pages.bring_to_front(page)
        pages.scroll_grid_to_top(page)
        rows = pages.tiles(page)
        rendered = [r for r in rows
                    if r['kind'] in ('image', 'video') and not r['busy']
                    and (r['kind'] != 'image' or r.get('img_w', 0) > 0)]
        return rows, rendered, Counter(r['name'] for r in rendered)

    _, before_rendered, before_hist = _snapshot()
    before_names = {r['name'] for r in before_rendered}
    deadline = time.time() + timeout_s
    stamp = time.strftime('%H%M%S')
    unique = f'one_{stamp}'
    last = 0
    while time.time() < deadline:
        rows, rendered, hist = _snapshot()
        last = len(rows)
        winner = None
        if rendered:
            top = rendered[0]                     # top-left = newest
            if top['name'] not in before_names:
                winner = top
            else:
                grew = {n: c - before_hist.get(n, 0)
                        for n, c in hist.items() if c > before_hist.get(n, 0)}
                if grew:
                    winner = next((r for r in rendered
                                   if grew.get(r['name'])), rendered[0])
        if winner is not None:
            tile = pages.find_tile_by_name(page, winner['name'], exact=True)
            if tile.count() != 1:
                tile = pages.find_tile_by_name(page, winner['name'])
            if tile.count() < 1:
                raise Blocked(f'tile {winner["name"]!r} vanished before rename')
            pages.rename_tile(page, tile.first, unique, log=log)
            deadline_ok = time.time() + 10
            while time.time() < deadline_ok:
                if pages.find_tile_by_name(page, unique,
                                           exact=True).count() == 1:
                    log(f'  newest tile -> {unique!r}')
                    return {'name': unique, 'kind': winner['kind']}
                page.wait_for_timeout(1000)
            log(f'  rename to {unique!r} unconfirmed; using {winner["name"]!r}')
            return {'name': winner['name'], 'kind': winner['kind']}
        log(f'  waiting for +1 tile: grid {last} rows, '
            f'{sum(hist.values())} rendered (before: {before_count})')
        page.wait_for_timeout(poll_s * 1000)
    raise Blocked(f'no new rendered tile within {timeout_s}s '
                  f'(grid {last} rows)')


def _pick_variant(subs: list[str], kind: str) -> str:
    for token in _PREFER.get(kind, _PREFER['video']):
        for item in subs:
            if token.lower() in item.lower():
                return token   # click_menu_item matches a substring, so the
                # token ('1080p') is the right click label - not the last word
                # of the item ('Upscaled')
    raise Blocked(f'no usable resolution in the Download sub-menu; it reads '
                  f'{subs}')


def download_tile(page: Page, name: str, dest: Path, kind: str = 'video',
                  log=print) -> int:
    """Download one named tile via its context menu. Returns the byte count."""
    pages.scroll_grid_to_top(page)
    tile = pages.find_tile_by_name(page, name, exact=True)
    if tile.count() != 1:
        tile = pages.find_tile_by_name(page, name)
    if tile.count() != 1:
        raise Blocked(f'{tile.count()} tiles match {name!r} - refusing to '
                      f'download by anything but a unique name')
    pages.open_tile_menu(page, tile)
    items = pages.menu_items(page)
    if not any('Download' in i for i in items):
        raise Blocked(f'no Download item in the tile menu; it reads {items}')
    try:
        pages.click_menu_item(page, 'Download', timeout_ms=4000)
    except Exception:  # noqa: BLE001 - only tolerable if the sub-menu opened
        pass
    subs = pages.menu_items(page)
    if not any(any(tok.lower() in s.lower() for tok in _PREFER.get(kind, ()))
               for s in subs):
        raise Blocked(f'the resolution sub-menu did not open; menu reads {subs}')
    choice = _pick_variant(subs, kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=300_000) as dl:
        pages.click_menu_item(page, choice)
    dl.value.save_as(str(dest))
    dest = _true_extension(dest)
    size = dest.stat().st_size
    if size < 50_000:
        raise Blocked(f'downloaded {dest.name} is only {size} bytes - treating '
                      f'it as a failed download')
    log(f'  downloaded {dest.name} ({size:,} bytes) via {choice!r}')
    return {'path': str(dest), 'bytes': size}


def rename(page: Page, old: str, new: str, log=print) -> str:
    """Rename by name and return the name that now resolves.

    `rename_tile` can report False when Angular re-creates the tile under the
    new label: the element it holds goes stale and `get_attribute` returns
    None even though the rename applied (measured 2026-11-06 - the run then
    downloaded by the OLD name and found nothing). So re-resolve in the grid,
    accept whichever of the two names is uniquely present, and fail loudly if
    neither is.
    """
    tile = pages.find_tile_by_name(page, old, exact=True)
    if tile.count() != 1:
        tile = pages.find_tile_by_name(page, old)
    if tile.count() != 1 and len(old) > 24:
        tile = pages.find_tile_by_name(page, old[:24])
    if tile.count() != 1:
        log(f'  ! {tile.count()} tiles match {old!r} - keeping the Flow name')
        return old
    pages.rename_tile(page, tile, new, log=log)
    deadline = time.time() + 10
    while time.time() < deadline:
        if pages.find_tile_by_name(page, new).count() == 1:
            return new
        if pages.find_tile_by_name(page, old).count() == 1:
            page.wait_for_timeout(1000)
            continue
        page.wait_for_timeout(1000)
    if pages.find_tile_by_name(page, new).count() == 1:
        return new
    if pages.find_tile_by_name(page, old).count() == 1:
        log(f'  ! rename to {new!r} did not stick; keeping {old!r}')
        return old
    raise Blocked(f'after renaming {old!r} neither that name nor {new!r} '
                  f'resolves to exactly one tile')


def write_manifest(out_dir: Path, data: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / 'manifest.json'
    # Write-then-replace: a manifest is now written after every tile, and a
    # kill mid-write must leave the previous good copy, never a truncated file
    # (the consumer json.load()s it without a guard).
    tmp = out_dir / 'manifest.json.tmp'
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                   encoding='utf-8')
    os.replace(tmp, path)
    return path

def ensure_ready(page: Page, name: str, timeout_s: int = 300,
                 log=print) -> str:
    """Wait until the tile's context menu is COMPLETE, and return its name.

    Two facts this encodes, both measured on 2026-11-06:
    * a tile's aria-label is the PROMPT while it renders and a short TITLE
      once Flow finishes ("Vintage brass telescope on table"), so a name read
      too early goes stale - hence the stability wait in wait_new_tiles and
      the prefix fallback here;
    * while a tile is still finalizing its menu offers only `delete`, so the
      menu itself is the readiness signal.
    """
    deadline = time.time() + timeout_s
    last: list[str] = []
    while time.time() < deadline:
        tile = pages.find_tile_by_name(page, name, exact=True)
        if not tile.count():
            tile = pages.find_tile_by_name(page, name)
        if not tile.count() and len(name) > 24:
            tile = pages.find_tile_by_name(page, name[:24])
        try:
            if tile.count() == 1:
                current = (tile.first.get_attribute('aria-label') or '').strip()
                pages.open_tile_menu(page, tile)
                last = pages.menu_items(page)
                page.keyboard.press('Escape')
                page.wait_for_timeout(300)
                if any('Download' in i for i in last):
                    return current or name
        except Exception:  # noqa: BLE001 - not ready is the normal case
            pass
        log(f'  tile {name!r} not ready yet (menu: {last})')
        page.wait_for_timeout(2000)
    raise Blocked(f'tile {name!r} never offered a Download action within '
                  f'{timeout_s}s (menu stayed {last})')
def _true_extension(dest: Path) -> Path:
    """Rename `dest` to the extension its BYTES actually are.

    Flow's 2K image download is JPEG even though the menu says nothing about
    format; saving it as .png produces a file every consumer misreads
    (measured 2026-11-06: a 3 MB "png" whose magic bytes are FF D8).
    """
    try:
        head = dest.read_bytes()[:12]
    except OSError:
        return dest
    if head.startswith(b'\x89PNG'):
        ext = '.png'
    elif head.startswith(b'\xff\xd8'):
        ext = '.jpg'
    elif head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        ext = '.webp'
    else:
        return dest
    if dest.suffix.lower() != ext:
        new = dest.with_suffix(ext)
        try:
            dest.replace(new)
            return new
        except OSError:
            return dest
    return dest