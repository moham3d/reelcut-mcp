"""The Agent surface: guidelines (instructions), the agent prompt box, sessions.

Measured 2026-11-06 on the chip build with Agent ON:

* `button[aria-label="Agent instructions"]` opens a panel whose rows are
  `input[placeholder="Instruction title"]` +
  `textarea[aria-label="Instruction description"]`, added with
  "Add instruction" and saved with "Done". Back discards.
* the agent's prompt box is a ProseMirror with
  `aria-label="What do you want to create?"` - a different box from the
  generation composer's, and the one `Start generation` submits from.

The write path here is deliberately conservative: it saves only when the
caller asks for it, and it re-reads the list to confirm the rows landed.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from . import composer, pages
from .errors import Blocked

TITLE_SEL = 'input.editable-text-input[placeholder="Instruction title"]'
DESC_SEL = 'textarea[aria-label="Instruction description"]'
# 2026-11-20 build: the compact composer renames the prompt box to
# "Describe your goal" — same ProseMirror class, same Start generation button.
AGENT_PROMPT = ('div.ProseMirror[aria-label="What do you want to create?"], '
                'div.ProseMirror[aria-label="Describe your goal"]')
TOGGLE_SEL = 'button[aria-label="Toggle instruction active"]'


def _active(button) -> bool:
    """Read a row's active toggle (aria-checked / aria-pressed)."""
    for attr in ('aria-checked', 'aria-pressed'):
        value = button.get_attribute(attr)
        if value is not None:
            return value.strip().lower() == 'true'
    return False


def read_instructions(page: Page, log=print) -> list[dict]:
    """Guidelines WITH their per-row active toggle.

    Flow stores these as switchable guidelines; a guideline whose toggle is OFF
    is NOT applied to generations (this is how a project silently loses its
    style: the text is still there, the switch is not). `list_instructions`
    predates the toggle and hides that state - prefer this one.
    """
    if not _open_panel(page, log=log):
        return []
    try:
        titles = [t.input_value() for t in page.locator(TITLE_SEL).all()]
        descs = [d.input_value() for d in page.locator(DESC_SEL).all()]
        toggles = page.locator(TOGGLE_SEL)
        states = [_active(toggles.nth(i)) for i in range(toggles.count())]
    except PWError:
        titles, descs, states = [], [], []
    _close_panel(page)
    rows = [{'title': t, 'description': d,
             'active': states[i] if i < len(states) else None}
            for i, (t, d) in enumerate(zip(titles, descs))]
    return [r for r in rows if r['title'].strip() or r['description'].strip()]


def set_instruction_active(page: Page, needle: str, active: bool = True,
                           log=print) -> list[dict]:
    """Turn one stored guideline's active switch on/off, then verify it stuck.

    `needle` matches the row's title+description (case-insensitive substring).
    `add_instructions` only writes text; this is the write path for the switch
    that decides whether the style guideline is actually applied.
    """
    needle = (needle or '').strip().lower()
    if not needle:
        raise Blocked('set_instruction_active needs a title/description substring')
    if not _open_panel(page, log=log):
        raise Blocked('could not open the Agent instructions panel')
    try:
        titles = page.locator(TITLE_SEL).all()
        descs = page.locator(DESC_SEL).all()
        toggles = page.locator(TOGGLE_SEL)
        n = min(len(titles), len(descs), toggles.count())
        hit = 0
        for i in range(n):
            title = titles[i].input_value()
            desc = descs[i].input_value()
            if needle not in f'{title} {desc}'.lower():
                continue
            hit += 1
            if _active(toggles.nth(i)) != active:
                toggles.nth(i).click(timeout=6000)
                page.wait_for_timeout(500)
                log(f"  instruction {title.strip() or '(untitled)'} -> "
                    f"{'active' if active else 'inactive'}")
        if not hit:
            raise Blocked(f'no stored guideline matches {needle!r}')
        done = page.locator('button').filter(has_text='Done')
        if done.count():
            done.first.click(timeout=6000)
            page.wait_for_timeout(1200)
    except Exception:
        _close_panel(page)
        raise
    after = read_instructions(page, log=log)
    matched = [r for r in after
               if needle in f"{r['title']} {r['description']}".lower()]
    if not matched or any(bool(r['active']) != active for r in matched):
        raise Blocked(f'instruction switch did not stick (wanted active={active}): '
                      f'{matched}')
    return after


def _open_panel(page: Page, log=print) -> bool:
    if page.locator(TITLE_SEL).count() or page.locator(DESC_SEL).count():
        return True
    btn = page.locator('button[aria-label="Agent instructions"]')
    for i in range(btn.count()):
        try:
            if btn.nth(i).is_visible():
                btn.nth(i).click(timeout=8000)
                page.wait_for_timeout(1200)
                return True
        except PWError:
            continue
    log('  could not open the Agent instructions panel')
    return False


def _close_panel(page: Page) -> None:
    """Leave via Back (discard) - never Done, which saves."""
    try:
        page.evaluate(
            """() => { const b = [...document.querySelectorAll(
                   '.agent-panel-content button')][0]; if (b) b.click(); }""")
        page.wait_for_timeout(800)
    except PWError:
        pass


def list_instructions(page: Page, log=print) -> list[dict]:
    """Current guidelines, read without saving anything."""
    if not _open_panel(page, log=log):
        return []
    try:
        titles = [t.input_value() for t in page.locator(TITLE_SEL).all()]
        descs = [d.input_value() for d in page.locator(DESC_SEL).all()]
    except PWError:
        titles, descs = [], []
    _close_panel(page)
    rows = [{'title': t, 'description': d} for t, d in zip(titles, descs)]
    # The editor always shows one empty row for adding; it is not a stored
    # guideline.
    return [r for r in rows if r['title'].strip() or r['description'].strip()]


def attach_references(page: Page, refs, log=print) -> list[str]:
    """Attach local reference images to the AGENT prompt box; return tile names.

    Flow's Agent prompt accepts ingredients, but the upload has to happen while
    the DIRECT composer is showing (the Agent's own "+" is a different picker
    whose upload never becomes a grid tile - see pages.upload_media). So:
    Agent OFF -> upload each ref as a tile -> Agent ON -> right-click tile ->
    "Add to prompt". The chip is verified by attach_tile_to_prompt.

    This is the style lock for image batches on the current Flow build: the
    project's instruction text alone no longer holds the illustrated look, but
    one attached style reference does - and it does not leak the reference's
    subject into the prompt.
    """
    if not refs:
        return []
    composer.set_agent(page, False, log=log)
    page.wait_for_timeout(900)
    tiles = []
    try:
        for ref in refs:
            tile = pages.upload_media(page, ref, log=log)
            if not tile:
                raise Blocked(f'reference {ref!r} did not land as a tile')
            tiles.append(tile)
    finally:
        composer.set_agent(page, True, log=log)
        page.wait_for_timeout(1500)
    for tile in tiles:
        pages.attach_tile_to_prompt(page, tile, log=log)
    log(f'  attached {len(tiles)} reference(s) to the agent prompt')
    return tiles


def parse_instructions_file(path: Path) -> list[dict]:
    """A guideline file: blocks separated by blank lines.

        Title line
        description, possibly several lines

    A block whose first line has no body uses the title as the description.
    """
    text = Path(path).read_text(encoding='utf-8')
    blocks, cur = [], []
    for line in text.splitlines():
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        title = b[0].strip()
        desc = ' '.join(x.strip() for x in b[1:]).strip()
        if not title:
            continue
        out.append({'title': title[:80], 'description': desc or title})
    return out


def add_instructions(page: Page, items: list[dict], log=print) -> list[dict]:
    """Add guidelines and SAVE with Done, then verify by re-reading."""
    if not items:
        return []
    if not _open_panel(page, log=log):
        raise Blocked('could not open the Agent instructions panel')
    try:
        for item in items:
            clicked = page.evaluate(
                """() => { const b = [...document.querySelectorAll('button')]
                     .find(x => (x.innerText || '').includes('Add instruction'));
                     if (b) { b.click(); return true; } return false; }""")
            if not clicked:
                raise Blocked('no "Add instruction" button in the editor')
            page.wait_for_timeout(700)
            try:
                # The title is a custom inline editor: it commits on Enter, and
                # a plain fill() left it EMPTY while the description saved
                # (measured 2026-11-06). Type, then Enter, then the textarea.
                title = page.locator(TITLE_SEL).last
                title.click(timeout=6000)
                page.wait_for_timeout(200)
                page.keyboard.press('Control+A')
                page.keyboard.type(item['title'])
                page.keyboard.press('Enter')
                page.wait_for_timeout(400)
                desc = page.locator(DESC_SEL).last
                desc.click(timeout=6000)
                page.keyboard.type(item['description'])
                page.wait_for_timeout(300)
            except PWError as e:
                raise Blocked(f'could not fill the instruction row: {e}')
            log(f"  + instruction {item['title']!r}")
        done = page.locator('button').filter(has_text='Done')
        if not done.count():
            raise Blocked('no Done button - refusing to leave unsaved rows behind')
        done.first.click(timeout=6000)
        page.wait_for_timeout(1500)
    except Exception:
        _close_panel(page)
        raise
    return list_instructions(page, log=log)


def _prompt_box(page: Page):
    """The agent prompt box that is actually on screen.

    Fresh projects open Agent mode as a full-session PANEL with its own
    (hidden) ProseMirror; `.first` then resolves to an invisible box and the
    click times out (measured 2026-11-12 on the 'Studio Funny Animals'
    project). Pick the first VISIBLE box; if none is visible, close the
    session panel and retry.
    """
    def _visible_box():
        boxes = page.locator(AGENT_PROMPT)
        for i in range(boxes.count()):
            try:
                if boxes.nth(i).is_visible():
                    return boxes.nth(i)
            except PWError:
                continue
        return None

    box = _visible_box()
    if box is not None:
        return box
    close = page.locator('.agent-panel-wrapper button[aria-label="Close"]')
    if close.count():
        close.first.click(timeout=6000)
        page.wait_for_timeout(1200)
    box = _visible_box()
    if box is None:
        raise Blocked('the agent prompt box (ProseMirror "What do you want to '
                      'create?") is not visible - is Agent ON?')
    return box


def type_prompt(page: Page, text: str, log=print) -> str:
    """Type into the agent's prompt box and read it back."""
    box = _prompt_box(page)
    box.click(timeout=8000)
    page.wait_for_timeout(300)
    page.keyboard.press('Control+A')
    page.keyboard.press('Delete')
    page.wait_for_timeout(300)
    page.keyboard.insert_text(text)
    page.wait_for_timeout(700)
    got = (box.inner_text() or '').strip()
    log(f'  agent prompt: {len(got)} chars')
    return got


def clear_prompt(page: Page) -> None:
    try:
        box = _prompt_box(page)
    except Blocked:
        return
    box.click(timeout=6000)
    page.keyboard.press('Control+A')
    page.keyboard.press('Delete')
    page.wait_for_timeout(300)


def prompt_text(page: Page) -> str:
    try:
        box = _prompt_box(page)
    except Blocked:
        return ''
    try:
        return (box.inner_text() or '').strip()
    except PWError:
        return ''


def submit(page: Page, log=print) -> bool:
    """Click Start generation and confirm the prompt was consumed.

    The click has to actually take: an account-panel read between typing and
    submitting once swallowed it, and the run then waited ten minutes for
    tiles that were never requested (2026-11-06). A prompt still sitting in
    the box after the click means it did not submit.
    """
    for attempt in (1, 2):
        btn = page.locator('button[aria-label="Start generation"]')
        if not btn.count():
            raise Blocked('no Start generation button in the agent panel')
        btn.first.click(timeout=10_000)
        page.wait_for_timeout(2500)
        if not prompt_text(page):
            return True
        log(f'  submit did not take (prompt still present) - retrying '
            f'(attempt {attempt})')
        page.wait_for_timeout(1000)
    raise Blocked('Start generation did not consume the prompt after two '
                  'clicks')
