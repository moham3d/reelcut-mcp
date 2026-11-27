"""Read and set the generation config in the composer's config popover.

Measured 2026-11-06 on the Agent-OFF composer: clicking the config chip
("🍌 Nano Banana 2.1 crop_9_16 x2" / `button[aria-label="Settings trigger"]`)
opens a CDK popover that is the WHOLE generation panel:

    [Image | Video]                      mode
    [Frames | Ingredients]               video sub-mode
    [16:9] [4:3] [1:1] [3:4] [9:16]      aspect
    <model family ▾>                     Nano Banana 2.1 / Omni 1.1 Flash / Veo 3.1 …
    [360p] [720p]                        video resolution
    [4s] [6s] [8s] [10s]                 video duration
    [Low] [Medium] [High] [Extra thorough]   image thinking level
    [x1] [x2] [x3] [x4]                  outputs
    "Generating will use N credits"      THE cost preview

That last line is the reason this module exists: a paid command must read the
price Flow itself computed, not guess from a credit-panel delta afterwards.
Measured: 4s=7, 6s=10, 8s=12, 10s=15 credits PER OUTPUT at Omni 1.1 Flash;
image mode reads 0.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from . import composer, pages
from . import selectors as S
from .errors import Blocked

COST_RE = re.compile(r'Generating will use\s+([\d,]+)\s+credits?', re.I)
LENGTH_RE = re.compile(r'Length\s+(\d+)\s*sec', re.I)
_POPOVER_JS = """() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const pop = [...document.querySelectorAll('.cdk-overlay-popover, .cdk-overlay-pane')]
    .find(e => (e.innerText || '').includes('Generating will use'));
  if (!pop) return null;
  const groups = [...pop.querySelectorAll('mat-button-toggle-group')].map(g => ({
    options: [...g.querySelectorAll('mat-button-toggle')].map(t => ({
      text: norm(t.innerText),
      checked: t.classList.contains('mat-button-toggle-checked')
               || (t.querySelector('button') || {getAttribute: () => null})
                    .getAttribute('aria-pressed') === 'true',
    })),
  }));
  const buttons = [...pop.querySelectorAll('button')]
    .map(b => norm(b.innerText || b.getAttribute('aria-label') || ''))
    .filter(Boolean);
  return {text: norm(pop.innerText), groups, buttons};
}"""


def _popover_open(page: Page) -> bool:
    try:
        return page.locator('.cdk-overlay-popover').filter(
            has_text='Generating will use').count() > 0
    except PWError:
        return False


def _trigger_visible(page: Page, timeout_s: float = 20.0) -> bool:
    """Wait for the config trigger to be visible, not merely present.

    A project that was last used in agent mode reopens IN that session; turning
    Agent OFF swaps the composer back, and the config chip appears a beat later.
    Clicking during that beat found nothing and the run stopped with "could not
    open the generation config popover" (measured 2026-11-06).
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for sel in S.SETTINGS_BTN_CANDIDATES:
            loc = page.locator(sel)
            try:
                for i in range(min(loc.count(), 3)):
                    if loc.nth(i).is_visible():
                        return True
            except PWError:
                continue
        page.wait_for_timeout(500)
    return False


def open_popover(page: Page, log=print) -> bool:
    """Open the composer config popover (Agent must be OFF), waiting for it."""
    if _popover_open(page):
        return True
    st = composer.agent_state(page)
    if st.get('on'):
        composer.set_agent(page, False, log=log)
    if not _trigger_visible(page, 20.0):
        # One more settle attempt: the switch may have flipped back on while
        # the panel was re-rendering.
        if composer.agent_state(page).get('on'):
            composer.set_agent(page, False, log=log)
        if not _trigger_visible(page, 10.0):
            # Button build: an open agent SESSION panel covers the composer,
            # hiding the config chip. Close it (X) and look once more
            # (measured 2026-11-12 on the 'Studio Funny Animals' project).
            if _close_session_panel(page):
                if not _trigger_visible(page, 10.0):
                    log('  still no config trigger after closing the session panel')
                    return False
            else:
                log('  no config trigger became visible after settling the composer')
                return False
    for _ in range(3):
        for sel in S.SETTINGS_BTN_CANDIDATES:
            loc = page.locator(sel)
            try:
                n = loc.count()
            except PWError:
                continue
            for i in range(min(n, 3)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    el.click(timeout=6000)
                except PWError:
                    continue
                page.wait_for_timeout(1200)
                if _popover_open(page):
                    return True
                pages.dismiss_overlays(page)
        page.wait_for_timeout(1000)
    return False


def _close_session_panel(page: Page) -> bool:
    """Delegate to composer.close_session_panel (same mechanism, one copy)."""
    return composer.close_session_panel(page)


def close_popover(page: Page) -> None:
    page.keyboard.press('Escape')
    page.wait_for_timeout(400)
    if pages.overlay_count(page):
        pages.dismiss_overlays(page)


def read(page: Page) -> dict:
    """The popover's current state + the credit preview, parsed."""
    raw = page.evaluate(_POPOVER_JS)
    if not raw:
        raise Blocked('the generation config popover is not open')
    out: dict = {
        'text': raw['text'],
        'buttons': raw['buttons'],
        'groups': raw['groups'],
        'mode': '', 'submode': '', 'aspect': '', 'model': '',
        'resolution': '', 'duration': '', 'outputs': '', 'thinking': '',
        'cost_credits': None, 'cost_text': '',
    }
    for g in raw['groups']:
        opts = [o['text'] for o in g['options']]
        chosen = next((o['text'] for o in g['options'] if o['checked']), '')
        joined = ' '.join(opts)
        if 'Image' in joined and 'Video' in joined:
            out['mode'] = 'Video' if 'Video' in chosen else 'Image'
        elif 'Frames' in joined and 'Ingredients' in joined:
            out['submode'] = 'Frames' if 'Frames' in chosen else 'Ingredients'
        elif '16:9' in joined and '9:16' in joined:
            out['aspect'] = chosen.split()[-1] if chosen else ''
        elif any(o.endswith('p') for o in opts) and ('360' in joined or '720' in joined):
            out['resolution'] = chosen.split()[-1] if chosen else ''
        elif any(o.endswith('s') for o in opts) and any(o[:-1].isdigit() for o in opts):
            out['duration'] = chosen.split()[-1] if chosen else ''
        elif any(o.startswith('x') for o in opts):
            out['outputs'] = chosen.split()[-1] if chosen else ''
        elif any(o in ('Low', 'Medium', 'High', 'Extra thorough') for o in opts):
            out['thinking'] = chosen.split()[-1] if chosen else ''
    for b in raw['buttons']:
        if 'arrow_drop_down' in b:
            out['model'] = b.replace('arrow_drop_down', '').strip()
            break
    # Omni 1.2 has no duration toggles: it exposes a "Length N sec" slider
    # (input[aria-label="Length"], 0..12 -> 3..15 s) instead.
    lm = LENGTH_RE.search(raw['text'])
    if lm:
        out['duration'] = f'{int(lm.group(1))}s'
        out['duration_control'] = 'slider'
    else:
        out['duration_control'] = 'toggles'
    m = COST_RE.search(raw['text'])
    if m:
        out['cost_text'] = m.group(0)
        out['cost_credits'] = int(m.group(1).replace(',', ''))
    return out


def _toggle(page: Page, label: str, log=print) -> bool:
    """Click the mat-button-toggle whose text ENDS with `label`.

    Text content glues the Material icon ligature to the label
    ("videocamVideo", "crop_freeFrames"), so an exact match never hits and a
    substring match hits too much ("16:9" inside "crop_16_9 16:9" is fine, but
    "Video" inside "Video generation" is not). Endswith is the right question.
    """
    loc = page.locator('mat-button-toggle').filter(
        has_text=re.compile(re.escape(label) + r'$'))
    try:
        if not loc.count():
            log(f'  no toggle labelled {label!r}')
            return False
        loc.first.click(timeout=6000)
        page.wait_for_timeout(700)
        return True
    except PWError:
        return False


def set_option(page: Page, label: str, log=print) -> bool:
    """Set a toggle and verify it is the checked one afterwards."""
    if not _toggle(page, label, log=log):
        return False
    st = read(page)
    values = {st['mode'], st['submode'], st['aspect'], st['resolution'],
              st['duration'], st['outputs'], st['thinking']}
    if label.split()[-1] in values:
        return True
    log(f'  ! {label!r} did not become the checked option (now {values})')
    return False


def set_model(page: Page, name: str, log=print) -> bool:
    """Pick a model family inside the popover (Nano Banana / Omni / Veo …)."""
    st = read(page)
    if name.lower() in (st['model'] or '').lower():
        return True
    btn = page.locator('.cdk-overlay-popover button').filter(
        has_text='arrow_drop_down')
    if not btn.count():
        log('  no model-family button in the popover')
        return False
    try:
        btn.first.click(timeout=6000)
        page.wait_for_timeout(1200)
        opt = page.locator('[role="menuitem"], mat-option, [role="option"]').filter(
            has_text=re.compile(re.escape(name), re.I))
        if not opt.count():
            log(f'  model {name!r} not offered; menu had: '
                f'{[t.strip()[:40] for t in page.locator("[role=menuitem], mat-option").all_inner_texts()]}')
            page.keyboard.press('Escape')
            return False
        opt.first.click(timeout=6000)
        page.wait_for_timeout(1200)
        after = read(page)
        return name.lower() in (after['model'] or '').lower()
    except PWError:
        return False


def set_duration(page: Page, seconds: int, log=print) -> bool:
    """Set the clip length in EITHER build of the duration control.

    * Omni 1.1: four toggles (4s / 6s / 8s / 10s).
    * Omni 1.2: a slider labelled "Length" (`input[aria-label="Length"]`,
      min 0 = 3 s, max 12 = 15 s, step 1) - so it is keyboard-driven: Home
      puts it at 3 s, then ArrowRight once per extra second.
    """
    open_popover(page, log=log)
    st = read(page)
    goal = f'{seconds}s'
    offered = [o['text'] for g in st['groups'] for o in g['options']
               if o['text'].endswith('s') and o['text'][:-1].isdigit()]
    if goal in offered:
        return set_option(page, goal, log=log)
    slider = page.locator('input[aria-label="Length"]')
    if not slider.count():
        log(f'  no duration control for {goal} (offered toggles: {offered})')
        return False
    try:
        slider.click(timeout=6000)
        page.wait_for_timeout(250)
        page.keyboard.press('Home')
        page.wait_for_timeout(350)
        for _ in range(max(0, seconds - 3)):
            page.keyboard.press('ArrowRight')
            page.wait_for_timeout(220)
        page.wait_for_timeout(500)
    except PWError:
        return False
    after = read(page)
    if after.get('duration') != goal:
        log(f'  slider did not reach {goal} (reads {after.get("duration")!r})')
        return False
    return True


def _offered_duration_text(st: dict) -> str:
    vals = sorted({o['text'] for g in st['groups'] for o in g['options']
                   if o['text'].endswith('s') and o['text'][:-1].isdigit()},
                  key=lambda t: int(t[:-1]))
    if vals:
        return ', '.join(vals)
    if st.get('duration_control') == 'slider':
        return '3s..15s (1s steps)'
    return 'unknown'


def configure(page: Page, *, mode: str | None = None,
              aspect: str | None = None, outputs: int | None = None,
              duration: str | None = None, resolution: str | None = None,
              model: str | None = None, thinking: str | None = None,
              submode: str | None = None, log=print) -> dict:
    """Set every requested control, re-opening the popover if a click closed it.

    Order matters: the MODEL is chosen before the duration, because selecting
    Omni 1.2 replaces the four duration toggles with a slider.
    """
    if not open_popover(page, log=log):
        raise Blocked('could not open the generation config popover on this '
                      'composer - is the page a project root with Agent OFF?')
    want: list[str] = []
    if mode:
        want.append('Video' if mode.lower().startswith('v') else 'Image')
    if submode:
        want.append(submode)
    if aspect:
        want.append(aspect)
    if resolution:
        want.append(resolution)
    if outputs:
        want.append(f'x{outputs}')
    if thinking:
        want.append(thinking)
    for label in want:
        if not set_option(page, label, log=log):
            open_popover(page, log=log)
            set_option(page, label, log=log)
    if model:
        set_model(page, model, log=log)
    open_popover(page, log=log)
    st = read(page)
    if aspect and st.get('aspect') != aspect:
        raise Blocked(
            f'this composer offers {st.get("aspect") or "no"} aspect, not '
            f'{aspect}; video supports 16:9 and 9:16 only, images support '
            f'16:9, 4:3, 1:1, 3:4, 9:16')
    if duration:
        secs = int(duration.rstrip('s'))
        if not set_duration(page, secs, log=log):
            st = read(page)
            raise Blocked(
                f'{st.get("model") or model!r} does not offer {duration} '
                f'(offered: {_offered_duration_text(st)}). Omni 1.1 has '
                f'4/6/8/10s; Omni 1.2 has 3..15s in 1s steps.')
    return read(page)
