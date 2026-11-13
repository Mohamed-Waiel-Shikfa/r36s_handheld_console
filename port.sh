#!/usr/bin/env bash
# port.sh: copy a cmu_graphics game onto the R36S SD card and add it to Ports.
#
#   ./port.sh GAME_FOLDER [--name "Display Name"] [--main main.py]
#             [--input auto|keyboard|joystick] [--card /path/to/EASYROMS] [--eject]
#
# GAME_FOLDER is the game's project root. The game appears in the console's
# Ports menu as "Display Name" (default: the folder name). Run setup.sh first.
#
# --input   keyboard: the gamepad types keys (controls.gptk, see README.md)
#           joystick: the gamepad acts as the CMU arcade box joystick (joystick.py games)
#           auto (default): joystick if the code imports joystick.py, else keyboard
# --eject   unmount and power off the card at the end (default: leave it mounted)
# Extra pip packages from requirements.txt are downloaded as aarch64 wheels into
# the game's _libs folder (needs uv or pip on this PC). Files matching patterns in
# the game's .portignore (rsync syntax, one per line) are not copied; files on the
# card matching the game's .portkeep (globs, e.g. saves/*.txt) are kept on re-port.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib.sh"

SRC="" NAME="" MAIN="" INPUT=auto CARD_ARG="" EJECT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME=$2; shift 2 ;;
    --main) MAIN=$2; shift 2 ;;
    --input) INPUT=$2; shift 2 ;;
    --card) CARD_ARG=$2; shift 2 ;;
    --eject) EJECT=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) [ -z "$SRC" ] || die "only one game folder at a time"; SRC=$1; shift ;;
  esac
done
[ -n "$SRC" ] || die "usage: ./port.sh GAME_FOLDER [--name NAME] (see --help)"
[ -d "$SRC" ] || die "$SRC is not a folder"
SRC=$(cd "$SRC" && pwd)
case "$INPUT" in auto|keyboard|joystick) ;; *) die "--input must be auto, keyboard or joystick" ;; esac

need python3 md5sum findmnt
find_card "$CARD_ARG"
[ -f "$CARD/ports/cmu_libs/launch.sh" ] || die "cmu_graphics isn't installed on this card yet: run ./setup.sh first"

# ---------------------------------------------------------------- what to run
if [ -z "$MAIN" ]; then
  if [ -f "$SRC/main.py" ]; then
    MAIN=main.py
  else
    mapfile -t cands < <(cd "$SRC" && grep -lE '^\s*(runApp|runAppWithScreens|cmu_graphics\.run)\(' -- *.py 2>/dev/null || true)
    [ "${#cands[@]}" = 1 ] || die "can't tell which file starts the game (${cands[*]:-none call runApp}): pass --main FILE"
    MAIN=${cands[0]}
  fi
fi
[ -f "$SRC/$MAIN" ] || die "$SRC/$MAIN not found"

[ -n "$NAME" ] || NAME=$(basename "$SRC" | sed -E 's/\.git$//; s/[_-]+/ /g')
NAME=$(printf '%s' "$NAME" | tr -d '/\\:*?"<>|')
[ -n "$NAME" ] || die "empty --name"
SLUG=$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')
[ -n "$SLUG" ] || SLUG=game
case "$SLUG" in cmu_libs|portmaster) SLUG="${SLUG}_game" ;; esac
DEST="$CARD/ports/$SLUG"

if [ "$INPUT" = auto ]; then
  # joystick.py is what turns gamepad events into onJoyPress & co., so key on importing it
  if grep -rqsE --include='*.py' '^\s*(import joystick|from joystick import)' "$SRC"; then
    INPUT=joystick
  else
    INPUT=keyboard
  fi
fi
say "porting '$NAME' ($MAIN, $INPUT controls) -> ports/$SLUG"

# ---------------------------------------------------------------- copy
EXCLUDES=(.git .github .venv venv env '*-venv' __pycache__ build dist node_modules .idea .vscode .DS_Store
          '*.exe' '*.pyc' '*.spec' '*.swp' log.txt cmu_graphics _libs .portignore .portkeep R36S_NOTES.md)
if [ -d "$SRC/cmu_graphics" ]; then
  warn "the game folder contains its own cmu_graphics: not copied, the card's sped-up version is used"
fi
# Files the game writes on the console (saves, high scores) listed in .portkeep
# (glob patterns relative to the game folder) survive re-porting.
KEEP=""
if [ -f "$SRC/.portkeep" ] && [ -d "$DEST" ]; then
  KEEP=$(mktemp -d)
  python3 -I - "$DEST" "$KEEP" "$SRC/.portkeep" <<'EOF'
import glob, os, shutil, sys
dest, keep, spec = sys.argv[1:]
for pat in (l.strip() for l in open(spec)):
    if not pat or pat.startswith('#'):
        continue
    for f in glob.glob(os.path.join(glob.escape(dest), pat), recursive=True):
        if os.path.isfile(f):
            out = os.path.join(keep, os.path.relpath(f, dest))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copyfile(f, out)
EOF
fi
# rsync updates in place (rewriting only what changed keeps writes to the card low);
# without rsync the folder is rebuilt from scratch.
command -v rsync >/dev/null || rm -rf "$DEST"
mkdir -p "$DEST"
if command -v rsync >/dev/null; then
  RSYNC_EX=()
  for e in "${EXCLUDES[@]}"; do RSYNC_EX+=("--exclude=$e"); done
  [ -f "$SRC/.portignore" ] && RSYNC_EX+=("--exclude-from=$SRC/.portignore")
  rsync -rt --checksum --delete --no-perms --no-owner --no-group "${RSYNC_EX[@]}" "$SRC/" "$DEST/"
else
  python3 -I - "$SRC" "$DEST" "${EXCLUDES[@]}" <<'EOF'
import fnmatch, os, shutil, sys
src, dest, *ex = sys.argv[1:]
pi = os.path.join(src, '.portignore')
if os.path.exists(pi):
    ex += [l.strip().strip('/') for l in open(pi) if l.strip() and not l.startswith('#')]
ign = lambda d, names: [n for n in names if any(fnmatch.fnmatch(n, p) for p in ex)]
shutil.copytree(src, dest, ignore=ign, dirs_exist_ok=True, copy_function=shutil.copyfile)
EOF
fi

if [ -n "$KEEP" ]; then
  n=$(find "$KEEP" -type f | wc -l)
  [ "$n" -gt 0 ] && cp -r "$KEEP/." "$DEST/" && say "kept $n save file(s) from the card (.portkeep)"
  rm -rf "$KEEP"
fi

# ---------------------------------------------------------------- extra pip packages
if [ -f "$SRC/requirements.txt" ]; then
  mapfile -t reqs < <(sed -E 's/#.*//; s/^\s+|\s+$//g' "$SRC/requirements.txt" | grep -v '^$' \
    | grep -viE '^(cmu[-_]graphics|pygame|pygame-ce|pillow|pycairo)([^a-z0-9_.-]|$)' || true)
  if [ "${#reqs[@]}" -gt 0 ]; then
    say "fetching aarch64 wheels for: ${reqs[*]}"
    WHL=$(mktemp -d)
    PIPARGS=(download --only-binary=:all: --python-version 3.13 --implementation cp --abi cp313 --abi abi3 --abi none
             --platform manylinux_2_36_aarch64 --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64
             --platform linux_aarch64 --platform any -d "$WHL")
    if command -v uvx >/dev/null; then PIP=(uvx pip)
    elif python3 -m pip --version >/dev/null 2>&1; then PIP=(python3 -m pip)
    else PIP=(); fi
    if [ "${#PIP[@]}" = 0 ]; then
      warn "no uv or pip on this PC: can't fetch ${reqs[*]}; the game may fail to import them"
    elif "${PIP[@]}" "${PIPARGS[@]}" "${reqs[@]}" >/dev/null 2>"$WHL/err.txt"; then
      mkdir -p "$DEST/_libs"
      python3 -I - "$WHL" "$DEST/_libs" <<'EOF'
import glob, os, sys, zipfile
src, dest = sys.argv[1:]
skip = ('pygame', 'PIL', 'pillow', 'cairo', 'cmu_graphics', 'numpy')  # console already has / can't use
for w in glob.glob(os.path.join(src, '*.whl')):
    name = os.path.basename(w).split('-')[0].lower()
    if name in [s.lower() for s in skip]:
        continue
    with zipfile.ZipFile(w) as z:
        z.extractall(dest)
    print('   +', os.path.basename(w))
EOF
    else
      warn "couldn't fetch aarch64 wheels for ${reqs[*]} (no prebuilt wheel?): $(tail -1 "$WHL/err.txt")"
    fi
    rm -rf "$WHL"
  fi
fi

# ---------------------------------------------------------------- controls + launcher
if [ "$INPUT" = keyboard ] && [ ! -f "$DEST/controls.gptk" ]; then
  # Mouse-driven games get the left stick as the mouse and A/B as its buttons.
  if grep -rqsE --include='*.py' 'def onMouse(Press|Release|Drag|Move)\(' "$SRC"; then
    cp "$CARD/ports/cmu_libs/controls-mouse.gptk" "$DEST/controls.gptk"
    say "mouse handlers found: left stick = mouse, A = left click, B = right click"
  else
    cp "$CARD/ports/cmu_libs/controls.gptk" "$DEST/controls.gptk"
  fi
fi
LAUNCHER="$CARD/ports/$NAME.sh"
cat > "$LAUNCHER" <<EOF
#!/bin/bash
# $NAME: cmu_graphics game, installed by r36s/port.sh on $(date +%Y-%m-%d)
GAME_DIR="$SLUG"
GAME_MAIN="$MAIN"
INPUT_MODE="$INPUT"   # keyboard | joystick (see r36s/README.md)
source "\$(dirname "\$0")/cmu_libs/launch.sh"
EOF
chmod +x "$LAUNCHER" 2>/dev/null || true

MAN=$(mktemp)
manifest "$CARD" "ports/$SLUG" "ports/$NAME.sh" > "$MAN"
verify_on_card "$MAN"
rm -f "$MAN"

say "'$NAME' is in Ports. Controls: $INPUT mode (README.md). Log: ports/$SLUG/log.txt"
[ "$EJECT" = 1 ] && eject_card
exit 0
