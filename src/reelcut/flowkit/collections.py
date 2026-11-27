# Flow collection primitives. Every mechanism here was mapped live on
# 2026-11-04 by scripts/probe_collections.py, probe_collections2.py,
# probe_collections3.py and probe_collections4.py against the slides project
# on account b. See the collections block in selectors.py for the full map.

"""Create, name, and move tiles into a Flow collection.

What the probes established (2026-11-04):

* "+" -> "New collection" creates an "Untitled collection" INSTANTLY - there
  is no naming dialog. The name is set afterwards through the collection
  tile's own Rename menu, which is pages.rename_tile's flow.
* A collection is itself a grid TILE. Its menu: Favourite / Download
  collection / Rename / Move all contents to bin. An ASSET tile's menu has
  NO collection item, and the collection view (/project/<uuid>/collection/
  <coll-uuid>) has no "add existing" affordance.
* The move is a DRAG of the asset tile onto the collection tile, and it is a
  MOVE, not a reference copy: the dragged tile leaves All media. Anything
  that still needs the tile in the root grid (the per-tile 2K download does)
  must run BEFORE the drag.
* Deleting a collection BINS its contents - the confirm dialog says "Move
  collection to bin? This will flatten the contents ... cannot be undone".

No collection state is tracked in a ledger here: the collection NAME is the
only handle, and create_or_get is idempotent by that name.
"""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import Page

from . import pages
from . import selectors as S
from .errors import Blocked

_UNTITLED_PREFIX = 'Untitled collection'


def _exact_tile(page: Page, name: str):
    """A tile locator matching the aria-label EXACTLY.

    find_tile_by_name is a substring needle: a collection named 'test' would
    also match a tile named 'test 2'. For a handle the operator typed,
    exactness is the contract."""
    return page.locator(f'{S.TILE_SEL}[aria-label="{name}"]')


def create_or_get(page: Page, name: str, log=print) -> str:
    """Return a collection named `name`, creating it if needed.

    Idempotent by name: a collection left behind by a crashed run is reused,
    not duplicated. Creation is "+" -> New collection (which instant-creates
    an untitled one) followed by a rename through the tile menu.
    """
    if _exact_tile(page, name).count():
        log(f'  collection {name!r} already exists - reusing it')
        return name

    pages.scroll_grid_to_top(page)
    page.keyboard.press('Escape')
    page.wait_for_timeout(300)
    page.locator(S.ADD_MEDIA_BTN).first.click(timeout=10_000)
    page.wait_for_timeout(1200)
    items = pages.menu_items(page)
    if not any('collection' in i.lower() for i in items):
        raise Blocked(f'the "+" menu has no "New collection" entry: {items}')
    pages.click_menu_item(page, 'New collection')
    page.wait_for_timeout(2500)
    pages.dismiss_overlays(page)

    pages.scroll_grid_to_top(page)
    untitled = [t['name'] for t in pages.tiles(page)
                if (t['name'] or '').startswith(_UNTITLED_PREFIX)]
    if not untitled:
        raise Blocked('the "New collection" click produced no untitled '
                      f'collection tile; the grid reads '
                      f'{[t["name"] for t in pages.tiles(page)][:8]}')
    log(f'  created {_UNTITLED_PREFIX} tile - renaming to {name!r}')
    tile = pages.find_tile_by_name(page, untitled[0]).first
    if not pages.rename_tile(page, tile, name, log=log):
        raise Blocked(f'the collection rename to {name!r} did not stick - '
                      'stop: a wrongly named collection would collect the '
                      'wrong images on the next run')
    if not _exact_tile(page, name).count():
        raise Blocked(f'no tile reads exactly {name!r} after the rename')
    return name


def contents(page: Page, name: str, log=print) -> list[str]:
    """Open the collection view, return the names of the tiles inside.

    Reads the grid of /project/<uuid>/collection/<coll-uuid> - the collection
    tile left-click opens it - then navigates back to the project root. The
    caller is responsible for landing back on the root (this function does
    that itself with go_back, because a reload after it would be wasted).
    """
    tile = _exact_tile(page, name)
    if not tile.count():
        raise Blocked(f'no collection tile named {name!r} to open')
    pages.scroll_grid_to_top(page)
    tile.first.click(timeout=10_000)
    page.wait_for_timeout(3000)
    names = [t['name'] for t in pages.tiles(page) if t['name']]
    page.go_back()
    page.wait_for_timeout(2500)
    pages.dismiss_overlays(page)
    log(f'  collection {name!r} holds {len(names)} tile(s)')
    return names


def move_tile_in(page: Page, tile_name: str, coll_name: str,
                 verify: bool = True, log=print) -> bool:
    """Drag one asset tile onto the collection tile; verify it landed.

    The drag is mouse-down, stepped mouse-move (so the drop target registers
    the hover), mouse-up - proven by probe_collections4.py. A straight
    drag_to() skips the hover dwell and was never tested; the stepped form
    is the one that worked. Verify opens the collection and confirms the
    tile is inside - a drag that silently failed would strand the image
    outside the collection the operator asked for.
    """
    src = _exact_tile(page, tile_name)
    if src.count() != 1:
        raise Blocked(f'{src.count()} tiles match {tile_name!r} exactly - '
                      'the move needs one unambiguous tile')
    dst = _exact_tile(page, coll_name)
    if not dst.count():
        raise Blocked(f'no collection tile named {coll_name!r} to drop onto')
    sb, db = src.first.bounding_box(), dst.first.bounding_box()
    if not sb or not db:
        raise Blocked(f'a drag endpoint has no bounding box: src={sb} dst={db}')
    # A background tab has no layout - the same rule the grid reads obey.
    pages.bring_to_front(page)
    pages.scroll_grid_to_top(page)
    sx, sy = sb['x'] + sb['width'] / 2, sb['y'] + sb['height'] / 2
    dx, dy = db['x'] + db['width'] / 2, db['y'] + db['height'] / 2
    log(f'  dragging {tile_name!r} onto {coll_name!r} '
        f'({sx:.0f},{sy:.0f}) -> ({dx:.0f},{dy:.0f})')
    page.mouse.move(sx, sy)
    page.wait_for_timeout(300)
    page.mouse.down()
    page.wait_for_timeout(200)
    steps = 12
    for i in range(1, steps + 1):
        page.mouse.move(sx + (dx - sx) * i / steps,
                        sy + (dy - sy) * i / steps)
        page.wait_for_timeout(60)
    page.wait_for_timeout(600)   # the drop target registers the hover
    page.mouse.up()
    page.wait_for_timeout(2500)
    pages.dismiss_overlays(page)

    if not verify:
        return True
    # The drag is a MOVE: once inside, the tile is no longer in the root
    # grid. Both halves are checked - in the collection AND out of the root.
    inside = tile_name in contents(page, coll_name, log=log)
    pages.scroll_grid_to_top(page)
    still_out = _exact_tile(page, tile_name).count() > 0
    if not inside or still_out:
        raise Blocked(f'the drag of {tile_name!r} did not land in '
                      f'{coll_name!r} (inside={inside}, still in the root '
                      f'grid={still_out})')
    log(f'  {tile_name!r} verified inside {coll_name!r}')
    return True


def download_all(page: Page, coll_name: str, dest_dir: Path, log=print) -> int:
    """Trigger the collection tile's 'Download collection' once. Returns the
    number of files that landed in dest_dir (0 = nothing arrived).

    NOT the pipeline's download path: this hands back whatever Flow chooses
    (probably a zip of originals, resolution unverified - the per-tile 2K
    download in driver/images.py is the mechanism with a proven resolution).
    Offered because it is free and sometimes exactly what an operator wants.
    """
    tile = _exact_tile(page, coll_name)
    if not tile.count():
        raise Blocked(f'no collection tile named {coll_name!r}')
    pages.open_tile_menu(page, tile.first)
    items = pages.menu_items(page)
    if not any('download collection' in i.lower() for i in items):
        page.keyboard.press('Escape')
        raise Blocked(f'{coll_name!r} does not read as a collection tile: '
                      f'{items}')
    dest_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in dest_dir.iterdir()}
    try:
        with page.expect_download(timeout=180_000) as dl:
            pages.click_menu_item(page, 'Download collection')
        dl.value.save_as(str(dest_dir / dl.value.suggested_filename))
    except Exception as e:  # noqa: BLE001 - reported, then folder-polled
        log(f'  expect_download did not fire ({type(e).__name__}); polling '
           'the folder for 60s')
        deadline = time.time() + 60
        while time.time() < deadline:
            new = [p for p in dest_dir.iterdir()
                   if p.name not in before and p.stat().st_size > 0]
            if new:
                break
            page.wait_for_timeout(3000)
    page.keyboard.press('Escape')
    page.wait_for_timeout(500)
    new = [p for p in dest_dir.iterdir()
           if p.name not in before and p.stat().st_size > 0]
    for p in new:
        log(f'  landed: {p.name} ({p.stat().st_size:,} bytes)')
    return len(new)
