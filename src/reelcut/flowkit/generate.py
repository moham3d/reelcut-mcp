"""One generation run: configure, price it, generate, harvest.

The order is deliberate and is the CLI's whole safety model:

    configure -> READ THE PRICE FLOW COMPUTES -> refuse if over budget
              -> type -> click -> wait -> rename -> download -> manifest

The price comes from the composer's own "Generating will use N credits" line,
read while the config popover is open and BEFORE anything is clicked. Images
read 0 and are treated as free; a video whose preview exceeds `max_credits`
stops the run before the credit is committed.

Measured costs (Omni 1.1 Flash, per output, 2026-11-06):
4s=7 · 6s=10 · 8s=12 · 10s=15 credits.
"""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import Page

from . import assets, composer, frames, genconfig, pages
from . import selectors as S
from .errors import Blocked


def _start_generation(page: Page) -> None:
    btn = page.locator(S.GEN_BTN_SEL)
    if not btn.count():
        raise Blocked('no Start generation button - is the composer mounted '
                      'and the prompt non-empty?')
    btn.first.click(timeout=12_000)
    page.wait_for_timeout(1500)


def run(page: Page, *, project: str, mode: str, prompt: str, out_dir: Path,
        outputs: int = 1, aspect: str | None = None,
        duration: str | None = None, resolution: str | None = None,
        model: str | None = None, thinking: str | None = None,
        submode: str | None = None, refs: tuple[str, ...] = (),
        download: str | None = None, max_credits: int = 15,
        start_frame: str | None = None, end_frame: str | None = None,
        dry_run: bool = False,
        timeout_s: int = 900, log=print) -> dict:
    stamp = time.strftime('%Y%m%d_%H%M%S')
    kind = 'image' if mode == 'image' else 'video'
    result: dict = {'stamp': stamp, 'project': project, 'mode': kind,
                    'prompt': prompt, 'outputs': outputs, 'started':
                    time.strftime('%Y-%m-%dT%H:%M:%S')}

    pages.open_project(page, project)
    pages.bring_to_front(page)
    page.wait_for_timeout(1500)
    pages.dismiss_modals(page, log=log)

    # The direct composer is the controllable one: Agent OFF exposes the
    # Image/Video switch and the price preview. Agent ON is the chat panel and
    # is `flow agent`'s business, not this command's.
    composer.set_agent(page, False, log=log)
    # A lingering agent SESSION keeps the button-build state alive: the config
    # popover never opens while session content covers the composer. Close the
    # panel, then re-assert OFF (closing flips chip builds back to Agent ON).
    if composer.close_session_panel(page, log=log):
        if composer.agent_state(page).get('on'):
            composer.set_agent(page, False, log=log)

    cfg = genconfig.configure(page, mode=mode, aspect=aspect, outputs=outputs,
                              duration=duration, resolution=resolution,
                              model=model, thinking=thinking, submode=submode,
                              log=log)
    result['config'] = {k: cfg.get(k) for k in
                        ('mode', 'submode', 'aspect', 'model', 'resolution',
                         'duration', 'outputs', 'thinking')}
    cost = cfg.get('cost_credits')
    result['cost_preview'] = cfg.get('cost_text')
    genconfig.close_popover(page)

    if kind == 'image':
        if cost not in (0, None):
            raise Blocked(f'an image shot now previews at {cost} credits - it '
                          f'used to be free; refusing before the click')
    else:
        if cost is None:
            raise Blocked('could not read the credit preview - refusing to '
                          'generate a paid clip blind')
        if cost > max_credits:
            raise Blocked(f'this run would use {cost} credits, over the '
                          f'--max-credits budget of {max_credits}. Lower the '
                          f'duration/outputs, or raise the budget deliberately.')
    result['budget_ok'] = True

    # Frames: fill the start/end slots BEFORE the prompt and the click. The
    # picker lists the project's own images, so stills generated earlier become
    # the anchors of the shot.
    if start_frame or end_frame:
        if kind != 'video':
            raise Blocked('start/end frames apply to video only')
        if cfg.get('submode') != 'Frames':
            raise Blocked('start/end frames need --submode frames (the composer '
                          'must be showing the Start/End slots)')
        if start_frame:
            frames.select_frame(page, 'Start', start_frame, log=log)
        if end_frame:
            frames.select_frame(page, 'End', end_frame, log=log)
        result['frames'] = {'start': start_frame or '', 'end': end_frame or ''}

    # Prompt: read it back. A truncated paste renders the wrong clip.
    pages.clear_prompt(page, log=log)
    got = pages.set_prompt(page, prompt, log=log)
    if not got or len(got) < max(20, len(prompt) * 0.9):
        raise Blocked(f'the prompt did not paste intact ({len(got)} of '
                      f'{len(prompt)} chars)')
    result['prompt_chars'] = len(got)

    chips = []
    for ref in refs:
        tile = pages.upload_media(page, ref, log=log)
        if not tile:
            raise Blocked(f'reference {ref!r} did not land as a tile')
        chip = pages.attach_tile_to_prompt(page, tile, log=log)
        chips.append({'file': str(ref), 'tile': tile,
                      'chip': bool(chip.get('src'))})
    result['references'] = chips
    if refs:
        page.wait_for_timeout(800)

    before = {t['name'] for t in pages.tiles(page)
              if (t['name'] or '').strip()}
    if dry_run:
        # Everything is configured, priced, framed and typed - but no click.
        # This is the free rehearsal an agent should run before a paid shot.
        result['dry_run'] = True
        result['prompt_chars'] = len(got)
        result['manifest'] = str(assets.write_manifest(out_dir, result))
        log('  DRY RUN: configured, framed and typed; Start generation was NOT '
            'clicked')
        return result
    log(f'  generating {outputs} {kind}(s) for {cost} credits...')
    _start_generation(page)
    new = assets.wait_new_tiles(page, before, outputs, timeout_s=timeout_s,
                                prompt=prompt, log=log)
    result['tiles'] = [t['name'] for t in new]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, tile in enumerate(new, 1):
        # The name read from the grid is the PROMPT until Flow finishes, then
        # a short title. ensure_ready waits for the finished state and hands
        # back the name that now resolves.
        settled = assets.ensure_ready(page, tile['name'], log=log)
        new_name = f'{kind}_{stamp}_{i:02d}'
        final = assets.rename(page, settled, new_name, log=log)
        ext = 'png' if kind == 'image' else 'mp4'
        dest = out_dir / f'{final}.{ext}'
        dl = assets.download_tile(page, final, dest, kind=kind, log=log)
        entries.append({'tile': tile['name'], 'name': final,
                        'file': dl['path'], 'bytes': dl['bytes']})
    result['files'] = entries
    result['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    result['manifest'] = str(assets.write_manifest(out_dir, result))
    return result
