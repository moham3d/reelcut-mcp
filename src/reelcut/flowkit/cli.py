"""`flow` - a standalone Google Flow CLI for images, videos and the agent.

    flow projects                     list the projects on this profile
    flow credits                      read the credit balance (0 credits)
    flow probe --project <uuid|name>  map the live UI: composer, agent, models,
                                      instructions, references, menus (0 credits)

Generation: image (free) and video (priced by the composer's own preview and
budget-gated); agent mode for guidelines and prompts. `flow probe` maps whatever
this build currently offers.

All commands attach to the real Chrome profile, so nothing here can run
alongside YT-Agent/SlideAgent on the same profile.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import (__version__, agent, assets, chrome, composer, config, console,
               generate, genconfig, inputs, pages, probe, schema,
               selftest as selftest_mod)
from .errors import Blocked

RUNS = Path(__file__).resolve().parent.parent / 'runs'


class Session:
    """Ensure the profile's Chrome is up, attach, and hand out a fresh page."""

    def __init__(self, page=None):
        self.pw = None
        self.ctx = None
        self.page = page

    def __enter__(self):
        chrome.ensure_chrome()
        self.pw = sync_playwright().start()
        _, self.ctx = chrome.attach(self.pw)
        if self.page is None:
            self.page = self.ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self.pw is not None:
                self.pw.stop()
        except Exception:  # noqa: BLE001
            pass
        return False


def _bind(args) -> None:
    chrome.configure(profile=getattr(args, 'profile', None),
                     port=getattr(args, 'port', None),
                     u_index=getattr(args, 'u_index', None))


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument('--profile', default=None, help='Chrome profile dir')
    p.add_argument('--port', type=int, default=None, help='CDP port (default 9223)')
    p.add_argument('-u', '--u', type=int, default=None, dest='u_index',
                   help='Google account index in the profile (default 0)')


# ------------------------------------------------------------------- commands


def _create_project(page, name: str = '', log=print) -> str:
    """Create a project from the home page and return its uuid.

    The control is a button whose text is the `add` ligature glued to
    "New project" (no aria-label), so it is found by text. Creating lands on
    the new project's page, whose only stable identity is the URL uuid.
    """
    page.goto(chrome.flow_url(), wait_until='domcontentloaded', timeout=45_000)
    page.wait_for_timeout(3500)
    pages.dismiss_modals(page, log=log)
    before = page.url
    ok = page.evaluate(
        """() => { const b = [...document.querySelectorAll('button')]
             .find(x => (x.innerText || '').replace(/\\s+/g, '')
                        .includes('Newproject'));
             if (b) { b.click(); return true; } return false; }""")
    if not ok:
        raise Blocked('no "New project" button on the Flow home page')
    deadline = time.time() + 30
    uuid = ''
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        if '/project/' in page.url and page.url != before:
            uuid = page.url.split('/project/')[1].split('?')[0].strip('/')
            break
    if not uuid:
        raise Blocked('clicked New project but no /project/<uuid> URL appeared')
    page.wait_for_timeout(2500)
    pages.dismiss_modals(page, log=log)
    if name:
        try:
            inp = page.locator('input.editable-text-input').first
            inp.click(timeout=6000)
            page.wait_for_timeout(300)
            page.keyboard.press('Control+A')
            page.keyboard.type(name)
            page.keyboard.press('Enter')
            page.wait_for_timeout(1200)
            log(f'  project renamed to {name!r}')
        except Exception as e:  # noqa: BLE001 - the uuid is the real handle
            log(f'  ! could not rename the new project ({type(e).__name__}); '
                f'it keeps Flow\'s timestamp name')
    return uuid


def cmd_projects(args) -> int:
    _bind(args)
    with Session() as s:
        if args.create is not None:
            uuid = _create_project(s.page, args.create or '', log=print)
            print(f'created project {uuid}')
            return 0
        found = pages.discover_projects(s.page)
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
        return 0
    if not found:
        print('no projects found (is this profile signed into Flow?)')
        return 1
    for title, uuid in found.items():
        print(f'{uuid}  {title}')
    return 0


def cmd_credits(args) -> int:
    _bind(args)
    with Session() as s:
        s.page.goto(chrome.flow_url(), wait_until='domcontentloaded', timeout=45_000)
        s.page.wait_for_timeout(2500)
        res = chrome.credits(s.page)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"credits: {res['credits'] or 'unreadable'}")
        if not res['closed']:
            print('! the account panel did not close - reload before driving further')
    return 0 if res['credits_num'] is not None else 1


def cmd_probe(args) -> int:
    _bind(args)
    with Session() as s:
        if not args.project:
            found = pages.discover_projects(s.page)
            print('pass --project <uuid|name>. Projects on this profile:')
            for title, uuid in found.items():
                print(f'  {uuid}  {title}')
            return 2
        uuid = pages.resolve_uuid(s.page, args.project)
        label = args.project if not config.UUID_RE.match(args.project) \
            else args.project[:8]
        data = probe.probe_project(s.page, uuid, label=label,
                                   agent=args.agent, log=print)
    _print_probe(data)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _print_probe(d: dict) -> None:
    print(f"\n== {d.get('title', '')}  ({d['uuid']})")
    print(f"   url: {d.get('url', '')[:110]}")
    print(f"   signed in: {d.get('signed_in_as') or 'unknown'}")
    print(f"   tiles: {len(d.get('tiles') or [])}")
    ag = d.get('agent') or {}
    print(f"   Agent: {ag.get('mode')} on={ag.get('on')} "
          f"(visible={ag.get('visible')}, matches={ag.get('count')})")
    ptxt = (d.get('agent_panel_text') or '').replace('\n', ' ')
    if ptxt:
        print(f"   panel: {ptxt[:300]}")

    st = d.get('settings') or {}
    print(f"\n-- Agent settings panel (opened={st.get('opened')})")
    text = (st.get('panel_text') or '').replace('\n', ' ')
    print(f"   {text[:600]}")
    for pane in (st.get('panes') or [])[:2]:
        toggles = [c for c in pane.get('controls', [])
                   if c['tag'] in ('mat-button-toggle', 'mat-radio-button')]
        if toggles:
            for c in toggles[:14]:
                print(f"     [{c['tag']}] {c['text'][:60]!r} "
                      f"checked={c['checked']} pressed={c['pressed']}")

    for mm in ((d.get('settings') or {}).get('model_menus') or d.get('model_menus') or []):
        print(f"\n-- model picker {mm['selector']} "
              f"(read {mm.get('label')!r}, {mm['found']} match(es))")
        for opt in mm.get('options') or []:
            print(f'     {opt}')

    for key, title in (('instructions', 'Agent instructions'),
                       ('ingredients', 'Add ingredients to the prompt box')):
        sec = d.get(key) or {}
        print(f"\n-- {title} (opened={sec.get('opened')})")
        if sec.get('tabs'):
            print(f"   tabs: {sec['tabs']}")
        for pane in (sec.get('panes') or [])[:3]:
            print(f"   <{pane['tag']} {pane['cls'][:40]}> {pane['text'][:400]!r}")
            for c in (pane.get('controls') or [])[:10]:
                print(f"     [{c['tag']}] aria={c['aria'][:40]!r} "
                      f"text={c['text'][:40]!r} pressed={c['pressed']}")

    print(f"\n-- project '+' menu: {d.get('add_media_menu')}")
    tm = d.get('tile_menu') or {}
    print(f"-- tile menu on {tm.get('tile', '')[:40]!r}: {tm.get('items')}")
    print(f"-- download sub-menu: {tm.get('download_items')}")

    for key, title in (('agent_panes', 'Agent ON panel'),
                       ('agent_controls', 'Agent ON composer controls')):
        if d.get(key):
            print(f"\n-- {title}")
            if key == 'agent_panes':
                for pane in d[key][:3]:
                    print(f"   <{pane['tag']} {pane['cls'][:40]}> "
                          f"{pane['text'][:500]!r}")
            else:
                for c in d[key][:16]:
                    print(f"     [{c['tag']}] aria={c['aria'][:40]!r} "
                          f"text={c['text'][:40]!r} pressed={c['pressed']}")
    print(f"\nprobe json: {d.get('probe_json')}")
    print(f"shots:      {d.get('dir')}")


def _need_project(args) -> str:
    ref = (args.project or os.environ.get('FLOW_PROJECT') or '').strip()
    if not ref:
        raise Blocked('pass --project <uuid|title> (or set FLOW_PROJECT)')
    return ref


def _out_dir(args, mode: str) -> Path:
    if args.out:
        return Path(args.out)
    slug = ''.join(c if c.isalnum() else '_' for c in (args.prompt or ''))[:28]
    return RUNS / f'{time.strftime("%Y%m%d_%H%M%S")}_{mode}_{slug}'


def _run_generation(args, mode: str) -> int:
    _bind(args)
    project = _need_project(args)
    refs = tuple(args.ref or ())
    for r in refs:
        if not Path(r).is_file():
            raise Blocked(f'reference image not found: {r}')
    # Normalise BEFORE touching Chrome: a typo must fail in a second, not after
    # a browser launch. This also refuses Veo by policy.
    # Video-only flags do not exist on the image parser; read them defensively
    # so the same runner serves both subcommands.
    start_frame = getattr(args, 'start_frame', None)
    end_frame = getattr(args, 'end_frame', None)
    max_credits = getattr(args, 'max_credits', 15)
    if mode == 'image' and (start_frame or end_frame):
        raise Blocked('--start-frame/--end-frame apply to video only')

    aspect = inputs.aspect(args.aspect)
    if mode == 'video' and aspect and aspect not in inputs.VIDEO_ASPECTS:
        raise Blocked(f'video offers only {" and ".join(inputs.VIDEO_ASPECTS)} '
                      f'on this build, not {aspect}; images offer '
                      f'{", ".join(inputs.IMAGE_ASPECTS)}')
    duration = inputs.duration(getattr(args, 'duration', None))
    resolution = inputs.resolution(getattr(args, 'res', None))
    model = inputs.model(args.model)
    thinking = inputs.thinking(getattr(args, 'thinking', None))
    submode = inputs.submode(getattr(args, 'submode', None))
    download = inputs.download(args.download)
    outputs = inputs.count(args.count)
    with Session() as s:
        uuid = pages.resolve_uuid(s.page, project)
        res = generate.run(
            s.page, project=uuid, mode=mode, prompt=args.prompt,
            out_dir=_out_dir(args, mode), outputs=outputs,
            aspect=aspect, duration=duration,
            resolution=resolution, model=model, thinking=thinking,
            submode=submode, refs=refs, download=download,
            start_frame=start_frame, end_frame=end_frame,
            dry_run=args.dry_run,
            max_credits=max_credits, timeout_s=args.wait, log=print)
    if res.get('dry_run'):
        print(f"\n== dry run: {res['config']}  price={res['cost_preview']}")
        if res.get('frames'):
            print(f"   frames: {res['frames']}")
        print(f"   prompt chars: {res.get('prompt_chars')}")
        print('   nothing was generated; rerun without --dry-run to spend')
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    print(f"\n== {mode} done: {res['cost_preview'] or 'free'}")
    print(f"   config: {res['config']}")
    if res.get('references'):
        print(f"   references: {[r['tile'] for r in res['references']]}")
    for f in res['files']:
        print(f"   {f['name']}  {f['bytes']:,} bytes  -> {f['file']}")
    print(f"   manifest: {res['manifest']}")
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_image(args) -> int:
    return _run_generation(args, 'image')


def cmd_video(args) -> int:
    return _run_generation(args, 'video')


def _safe_credits(page, log=print) -> dict:
    """Read the balance, and recover if the account panel refuses to close.

    A stuck panel leaves a transparent CDK backdrop over the page; every later
    click is then intercepted and the run dies with a Playwright timeout that
    says nothing about the cause. Reloading the page is the only guaranteed
    reset, and it is safe here because a balance read makes no grant.
    """
    try:
        res = chrome.credits(page, log=log)
    except Exception as e:  # noqa: BLE001 - a read, not a decision
        log(f'  credits read failed ({type(e).__name__})')
        return {}
    if not res.get('closed'):
        log('  account panel stuck after the read - reloading the project')
        try:
            pages.reload(page)
            page.wait_for_timeout(1500)
            pages.dismiss_modals(page, log=log)
        except Exception as e:  # noqa: BLE001
            log(f'  reload after the stuck panel failed ({type(e).__name__})')
        res['reloaded'] = True
    return res


_AGENT_LABEL_RE = re.compile(r'^cli\d{6}_(\d+)$')


def label_index(name: str) -> int | None:
    """The agent's own ordered label (`cli<HHMMSS>_<n>`), or None.

    The batch prompt ends with a naming directive, so a compliant agent labels
    the stills in prompt order. That label is the only ordered handle Flow
    exposes - the grid's DOM order is NOT prompt order (a measured 20-image
    batch came back 2->8, 3->13, 8->17, 19->12).
    """
    m = _AGENT_LABEL_RE.match((name or '').strip())
    return int(m.group(1)) if m else None


def order_tiles_by_label(new: list[dict]) -> list[dict]:
    """Order harvested tiles by the agent's label index when it is trustworthy.

    Only when EVERY tile carries a parseable label and the indices are exactly
    1..N do we trust them. A partial or non-permutation set keeps the DOM order:
    a half-sorted list would be worse than the stable-but-arbitrary one.
    """
    idx = [label_index(t['name']) for t in new]
    if (not idx or any(i is None for i in idx)
            or sorted(idx) != list(range(1, len(new) + 1))):
        return new
    return [t for _, t in sorted(zip(idx, new), key=lambda pair: pair[0])]


def cmd_agent(args) -> int:
    """Agent mode: guidelines, prompt, and (opt-in) generation."""
    _bind(args)
    project = _need_project(args)
    if not (args.list or args.instructions or args.prompt
            or args.instructions_state or args.activate_instruction
            or args.deactivate_instruction):
        raise Blocked('pass --list, --instructions <file>, --instructions-state, '
                      '--activate-instruction, or -p <prompt>')
    with Session() as s:
        uuid = pages.resolve_uuid(s.page, project)
        pages.open_project(s.page, uuid)
        s.page.wait_for_timeout(1500)
        pages.dismiss_modals(s.page, log=print)
        composer.set_agent(s.page, True, log=print)
        s.page.wait_for_timeout(1500)

        if args.list:
            items = agent.list_instructions(s.page, log=print)
            print(json.dumps(items, indent=2, ensure_ascii=False))
            return 0

        if args.instructions:
            items = agent.parse_instructions_file(Path(args.instructions))
            if not items:
                raise Blocked(f'{args.instructions} holds no instruction blocks')
            after = agent.add_instructions(s.page, items, log=print)
            print(f'saved {len(items)} instruction(s); now stored: '
                  f'{[i["title"] for i in after]}')

        if (args.instructions_state or args.activate_instruction
                or args.deactivate_instruction):
            for needle in args.activate_instruction:
                agent.set_instruction_active(s.page, needle, True, log=print)
            for needle in args.deactivate_instruction:
                agent.set_instruction_active(s.page, needle, False, log=print)
            states = agent.read_instructions(s.page, log=print)
            print(json.dumps(states, indent=2, ensure_ascii=False))
            if not (args.list or args.instructions or args.prompt):
                return 0

        if args.prompt:
            prompt = args.prompt
            if args.generate and args.expect and not args.no_auto_name:
                # The agent HONOURS naming instructions (measured: it produced
                # Image4..Image10 exactly), so give every batch a unique prefix.
                # Fixed names ("Image1..Image10") collide with the previous
                # batch's tiles and the new-tile diff then under-counts - one
                # 10-image run saw only 7 "new" tiles for that reason.
                prefix = f'cli{time.strftime("%H%M%S")}_'
                prompt = prompt.rstrip() + (
                    f'\nName each generated image exactly {prefix}1 through '
                    f'{prefix}{args.expect} in order. Do NOT skip naming order.')
            read_credits = bool(args.generate and not args.expect
                                and not args.skip_credit_read)
            if args.generate:
                # A balance read is a real page interaction: it opens Flow's
                # account panel, and on 2026-11-06 a panel that refused to
                # close left a transparent backdrop intercepting every click,
                # so a 20-image batch never even submitted. An --expect batch
                # is an image batch (free), so it skips the read entirely.
                before = (_safe_credits(s.page) if read_credits else {})
                before_tiles = {t['name'] for t in pages.tiles(s.page)
                                if (t['name'] or '').strip()}
            refs = [str(r) for r in (args.ref or [])]
            for r in refs:
                if not Path(r).is_file():
                    raise Blocked(f'reference image not found: {r}')
            if refs:
                agent.attach_references(s.page, refs, log=print)
            before_count = None
            if args.generate and args.expect == 1:
                # count-based harvest (one submit = one tile, name collisions
                # impossible); the snapshot must include any ref-upload tile
                before_count = len(pages.tiles(s.page))
            got = agent.type_prompt(s.page, prompt, log=print)
            if len(got) < max(10, len(prompt) * 0.9):
                raise Blocked(f'the agent prompt did not take ({len(got)} of '
                              f'{len(prompt)} chars)')
            if not args.generate:
                agent.clear_prompt(s.page)
                print('dry run: prompt typed and read back, then cleared '
                      '(pass --generate to submit)')
            else:
                agent.submit(s.page, log=print)
                print('submitted to the agent; watching for a credit change...')
                if args.expect:
                    if args.expect == 1:
                        new = [assets.wait_one_new_tile(
                            s.page, before_count, timeout_s=args.wait,
                            log=print)]
                    else:
                        new = order_tiles_by_label(assets.wait_new_tiles(
                            s.page, before_tiles, args.expect,
                            timeout_s=args.wait, prompt=prompt, log=print))
                    out = Path(args.out) if args.out else (
                        RUNS / f'{time.strftime("%Y%m%d_%H%M%S")}_agent')
                    batch_stamp = time.strftime('%Y%m%d_%H%M%S')
                    entries = []
                    manifest = None
                    for i, t in enumerate(new, 1):
                        settled = assets.ensure_ready(s.page, t['name'], log=print)
                        target = f'agent_{batch_stamp}_{i:02d}'
                        # Try to rename the project tile, but download under OUR
                        # name either way: the manifest index must stay parseable
                        # even when a rename does not stick (a Flow title used to
                        # make the consumer skip the image entirely).
                        located = assets.rename(s.page, settled, target, log=print)
                        ext = 'png' if t['kind'] == 'image' else 'mp4'
                        dest = Path(out) / f'{target}.{ext}'
                        dl = assets.download_tile(s.page, located, dest,
                                                  kind=t['kind'], log=print)
                        entries.append({'name': target, 'file': dl['path'],
                                        'bytes': dl['bytes'], 'kind': t['kind'],
                                        'index': i, 'tile': settled})
                        # Write after EVERY tile: a crash mid-harvest leaves a
                        # truthful partial manifest the caller can salvage from.
                        manifest = assets.write_manifest(
                            Path(out), {'prompt': prompt, 'files': entries})
                    for e in entries:
                        print(f"   {e['name']}  {e['bytes']:,} bytes  "
                              f"-> {e['file']}")
                    print(f'   manifest: {manifest}')
                    if args.json:
                        print(json.dumps({'prompt': prompt, 'files': entries,
                                          'manifest': str(manifest)},
                                         indent=2, ensure_ascii=False))
                after = {}
                if read_credits:
                    s.page.wait_for_timeout(3000)
                    after = _safe_credits(s.page)
                delta = None
                if before.get('credits_num') is not None and \
                        after.get('credits_num') is not None:
                    delta = before['credits_num'] - after['credits_num']
                if read_credits:
                    print(f'credits: {before.get("credits")} -> '
                          f'{after.get("credits")} (spent {delta})')
                else:
                    print('credits read skipped (image batch; images are free)')
                if delta is not None and delta > args.max_credits:
                    print(f'! SPENT {delta} credits, over the --max-credits '
                          f'budget of {args.max_credits}')
                    return 1
    return 0


def cmd_config(args) -> int:
    _bind(args)
    project = _need_project(args)
    with Session() as s:
        uuid = pages.resolve_uuid(s.page, project)
        pages.open_project(s.page, uuid)
        s.page.wait_for_timeout(1500)
        pages.dismiss_modals(s.page, log=print)
        composer.set_agent(s.page, False, log=print)
        want = dict(mode=inputs.mode(args.mode),
                    aspect=inputs.aspect(args.aspect),
                    outputs=inputs.count(args.count) if args.count else None,
                    duration=inputs.duration(args.duration),
                    resolution=inputs.resolution(args.res),
                    model=inputs.model(args.model),
                    thinking=inputs.thinking(args.thinking),
                    submode=inputs.submode(args.submode))
        want = {k: v for k, v in want.items() if v}
        cfg = (genconfig.configure(s.page, log=print, **want)
               if want else None)
        if cfg is None:
            if not genconfig.open_popover(s.page, log=print):
                raise Blocked('could not open the config popover')
            cfg = genconfig.read(s.page)
        genconfig.close_popover(s.page)
    print(json.dumps({k: cfg.get(k) for k in
                      ('mode', 'submode', 'aspect', 'model', 'resolution',
                       'duration', 'outputs', 'thinking', 'cost_text',
                       'cost_credits')}, indent=2, ensure_ascii=False))
    if args.raw:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
    return 0


def cmd_schema(args) -> int:
    if args.json:
        print(json.dumps(schema.SCHEMA, indent=2, ensure_ascii=False))
    else:
        print(f"flow {schema.SCHEMA['version']} - {schema.SCHEMA['summary']}")
        for c in schema.SCHEMA['commands']:
            print(f"  {c['name']:<9} credits={str(c['credits']):<12} "
                  f"{c['purpose']}")
        print('\nexit codes: ' + ', '.join(
            f'{k}={v}' for k, v in schema.SCHEMA['exit_codes'].items()))
    return 0


def cmd_selftest(args) -> int:
    code = selftest_mod.run(verbose=not args.json)
    if args.json:
        print(json.dumps({'ok': code == 0, 'passed': selftest_mod.PASS,
                          'failed': selftest_mod.FAIL}, indent=2))
    return code


# ----------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='flow', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version=f'flow {__version__}')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('projects', help='list projects, or create one')
    _common(p); p.add_argument('--json', action='store_true')
    p.add_argument('--create', nargs='?', const='', default=None,
                   metavar='NAME', help='create a new project (optionally named)')
    p.set_defaults(func=cmd_projects)

    p = sub.add_parser('credits', help='read the credit balance (0 credits)')
    _common(p); p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_credits)

    p = sub.add_parser('probe', help='map the live UI (0 credits)')
    _common(p)
    p.add_argument('--project', default='', help='project uuid or exact title')
    p.add_argument('--agent', choices=['leave', 'on', 'off'], default='leave',
                   help='also dump the panel with Agent forced ON/OFF')
    p.add_argument('--json', action='store_true', help='also print the raw JSON')
    p.set_defaults(func=cmd_probe)

    def _gen(p, mode):
        _common(p)
        p.add_argument('-p', '--prompt', required=True,
                       help='what to make (one continuous shot reads best)')
        p.add_argument('--project', default='',
                       help='project uuid or exact title (or set FLOW_PROJECT)')
        p.add_argument('--out', default='',
                       help='output directory (default runs/<stamp>_<mode>_<slug>)')
        p.add_argument('--count', type=int, default=1,
                       help='outputs per click: only 1 is supported (Flow titles '
                            'duplicates identically); use `flow agent --expect N` '
                            'for batches')
        p.add_argument('--aspect', default=None,
                       help='9:16 | 16:9 | 1:1 | 4:3 | 3:4 (aliases: portrait, '
                            'landscape, square, 9x16)')
        p.add_argument('--model', default=None,
                       help='Omni 1.1 Flash (video) or Nano Banana 2.1 (images); '
                            'aliases omni / nano. Veo is refused by policy')
        p.add_argument('--ref', action='append', default=[],
                       help='reference image path to upload and attach (repeatable)')
        p.add_argument('--download', default=None,
                       help='2K | 1080p | 720p/Original | 4K | gif '
                            '(default: 2K for images, 1080p for video)')
        p.add_argument('--dry-run', action='store_true',
                       help='configure, price, frame and type - but do NOT click')
        p.add_argument('--wait', type=int, default=900,
                       help='render timeout in seconds (default 900)')
        p.add_argument('--json', action='store_true',
                       help='also print the machine-readable result')
        if mode == 'image':
            p.add_argument('--thinking', default=None,
                           help='Low | Medium | High | Extra thorough')
        else:
            p.add_argument('--duration', default=None,
                           help='4s | 6s | 8s | 10s (aliases: 4, 6, 8, 10)')
            p.add_argument('--res', default=None, help='360p | 720p')
            p.add_argument('--submode', default=None,
                           help='Ingredients (default, --ref) or Frames '
                                '(start/end slots)')
            p.add_argument('--start-frame', default=None,
                           help='frames mode: project image name for the first frame')
            p.add_argument('--end-frame', default=None,
                           help='frames mode: project image name for the last frame')
            p.add_argument('--max-credits', type=int, default=15,
                           help='refuse if the composer price preview exceeds this '
                                '(default 15)')
        p.set_defaults(func=cmd_image if mode == 'image' else cmd_video)

    p = sub.add_parser('image', help='generate image(s) - free, verified by the '
                                     'composer cost preview')
    _gen(p, 'image')

    p = sub.add_parser('video', help='generate video(s) - priced by the '
                                     'composer preview, budget-gated')
    _gen(p, 'video')

    p = sub.add_parser('config', help='read (or set) the generation config and '
                                      'its credit preview')
    _common(p)
    p.add_argument('--project', default='',
                   help='project uuid or exact title')
    p.add_argument('--mode', default=None, help='Image | Video')
    p.add_argument('--aspect', default=None, help='9:16 | 16:9 | 1:1 | 4:3 | 3:4')
    p.add_argument('--count', type=int, default=None,
                   help='outputs per click (1 only)')
    p.add_argument('--model', default=None,
                   help='Omni 1.1 Flash | Nano Banana 2.1 (Veo refused)')
    p.add_argument('--thinking', default=None,
                   help='Low | Medium | High | Extra thorough (image)')
    p.add_argument('--duration', default=None, help='4s | 6s | 8s | 10s')
    p.add_argument('--res', default=None, help='360p | 720p')
    p.add_argument('--submode', default=None, help='Ingredients | Frames')
    p.add_argument('--raw', action='store_true',
                   help='also print the raw popover dump')
    p.set_defaults(func=cmd_config)

    p = sub.add_parser('agent', help='Agent mode: guidelines, prompt, opt-in generation')
    _common(p)
    p.add_argument('--project', default='')
    p.add_argument('--list', action='store_true', help='list the stored guidelines')
    p.add_argument('--instructions', default='',
                   help='file of guideline blocks (title line + description)')
    p.add_argument('--instructions-state', action='store_true',
                   help='print stored guidelines with their active on/off switch')
    p.add_argument('--activate-instruction', action='append', default=[],
                   metavar='TEXT',
                   help='activate the stored guideline whose title+description '
                        'contains TEXT (repeatable)')
    p.add_argument('--deactivate-instruction', action='append', default=[],
                   metavar='TEXT',
                   help='deactivate the stored guideline matching TEXT (repeatable)')
    p.add_argument('-p', '--prompt', default='', help='agent prompt (dry run by default)')
    p.add_argument('--ref', action='append', default=[],
                   help='reference image to attach to the agent prompt, e.g. the '
                        'style lock (repeatable; uploaded as a project tile first)')
    p.add_argument('--generate', action='store_true',
                   help='submit the prompt to the agent (this can spend)')
    p.add_argument('--max-credits', type=int, default=15)
    p.add_argument('--expect', type=int, default=0,
                   help='harvest this many new tiles the agent creates')
    p.add_argument('--out', default='', help='output directory for --expect')
    p.add_argument('--wait', type=int, default=600, help='harvest timeout (s)')
    p.add_argument('--no-auto-name', action='store_true',
                   help='do not append a unique naming directive to the prompt')
    p.add_argument('--json', action='store_true')
    p.add_argument('--skip-credit-read', action='store_true',
                   help='do not read the balance (avoids the account panel)')
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser('schema', help='print the machine-readable command '
                                      'contract (JSON by default)')
    p.add_argument('--json', action='store_true', default=True)
    p.add_argument('--text', dest='json', action='store_false')
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser('selftest', help='offline checks of the pure logic '
                                        '(no Chrome, no credits)')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_selftest)
    return ap


def main(argv=None) -> int:
    console.ensure_utf8()
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Blocked as e:
        print(f'BLOCKED: {e}')
        return 1
    except RuntimeError as e:
        print(f'ERROR: {e}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
