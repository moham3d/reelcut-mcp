"""Guided first run for non-technical creators.

    python -m reelcut.firstrun              # check, then walk through setup
    python -m reelcut.firstrun --status     # report only (no browser)
    reelcut setup                           # same as the first form

What it does, in order, with plain-language messages:
1. Gemini key present?  (needed for transcription and image B-rolls)
2. Flow browser session: signed in? if not, a Chrome window opens and waits
   for the creator to sign in; once in, it reads the account and the credits.
3. Picks the Flow project and stores it in `.env`.

It never renders and never spends credits. Every failure is translated into a
"what to do next" line instead of a traceback.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ENV = REPO / ".env"
LINKS = {
    "key": "https://aistudio.google.com/apikey",
    "flow": "https://flow.google.com",
    "chrome": "https://www.google.com/chrome/",
}
OK, WARN, BAD = "[ok]", "[!]", "[x]"


def _say(line: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print(line, flush=True)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _env_has(key: str) -> bool:
    if os.environ.get(key):
        return True
    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "=") and line.split("=", 1)[1].strip():
                return True
    return False


def _write_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.is_file() else []
    lines = [ln for ln in lines if not ln.startswith(key + "=")]
    lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _explain(exc: Exception) -> str:
    """Turn a library failure into something a creator can act on."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "playwright" in text or "no module named" in text:
        return ("مكتبة المتصفح غير مثبتة. شغّل install.bat مرة واحدة ثم أعد المحاولة.\n"
                "(browser library missing - run install.bat once, then retry)")
    if "chrome" in text or "debug port" in text or "cdp" in text:
        return ("متصفح Chrome لم يفتح. أغلق أي نافذة Chrome قديمة ثم أعد المحاولة.\n"
                f"(Chrome did not start - close old windows and retry. {LINKS['chrome']})")
    if "no prompt box" in text:
        return ("الصفحة فتحت لكن الحساب غير مسجّل أو المشروع غير موجود.\n"
                "سجّل الدخول من النافذة المفتوحة ثم شغّل: reelcut setup\n"
                "(not signed in, or the project uuid is wrong)")
    if "gemini_api_key" in text or "api key" in text:
        return (f"مفتاح Gemini مطلوب: {LINKS['key']}\n"
                "(set GEMINI_API_KEY, then run reelcut setup again)")
    return ("حدث خطأ غير متوقع. جرّب: reelcut setup --status\n"
            f"(unexpected error: {type(exc).__name__})")


def _credits_text(credits: Any) -> str:
    if isinstance(credits, dict):
        for key in ("credits", "credits_text", "credits_num"):
            value = credits.get(key)
            if value not in (None, "", 0):
                return str(value)
    if isinstance(credits, (int, float)) and credits:
        return str(credits)
    return "غير مقروء (افتح Flow واتأكد من الرصيد)"


# --------------------------------------------------------------------- status
def status() -> dict[str, Any]:
    """Offline-friendly snapshot: key presence, Flow config, last login marker."""
    marker = Path.home() / ".reelcut" / "flow_session.json"
    last = {}
    if marker.is_file():
        try:
            last = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            last = {}
    from . import broll_flow

    info = broll_flow.available()
    return {
        "gemini_key": _env_has("GEMINI_API_KEY"),
        "flow": info,
        "project": info.get("project") or "",
        "last_login": last.get("at", ""),
        "signed_in_as": last.get("signed_in_as", ""),
        "credits": last.get("credits", ""),
        "ready": bool(_env_has("GEMINI_API_KEY") and info.get("configured")),
    }


def print_status(data: dict[str, Any] | None = None) -> None:
    data = data or status()
    _say("")
    _say("  حالة الإعداد / setup status")
    _say("  " + "-" * 44)
    key_state = "موجود" if data["gemini_key"] else "ناقص"
    _say(f"  {OK if data['gemini_key'] else BAD} مفتاح Gemini ({key_state})")
    flow = data["flow"]
    if flow.get("chrome"):
        _say(f"  {OK} متصفح Chrome يعمل")
    else:
        _say(f"  {BAD} متصفح Chrome غير مفتوح")
    if data.get("signed_in_as"):
        _say(f"  {OK} حساب Flow: {data['signed_in_as']}")
    elif flow.get("configured"):
        _say(f"  {WARN} المشروع متحدد لكن لا يوجد تسجيل دخول محفوظ")
    else:
        _say(f"  {BAD} لا يوجد تسجيل دخول Flow")
    _say(f"  {'-' * 44}")
    _say(f"  الكريديت: {_credits_text(data.get('credits'))}")
    _say(f"  المشروع: {data.get('project') or 'غير متحدد'}")
    missing = []
    if not data["gemini_key"]:
        missing.append("مفتاح Gemini")
    if not data.get("signed_in_as"):
        missing.append("تسجيل الدخول إلى Flow")
    elif not flow.get("project"):
        missing.append("اختيار مشروع Flow")
    _say(f"  {'جاهز للعمل' if not missing else 'ناقص: ' + ' و '.join(missing)}")
    _say("")


# ---------------------------------------------------------------------- setup
def setup(wait_seconds: int = 180, ask_key: bool = True,
          interactive: bool = True) -> int:
    _say("")
    _say("  ============================================")
    _say("     ReelCut — الإعداد الأول / first setup")
    _say("  ============================================")

    # 1) Gemini key ---------------------------------------------------------
    if _env_has("GEMINI_API_KEY"):
        _say(f"  {OK} مفتاح Gemini موجود")
    elif interactive and ask_key:
        _say("")
        _say("  محتاجين مفتاح Gemini (مجاني للبدء) للنسخ والتوليد.")
        _say(f"  1) افتح: {LINKS['key']}")
        _say("  2) اضغط Create API key ثم انسخ المفتاح")
        key = _ask("  الصق المفتاح هنا ثم Enter (أو Enter للتخطي): ")
        if key:
            _write_env("GEMINI_API_KEY", key)
            _say(f"  {OK} تم حفظ المفتاح في .env")
        else:
            _say(f"  {WARN} تم التخطي — النسخ والتوليد بالصور لن يعملا حتى تضيفه")
    else:
        _say(f"  {WARN} مفتاح Gemini غير موجود ({LINKS['key']})")

    # 2) Flow session ------------------------------------------------------
    _say("")
    try:
        from .broll_flow import login
    except Exception as exc:  # noqa: BLE001
        _say(f"  {BAD} " + _explain(exc))
        return 3

    _say("  الخطوة التالية: تسجيل الدخول إلى Google Flow (مرة واحدة فقط).")
    if interactive:
        _say("  سيفتح Chrome الآن. سجّل الدخول بنفس حساب Google ثم اتركه مفتوحًا.")
    try:
        info = login(wait_seconds=wait_seconds if interactive else 0)
    except Exception as exc:  # noqa: BLE001
        _say(f"  {BAD} " + _explain(exc))
        return 3

    if info.get("status") == "sign_in_required":
        _say(f"  {WARN} لم يكتمل تسجيل الدخول بعد.")
        _say("     سجّل الدخول في نافذة Chrome المفتوحة، ثم شغّل: reelcut setup")
        return 3
    account = info.get("signed_in_as") or "(غير مقروء)"
    _say(f"  {OK} تم تسجيل الدخول: {account}")
    _say(f"  الكريديت: {_credits_text(info.get('credits'))}")

    # 3) Project -----------------------------------------------------------
    projects = info.get("projects") or []
    chosen = info.get("project") or ""
    if not chosen and projects:
        _say("")
        _say("  اختر مشروع Flow الذي سيتم التوليد داخله:")
        for i, p in enumerate(projects, 1):
            _say(f"    {i}) {p['title']}  ({p['uuid'][:8]}…)")
        if interactive:
            pick = _ask("  رقم المشروع (Enter للتخطي): ")
            if pick.isdigit() and 1 <= int(pick) <= len(projects):
                try:
                    from .broll_flow import _save_env_project

                    _save_env_project(projects[int(pick) - 1]["uuid"])
                    chosen = projects[int(pick) - 1]["uuid"]
                except Exception as exc:  # noqa: BLE001
                    _say(f"  {WARN} " + _explain(exc))
        if not chosen:
            _say(f"  {WARN} لم يتم اختيار مشروع — شغّل: reelcut flow-login --project <uuid>")

    # summary --------------------------------------------------------------
    _say("")
    _say("  ============================================")
    if chosen:
        _say(f"  {OK} الإعداد جاهز")
    else:
        _say(f"  {WARN} باقي اختيار مشروع Flow")
    _say("")
    _say("  أوامر للبدء:")
    _say("    reelcut status                       # فحص الحالة بأي وقت")
    _say("    reelcut transcribe-gemini <مجلد>      # نص + توقيت دقيق")
    _say("    reelcut brolls <مجلد>                 # عرض سعر الـB-rolls")
    _say("    reelcut brolls <مجلد> --confirm --provider gemini_image")
    _say("  ============================================")
    _say("")
    return 0 if chosen else 3


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--status" in args:
        print_status()
        return 0
    wait = 180
    if "--wait" in args:
        try:
            wait = int(args[args.index("--wait") + 1])
        except (IndexError, ValueError):
            wait = 180
    ask_key = "--no-key-prompt" not in args
    interactive = ("--no-input" not in args
                   and not os.environ.get("REELCUT_NO_INPUT"))
    try:
        return setup(wait_seconds=wait, ask_key=ask_key, interactive=interactive)
    except KeyboardInterrupt:
        _say("\n  تم الإلغاء. لا تغيير حصل.")
        return 130
    except Exception as exc:  # noqa: BLE001 - last line of defence
        _say(f"  {BAD} " + _explain(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


