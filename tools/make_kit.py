#!/usr/bin/env python3
"""Make the release kit: everything needed to run the host against a Civ V install, nothing needed to build it.

  python tools/make_kit.py [--exe BUILD] [--out dist] [--python VERSION|ZIP|none] [--worktree] [--smoke]

The kit is dist/civ5-headless-<version>-<commit>.zip with one top folder:

  bin/                    civ5headless.exe and its .pdb (from a tools/build_host.py build), build-info.json
  python/civ5headless/    the c5h package
  profiles/, probes/      the host's UI emulation profiles and Lua probes
  examples/               game specs, queues and example probes
  docs/                   the user guide, verification, profiles, engine contract, database builder, DLL additions,
                          saved games, probes
  runtime/                a private Python (python.org's embeddable package), so users need not install one
  c5h.cmd                 the command line (finds runtime\\python.exe, else Python on PATH or the py launcher)
  kit.json                version, commit, hashes; its presence makes config treat the folder as a kit (workspace,
                          bin\\civ5headless.exe, profiles\\, probes\\)
  README.md, QUICKSTART.txt, LICENSE, SOURCE.txt, licenses/SDK-NOTICE.md

Running needs no compiler: the exe uses the Visual C++ 2008 runtime that Civ V installs (its own DLLs need the same
runtime) and loads the Firaxis DLLs from the user's game folder. The c5h tools run on the bundled Python.

The files come from git (git archive HEAD), so the kit is exactly a commit plus an exe built from it: the build must be
successful, from clean sources, and built at HEAD or at a commit whose host sources (HOST_SOURCES) HEAD still has
unchanged. --worktree takes the working tree instead and allows a dirty or older
build, for testing only (the kit is then marked dirty).

--python: the Python bundled in runtime/. By default the pinned PYTHON_EMBED (the version the tests run on), downloaded
once from python.org into build/python-embed/ and checked against its pinned SHA-256; VERSION downloads another
version's 64-bit embeddable package (its hash is printed and recorded, not checked); ZIP takes a local package; none
bundles nothing (users then need Python 3.11 or newer). Every .exe, .dll and .pyd in the package must carry a valid
Authenticode signature (Python Software Foundation, or Microsoft for the C runtime). Its ._pth file gets ..\\python
added, since the embeddable Python ignores PYTHONPATH.

--smoke unpacks the zip into a temporary folder and runs c5h doctor there: the kit's exe, profiles and probes must be
found, and the bundled Python must be the one that ran.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXE = 'civ5headless.exe'
# The bundled Python: python.org's Windows embeddable package (64-bit), the version the tests run on.
PYTHON_EMBED = {'version': '3.12.10', 'sha256': '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3'}
PYTHON_URL = 'https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip'
SIGNERS = ('CN=Python Software Foundation', 'CN=Microsoft')
# What the kit takes from the repository (files or folders).
KIT_PATHS = ['python/civ5headless', 'profiles', 'probes', 'examples', 'c5h.cmd', 'civ5headless.example.toml',
             'README.md', 'CHANGELOG.md', 'LICENSE', 'docs/running.md', 'docs/user-guide.md', 'docs/verification.md', 'docs/profiles.md',
             'docs/engine-contract.md', 'docs/database-builder.md', 'docs/dll-additions.md', 'docs/saves.md',
             'docs/probes.md', 'docs/ai-logging.md']
RENAMED = {'sdk/README.md': 'licenses/SDK-NOTICE.md'}
# What civ5headless.exe is built from (tools/build_host.py).
HOST_SOURCES = ['host', 'sdk', 'tools/build_host.py', 'tools/toolchain.py']
REPOSITORY_URL = 'https://github.com/ruskyandrei/civ5-headless'

QUICKSTART = """civ5-headless {version}

Runs Civilization V (Brave New World) AI autoplay games without the game, with your DLL and mods.

Needs: Civilization V with Brave New World, started at least once (it writes its caches then); {python}.
Not needed: a compiler. civ5headless.exe uses the Visual C++ 2008 runtime that Civ V installs.

  c5h doctor                                    what was found (game, My Games, mods, DLL, host) and what is missing
  c5h run examples\\specs\\continents-tiny.json --turns 30
                                                a short game with your active mods and DLL; the run goes to runs\\
  c5h verify --host-only                        the host-only part of the verification suite (about 5 minutes)
  c5h verify                                    host against the real game (needs the game and FireTuner on)

A full game: Continental Drift, Standard size, Epic speed, Immortal, on a new random map each time. Epic's last
turn is 750; the game stops at the first victory before that (a Vox Populi test game ended in a cultural victory
at turn 404 after 31 minutes). Progress is printed every 10 turns; open runs\\continental-drift-<date>-<time>\\report.html
at the end:

  c5h run examples\\specs\\continental-drift.json --turns 750

Specs come in two kinds: NAME.json draws new seeds for every run (a new map and game each time; the seeds are kept
in the run folder's spec-resolved.json), NAME-fixed-seed.json replays the same map and game every time.

Run c5h.cmd from this folder (or put the folder on PATH). Settings go in civ5headless.toml next to it; see
civ5headless.example.toml. docs\\running.md shows every option with examples, docs\\user-guide.md explains how it
works, docs\\verification.md covers the verification suite, and docs\\probes.md shows how to get your own data out
of a game with Lua (examples in examples\\probes).
"""

SOURCE = """civ5-headless is free software under the GNU General Public License v3 (LICENSE).

This kit was made from commit {commit} of the civ5-headless repository:
  {url}/tree/{commit}
The source code of civ5headless.exe is the host/ and sdk/ folders at that commit; tools/build_host.py builds it.
"""


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            h.update(part)
    return h.hexdigest().upper()


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def find_build(arg):
    """The build folder (with civ5headless.exe and build-result.json) from --exe or out/latest.txt."""
    if arg:
        p = Path(arg).resolve()
        build = p.parent if p.is_file() else p
    else:
        marker = ROOT / 'out/latest.txt'
        if not marker.is_file():
            raise SystemExit('no out/latest.txt: build the host first (python tools/build_host.py) or pass --exe')
        build = Path(marker.read_text(encoding='utf-8').strip())
    if not (build / EXE).is_file():
        raise SystemExit(f'no {EXE} in {build}')
    result = build / 'build-result.json'
    info = json.loads(result.read_text(encoding='utf-8')) if result.is_file() else {}
    if info.get('status') != 'success':
        raise SystemExit(f'{result}: not a successful tools/build_host.py build')
    return build, info


def stage_from_git(stage, commit):
    """Extract the kit's paths at COMMIT into stage (git archive applies .gitattributes line endings)."""
    sources = KIT_PATHS + list(RENAMED)
    data = subprocess.run(['git', 'archive', '--format=tar', commit, '--', *sources], cwd=ROOT,
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(stage, filter='data')
    for src, dst in RENAMED.items():
        (stage / dst).parent.mkdir(parents=True, exist_ok=True)
        (stage / src).replace(stage / dst)
    shutil.rmtree(stage / 'sdk', ignore_errors=True)


def stage_from_worktree(stage):
    for rel in KIT_PATHS:
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, stage / rel, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, stage / rel)
    for src, dst in RENAMED.items():
        (stage / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / src, stage / dst)


def python_package(choice):
    """(zip path, version, pinned) for --python: the pinned version, another VERSION (downloaded), or a local ZIP."""
    if Path(choice).suffix.lower() == '.zip':
        p = Path(choice).resolve()
        if not p.is_file():
            raise SystemExit(f'--python: {p} does not exist')
        m = re.search(r'python-(\d+\.\d+\.\d+)', p.name)
        return p, m.group(1) if m else '?', False
    if not re.fullmatch(r'3\.\d+\.\d+', choice):
        raise SystemExit(f'--python: {choice!r} is not a version (3.x.y), a .zip or none')
    url = PYTHON_URL.format(v=choice)
    cached = ROOT / 'build/python-embed' / url.rsplit('/', 1)[1]
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        print(f'downloading {url}', flush=True)
        part = cached.with_suffix('.part')
        with urllib.request.urlopen(url, timeout=120) as r, part.open('wb') as f:
            shutil.copyfileobj(r, f)
        part.replace(cached)
    pinned = choice == PYTHON_EMBED['version']
    if pinned and sha256(cached) != PYTHON_EMBED['sha256']:
        raise SystemExit(f'{cached}: SHA-256 {sha256(cached)} is not the pinned {PYTHON_EMBED["sha256"]}; delete it '
                         'to download again')
    return cached, choice, pinned


def check_signatures(folder):
    """Every .exe, .dll and .pyd must carry a valid Authenticode signature by one of SIGNERS (PowerShell)."""
    script = ("Get-ChildItem -LiteralPath $env:C5H_KIT_RUNTIME -Recurse -Include *.exe,*.dll,*.pyd | ForEach-Object { "
              "$s = Get-AuthenticodeSignature -LiteralPath $_.FullName; "
              "'{0}|{1}|{2}' -f $_.Name, $s.Status, $(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' }) }")
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        raise SystemExit('PowerShell is needed to check the bundled Python\'s signatures')
    proc = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True,
                          env=dict(os.environ, C5H_KIT_RUNTIME=str(folder)))
    rows = [line.split('|', 2) for line in proc.stdout.splitlines() if line.count('|') >= 2]
    if proc.returncode or not rows:
        raise SystemExit(f'signature check failed: {proc.stderr.strip()[-500:]}')
    bad = [f'{name} ({status}, {signer or "unsigned"})' for name, status, signer in rows
           if status != 'Valid' or not signer.startswith(SIGNERS)]
    if bad:
        raise SystemExit('the Python package has files without a valid PSF or Microsoft signature: ' + ', '.join(bad))
    return len(rows)


def add_runtime(stage, choice):
    """Unpack the embeddable Python into runtime/ and let it find ..\\python. Returns kit.json's python entry."""
    package, version, pinned = python_package(choice)
    runtime = stage / 'runtime'
    with zipfile.ZipFile(package) as z:
        z.extractall(runtime)
    pth = sorted(runtime.glob('python3*._pth'))
    m = re.fullmatch(r'python3(\d+)\._pth', pth[0].name) if pth else None
    if not m or int(m.group(1)) < 11:
        raise SystemExit(f'{package}: not an embeddable Python 3.11 or newer (no python311._pth or later)')
    lines = pth[0].read_text(encoding='utf-8').splitlines()
    pth[0].write_text('\n'.join(lines + ['..\\python']) + '\n', encoding='utf-8')
    for name in ('_sqlite3.pyd', 'sqlite3.dll', 'python.exe'):
        if not (runtime / name).is_file():
            raise SystemExit(f'{package}: {name} is missing; c5h needs it')
    signed = check_signatures(runtime)
    probe = subprocess.run([str(runtime / 'python.exe'), '-c', 'import sys, sqlite3, tomllib, ctypes, winreg; '
                            'import civ5headless.cli; print(sys.version.split()[0])'],
                           capture_output=True, text=True, cwd=stage)
    if probe.returncode:
        raise SystemExit(f'the bundled Python cannot import the c5h package: {probe.stderr.strip()[-800:]}')
    return {'version': probe.stdout.strip(), 'package': package.name, 'sha256': sha256(package), 'pinned': pinned,
            'signedFiles': signed}


def make_zip(stage, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage.rglob('*')):
            if p.is_file():
                z.write(p, Path(stage.name) / p.relative_to(stage))


def smoke(zip_path, bundled):
    """Unpack into a temporary folder and run c5h doctor --json there; the kit's own files (and Python) must be used."""
    with tempfile.TemporaryDirectory(prefix='c5h-kit-') as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        kit = next(Path(tmp).iterdir())
        env = {k: v for k, v in os.environ.items() if k not in ('C5H_CONFIG', 'PYTHONPATH')}
        proc = subprocess.run(['cmd', '/c', str(kit / 'c5h.cmd'), 'doctor', '--json'], cwd=kit, env=env,
                              capture_output=True, text=True, errors='replace')
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise SystemExit(f'smoke: c5h doctor did not print JSON (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n'
                             f'{proc.stderr[-2000:]}')
        values = report.get('config', report).get('values', {})
        problems = []
        for key, want in (('workspace', kit), ('exe', kit / 'bin' / EXE), ('profiles', kit / 'profiles'),
                          ('probes', kit / 'probes')):
            got = (values.get(key) or {}).get('value')
            if not got or os.path.normcase(str(Path(got))) != os.path.normcase(str(want)):
                problems.append(f'{key}: {got} (expected {want})')
        if bundled:
            detail = next((c.get('detail', '') for c in report.get('checks', []) if c.get('what') == 'Python'), '')
            if os.path.normcase(str(kit / 'runtime')) not in os.path.normcase(detail):
                problems.append(f'Python: not the bundled runtime ({detail[:200]})')
        print(f'smoke: c5h doctor exit {proc.returncode} in {kit.name}' + ('' if not problems else ': ' + '; '.join(problems)))
        return not problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', help='build folder or exe (default: out/latest.txt)')
    ap.add_argument('--out', default=str(ROOT / 'dist'), help='output folder (default: dist/)')
    ap.add_argument('--python', default=PYTHON_EMBED['version'], metavar='VERSION|ZIP|none',
                    help=f"Python bundled in runtime/ (default: {PYTHON_EMBED['version']}, pinned; none: no runtime)")
    ap.add_argument('--worktree', action='store_true', help='take files from the working tree; allow a dirty or older build')
    ap.add_argument('--smoke', action='store_true', help='unpack the kit into a temporary folder and run c5h doctor there')
    args = ap.parse_args()

    build, info = find_build(args.exe)
    head = git('rev-parse', 'HEAD')
    dirty_paths = git('status', '--porcelain', '--', *KIT_PATHS, *HOST_SOURCES).splitlines()
    problems = []
    built_at = info.get('git_head') or ''
    if built_at != head:
        # An exe built at an earlier commit is still HEAD's when nothing it is built from changed since.
        same = subprocess.run(['git', 'diff', '--quiet', built_at, head, '--', *HOST_SOURCES], cwd=ROOT).returncode == 0 \
            if re.fullmatch(r'[0-9a-f]{40}', built_at) else False
        if not same:
            problems.append(f"the exe was built at {built_at[:10] or '?'} and the host sources changed since (HEAD {head[:10]})")
    if info.get('git_dirty_paths'):
        problems.append('the exe was built from uncommitted host sources')
    if dirty_paths and args.worktree:
        problems.append('the working tree has uncommitted kit files')
    if problems and not args.worktree:
        raise SystemExit('not a clean kit: ' + '; '.join(problems) + ' (commit, rebuild, or --worktree for a test kit)')
    dirty = bool(problems)

    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    name = f'civ5-headless-{version}-{head[:9]}' + ('-dirty' if dirty else '')
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stage = out / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    if args.worktree:
        stage_from_worktree(stage)
    else:
        stage_from_git(stage, head)

    (stage / 'bin').mkdir()
    shutil.copy2(build / EXE, stage / 'bin' / EXE)
    pdb = build / 'civ5headless.pdb'
    if pdb.is_file():
        shutil.copy2(pdb, stage / 'bin' / pdb.name)
    plan = info.get('plan', {})
    compiler = (plan.get('toolchain') or {}).get('compiler') or plan.get('compiler', '')
    build_info = {'gitHead': info.get('git_head'), 'dirtySources': bool(info.get('git_dirty_paths')),
                  'config': plan.get('config'), 'built': info.get('finished_utc'),
                  'compiler': next((l for l in compiler.splitlines() if 'Version' in l), compiler.strip()),
                  'exeSha256': sha256(stage / 'bin' / EXE)}
    (stage / 'bin/build-info.json').write_text(json.dumps(build_info, indent=1) + '\n', encoding='utf-8')

    runtime = None if args.python.lower() == 'none' else add_runtime(stage, args.python)
    python = f'nothing else (Python {runtime["version"]} is bundled in runtime\\)' if runtime else \
        'Python 3.11 or newer (python.org; c5h.cmd finds python on PATH or the py launcher)'
    (stage / 'QUICKSTART.txt').write_text(QUICKSTART.format(version=f'{version} ({head[:9]})', python=python),
                                          encoding='utf-8')
    (stage / 'SOURCE.txt').write_text(SOURCE.format(commit=head, url=REPOSITORY_URL), encoding='utf-8')
    kit = {'name': name, 'version': version, 'commit': head, 'dirty': dirty, 'problems': problems,
           'made': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
           'exe': build_info, 'python': runtime or '>=3.11', 'pythonBundled': bool(runtime)}
    (stage / 'kit.json').write_text(json.dumps(kit, indent=1) + '\n', encoding='utf-8')

    zip_path = out / f'{name}.zip'
    make_zip(stage, zip_path)
    shutil.rmtree(stage)
    print(json.dumps({'zip': str(zip_path), 'bytes': zip_path.stat().st_size, 'sha256': sha256(zip_path),
                      'dirty': dirty, 'problems': problems, 'python': kit['python']}, indent=1))
    if args.smoke and not smoke(zip_path, bool(runtime)):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
