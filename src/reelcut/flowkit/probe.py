"""`flow probe` - map what this Flow build actually offers, at 0 credits.

The CLI is meant to use "all available tools of Flow", and the only honest way
to know what those are is to ask the live page: the product drifted three times
in two months (the Settings buttons, the Agent settings panel, the publish
dialog), so a driver built on remembered UI is a driver that breaks silently.

What it inspects, all read-only:
  composer controls      every visible button/input in the composer and its aria
  Agent switch           ON/OFF in either build shape
  Agent settings panel   the full panel text, every control, the model picker
  model menus            options listed, never selected
  instructions           the Agent-instructions control, opened and dumped
  ingredients            "Add ingredients to the prompt box", opened and dumped
  project "+" menu       what a project-level upload/character/scene offers
  tile menu              the per-clip actions, incl. the Download sub-menu
  Agent ON               optional: what the agent panel shows when enabled

It never types a prompt, never clicks Generate, never picks a model option and
never downloads. A probe run therefore cannot spend a credit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import Page

from . import chrome, composer, pages, settings
from . import selectors as S
from .errors import Blocked

RUNS = Path(__file__).resolve().parent.parent / 'runs'


def _shot(page: Page, out_dir: Path, name: str) -> str:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f'{name}.png'
        page.screenshot(path=str(p))
        return str(p)
    except Exception:  # noqa: BLE001 - a screenshot must never fail the probe
        return ''


def _menu_items(page: Page) -> list[str]:
    try:
        return [m for m in pages.menu_items(page) if (m or '').strip()]
    except Exception:  # noqa: BLE001
        return []


def _close_menu(page: Page) -> None:
    page.keyboard.press('Escape')
    page.wait_for_timeout(400)
    if pages.overlay_count(page):
        pages.dismiss_overlays(page)


def _probe_settings(page: Page) -> dict:
    """Open the Agent settings panel, dump it, close it."""
    out: dict = {'opened': False, 'panes': [], 'panel_text': ''}
    try:
        settings.open_settings(page)
        out['opened'] = True
    except Blocked as e:
        out['error'] = str(e)[:400]
        return out
    try:
        out['panes'] = composer.overlay_panes(page)
        out['panel_text'] = ' | '.join(
            p['text'] for p in out['panes'] if p.get('text'))[:3000]
        # The model pickers live INSIDE this panel; probing them after closing
        # it found zero of them (and reported "no models") on 2026-11-06.
        out['model_menus'] = _model_pickers(page)
    finally:
        try:
            settings.close_settings(page)
        except Exception:  # noqa: BLE001
            pass
    return out


def _probe_plus_menu(page: Page) -> list[str]:
    loc = page.locator(S.ADD_MEDIA_BTN)
    if not loc.count():
        return []
    try:
        loc.first.click(timeout=8000)
    except Exception:  # noqa: BLE001
        return []
    page.wait_for_timeout(900)
    items = _menu_items(page)
    _close_menu(page)
    return items


def _probe_tile_menu(page: Page) -> dict:
    """Right-click the first tile: list its actions, then the Download sub-menu."""
    out = {'tile': '', 'items': [], 'download_items': []}
    ts = pages.tiles(page)
    if not ts:
        return out
    tile = ts[0]
    out['tile'] = tile['name']
    try:
        pages.open_tile_menu(page, pages.find_tile_by_name(page, tile['name']))
    except Exception as e:  # noqa: BLE001
        out['error'] = type(e).__name__
        return out
    out['items'] = _menu_items(page)
    if any('Download' in i for i in out['items']):
        try:
            pages.click_menu_item(page, 'Download')
            page.wait_for_timeout(900)
            out['download_items'] = _menu_items(page)
        except Exception as e:  # noqa: BLE001
            out['download_error'] = type(e).__name__
    _close_menu(page)
    return out


def _model_pickers(page: Page) -> list[dict]:
    out = []
    for sel in (S.VIDEO_MODEL_BTN, S.IMAGE_MODEL_BTN):
        try:
            if page.locator(sel).count():
                out.append(composer.model_menu(page, sel))
        except Exception:  # noqa: BLE001
            continue
    return out


def probe_project(page: Page, uuid: str, label: str,
                  agent: str = 'leave', log=print) -> dict:
    """Map one project. `agent` is leave | on | off (restores OFF afterwards)."""
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out_dir = RUNS / 'probes' / f'{stamp}_{label}'
    data: dict = {
        'label': label, 'uuid': uuid, 'stamp': stamp,
        'started': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'dir': str(out_dir),
    }

    pages.open_project(page, uuid)
    pages.bring_to_front(page)
    page.wait_for_timeout(2000)
    # A first-run changelog ("Introducing Omni 1.2", 2026-11-06) sits over the
    # composer and makes every read below look empty. Clear it, then settle.
    pages.dismiss_modals(page, log=log)
    page.wait_for_timeout(800)

    # Agent ON and Agent OFF are two DIFFERENT composers (measured 2026-11-06):
    # ON is the chat-style panel (the one YT-Agent's video runs paste into),
    # OFF swaps to the direct image composer whose chip prints the image
    # config. So the switch comes FIRST, and everything below is read in the
    # requested state. The original state is restored at the end.
    initial_agent = composer.agent_state(page)
    data['agent_initial'] = initial_agent
    if agent in ('on', 'off'):
        data['agent_forced'] = composer.set_agent(page, agent == 'on', log=log)
        page.wait_for_timeout(1500)
    data['url'] = page.url
    data['title'] = page.title()
    try:
        data['signed_in_as'] = chrome.signed_in_as(page)
    except Exception:  # noqa: BLE001
        data['signed_in_as'] = ''
    data['tiles'] = [t['name'] for t in pages.tiles(page)]
    data['composer_controls'] = composer.composer_controls(page)
    data['agent'] = composer.agent_state(page)
    data['agent_panel_text'] = composer.panel_text(page)
    data['screenshot_composer'] = _shot(page, out_dir, '00_composer')

    log('  settings panel...')
    data['settings'] = _probe_settings(page)
    data['settings_screenshot'] = _shot(page, out_dir, '01_settings')

    log('  model menus...')
    data['model_menus'] = _model_pickers(page)

    log('  instructions...')
    data['instructions'] = composer.open_instructions(page)
    data['instructions_screenshot'] = _shot(page, out_dir, '02_instructions')

    log('  ingredients...')
    data['ingredients'] = composer.open_ingredients(page)
    data['ingredients_screenshot'] = _shot(page, out_dir, '03_ingredients')

    log('  "+" menu and tile menu...')
    data['add_media_menu'] = _probe_plus_menu(page)
    data['tile_menu'] = _probe_tile_menu(page)

    if agent in ('on', 'off'):
        data['agent_panes'] = composer.overlay_panes(page)
        data['agent_controls'] = composer.composer_controls(page)
        data['agent_panel_text_forced'] = composer.panel_text(page)

    # Leave the project as it was found.
    if agent in ('on', 'off') and initial_agent.get('on') is not None \
            and initial_agent['on'] != (agent == 'on'):
        composer.set_agent(page, initial_agent['on'], log=log)
        data['agent_restored_to'] = initial_agent['on']

    data['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'probe.json').write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    data['probe_json'] = str(out_dir / 'probe.json')
    return data


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return ''
