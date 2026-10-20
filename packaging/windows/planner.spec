# PyInstaller spec for the Windows build of the Idaho Hunt Planner.
#
#   pyinstaller --clean --noconfirm packaging/windows/planner.spec
#
# Produces `dist/IdahoHuntPlanner/IdahoHuntPlanner.exe` plus its runtime directory.
# `packaging/windows/installer.iss` then packs that directory into a single
# `IdahoHuntPlanner-Setup-<version>.exe`.
#
# One-dir, not one-file, on purpose: a one-file build re-extracts ~80 MB into a temp
# directory on every launch (slow start, and a favourite of antivirus heuristics). The
# thing the user downloads is still a single file — the installer.
#
# The app finds its own data by walking up from `__file__`
# (`planner/app.py`: `REPO_ROOT = Path(__file__).resolve().parent.parent`), so the
# bundle has to reproduce the repo's shape around the frozen package: `static/` and
# `docs/` at the bundle root, `schema.sql` and `migrations/` inside `planner/`. The
# database is NOT bundled — `ihp_win.py` points it at %LOCALAPPDATA%.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

REPO_ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821 (SPECPATH is injected)

# (path relative to the repo root, destination inside the bundle). The test
# `tests/test_windows_packaging.py` reads this list and asserts every source exists.
DATA_FILES = [
    ("static", "static"),
    ("planner/schema.sql", "planner"),
    ("planner/migrations", "planner/migrations"),
    ("docs/seasons_template.csv", "docs"),
]

datas = [(str(REPO_ROOT / source), dest) for source, dest in DATA_FILES]
binaries = []
hiddenimports = []

# pdfplumber/pdfminer.six load CMap and encoding tables from package data at runtime,
# and pdfminer imports some codecs lazily — collect_all catches both.
for package in ("pdfplumber", "pdfminer", "pypdfium2_raw", "charset_normalizer"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:  # an optional dependency that isn't installed: skip it
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(  # noqa: F821
    [str(REPO_ROOT / "packaging" / "windows" / "ihp_win.py")],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.random._examples"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IdahoHuntPlanner",
    debug=False,
    strip=False,
    upx=False,
    # A console window: it is where the first-run regulation download reports its
    # progress, and where a crash is readable instead of silent.
    console=True,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="IdahoHuntPlanner",
)
