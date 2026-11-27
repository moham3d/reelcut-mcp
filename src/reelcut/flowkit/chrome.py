"""Attach to (or cold-start) the Chrome that is signed into Google Flow.

Forked from YT-Agent/yta/driver/chrome.py @ 2026-11-06.

DIVERGES:
* No `accounts.py`. One configurable profile/port/`u_index` (see config.py),
  because the CLI is a personal tool rather than the two-account factory.
* The render-account assertion is gone. It exists to protect a shared credit
  pool; here the operator IS the account, and `flow credits` prints what is
  left instead of refusing.
* Everything about the credit panel, the overlay snapshot and the
  never-click-off-Flow guard is carried over unchanged - those were written
  against live failures and are the parts worth keeping.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from playwright.sync_api import Browser, BrowserContext, Error as PWError
from playwright.sync_api import Page

from . import config
from . import selectors as S
from .errors import Blocked

_PROFILE: dict | None = None


def configure(profile: str | None = None, port: int | None = None,
              u_index: int | None = None, email: str = '') -> dict:
    """Bind this process to one Chrome profile. Call before anything else."""
    global _PROFILE
    _PROFILE = {
        'profile': profile or config.DEFAULT_PROFILE,
        'port': int(port if port is not None else config.DEFAULT_PORT),
        'u_index': int(u_index if u_index is not None else config.DEFAULT_U),
        'email': email,
    }
    return _PROFILE


def bound_profile() -> dict:
    if _PROFILE is None:
        raise RuntimeError('no profile is bound - call flowkit.chrome.configure() first')
    return _PROFILE


def flow_url(path: str = '') -> str:
    """A Flow URL addressed to the configured account (bare for index 0)."""
    n = bound_profile()['u_index']
    return f'{S.FLOW_HOME}u/{n}/{path}' if n else f'{S.FLOW_HOME}{path}'


# ------------------------------------------------------------------ attaching


def cdp_alive(port: int | None = None, timeout: float = 3.0) -> bool:
    p = port if port is not None else bound_profile()['port']
    try:
        with urllib.request.urlopen(config.cdp_url(p) + '/json/version',
                                    timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def ensure_chrome(wait_s: int = 45, log=print) -> bool:
    """Make sure this profile's Chrome is up with its debug port open.

    Two caveats worth knowing:
    * Chrome only honours --remote-debugging-port on a *cold* start. If the
      profile is already open in a window without the port, the launch is
      forwarded to the running instance and no port ever opens.
    * Two Chromes on one --user-data-dir corrupt the profile, so the CLI must
      not be run alongside YT-Agent/SlideAgent on the same profile (they take
      the same lock for exactly this reason).
    """
    cfg = bound_profile()
    port, profile = cfg['port'], cfg['profile']

    if cdp_alive(port):
        return True

    exe = config.chrome_exe()
    if not os.path.exists(exe):
        raise RuntimeError(f'Chrome not found at {exe!r}; set FLOW_CHROME_EXE')

    if os.path.exists(os.path.join(profile, 'SingletonLock')):
        raise RuntimeError(
            f'profile {profile} is open in another Chrome window that has no '
            f'debug port on {port}. Close it and retry (or start that window '
            f'with --remote-debugging-port={port}).')

    if os.path.isdir(profile) and not os.path.isdir(os.path.join(profile, 'Default')):
        log(f'  ! profile {profile} exists but has no Default/ - never signed in?')

    launch = [exe, f'--remote-debugging-port={port}',
              f'--user-data-dir={profile}',
              '--no-first-run', '--no-default-browser-check']
    env = dict(os.environ)
    if os.name != 'nt':
        env.setdefault('DISPLAY', ':99')
    kwargs: dict = {'close_fds': True, 'env': env}
    if os.name == 'nt':
        kwargs['creationflags'] = (
            getattr(subprocess, 'DETACHED_PROCESS', 0)
            | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    else:
        kwargs['start_new_session'] = True
    subprocess.Popen(launch, **kwargs)

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if cdp_alive(port):
            return True
        time.sleep(1.5)
    raise RuntimeError(f'Chrome did not open its debug port on {port} within '
                       f'{wait_s}s (profile {profile}).')


def attach(pw) -> tuple[Browser, BrowserContext]:
    """Attach over CDP to the running Chrome. Uses the REAL profile, so the
    Google session stays intact and no browser download is needed."""
    cfg = bound_profile()
    url = config.cdp_url(cfg['port'])
    browser = pw.chromium.connect_over_cdp(url)
    if not browser.contexts:
        raise RuntimeError(f'no browser context on {url}')
    if len(browser.contexts) > 1:
        flow_ctxs = [c for c in browser.contexts
                     if any('flow.google.com' in p.url for p in c.pages)]
        if len(flow_ctxs) != 1:
            raise RuntimeError(
                f'{len(browser.contexts)} browser contexts on {url}, '
                f'{len(flow_ctxs)} showing Flow - refusing to guess which to drive')
        return browser, flow_ctxs[0]
    return browser, browser.contexts[0]


def find_project_page(ctx: BrowserContext, uuid: str) -> Page:
    """An existing tab already on that project, else a new tab."""
    for p in ctx.pages:
        if f'/project/{uuid}' in p.url:
            return p
    return ctx.new_page()


def signed_in(page: Page) -> bool:
    return 'accounts.google.com' not in page.url


# ------------------------------------------------------------ identity signals


def account_signals(page: Page) -> dict:
    out = {'url': '', 'title': '', 'aria_labels': [], 'email': ''}
    try:
        out['url'] = page.url
        out['title'] = page.title()
    except Exception:  # noqa: BLE001
        pass
    try:
        labels = page.eval_on_selector_all(
            S.GOOGLE_ACCT_SEL,
            "els => els.map(e => e.getAttribute('aria-label') || '')")
        out['aria_labels'] = [x for x in labels if x][:10]
    except Exception:  # noqa: BLE001
        pass
    out['email'] = config.email_from_labels(out['aria_labels'])
    return out


def signed_in_as(page: Page) -> str:
    """The signed-in Google account, or '' when unreadable (unknown != wrong)."""
    return account_signals(page)['email']


# ------------------------------------------------------------ the credit panel

_NUM_RE = re.compile(r'\d[\d,]*')
_NUM_BEFORE_CREDITS_RE = re.compile(r'(\d[\d,]*)\D{0,40}?credits?\b', re.I)


def _as_int(text: str) -> int | None:
    try:
        return int((text or '').replace(',', ''))
    except (TypeError, ValueError):
        return None


def _parse_credits(text: str) -> str:
    """The balance out of the panel text, as Flow displays it."""
    m = _NUM_BEFORE_CREDITS_RE.search(text or '') or _NUM_RE.search(text or '')
    return m.group(1) if (m and m.re.groups) else (m.group(0) if m else '')


def _navigates_off_flow(href: str) -> bool:
    """Compare the HOST, never a substring: Google's sign-out link carries
    `continue=https://flow.google.com/...` in its query string."""
    if not href:
        return False
    try:
        host = urllib.parse.urlsplit(href).netloc.lower()
    except ValueError:
        return True
    if not host:
        return False
    return host != 'flow.google.com' and not host.endswith('.flow.google.com')


def overlay_snapshot(page: Page) -> str:
    """What is in the CDK overlay container right now (diagnostic, not a verdict)."""
    try:
        return page.evaluate(
            """() => {
                 const c = document.querySelector('.cdk-overlay-container');
                 if (!c) return '(no .cdk-overlay-container on this page)';
                 if (!c.children.length) return '(the overlay container is empty)';
                 return [...c.querySelectorAll('*')].slice(0, 12).map(e => {
                   const cls = (typeof e.className === 'string' ? e.className : '')
                     .trim().split(/\\s+/).filter(Boolean).slice(0, 2).join('.');
                   return e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
                          + (cls ? '.' + cls : '');
                 }).join('  |  ');
               }""")
    except PWError:
        return '(snapshot failed)'


def open_account_panel(page: Page, log=print, timeout_ms: int = 15_000) -> bool:
    """Click Flow's account button; idempotent; never follows a link off Flow."""
    try:
        if page.locator(S.ACCOUNT_PANEL_SEL).count():
            return True
        btn = page.locator(S.ACCOUNT_PANEL_BTN_SEL)
        n = btn.count()
        if not n:
            log('  no account button on this page - is Flow signed in?')
            return False
        el = btn.first
        for i in range(n):
            try:
                if btn.nth(i).is_visible():
                    el = btn.nth(i)
                    break
            except PWError:
                continue
        try:
            info = el.evaluate(
                "e => ({tag: e.tagName.toLowerCase(), "
                "href: e.getAttribute('href'), "
                "text: (e.innerText || '').trim().slice(0, 40)})")
        except PWError:
            info = {}
        if _navigates_off_flow(info.get('href') or ''):
            log('  refusing to click an element that navigates off Flow')
            return False
        before = page.url
        try:
            el.click(timeout=timeout_ms)
        except PWError as e:
            log(f'  clicking the account button failed: {type(e).__name__}')
            return False
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if page.locator(S.ACCOUNT_PANEL_SEL).count():
                return True
            page.wait_for_timeout(500)
        log(f'  "{S.ACCOUNT_PANEL_SEL}" never appeared; overlay holds: '
            f'{overlay_snapshot(page)}')
        if page.url != before:
            log(f'  the page NAVIGATED instead: {before[:60]} -> {page.url[:60]}')
        return False
    except PWError:
        return False


def close_account_panel(page: Page, log=print) -> bool:
    """Close it and CONFIRM it is gone - a stray backdrop swallows later clicks."""
    try:
        if not page.locator(S.ACCOUNT_PANEL_SEL).count():
            return True
        page.keyboard.press('Escape')
        page.wait_for_timeout(600)
        if page.locator(S.ACCOUNT_PANEL_SEL).count():
            bd = page.locator(S.BACKDROP_SEL)
            if bd.count():
                try:
                    bd.first.click(timeout=3000)
                except PWError:
                    pass
                page.wait_for_timeout(600)
        gone = page.locator(S.ACCOUNT_PANEL_SEL).count() == 0
        if not gone:
            log('  ! account panel did NOT close - reload before driving further')
        return gone
    except PWError:
        return False


def credits(page: Page, log=print) -> dict:
    """Read the credit balance: open, read, close.

    `closed` False means the panel is still open and the page is unsafe; the
    caller must stop rather than carry on.
    """
    out = {'opened': False, 'closed': False, 'credits_text': '',
           'panel_text': '', 'credits': '', 'credits_num': None}
    if not open_account_panel(page, log=log):
        out['closed'] = True
        return out
    out['opened'] = True
    try:
        # The panel mounts before its content fills in, so a single read can
        # catch an empty shell (measured: "Sign out..." with no balance). Poll
        # for a line that actually mentions credits.
        panel = page.locator(S.ACCOUNT_PANEL_SEL).first
        text = ''
        for _ in range(14):
            try:
                text = panel.inner_text() or ''
            except PWError:
                text = ''
            if re.search(r'credits?', text, re.I):
                break
            page.wait_for_timeout(500)
        out['panel_text'] = text[:800]
        region = page.locator(S.CREDITS_SEL)
        if region.count():
            out['credits_text'] = (region.first.inner_text() or '')[:300]
    except PWError:
        pass
    out['credits'] = _parse_credits(out['credits_text'] or out['panel_text'])
    out['credits_num'] = _as_int(out['credits'])
    out['closed'] = close_account_panel(page, log=log)
    if not out['closed']:
        try:
            page.keyboard.press('Escape')
            page.wait_for_timeout(400)
            out['closed'] = overlay_count(page) == 0
        except PWError:
            pass
    return out
