# Install (first time)

Two commands total, no terminal knowledge needed. The installer also checks that
you are signed in to Google Flow and shows your credit balance.

## Windows

1. Download/clone this folder.
2. Double-click **`install.bat`**.
3. Answer two things when asked:
   - **Gemini API key** — press the link it prints
     (https://aistudio.google.com/apikey → *Create API key*), copy it, paste it.
   - **Google sign-in** — a Chrome window opens; sign in once with the account
     that has Flow, leave it open, come back to the black window.
4. Pick your Flow project from the numbered list (the last question).

That is the whole setup. Later runs never ask again.

## Linux / macOS

```bash
chmod +x install.sh && ./install.sh
```

The same questions appear in the terminal; the Chrome window opens the same way.

## What the installer does

| step | what happens |
|---|---|
| 1 | finds Python 3.11+ (otherwise tells you where to get it) |
| 2 | creates `.venv` in this folder |
| 3 | installs the tool (bootstraps `pip` if the venv has none) |
| 4 | runs the guided setup: Gemini key → Google sign-in → credits → project |

Nothing is rendered and no credit is spent during setup.

## Check your state at any time

```powershell
.venv\Scripts\reelcut.exe status        # Windows
.venv/bin/reelcut status                # Linux/macOS
```

It prints, in plain language: Gemini key present? Chrome running? signed in as
whom? credit balance? which project? what is still missing.

## If something goes wrong

| what you see | what to do |
|---|---|
| `Python 3.11+ was not found` | install Python from python.org, tick *Add to PATH*, run the installer again |
| `pip is missing in .venv` | delete the `.venv` folder, run the installer again |
| `Dependency install failed` | check the internet, run the installer again |
| Chrome opens but Flow asks you to sign in | sign in once in that window, then run `reelcut setup` again |
| `Chrome did not open its debug port` | close every Chrome window, run `reelcut setup` again |
| `no prompt box on the project page` | the account is signed out **or** the project uuid is wrong — run `reelcut setup` |
| `GEMINI_API_KEY is not set` | run `reelcut setup` and paste the key, or add it to `.env` |

Every message is paired with an English hint, and the tool never prints a raw
traceback in these paths.

## Manual path (if you prefer)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m reelcut.firstrun
```

Flags: `--status` (report only), `--wait 300` (wait longer for the sign-in),
`--no-key-prompt`, `--no-input` (unattended).
