"""The project settings panel: open, read, CHECK, close.

Settings are CHECK-ONLY. The video model, the aspect ratio and the credit
policy are SAVED per-project settings, and none is silently changed: a saved
setting belongs to whoever saved it. A wrong setting stops the run and says
what to fix. (The model used to be auto-corrected; that branch had never
executed on any account, which made it an unproven mutation that would only
ever run when something was already wrong. Removed, and `ensure_video_model`
kept below for deliberate manual use only.)

The credit-policy check reads the CHECKED radio, and that detail is
load-bearing: the radio group's text contains BOTH options' labels, so
testing that text for the auto-spend phrase is true whichever one is selected
- a guard that cannot fail. It did exactly that for a while, and one account
sat on the wrong setting the whole time.

TWO PANEL SHAPES (2026-11-05). The panel is now titled "Agent settings" and
has a Save button; the layout (this project's, video mode) is:

    Confirm before generating   [Always] [Never]        <- mat-radio-group
    Image generation default    [16:9][4:3][1:1][3:4][9:16] x1..x4  <model>
    Video generation default    [16:9][9:16]   x1..x4   Omni 1.1 Flash ▾

All three required values are STILL readable there - the video model picker
kept its `button.video-model-picker-button` shape (aria "Video generation
default model"), it just sits at the bottom of a scrolling panel, below the
fold. (A screenshot cut it off once and briefly suggested the model had been
removed; the DOM said otherwise, which is why the read is by selector and the
verdict by value.) The heading-based reader (`_VIDEO_ROW_JS`) is the FALLBACK
for a build where that picker disappears: then the aspect ratio is still
asserted, and the model is recorded as unknown rather than wedging the run -
the same three-valued shape as the identity guard. Evidence:
scripts/probe_settings_panels.py and probe_settings_button.py (0 credits).
"""

from __future__ import annotations

import time

from playwright.sync_api import Error as PWError, Page

from . import pages
from . import selectors as S
from .errors import Blocked


def settings_open(page: Page) -> bool:
    """The panel swaps the prompt bar for a Back button; Escape does NOT close
    it, so Back is the only reliable signal in either direction."""
    return page.locator(S.SETTINGS_BACK_SEL).count() > 0


def _panel_opened(page: Page, timeout_s: float = 4.0) -> bool:
    """Poll for the panel's Back button: a click's only trustworthy receipt.

    The tile-view menu the header button opens has no Back, and neither has the
    page itself, so this is what turns "a button got clicked" into "the panel
    is open".
    """
    end = time.time() + timeout_s
    while time.time() < end:
        if settings_open(page):
            return True
        page.wait_for_timeout(250)
    return False


def open_settings(page: Page) -> None:
    """Open the panel, or raise `Blocked` saying why it could not be opened.

    Four ways this lookup fails, and they need different fixes:

    * **Two buttons, one name.** Since 2026-11-05 the page renders a header
      `settings_2` button and the composer's `tune` button, BOTH labelled
      "Settings". The header one opens the tile-view menu. A bare label picks
      whichever comes first in the DOM.
    * **A hidden duplicate twin.** Measured 2026-11-05 on account a's Islamic
      project: the same aria-label matched more than one node and `.first` was
      an invisible one, so a 30s visibility wait expired even though a visible
      trigger existed a few hundred pixels away. The composer also sits at a
      different x per project (b: 1439, a: 983), so geometry is not an anchor
      either.
    * **Wrong view.** The composer exists only on the project ROOT; on
      `/project/<uuid>/edit/<generation-id>` there is none at all.
      `pages.at_project_root` is the guard for that one.
    * **A genuinely wrong saved setting.** Those failures are FAST and must
      keep blocking - the verdict is never retried, only the lookup.

    So the lookup walks SETTINGS_BTN_CANDIDATES and accepts a candidate only
    when the PANEL OPENED (a click that opens the tile-view menu is a miss and
    the ladder moves on). A miss costs a few seconds; a wrong verdict never
    happens, because the verdict is read after the panel is confirmed open.
    """
    if settings_open(page):
        return
    pages.dismiss_overlays(page)   # a backdrop makes a present button unclickable
    tried: list[str] = []
    for sel in S.SETTINGS_BTN_CANDIDATES:
        loc = page.locator(sel)
        try:
            n = min(loc.count(), 3)   # a hidden twin, if any, is one of these
        except PWError as e:
            tried.append(f'{sel}: unreadable ({type(e).__name__})')
            continue
        for i in range(n):
            btn = loc.nth(i)
            try:
                btn.wait_for(state='visible', timeout=4_000)
                btn.click(timeout=6_000)
            except PWError as e:
                tried.append(f'{sel}[{i}]: {type(e).__name__}')
                pages.dismiss_overlays(page)
                continue
            if _panel_opened(page):
                page.wait_for_timeout(400)   # let the panel settle
                return
            tried.append(f'{sel}[{i}]: clicked, no panel')
            pages.dismiss_overlays(page)
            try:
                page.keyboard.press('Escape')
            except PWError:
                pass
            page.wait_for_timeout(250)
    raise Blocked(
        f'the settings panel could not be opened by any candidate trigger '
        f'({", ".join(tried) or "none matched"}). This step only CHECKS saved '
        'settings and never changes them, so the run stops here rather than '
        'guess. Check, in this order: (0) TWO buttons are named "Settings" - the '
        'header one opens the tile view menu, the composer `tune` one opens the '
        'panel - and a hidden duplicate can share the aria-label, so run '
        'scripts/probe_settings_panels.py <account> <genre> (0 credits) and read '
        'what each candidate opens; add the right one to '
        'SETTINGS_BTN_CANDIDATES in yta/driver/selectors.py. (1) is the page the '
        'project ROOT? The composer exists only there, never on '
        '/project/<uuid>/edit/<id>; pages.at_project_root(url, uuid) answers '
        'that. (2) has Flow renamed or MOVED the button into another container? '
        'The candidate ladder is the place to teach it, not open_settings.')


def close_settings(page: Page) -> None:
    """Escape is ignored by this panel - it must be left via Back."""
    if not settings_open(page):
        return
    try:
        page.locator(S.SETTINGS_BACK_SEL).first.click(timeout=8000)
        page.wait_for_timeout(900)
    except Exception:  # noqa: BLE001 - a stray overlay swallowed the click
        if not pages.dismiss_overlays(page):
            pages.reload(page)
            raise RuntimeError('settings panel wedged behind an overlay; page reloaded')


# The current panel has no model picker, so the video row is found by its
# section HEADING and read as the first toggle group after it in DOCUMENT
# order. That ordering is the whole point: the heading and its group are
# siblings inside the panel, so any "walk up to the nearest ancestor that owns
# a group" logic lands on the panel itself and reads the IMAGE row's group
# first (it sits above). compareDocumentPosition asks the exact question.
_VIDEO_ROW_JS = """() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const heading = [...document.querySelectorAll('*')].find(el =>
      el.children.length === 0 && /video generation/i.test(el.textContent || ''));
  if (!heading) return null;
  const groups = [...document.querySelectorAll('mat-button-toggle-group')];
  const after = groups.filter(g =>
      (heading.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0);
  const g = after[0];
  return {
    heading: norm(heading.textContent),
    options: g ? [...g.querySelectorAll('mat-button-toggle')].map(t => ({
      text: norm(t.innerText),
      checked: t.classList.contains('mat-button-toggle-checked')
               || t.getAttribute('aria-pressed') === 'true'
               || (t.querySelector('button') || {getAttribute: () => null})
                    .getAttribute('aria-pressed') === 'true',
    })) : [],
  };
}"""


def _row_of(page: Page, button_sel: str) -> dict:
    """Read the settings row that owns a model picker: model, aspect, outputs.

    The panel has two near-identical rows (Image generation / Video
    generation), each with its own model picker, its own aspect-ratio toggle
    group and its own outputs group. Scoping by the picker button is the only
    way to tell them apart - both contain a "9:16" toggle.
    """
    return page.eval_on_selector(
        button_sel,
        """btn => {
             let row = btn.closest('[class*="setting"], [class*="row"], section, mat-card')
                       || btn.parentElement;
             const label = (btn.querySelector('.model-select-trigger-label')?.innerText || '').trim();
             const toggles = [...row.querySelectorAll('mat-button-toggle-group')].map(g => ({
               options: [...g.querySelectorAll('mat-button-toggle')].map(t => ({
                 text: (t.innerText || '').trim().replace(/\\s+/g, ' '),
                 checked: t.classList.contains('mat-button-toggle-checked')
                          || t.querySelector('button')?.getAttribute('aria-pressed') === 'true',
               })),
             }));
             return {model: label, rowText: (row.innerText || '').trim().replace(/\\s+/g, ' '),
                     toggles};
           }""",
    )


def video_settings(page: Page, already_open: bool = False) -> dict:
    if not already_open:
        open_settings(page)
    try:
        if page.locator(S.VIDEO_MODEL_BTN).count():
            # Legacy build: the row is scoped by its model-picker button, which
            # is also how the model itself is read.
            return {**_row_of(page, S.VIDEO_MODEL_BTN), 'shape': 'model-picker'}
        row = page.evaluate(_VIDEO_ROW_JS)
        if row is None:
            raise Blocked(
                'the settings panel is open but has no "Video generation" row - '
                'Flow changed the panel layout AGAIN. Read it without spending: '
                'uv run python scripts/probe_settings_panels.py b (0 credits), '
                'then update SETTINGS_BTN_SEL / _VIDEO_ROW_JS in '
                'yta/driver/settings.py. Nothing was spent.')
        return {'shape': 'agent-settings', 'model': '',
                'rowText': row['heading'],
                'toggles': [{'options': row['options']}] if row['options'] else []}
    finally:
        if not already_open:
            close_settings(page)


def ensure_video_model(page: Page) -> str:
    """Drive Flow's model menu to select the required model, and return what it
    reads.

    **Nothing calls this, on purpose** - check_project_settings refuses when
    the model is wrong. It is kept because it is the only code that knows how
    to drive Flow's model picker. If you call it, call it deliberately: it
    CHANGES a saved setting.
    """
    open_settings(page)
    try:
        if not page.locator(S.VIDEO_MODEL_BTN).count():
            raise Blocked(
                'this Flow build has no video-model picker (the panel is "Agent '
                'settings" now), so the model cannot be changed from here. Set it '
                'in Flow by hand if it is ever wrong; check_project_settings '
                'records it as unknown and still gates the aspect ratio.')
        current = page.locator(
            f'{S.VIDEO_MODEL_BTN} {S.MODEL_LABEL_SEL}').first.inner_text().strip()
        if current == S.GEN_MODEL:
            return current
        page.locator(S.VIDEO_MODEL_BTN).first.click(timeout=8000)
        page.wait_for_selector(S.MODEL_MENU_SEL, timeout=8000)
        page.locator(S.MODEL_MENU_SEL).filter(
            has_text=S.GEN_MODEL).first.click(timeout=8000)
        page.wait_for_timeout(900)
        return page.locator(
            f'{S.VIDEO_MODEL_BTN} {S.MODEL_LABEL_SEL}').first.inner_text().strip()
    finally:
        pages.dismiss_overlays(page)
        close_settings(page)


def _policy_state(page: Page) -> dict:
    """Which credit-policy radio is actually SELECTED, plus the raw markup.

    `checked` empty means it could not be read - the caller must treat that as
    unknown rather than as agreement.
    """
    out = {'text': '', 'checked': '', 'html': ''}
    group = page.locator(S.RADIO_GROUP_SEL).first
    try:
        out['text'] = group.inner_text().replace('\n', ' ').strip()
    except PWError:
        pass
    try:
        out['html'] = (group.evaluate('e => e.outerHTML') or '')[:1500]
    except PWError:
        pass
    # Material marks the selection on the inner input; the aria fallbacks cover
    # the variants where it is on the host element instead.
    for sel in ('input[type="radio"]:checked',
                'mat-radio-button[aria-checked="true"]',
                '[aria-checked="true"]'):
        try:
            loc = group.locator(sel)
            if loc.count():
                out['checked'] = (loc.first.evaluate(
                    "e => ((e.closest('mat-radio-button') || e.parentElement || e)"
                    ".innerText || '').replace(/\\n/g, ' ').trim()") or '')[:160]
                break
        except PWError:
            continue
    return out


def check_project_settings(page: Page, log=print) -> dict:
    """CHECK the settings the pipeline depends on, and refuse if one is wrong.

    Three values, three outcomes - and they are NOT symmetric:

    * aspect ratio: readable on both builds -> a wrong one BLOCKS.
    * credit policy: readable on both builds -> "Always" (ask first) BLOCKS,
      because an unattended run would sit on a confirmation dialog.
    * video model: readable only on the legacy build. On the current one it is
      recorded as unknown and the run continues (a warning in the log), with
      `model_verified: False` in the report. A UI change must not wedge every
      run; the aspect ratio is what would visibly ruin the product, and that
      is still gated.
    """
    open_settings(page)
    try:
        vs = video_settings(page, already_open=True)
        shape = vs.get('shape', 'model-picker')
        if shape == 'model-picker' and vs['model'] != S.GEN_MODEL:
            raise Blocked(
                f'video model is {vs["model"]!r}, not {S.GEN_MODEL!r} - set it in '
                f'Flow once. Like the aspect ratio and the credit policy, the model '
                f'is a saved project setting and this pipeline does not change it.')

        ratio = next((g for g in vs['toggles']
                      if any(S.ASPECT in o['text'] for o in g.get('options', []))),
                     None)
        chosen = next((o['text'] for o in ratio['options'] if o.get('checked')), '') if ratio else ''
        if S.ASPECT not in chosen:
            raise Blocked(
                f'aspect ratio reads {chosen!r}, not {S.ASPECT!r} '
                f'(panel shape {shape!r}, row {vs.get("rowText", "")[:60]!r}) - '
                'set it in Flow once (the ratio is a saved project setting), '
                'then re-run. Nothing was spent.')

        st = _policy_state(page)
        if st['checked']:
            auto = 'spend credits automatically' in st['checked']
        else:
            # Could not read the selection; fall back to the weak text test and
            # SAY so rather than implying the setting was verified.
            auto = 'spend credits automatically' in st['text']
        if not auto:
            raise Blocked(
                'Flow is set to ask before spending credits, so an unattended run '
                'would hang on that prompt. Set the project\'s credit policy to '
                f'Never. (selected option reads {st["checked"]!r})')

        model = vs.get('model') or ''
        if shape != 'model-picker':
            log('settings: this Flow build does not expose the video model in the '
                'Agent settings panel - aspect ratio and credit policy were '
                'verified, the model is recorded as unknown')
        settings = {'model': model or 'unknown',
                    'model_verified': bool(model) and model == S.GEN_MODEL,
                    'shape': shape,
                    'ratio': chosen,
                    'policy_checked': st['checked'],
                    'policy_verified': bool(st['checked'])}
        # EVERY toggle group in the video row, not just the aspect one, so a
        # project configured to produce something other than a video is at
        # least visible in the report.
        settings['toggle_groups'] = [
            {'options': [o['text'] for o in g.get('options', [])],
             'checked': next((o['text'] for o in g.get('options', []) if o.get('checked')), '')}
            for g in (vs.get('toggles') or [])]
        if not st['checked']:
            # Only when the read FAILED is the markup worth keeping.
            settings['policy_text'] = st['text'][:200]
            settings['policy_html'] = st['html']
        return settings
    finally:
        close_settings(page)
        if pages.backdrop_count(page):
            log('stray backdrop after settings; reloading to clear')
            pages.reload(page)
