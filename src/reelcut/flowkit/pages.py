"""Navigation, the clip grid, overlays, the prompt box and the context menu.

Ported from flow-playwright's flow_lib.py. The traps this module encodes:

* The grid VIRTUALIZES and a background tab has no layout, so every grid read
  must happen in a foreground tab after scrolling to the top, or it reads as
  "this project has no clips" and stops a publish that would have worked.
* A CDK overlay left open (`.cdk-overlay-backdrop`) swallows pointer events.
  Escape clears some overlays and not others; a reload is the only guaranteed
  reset, and it is safe ONLY while no YouTube grant is live in the session.
* A tile's aria-label is the only identity a clip exposes, and Flow rewrites
  it as renders finish - so names this pipeline sets are the durable handles.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PWError, Page
from playwright.sync_api import TimeoutError as PWTimeout

from . import config
from . import chrome
from . import selectors as S


# ------------------------------------------------------------------ navigation


def discover_projects(page: Page, settle_ms: int = 7000) -> dict[str, str]:
    """Map project title -> uuid from the Flow home page, for the BOUND account."""
    page.goto(chrome.flow_url(), wait_until='domcontentloaded', timeout=45_000)
    page.wait_for_timeout(settle_ms)
    cards = page.eval_on_selector_all(
        S.PROJECT_CARD_SEL,
        """els => els.map(e => {
             const a = e.querySelector('a[href*="/project/"]');
             const href = a ? a.getAttribute('href') : '';
             return {
               title: (e.innerText || '').split('\\n')[0].trim(),
               uuid: href ? href.replace(/\\/+$/, '').split('/').pop() : '',
             };
           })""",
    )
    out: dict[str, str] = {}
    for c in cards:
        if c['title'] and c['uuid']:
            out.setdefault(c['title'], c['uuid'])
    return out


def resolve_uuid(page: Page, ref: str) -> str:
    """Resolve a configured project reference to a Flow project uuid.

    The normal case is a uuid, returned as-is with NO navigation and NO
    scraping. A non-uuid `ref` is treated as a project TITLE and looked up on
    the Flow home page - the manual refresh path. The result is NOT cached: a
    title-keyed cache is what let a stale title outlive a rename, and a
    process-global one would leak a uuid across accounts.
    """
    if config.UUID_RE.match(ref or ''):
        return ref
    found = discover_projects(page)
    if ref not in found:
        raise RuntimeError(f'project {ref!r} not found on the Flow home page; '
                           f'saw: {sorted(found)}')
    return found[ref]


def _document_ready(page: Page) -> bool:
    """True once the document has parsed far enough to query.

    Flow's SPA can miss the `domcontentloaded` event while still being
    perfectly usable; a TimeoutError is then a false failure - Playwright's
    call log says "navigated to ..." even though the wait never returned.
    """
    try:
        return page.evaluate(
            "() => ['interactive', 'complete'].includes(document.readyState)"
        ) is True
    except Exception:  # noqa: BLE001
        return False


def at_project_root(url: str, uuid: str) -> bool:
    """Is `url` the project ROOT - not a deeper view of the same project?

    `/project/<uuid>/edit/<generation-id>` is the per-generation editor, and it
    has **no composer**: no prompt bar, no tile grid, and no settings trigger.
    A substring test for `/project/<uuid>` matches it too, so a page left there
    was treated as "already open" and the run proceeded on a view where
    `check_settings`'s button does not exist - costing an 80s timeout, then a
    Blocked, on a page that is one navigation away from fine. Verified live on
    2026-10-08: on `/edit/<id>` the settings trigger matched 0 elements and the
    only "Settings" node on the page was the hidden zero-box `Settings trigger`
    twin; on the root the same selector matched the visible 32x32 button.

    Query and fragment are ignored, and a trailing slash is not a deeper view.
    """
    path = urlparse(url).path.rstrip('/')
    return path.endswith(f'/project/{uuid}')


def goto_project(page: Page, uuid: str, attempts: int = 3,
                 timeout_ms: int = 45_000) -> None:
    """Navigate to a project, tolerating a slow cold load.

    `domcontentloaded` on Flow's SPA can exceed 45s cold; the timeout aborts a
    run that would have succeeded a moment later. Retrying is safe, and
    `commit` is the fallback for a document that arrives without firing the
    event.
    """
    url = chrome.flow_url(f'project/{uuid}')
    last: Exception | None = None
    for _ in range(attempts):
        for wait in ('domcontentloaded', 'commit'):
            try:
                page.goto(url, wait_until=wait, timeout=timeout_ms)
                return
            except PWTimeout as e:  # noqa: PERF203 - retry is the point
                last = e
                # at_project_root, not `url in page.url`: a timeout on a page
                # still sitting on `/project/<uuid>/edit/<id>` would satisfy the
                # substring test and be accepted as a successful navigation.
                if at_project_root(page.url, uuid) and _document_ready(page):
                    return
        page.wait_for_timeout(2000)
    raise last if last else RuntimeError('goto_project failed')


def open_project(page: Page, uuid: str, settle_ms: int = 4000) -> None:
    if not at_project_root(page.url, uuid):
        goto_project(page, uuid)
    bring_to_front(page)
    page.wait_for_timeout(settle_ms)


def bring_to_front(page: Page) -> None:
    """A background tab has no layout and renders zero tiles."""
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001 - never fatal; the grid wait is the check
        pass


def reload(page: Page, settle_ms: int = 5000, attempts: int = 3,
           timeout_ms: int = 45_000) -> None:
    """Deterministic state reset. Free - no generation, no credits.

    Tolerates the same slow cold load as goto_project(). A bare page.reload()
    once timed out on an already-navigated page and killed a publish attempt.
    """
    if 'about:' in page.url or not page.url:
        raise RuntimeError(f'cannot reload {page.url!r} - not on a real page')
    last: Exception | None = None
    for _ in range(attempts):
        try:
            page.reload(wait_until='domcontentloaded', timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)
            return
        except PWTimeout as e:  # noqa: PERF203 - retry is the point
            last = e
            if _document_ready(page):
                page.wait_for_timeout(settle_ms)
                return
            page.wait_for_timeout(2000)
    raise last if last else RuntimeError('reload failed')


# -------------------------------------------------------------------- overlays


def overlay_count(page: Page) -> int:
    return page.locator(S.OVERLAY_SEL).count()


def backdrop_count(page: Page) -> int:
    """Only the backdrop is harmful; a lingering popover is usually a tooltip."""
    return page.locator(S.BACKDROP_SEL).count()


def dismiss_overlays(page: Page) -> bool:
    """Try to close stray menus cheaply. Escape is unreliable here."""
    for _ in range(4):
        if overlay_count(page) == 0:
            return True
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)
    return overlay_count(page) == 0


def overlay_report(page: Page) -> list[dict]:
    """The overlay stack in a readable form, for a run report.

    A blocked click was once undiagnosable after the fact: Playwright names
    the blocker only as a tag plus a class, and by the time anyone reads the
    report the page is gone.
    """
    try:
        return page.eval_on_selector_all(
            f'{S.OVERLAY_CONTAINER_SEL} > *',
            """els => els.map(e => ({
                 tag: e.tagName.toLowerCase(),
                 cls: String(e.className || ''),
                 aria: e.getAttribute('aria-label') || '',
                 text: (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
               }))""",
        )
    except Exception:  # noqa: BLE001 - diagnostics must never mask the error
        return []


def ensure_clickable(page: Page, uuid: str, reload_ok: bool = True,
                     log=print) -> bool:
    """Make the page clickable again when a CDK overlay swallows clicks.

    Escalation: Escape (clears some overlays), then a reload (the only
    guaranteed reset). A reload is safe ONLY while no YouTube grant is live in
    this page session - the grant does not survive one - so the caller decides
    with `reload_ok`. Pass False for anything after connect. `uuid` is the
    project to come back to. Returns whether the page is clean; never raises
    for a blocker.
    """
    if backdrop_count(page) == 0:
        return True

    log(f'  a CDK backdrop is covering the page '
        f'({backdrop_count(page)} backdrop(s), {overlay_count(page)} overlay node(s))')
    for o in overlay_report(page)[:4]:
        log(f'    blocker: {o["tag"]}.{o["cls"][:50]} '
            f'aria={o["aria"][:30]!r} text={o["text"][:70]!r}')

    for _ in range(4):
        page.keyboard.press('Escape')
        page.wait_for_timeout(600)
        if backdrop_count(page) == 0:
            log('  Escape cleared it')
            return True

    if not reload_ok:
        log('  Escape did not clear it, and a reload is not safe here (it would '
            'drop the YouTube grant) - leaving the page alone')
        return False

    log('  Escape did not clear it - reloading to reset the page')
    reload(page)
    wait_for_grid(page)  # the grid virtualizes; it has to re-render first
    ok = backdrop_count(page) == 0
    log(f'  page reset to /project/{uuid[:8]}... clickable={ok}')
    return ok


# ------------------------------------------------------------------- the grid


def scroll_grid_to_top(page: Page) -> None:
    """The grid virtualizes; only visible rows exist, and the newest clip is
    top-left, so it is only queryable after scrolling all the way up."""
    try:
        page.eval_on_selector(S.SCROLL_VIEWPORT_SEL, 'el => { el.scrollTop = 0; }')
    except Exception:  # noqa: BLE001 - selector absent on an empty project
        pass
    page.wait_for_timeout(1200)


def tiles(page: Page) -> list[dict]:
    """Visible clip tiles in DOM order.

    `busy` is a POSITIVE test for a real progress element. Do not widen it to
    a substring match on class names: every FINISHED tile carries a
    `footer-overlay-has-progress-bar` div, so matching /progress/ reports the
    whole grid as rendering forever (measured: 7 of 9 finished tiles matched).
    """
    return page.eval_on_selector_all(
        S.TILE_SEL,
        """els => els.map((e, i) => {
             const b = e.getBoundingClientRect();
             const busy = e.querySelectorAll(
               'mat-progress-bar, [role="progressbar"], .mdc-linear-progress').length > 0;
             return {i, name: e.getAttribute('aria-label') || '',
                     busy,
                     img_w: (e.querySelector('img') || {}).naturalWidth || 0,
                     kind: e.querySelector('flow-collection-tile') ? 'collection'
                           : e.querySelector('flow-video-tile') ? 'video'
                           : e.querySelector('flow-image-tile') ? 'image'
                           : 'other',
                     x: Math.round(b.x), y: Math.round(b.y),
                     w: Math.round(b.width), h: Math.round(b.height)};
           })""",
    )


def wait_for_grid(page: Page, timeout_s: int = 60, poll_s: int = 2) -> list[dict]:
    """Poll until the clip grid has actually rendered, then return it.

    `tiles()` returns [] for two very different situations: a project with no
    clips, and a grid that has not rendered yet. The second is a false
    negative that aborts a publish which would have worked, so wait before
    believing the first.
    """
    bring_to_front(page)
    t0 = time.time()
    seen: list[dict] = []
    while True:
        scroll_grid_to_top(page)
        seen = tiles(page)
        if seen or time.time() - t0 >= timeout_s:
            return seen
        page.wait_for_timeout(poll_s * 1000)


def newest_tile(page: Page):
    """Top-left tile = newest clip, per Flow's grid ordering."""
    wait_for_grid(page)
    return page.locator(S.TILE_SEL).first


def find_tile_by_name(page: Page, needle: str, exact: bool = False):
    """Locate a tile by its aria-label (= the clip's name).

    `exact=True` matches the FULL label. Substring matching is fine for a short
    human needle, but it silently over-matches generated names: "cli061800_1"
    is a substring of "cli061800_10".."cli061800_19", so an ambiguous locator
    (count > 1) made a harvest report an empty context menu and stall
    (measured 2026-11-06, a 20-image batch).
    """
    if exact:
        safe = needle.replace('\\', '\\\\').replace('"', '\\"')
        return page.locator(f'{S.TILE_SEL}[aria-label="{safe}"]')
    return page.locator(f'{S.TILE_SEL}[aria-label*="{needle}"]')


def wait_for_new_tile(page: Page, before: set[str], timeout_s: int = 600,
                      poll_s: int = 6, settle_s: int = 150,
                      before_count: int | None = None,
                      on_poll=None) -> dict | None:
    """Wait for the clip this run generated to appear in the grid.

    Flow pushes no event (batchexecute RPC only), so polling is the signal.
    A tile appears seconds after submission while the render behind it takes
    minutes, so "a new tile exists" is not "the clip is done". Two signals
    cover each other's blind spots:

    - name: a tile whose aria-label was not in the grid before. Blind to a
      name that repeats an existing one.
    - count: the grid grew, two consecutive polls (the count is read off
      whatever is rendered, so it jitters; a false positive publishes an OLD
      clip). Blind to a clip added to a virtualized window that is already
      full.

    A tile that showed a real progress element and then went clean is
    evidence; with no evidence it waits out `settle_s` from first sighting.
    The asymmetry is the point: wrong detection costs waiting, never a wrong
    answer. Returns None only if no new tile appeared at all.
    """
    t0 = time.time()
    first_seen: float | None = None
    saw_progress = False
    clean_polls = 0
    grown_polls = 0
    last: dict | None = None
    while time.time() - t0 < timeout_s:
        scroll_grid_to_top(page)
        current = tiles(page)
        new = [t for t in current if t['name'] and t['name'] not in before]
        detected_by = 'name'
        if not new and before_count is not None:
            if len(current) > before_count:
                grown_polls += 1
            else:
                grown_polls = 0
            if grown_polls >= 2:
                top = next((t for t in current if t['name']), None)
                if top is not None:
                    new = [top]
                    detected_by = 'count'
        if new:
            last = dict(new[0])
            last['detected_by'] = detected_by
            if first_seen is None:
                first_seen = time.time()
            if last['busy']:
                saw_progress = True
                clean_polls = 0
            else:
                clean_polls += 1
                if saw_progress and clean_polls >= 2:
                    last['settled'] = 'progress-cleared'
                    if on_poll:
                        on_poll(time.time() - t0, len(current), last, True)
                    return last
            if time.time() - first_seen >= settle_s:
                last['settled'] = ('settle-cap-unseen' if not saw_progress
                                   else 'settle-cap-still-busy')
                if on_poll:
                    on_poll(time.time() - t0, len(current), last, not last['busy'])
                return last
        if on_poll:
            on_poll(time.time() - t0, len(current), last, False)
        page.wait_for_timeout(poll_s * 1000)
    if last is not None:
        last['settled'] = 'timeout'
    return last


# -------------------------------------------------------------------- the menu


def open_tile_menu(page: Page, tile) -> None:
    """Right-click a clip tile. This is the whole reason the port exists:
    left-click opens the scene editor, which has no publish option."""
    tile.click(button='right', timeout=10_000)
    page.wait_for_selector(S.MENU_SEL, timeout=8000)
    page.wait_for_timeout(400)


def menu_items(page: Page) -> list[str]:
    return page.eval_on_selector_all(
        S.MENU_ITEM_SEL,
        "els => els.map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))",
    )


def click_menu_item(page: Page, name: str, timeout_ms: int = 8000) -> None:
    """Click a context-menu entry by substring of its visible text.

    Scoped to [role="menuitem"] on purpose: that is what makes it impossible
    to reach the publish dialog's own Publish button.

    Pass the LABEL ONLY - never a string read back from `menu_items()`.
    `menu_items()` reports `innerText`, which puts a space between an entry's
    Material icon ligature and its label ("add_2 Add to prompt"), while the
    `has_text` matcher compares `textContent`, which has no such space. The
    long form therefore matches nothing at all and burns the full timeout;
    that cost two probe runs on 2026-10-08. The fallback below makes a name
    read back from `menu_items()` work anyway, and the failure path when
    neither matcher finds anything is unchanged.
    """
    item = page.locator(S.MENU_ITEM_SEL, has_text=name)
    if item.count() == 0:
        idx = page.evaluate(
            """([sel, want]) => {
                 const norm = s => (s || '').replace(/\\s+/g, '').toLowerCase();
                 const els = Array.from(document.querySelectorAll(sel));
                 const w = norm(want);
                 return els.findIndex(e => norm(e.textContent).includes(w));
               }""",
            [S.MENU_ITEM_SEL, name])
        if idx >= 0:
            page.locator(S.MENU_ITEM_SEL).nth(idx).click(timeout=timeout_ms)
            page.wait_for_timeout(1500)
            return
    item.first.click(timeout=timeout_ms)
    page.wait_for_timeout(1500)


# ------------------------------------------------- the last-frame link (arcs)
#
# Part N+1 of an arc must OPEN on part N's final frame. Text alone does not
# achieve that (proved 2026-10-08: p1's last frame and p2's first frame showed
# the same character in a different room, with a different phone and different
# lighting). The mechanism that does is Flow's own: upload the extracted frame
# through the main page's "+" menu, then right-click the tile it becomes and
# choose "Add to prompt".

_UPLOAD_STATE_JS = """(name) => {
  const els = Array.from(document.querySelectorAll('flow-grid-tile-container'))
    .filter(e => (e.getAttribute('aria-label') || '') === name);
  const texts = els.map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim());
  return {count: els.length, texts,
          uploading: texts.filter(t => /\\d+\\s*%/.test(t)).length};
}"""


def tile_upload_state(page: Page, name: str) -> dict:
    """How many tiles carry `name`, and how many are still counting up."""
    scroll_grid_to_top(page)
    return page.evaluate(_UPLOAD_STATE_JS, name)


def upload_media(page: Page, path, timeout_s: int = 180, poll_s: int = 3,
                 log=print) -> str:
    """Upload a local file through the main page's "+" menu; return its tile name.

    Two things here are not guessable, and both cost a run on 2026-10-08:

    * the "+" is `Add media menu`, whose first entry is "Upload". The
      composer's "Add ingredients to the prompt box" is a DIFFERENT popover:
      its upload never becomes a tile, so it can never be right-clicked and
      its detail-pane button stays disabled while Flow ingests the file.
    * the tile is named after the FILE, extension included, so the caller
      knows the name before the upload has finished.

    Flow renders the tile immediately and fills it in place - "image 13%",
    "image 33%", ... - so "a tile exists" is not "the upload finished". Right-
    clicking too early finds a tile menu holding nothing but "Move to bin" and
    "Delete". Settled means the percentage stopped, which is what the caller
    needs before it attaches.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f'nothing to upload: {path}')
    tile_name = path.name
    before = tile_upload_state(page, tile_name)['count']
    log(f'  uploading {tile_name} ({before} tile(s) already carry that name)')
    page.keyboard.press('Escape')
    page.wait_for_timeout(300)
    page.locator(S.ADD_MEDIA_BTN).first.click(timeout=10_000)
    page.wait_for_timeout(1200)
    items = menu_items(page)
    if not any('upload' in i.lower() for i in items):
        raise RuntimeError(f'the "+" menu has no Upload entry: {items}')
    with page.expect_file_chooser(timeout=15_000) as fc:
        click_menu_item(page, 'Upload')
    fc.value.set_files(str(path))
    deadline = time.time() + timeout_s
    while True:
        st = tile_upload_state(page, tile_name)
        if st['count'] > before and st['uploading'] == 0:
            log(f'  {tile_name} uploaded and settled ({st["count"]} tile(s))')
            return tile_name
        if time.time() >= deadline:
            raise RuntimeError(
                f'{tile_name} did not settle within {timeout_s}s: '
                f'{st["count"]} tile(s), {st["uploading"]} still uploading '
                f'{st["texts"][:3]}')
        page.wait_for_timeout(poll_s * 1000)


def ingredient_chips(page: Page) -> list[dict]:
    """The frames currently attached to the prompt box.

    They live in `flow-ingredient-bar`, NOT in the ProseMirror editor - which
    is why `clear_prompt()` cannot remove one, and why a frame attached for
    part 2 would still be attached when part 3 renders.
    """
    return page.eval_on_selector_all(
        S.INGREDIENT_CHIP,
        """els => els.map(e => {
             const img = e.querySelector('img');
             const r = e.getBoundingClientRect();
             return {aria: e.getAttribute('aria-label') || '',
                     busy: e.getAttribute('aria-busy') || '',
                     src: img ? (img.getAttribute('src') || '') : '',
                     w: Math.round(r.width), h: Math.round(r.height)};
           })""")


def clear_ingredients(page: Page, log=print) -> int:
    """Detach every frame from the prompt box; returns how many were removed.

    Clicking a chip removes it (observed: one click and the whole bar is
    gone). This must run before each new attach - otherwise part 3 renders
    with part 2's start frame still attached, and the model is handed the
    wrong image to open on.
    """
    removed = 0
    for _ in range(6):
        chips = page.locator(S.INGREDIENT_CHIP)
        n = chips.count()
        if n == 0:
            break
        try:
            chips.first.click(timeout=4000)
        except Exception:  # noqa: BLE001 - a stuck chip is reported by count
            break
        page.wait_for_timeout(700)
        if page.locator(S.INGREDIENT_CHIP).count() >= n:
            page.keyboard.press('Escape')   # a preview opened, not a removal
            page.wait_for_timeout(500)
            break
        removed += 1
    if removed:
        # No provenance claim: this can only see that a chip was attached and
        # now is not. The caller knows why it was there.
        log(f'  detached {removed} attached frame(s)')
    return removed


def attach_tile_to_prompt(page: Page, name: str, log=print) -> dict:
    """Right-click a grid tile -> "Add to prompt"; return the chip that appeared.

    Fail-closed on purpose: the click proves nothing by itself, so the
    ingredient bar is read back. No chip means part N+1 would render blind -
    the exact failure this mechanism exists to remove - so it raises.
    """
    tile = find_tile_by_name(page, name)
    if tile.count() < 1:
        raise RuntimeError(f'no tile named {name!r} to attach to the prompt')
    open_tile_menu(page, tile.first)
    items = menu_items(page)
    if not any('add to prompt' in i.lower() for i in items):
        raise RuntimeError(
            f'the tile menu for {name!r} has no "Add to prompt": {items}')
    click_menu_item(page, 'Add to prompt')
    page.wait_for_selector(S.INGREDIENT_CHIP, timeout=20_000)
    page.wait_for_timeout(500)
    chips = ingredient_chips(page)
    if not chips:
        raise RuntimeError(f'{name!r} reported as attached but no chip is in '
                           'the ingredient bar')
    log(f'  attached {name} to the prompt ({len(chips)} chip(s) in the bar)')
    return chips[0]


def rename_tile(page: Page, tile, new_name: str, log=print) -> bool:
    """Rename a clip through its context menu, and confirm the name stuck.

    A name WE choose is the one handle that still resolves in a later run:
    Flow rewrites the aria-label itself as a render finishes, and renaming
    moves a clip to the FRONT of the grid, so position is no handle either.
    Returns True only when the label reads back as `new_name`. Failure is
    reported, never raised: the rename is a convenience and must not be able
    to cost the publish that follows it.
    """
    if new_name == (tile.get_attribute('aria-label') or ''):
        return True
    # A tile that is still finalizing offers ONLY "delete" in its context menu
    # (measured on the frame-upload path in 2026-10-08, and again on the 10s
    # rename in 2026-11-05 where it stranded a paid-for clip behind a publish
    # that locates by name). That is "too early", not "no rename": dismiss and
    # give the tile a couple of seconds, once.
    for attempt in (1, 2):
        open_tile_menu(page, tile)
        items = ' | '.join(str(i) for i in menu_items(page))
        if 'Rename' not in items:
            log(f'  rename: the menu is not ready yet ({items[:70]!r})')
            page.keyboard.press('Escape')
            page.wait_for_timeout(2000)
            if attempt == 1:
                continue
            return False
        click_menu_item(page, 'Rename')
        break
    try:
        page.wait_for_selector(S.RENAME_INPUT_SEL, timeout=8000)
    except PWTimeout:
        log('  rename: no editor appeared - the clip keeps its name')
        page.keyboard.press('Escape')
        return False
    page.wait_for_timeout(300)
    page.fill(S.RENAME_INPUT_SEL, new_name)
    page.click(S.RENAME_DONE_SEL)
    page.wait_for_timeout(1200)
    if page.locator(S.RENAME_INPUT_SEL).count():
        log('  rename: the editor is still open - the name was not stored')
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)
        return False
    try:
        after = tile.get_attribute('aria-label')
    except PWError:
        after = None   # Angular re-created the tile; ask the grid instead
    if after != new_name:
        scroll_grid_to_top(page)
        after = next((t['name'] for t in tiles(page) if t['name'] == new_name), None)
    ok = after == new_name
    log(f'  rename -> {new_name!r}: '
        + ('confirmed' if ok else f'NOT confirmed (label reads {after!r})'))
    return ok


# -------------------------------------------------------------------- the prompt


def read_prompt(page: Page) -> str:
    return page.eval_on_selector(
        S.PROMPT_SEL, "el => (el.innerText || '').trim().replace(/\\s+/g, ' ')")


def clear_prompt(page: Page, attempts: int = 3, log=print) -> bool:
    """Empty Flow's prompt box, verifying it actually emptied.

    If the clear does not take, the next insert_text APPENDS, and Flow renders
    the old prompt glued to the new one. So it retries and reports honestly.
    """
    box = page.locator(S.PROMPT_SEL).first
    for _ in range(attempts):
        box.click(timeout=10_000)
        page.wait_for_timeout(250)
        page.keyboard.press('Control+A')
        page.keyboard.press('Delete')
        page.wait_for_timeout(400)
        if not read_prompt(page):
            return True
        page.wait_for_timeout(300)
    log(f'  ! clear_prompt failed; box still holds {read_prompt(page)[:60]!r}')
    return False


def set_prompt(page: Page, text: str, log=print) -> str:
    """Type a prompt into Flow's ProseMirror box and return what landed.

    ProseMirror is not a textarea: click in, clear with a real select-all +
    Delete, then keyboard.insert_text (CDP Input.insertText) so newlines and
    Arabic survive. If the box cannot be emptied this returns '' rather than
    typing into a dirty box - an appended prompt renders the wrong clip and
    still costs credits.
    """
    if not clear_prompt(page, log=log):
        return ''
    box = page.locator(S.PROMPT_SEL).first
    box.click(timeout=10_000)
    page.wait_for_timeout(250)
    page.keyboard.insert_text(text)
    page.wait_for_timeout(600)
    return read_prompt(page)


def start_generation_available(page: Page) -> bool:
    return page.locator(S.GEN_BTN_SEL).count() > 0

def dismiss_modals(page: Page, log=print) -> list[str]:
    """Close first-run changelogs/announcements that cover the composer.

    Measured 2026-11-06: an "Introducing Omni 1.2" modal sat over the whole
    project page and made every composer read look empty (no prompt box, no
    model pickers, three buttons). Escape does not close it; its exit is a
    primary button. A probe that does not clear this reports a lie.
    """
    closed: list[str] = []
    for text in ('Get started', 'Got it', 'Dismiss', 'Maybe later', 'Close',
                 'Not now'):
        loc = page.locator(
            '[role="dialog"] button, mat-dialog-container button, .cdk-overlay-pane button'
        ).filter(has_text=text)
        if not loc.count():
            continue
        try:
            loc.first.click(timeout=4000)
            page.wait_for_timeout(900)
            closed.append(text)
        except PWError:
            continue
    if closed:
        log(f'  dismissed modal(s): {closed}')
    return closed