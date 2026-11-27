"""Offline checks - `flow selftest`. No Chrome, no network, no credits.

These pin the pure logic the paid paths depend on: the cost-preview parser (a
wrong number is a wrong budget), the prompt-vs-title filter (a stale name
waited ten minutes on a placeholder once), the file-extension sniffer (a JPEG
saved as .png misled every consumer), the instruction-file parser, and the
friendly-argument normaliser (an agent will type "9x16" and "10").
"""

from __future__ import annotations

import tempfile
from pathlib import Path

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, fn) -> None:
    try:
        ok = fn()
    except Exception as e:  # noqa: BLE001
        FAIL.append(f'{name}: raised {type(e).__name__}: {e}')
        return
    (PASS if ok else FAIL).append(name if ok else f'{name}: returned False')


def _cost() -> bool:
    from .genconfig import COST_RE
    a = COST_RE.search('... Generating will use 30 credits')
    b = COST_RE.search('Generating will use 0 credits')
    c = COST_RE.search('Generating will use 1,200 credits')
    return (a and int(a.group(1)) == 30 and b and int(b.group(1)) == 0
            and c and int(c.group(1).replace(',', '')) == 1200)


def _prompt_filter() -> bool:
    from .assets import _looks_like_prompt
    p = 'A weathered brass compass on an old map, candlelight, photorealistic'
    return (_looks_like_prompt(p, p)
            and not _looks_like_prompt('Old brass compass on map', p)
            and not _looks_like_prompt('', p))


def _agent_order() -> bool:
    """The batch harvest must sort by the agent's label, not by grid position.

    DOM order is not prompt order (measured: a 20-image batch came back
    2->8, 3->13, 8->17, 19->12), so the label index is the only ordered handle.
    A partial or non-permutation label set must fall back to the stable order.
    """
    from .cli import label_index, order_tiles_by_label
    tiles = [{'name': n} for n in
             ('cli120000_3', 'cli120000_1', 'cli120000_2')]
    ordered = [t['name'] for t in order_tiles_by_label(tiles)]
    ok = ordered == ['cli120000_1', 'cli120000_2', 'cli120000_3']
    mixed = [{'name': 'Old brass compass'}, {'name': 'cli120000_1'}]
    ok = ok and order_tiles_by_label(mixed) == mixed          # no half-sort
    gaps = [{'name': 'cli120000_4'}, {'name': 'cli120000_5'}]
    ok = ok and order_tiles_by_label(gaps) == gaps            # not 1..N
    return ok and label_index('agent_1') is None and label_index('cli1') is None


def _manifest_atomic() -> bool:
    """The manifest is rewritten after every tile; a kill must not truncate it."""
    import json as _json
    from .assets import write_manifest
    with tempfile.TemporaryDirectory() as d:
        first = write_manifest(Path(d), {'files': [{'name': 'a'}]})
        second = write_manifest(Path(d), {'files': [{'name': 'a'},
                                                   {'name': 'b'}]})
        data = _json.loads(second.read_text(encoding='utf-8'))
        return (first == second and len(data['files']) == 2
                and not (Path(d) / 'manifest.json.tmp').exists())


def _extensions() -> bool:
    from .assets import _true_extension
    with tempfile.TemporaryDirectory() as d:
        jpg = Path(d) / 'x.png'
        jpg.write_bytes(b'\xff\xd8\xff\xe0' + b'0' * 64)
        png = Path(d) / 'y.jpg'
        png.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
        return (_true_extension(jpg).suffix == '.jpg'
                and _true_extension(png).suffix == '.png')


def _instructions() -> bool:
    from .agent import parse_instructions_file
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 'i.txt'
        f.write_text('First rule\nDo the thing.\n\nSecond rule\nAnd this.\n',
                     encoding='utf-8')
        items = parse_instructions_file(f)
        return (len(items) == 2 and items[0]['title'] == 'First rule'
                and items[0]['description'] == 'Do the thing.'
                and items[1]['title'] == 'Second rule')


def _args() -> bool:
    from . import inputs
    from .errors import Blocked
    ok = (inputs.aspect('9x16') == '9:16' and inputs.aspect('portrait') == '9:16'
          and inputs.duration('10') == '10s' and inputs.duration('15s') == '15s'
          and inputs.resolution('720') == '720p'
          and inputs.model('nano') == 'Nano Banana 2.1'
          and inputs.model('omni') == 'Omni 1.1 Flash'
          and inputs.model('omni 1.2') == 'Omni 1.2 Flash'
          and inputs.thinking('extra') == 'Extra thorough'
          and inputs.mode('video') == 'Video'
          and inputs.submode('references') == 'Ingredients'
          and inputs.count(1) == 1)
    try:
        inputs.duration('2s')   # below Omni 1.2's slider minimum
        ok = False
    except Blocked:
        pass
    try:
        inputs.duration('16s')  # beyond the slider maximum
        ok = False
    except Blocked:
        pass
    try:
        inputs.count(2)       # duplicate-titled outputs must be refused
        ok = False
    except Blocked:
        pass
    try:
        inputs.model('veo-3.1-fast')
        ok = False            # must raise: Veo is disabled by policy
    except Blocked:
        pass
    try:
        inputs.aspect('17:9')
        ok = False            # must raise: unknown aspect
    except Blocked:
        pass
    return ok


def _selectors() -> bool:
    from . import selectors as S
    needed = ['FLOW_HOME', 'TILE_SEL', 'PROMPT_SEL', 'GEN_BTN_SEL',
              'SETTINGS_BTN_CANDIDATES', 'AGENT_CHIP_SEL', 'VIDEO_AGENT_BTN',
              'ADD_MEDIA_BTN', 'INGREDIENT_BAR', 'RENAME_INPUT_SEL']
    if not all(hasattr(S, n) for n in needed):
        return False
    blob = ' '.join(str(getattr(S, n)) for n in needed
                    if isinstance(getattr(S, n), str))
    return '_ngcontent' not in blob and 'cdk-overlay-' not in blob.replace(
        'SETTINGS', '')


def _schema_matches_parser() -> bool:
    """Every command the schema advertises must exist in the real parser."""
    from .schema import SCHEMA
    from .cli import build_parser
    parser = build_parser()
    subs = set()
    for action in parser._actions:
        if hasattr(action, 'choices') and isinstance(action.choices, dict):
            subs |= set(action.choices)
    advertised = {c['name'] for c in SCHEMA['commands']}
    return advertised == subs


def run(verbose: bool = True) -> int:
    FAIL.clear()
    PASS.clear()
    check('cost-preview parser', _cost)
    check('prompt vs title filter', _prompt_filter)
    check('agent batch label ordering', _agent_order)
    check('atomic manifest rewrite', _manifest_atomic)
    check('true file extension', _extensions)
    check('instruction file parser', _instructions)
    check('friendly argument normaliser', _args)
    check('selector registry sanity', _selectors)
    check('schema matches parser', _schema_matches_parser)
    if verbose:
        for p in PASS:
            print(f'  ok   {p}')
        for f in FAIL:
            print(f'  FAIL {f}')
        print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 0 if not FAIL else 1
