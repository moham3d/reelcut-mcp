"""Start/end frames for video: the native version of the arc's frame chain.

Measured 2026-11-06 on the chip build:

* with Video + `Frames` selected, the composer shows `flow-ingredient-bar`
  (class `prompt-ingredient-bar`) holding **Start**, **Swap first and last
  frames**, and **End**;
* clicking a slot opens a popover titled **"Select a frame image"** - a tabbed
  picker (Images / Uploads), an "Upload media" button, a search box and the
  project's recent assets;
* picking an asset closes the popover and fills the slot.

So a frame is chosen by NAME from the project's own images, which is exactly
what a storyboard needs: generate stills, then animate between two of them.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from . import pages
from . import selectors as S
from .errors import Blocked

POPOVER_TITLE = 'Select a frame image'


def bar_present(page: Page) -> bool:
    try:
        return page.locator(S.FRAME_BAR).count() > 0
    except PWError:
        return False


def _click_slot(page: Page, slot: str) -> bool:
    return bool(page.evaluate(
        """(slot) => {
             const bar = document.querySelector('flow-ingredient-bar');
             if (!bar) return false;
             const b = [...bar.querySelectorAll('button')]
               .find(x => (x.innerText || '').trim() === slot);
             if (!b) return false;
             b.click();
             return true;
           }""", slot))


def _wait_picker(page: Page, timeout_s: float = 15.0) -> bool:
    deadline = __import__('time').time() + timeout_s
    while __import__('time').time() < deadline:
        try:
            if page.locator('.cdk-overlay-popover').filter(
                    has_text=POPOVER_TITLE).count():
                return True
        except PWError:
            pass
        page.wait_for_timeout(500)
    return False


def _pick_asset(page: Page, asset: str) -> bool:
    """Click the project asset whose visible text IS `asset` (exact)."""
    clicked = page.evaluate(
        """(asset) => {
             const panes = [...document.querySelectorAll('.cdk-overlay-popover')]
               .filter(p => (p.innerText || '').includes('Select a frame image'));
             const pane = panes[panes.length - 1];
             if (!pane) return 'no pane';
             const hit = [...pane.querySelectorAll('button')]
               .find(x => (x.innerText || '').trim() === asset);
             if (hit) { hit.click(); return 'ok'; }
             const fuzzy = [...pane.querySelectorAll('button')]
               .find(x => (x.innerText || '').includes(asset));
             if (fuzzy) { fuzzy.click(); return 'fuzzy'; }
             return 'not found';
           }""", asset)
    if clicked == 'ok':
        return True
    if clicked == 'fuzzy':
        return True
    return False


def select_frame(page: Page, slot: str, asset: str, log=print) -> None:
    """Fill the Start or End slot with a project asset, by name."""
    slot = slot.strip().capitalize()
    if slot not in ('Start', 'End'):
        raise Blocked(f'frame slot must be Start or End, not {slot!r}')
    if not bar_present(page):
        raise Blocked('the frame bar is not on the composer - pass '
                      '`--submode frames` (Video mode only)')
    if not _click_slot(page, slot):
        raise Blocked(f'no {slot!r} slot button in the frame bar')
    if not _wait_picker(page):
        raise Blocked(f'the "{POPOVER_TITLE}" picker did not open for the '
                      f'{slot} slot')
    if not _pick_asset(page, asset):
        # Leave the picker closed so the page stays usable.
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)
        raise Blocked(f'asset {asset!r} is not in the frame picker (it lists '
                      f'the project\'s images and uploads by name)')
    page.wait_for_timeout(1200)
    log(f'  {slot} frame <- {asset!r}')
