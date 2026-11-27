"""The Flow composer: prompt box, Agent switch, instructions, references.

Forked from YT-Agent/yta/driver/images.py (Agent switch, both build shapes) and
SlideAgent's composer notes @ 2026-11-06.

DIVERGES: this module is READ-MOSTLY. It exposes the controls the CLI needs to
report on (`agent_state`, `overlay_panes`, `composer_controls`) and the two
openers that reveal otherwise-invisible state (instructions, ingredients).
Nothing here generates; generation lives in image.py/video.py.

Two facts it encodes, both measured:
* Agent is a CHIP on the 2026-10-10 build and a `button[aria-label="Agent
  instructions"]` with `aria-pressed` on the 2026-11-05 build. The chip shape is
  kept as a fallback, not deleted.
* The composer's "Add ingredients to the prompt box" popover is a DIFFERENT
  mechanism from the project "+" -> tile -> "Add to prompt" path: the popover
  never creates a tile. Both can attach references; only the tile path gives a
  handle you can right-click later.
"""

from __future__ import annotations

import time

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from . import pages
from . import selectors as S
from .errors import Blocked

# Every visible overlay pane, its text and its controls. The generic answer to
# "what did that click open?" - the same shape that settled the settings panel.
_PANES_JS = """() => {
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 20 && r.height > 10 && s.visibility !== 'hidden' &&
           s.display !== 'none' && s.opacity !== '0';
  };
  const seen = new Set(), out = [];
  for (const el of document.querySelectorAll(
        '[role="dialog"], mat-dialog-container, .cdk-overlay-pane, '
        + '[role="menu"], mat-mdc-menu-panel, flow-tile-view-settings, '
        + '[class*="agent-panel"], [class*="popover"], [class*="panel"]')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 100 || r.height < 60) continue;
    const key = Math.round(r.x) + ',' + Math.round(r.y) + ',' +
                Math.round(r.width) + ',' + Math.round(r.height);
    if (seen.has(key)) continue;    // nested panes report the same box
    seen.add(key);
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 90),
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1200),
      controls: [...el.querySelectorAll(
          'button, input, textarea, [contenteditable="true"], mat-radio-button, '
          + 'mat-button-toggle, [role="menuitem"], [role="radio"], [role="switch"], '
          + 'mat-checkbox, mat-option')]
        .filter(vis)
        .map(c => ({
          tag: c.tagName.toLowerCase(),
          aria: (c.getAttribute('aria-label') || '').trim().slice(0, 80),
          text: (c.innerText || c.value || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
          checked: c.getAttribute('aria-checked')
                   || (c.querySelector && c.querySelector('input')
                       && c.querySelector('input').checked)
                   || c.classList.contains('mat-button-toggle-checked') || false,
          pressed: c.getAttribute('aria-pressed'),
        })).slice(0, 40),
    });
  }
  return out;
}"""


def close_session_panel(page: Page, log=print) -> bool:
    """Dismiss an open agent SESSION panel ('Untitled session' / suggestions).

    A lingering session hides the direct composer AND keeps the button-build
    state alive: set_agent(False) alone still rendered session content, and
    the config popover never opened. Closing the panel flips the composer to
    the chip build where the tune chip and Start generation live (measured
    2026-11-12 on the 'Studio Funny Animals' project). True when a visible
    Close (X) was present and clicked.
    """
    close = page.locator('.agent-panel-wrapper button[aria-label="Close"]')
    try:
        if close.count() and close.first.is_visible():
            close.first.click(timeout=6000)
            page.wait_for_timeout(1200)
            log('  closed the agent session panel')
            return True
    except PWError:
        pass
    return False


def overlay_panes(page: Page) -> list[dict]:
    """Visible overlay/panel panes with their text and controls."""
    try:
        return page.evaluate(_PANES_JS)
    except PWError:
        return []


def composer_controls(page: Page) -> list[dict]:
    """Visible buttons/inputs inside the composer(s), with aria labels."""
    try:
        return page.evaluate(
            """() => {
                 const vis = el => {
                   const r = el.getBoundingClientRect(), s = getComputedStyle(el);
                   return r.width > 6 && r.height > 6 && s.visibility !== 'hidden'
                          && s.display !== 'none';
                 };
                 const roots = [...document.querySelectorAll(
                     '.agent-panel-prompt-box, .agent-panel-wrapper, '
                     + 'flow-ingredient-bar, .submit-controls, '
                     + 'div.ProseMirror[contenteditable="true"]')].filter(vis);
                 const out = [];
                 for (const root of roots) {
                   for (const el of root.querySelectorAll(
                       'button, input, [contenteditable="true"], textarea')) {
                     if (!vis(el)) continue;
                     out.push({
                       root: String(root.className || root.tagName).slice(0, 40),
                       tag: el.tagName.toLowerCase(),
                       aria: (el.getAttribute('aria-label') || '').trim(),
                       text: (el.innerText || el.getAttribute('placeholder') || '')
                             .replace(/\\s+/g, ' ').trim().slice(0, 60),
                       pressed: el.getAttribute('aria-pressed'),
                       editable: el.getAttribute('contenteditable'),
                     });
                   }
                 }
                 return out;
            }""")
    except PWError:
        return []


def agent_state(page: Page) -> dict:
    """Read the Agent switch in EITHER build shape. `on` is None when unknown."""
    chip = page.locator(S.AGENT_CHIP_SEL)
    try:
        if chip.count():
            cls = chip.first.get_attribute('class') or ''
            return {'mode': 'chip', 'on': 'agent-mode-chip-checked' in cls,
                    'count': chip.count(), 'visible': chip.first.is_visible()}
    except PWError:
        pass
    btn = page.locator(S.VIDEO_AGENT_BTN)
    try:
        n = btn.count()
    except PWError:
        n = 0
    if not n:
        # With Agent ON the switch can disappear entirely: the composer turns
        # into the agent panel, whose prompt box is the only surviving signal.
        try:
            if page.locator('div.ProseMirror[aria-label="What do you want to '
                            'create?"]').count():
                return {'mode': 'panel', 'on': True, 'count': 1, 'visible': True}
        except PWError:
            pass
        # 2026-11-20 compact build: the agent IS the composer ("Describe your
        # goal") — no switch exists, and generation is agent-native.
        try:
            if page.locator('div.ProseMirror[aria-label="Describe your '
                            'goal"]').count():
                return {'mode': 'describe', 'on': True, 'count': 1,
                        'visible': True}
        except PWError:
            pass
        return {'mode': 'none', 'on': None, 'count': 0, 'visible': False}
    on = None
    for i in range(n):
        try:
            if btn.nth(i).is_visible():
                pressed = (btn.nth(i).get_attribute('aria-pressed') or '').lower()
                on = pressed == 'true'
                break
        except PWError:
            continue
    if on is None:
        # The switch can be present but hidden once the agent panel takes the
        # composer over; the agent's own prompt box is the surviving signal.
        try:
            if page.locator('div.ProseMirror[aria-label="What do you want to '
                            'create?"]').count():
                return {'mode': 'panel', 'on': True, 'count': n,
                        'visible': True}
        except PWError:
            pass
        try:
            if page.locator('div.ProseMirror[aria-label="Describe your '
                            'goal"]').count():
                return {'mode': 'describe', 'on': True, 'count': n,
                        'visible': True}
        except PWError:
            pass
    return {'mode': 'button', 'on': on, 'count': n, 'visible': on is not None}


def set_agent(page: Page, want_on: bool, log=print,
              timeout_s: float = 20.0) -> dict:
    """Idempotently settle the Agent switch, retrying through transitions.

    Fresh/empty projects open a full-session start panel that hides the Agent
    switch entirely (agent_state reads mode 'none'). Closing the session panel
    is the only way out of that state (measured 2026-11-20 on the 'Studio
    Funny Animals' project).

    The same control toggles both ways, but it disappears for a moment while
    the panel reorganises: a single read one second after opening a project can
    see `panel` with no visible switch and wrongly report "did not settle"
    (measured 2026-11-06). Poll until it matches or the timeout expires.
    """
    st = agent_state(page)
    if st.get('mode') == 'describe' and not want_on:
        raise Blocked(
            'the 2026-11-20 compact build has no Agent OFF - the composer is '
            'agent-native ("Describe your goal"). Video/config commands are '
            'not supported on this build yet; image agent batches work.')
    if st.get('mode') == 'none':
        if close_session_panel(page, log=log):
            page.wait_for_timeout(1000)
            st = agent_state(page)
        if st.get('mode') == 'none':
            # one more settle cycle before giving up
            page.wait_for_timeout(2000)
            st = agent_state(page)
            if st.get('mode') == 'none' and close_session_panel(page, log=log):
                st = agent_state(page)
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        st = agent_state(page)
        last = st
        if st['on'] == want_on:
            log(f'  Agent {"ON" if want_on else "OFF"} ({st["mode"]} build)')
            return st
        if st['mode'] == 'panel' and not want_on:
            # Inside an agent session the switch is gone. The session panel's
            # own Close button hands the composer back, and the switch
            # reappears (measured 2026-11-06).
            closed = page.evaluate(
                """() => { const b = [...document.querySelectorAll(
                     '.agent-panel-wrapper button, .agent-panel-content button')]
                     .find(x => (x.getAttribute('aria-label') || '') === 'Close');
                     if (b) { b.click(); return true; } return false; }""")
            if closed:
                log('  closed the agent session panel to reach the switch')
                page.wait_for_timeout(1800)
                continue
        if st['on'] is None and st['mode'] == 'none':
            page.wait_for_timeout(1000)
            continue
        sel = S.AGENT_CHIP_SEL if st['mode'] == 'chip' else S.VIDEO_AGENT_BTN
        clicked = False
        for i in range(max(1, st['count'])):
            el = page.locator(sel).nth(i)
            try:
                if not el.is_visible():
                    continue
                el.click(timeout=8000)
                clicked = True
                break
            except PWError:
                continue
        if not clicked:
            page.wait_for_timeout(800)
            continue
        page.wait_for_timeout(1500)
    raise Blocked(f'Agent did not settle {"ON" if want_on else "OFF"} within '
                  f'{timeout_s}s (reads {last})')


def _click_control(page: Page, aria: str, log=print) -> bool:
    """Click a composer control by exact aria-label, preferring a visible match."""
    loc = page.locator(f'button[aria-label="{aria}"]')
    n = loc.count()
    for i in range(n):
        try:
            if loc.nth(i).is_visible():
                loc.nth(i).click(timeout=8000)
                page.wait_for_timeout(1200)
                return True
        except PWError:
            continue
    log(f'  no visible button[aria-label="{aria}"] ({n} match(es))')
    return False


def open_instructions(page: Page, log=print) -> dict:
    """Open the Agent-instructions control and dump what it reveals.

    Read-only: it opens, reports, and closes. Never types and never saves.
    """
    out = {'opened': False, 'panes': []}
    if not _click_control(page, 'Agent instructions', log=log):
        return out
    out['opened'] = True
    out['panes'] = overlay_panes(page)
    # Close: an explicit Close/Back first, Escape last - Flow ignores Escape on
    # some panels.
    for label in ('Close', 'Back', 'Cancel'):
        loc = page.locator(f'button[aria-label="{label}"]')
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=4000)
                page.wait_for_timeout(600)
                break
        except PWError:
            continue
    if pages.overlay_count(page):
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)
    return out


def open_ingredients(page: Page, log=print) -> dict:
    """Open the composer's "Add ingredients to the prompt box" popover."""
    out = {'opened': False, 'panes': []}
    if not _click_control(page, 'Add ingredients to the prompt box', log=log):
        return out
    out['opened'] = True
    out['panes'] = overlay_panes(page)
    # The popover is a tabbed asset picker (All / Images / Videos / Voices /
    # Characters / Avatars / Uploads) with a search box and a Recent list -
    # i.e. a reference can be an asset, a character or an avatar voice, not
    # just a local file. Record the tabs so the CLI can expose them.
    tabs: list[str] = []
    for pane in out['panes']:
        for c in pane.get('controls', []):
            t = (c.get('text') or '').strip()
            if any(t == n or t.endswith(' ' + n) for n in
                   ('All', 'Images', 'Videos', 'Voices', 'Characters',
                    'Avatars', 'Uploads', 'Upload media')) and t not in tabs:
                tabs.append(t)
    out['tabs'] = tabs
    for label in ('Close', 'Cancel', 'Back'):
        loc = page.locator(f'button[aria-label="{label}"]')
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=4000)
                page.wait_for_timeout(500)
                break
        except PWError:
            continue
    if pages.overlay_count(page):
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)
    return out


def model_menu(page: Page, picker_sel: str, log=print) -> dict:
    """Click a model picker, list its options, close. Never selects one."""
    out = {'selector': picker_sel, 'found': 0, 'label': '', 'options': []}
    loc = page.locator(picker_sel)
    try:
        out['found'] = loc.count()
    except PWError:
        return out
    if not out['found']:
        return out
    el = loc.first
    try:
        out['label'] = (el.inner_text() or '').strip().replace('\n', ' ')[:80]
        el.click(timeout=8000)
    except PWError:
        return out
    page.wait_for_timeout(1200)
    out['options'] = [
        (t or '').replace('\n', ' ').strip()[:80]
        for t in page.eval_on_selector_all(
            '[role="menuitem"], mat-option, [role="option"]',
            'els => els.map(e => e.innerText || "")')
        if (t or '').strip()
    ]
    if not out['options']:
        # Some pickers open a dialog/pane instead of a menu list; dump what
        # actually appeared rather than reporting an empty option list.
        out['panes'] = overlay_panes(page)
    page.keyboard.press('Escape')
    page.wait_for_timeout(500)
    if pages.overlay_count(page):
        pages.dismiss_overlays(page)
    return out

def panel_text(page: Page, maxlen: int = 2000) -> str:
    """The persistent agent panel's text (NOT an overlay - it is the sidebar).

    With Agent ON the composer's home is a chat panel, so overlay_panes() sees
    nothing even though the panel is right there. This reads it.
    """
    for sel in ('.agent-panel-content', '.agent-panel-prompt-box',
                '.agent-panel-wrapper', '.agent-panel-animation-container',
                'flow-agent-panel'):
        loc = page.locator(sel)
        try:
            if loc.count():
                return (loc.first.inner_text() or '').strip()[:maxlen]
        except PWError:
            continue
    return ''