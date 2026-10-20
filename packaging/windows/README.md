# Windows packaging

One file for the user to download — `IdahoHuntPlanner-Setup-<version>.exe` — containing
the app, Python 3.12, Flask and pdfplumber. No Python install, no virtualenv, no command
line. It is the Windows counterpart of `scripts/setup.sh` + `Install Idaho Hunt
Planner.command` (T-30), which stay the macOS path.

| File | What it is |
|---|---|
| `ihp_win.py` | The program the exe runs: picks a writable data directory, starts the Flask app, waits for `/api/health`, fetches the IDFG brochures on a first run, opens the browser |
| `planner.spec` | PyInstaller recipe (one-dir build, `static/`, `schema.sql`, `migrations/` bundled) |
| `installer.iss` | Inno Setup script that packs that build into the single setup exe |
| `../../.github/workflows/windows-installer.yml` | Where both are actually built, smoke-tested and uploaded |

## Build it

The build must run on Windows. Normally you don't: push the branch and let the
**Windows installer** workflow build it, then download the `IdahoHuntPlanner-Setup`
artifact from the run.

To do it by hand on a Windows machine with Python 3.12 and
[Inno Setup 6](https://jrsoftware.org/isdl.php):

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm packaging\windows\planner.spec
iscc packaging\windows\installer.iss
```

`dist\IdahoHuntPlanner\IdahoHuntPlanner.exe` is the runnable app;
`dist\installer\IdahoHuntPlanner-Setup-<version>.exe` is what you hand to someone.

## Decisions worth knowing

- **One-dir build inside a one-file installer.** A PyInstaller one-file exe unpacks
  ~80 MB to a temp directory on every launch — slow, and a magnet for antivirus
  heuristics. The single file the user downloads is the installer.
- **The data directory is not the install directory.** `planner/app.py` keeps the DB in
  `data/` beside the code, which is wrong for an installed program, so `ihp_win.py`
  passes `create_app(db_path=…)` pointing at `%LOCALAPPDATA%\IdahoHuntPlanner`
  (`IHP_DIR` overrides). Uninstall leaves it — those are the user's tags and reviewed
  seasons.
- **The bundle mirrors the repo layout.** The app resolves `static/` by walking up from
  `planner/app.py`, so the spec puts `static/`, `docs/seasons_template.csv`,
  `planner/schema.sql` and `planner/migrations/` in the same shape around the frozen
  package. Add a new data file to the app and you must add it to `DATA_FILES` in
  `planner.spec` — `tests/test_windows_packaging.py` checks the paths exist, but it
  cannot know about a file nobody listed.
- **Per-user install.** `PrivilegesRequired=lowest`, so no UAC prompt and nothing is
  written outside the user profile.
- **Console window on purpose.** The first-run IDFG download takes minutes; the console
  is where it reports what it staged, and where a crash is readable instead of silent.
- **No code signing.** SmartScreen will warn on first run ("Windows protected your
  PC" → More info → Run anyway). Signing needs a purchased certificate.
