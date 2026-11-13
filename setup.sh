#!/usr/bin/env bash
# setup.sh: install cmu_graphics on an R36S SD card (dArkOS + PortMaster), from a Linux PC.
#
# Puts pygame + pycairo (Debian 13 arm64 builds), cmu_graphics 1.1.44, the speed
# patches (fastpath.py) and the shared game launcher into EASYROMS/ports/cmu_libs.
# Nothing on the console's system partition is touched. Safe to re-run.
#
#   ./setup.sh [--card /path/to/EASYROMS] [--demos] [--eject]
#
#   --demos     also install the two benchmark games (CMU FPS Demo, CMU Swarm)
#   --eject     unmount and power off the card at the end (default: leave it mounted)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib.sh"

CARD_ARG="" DEMOS=0 EJECT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --card) CARD_ARG=$2; shift 2 ;;
    --demos) DEMOS=1; shift ;;
    --eject) EJECT=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done

need curl python3 sha256sum md5sum findmnt
find_card "$CARD_ARG"
say "SD card: $CARD"

# The console's Python must match the pygame/pycairo builds (CPython 3.13, aarch64).
ROOTFS_DEV=$(dev_by_label ROOTFS)
ROOTFS=""
[ -n "$ROOTFS_DEV" ] && ROOTFS=$(findmnt -rn -o TARGET -S "$ROOTFS_DEV" 2>/dev/null | head -1 || true)
if [ -n "$ROOTFS" ]; then
  [ -x "$ROOTFS/usr/bin/python3.13" ] || die "console Python isn't 3.13 ($ROOTFS/usr/bin/python3.13 missing): these builds won't load"
  for lib in libSDL2-2.0.so.0 libcairo.so.2 libSDL2_ttf-2.0.so.0 libSDL2_image-2.0.so.0 libSDL2_mixer-2.0.so.0; do
    # -L too: these are symlinks to absolute paths that only resolve on the console
    [ -e "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || [ -L "$ROOTFS/usr/lib/aarch64-linux-gnu/$lib" ] || warn "console is missing $lib; pygame may not load"
  done
else
  warn "ROOTFS partition not mounted: skipping the Python 3.13 / SDL2 compatibility check"
fi

# ---------------------------------------------------------------- downloads (pinned)
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/r36s-cmu"
mkdir -p "$CACHE"
PYGAME_DEB=python3-pygame_2.6.1-1+b2_arm64.deb
CAIRO_DEB=python3-cairo_1.27.0-2_arm64.deb
CMU_WHL=cmu_graphics-1.1.44-py3-none-any.whl
say "downloading pygame 2.6.1, pycairo 1.27.0 (Debian arm64) and cmu_graphics 1.1.44"
fetch "https://deb.debian.org/debian/pool/main/p/pygame/$PYGAME_DEB" \
  753af87964217b82c3d185b5712096c623140ac8957c47fc9910d669a9f7fb3e "$CACHE/$PYGAME_DEB" \
  https://snapshot.debian.org/file/0bb92d71b4806a618104db6de8e25c8f10ae30d4
fetch "https://deb.debian.org/debian/pool/main/p/pycairo/$CAIRO_DEB" \
  bbcec3d07448914ea81df1d2c74b25a54e3707f0452a3326f93a47a0ed7e05a5 "$CACHE/$CAIRO_DEB" \
  https://snapshot.debian.org/file/3b6258ba50f6d0a7465c739fd62c326146f53c88
fetch "https://files.pythonhosted.org/packages/63/bc/843f49fa7325b49e2f16fc73a59fbd3035b5cf16dc3b122ad98ef1803584/$CMU_WHL" \
  53e089a1a3a6da6b9a6cff7eab3ef3cb418ce6379644eae9f9ae1f8e39c22879 "$CACHE/$CMU_WHL"

# ---------------------------------------------------------------- build cmu_libs
STAGE="$CACHE/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/cmu_libs"
say "assembling cmu_libs"
python3 -I - "$CACHE" "$STAGE" "$PYGAME_DEB" "$CAIRO_DEB" "$CMU_WHL" <<'EOF'
import io, os, sys, tarfile, zipfile
cache, stage, pygameDeb, cairoDeb, cmuWhl = sys.argv[1:]
out = os.path.join(stage, 'cmu_libs')

def debData(path):
    """data.tar.* member of a .deb (an ar archive)."""
    with open(path, 'rb') as f:
        assert f.read(8) == b'!<arch>\n', path
        while True:
            hdr = f.read(60)
            if len(hdr) < 60:
                raise SystemExit(f'no data.tar in {path}')
            name = hdr[:16].decode().strip().rstrip('/')
            size = int(hdr[48:58])
            body = f.read(size)
            if size % 2:
                f.read(1)
            if name.startswith('data.tar'):
                return tarfile.open(fileobj=io.BytesIO(body))

prefix = './usr/lib/python3/dist-packages/'
for deb, pkg in ((pygameDeb, 'pygame'), (cairoDeb, 'cairo')):
    tf = debData(os.path.join(cache, deb))
    for m in tf.getmembers():
        if not m.name.startswith(prefix + pkg + '/'):
            continue
        rel = m.name[len(prefix):]
        dest = os.path.join(out, rel)
        if m.isdir():
            os.makedirs(dest, exist_ok=True)
        elif m.isfile():  # skips symlinks (exFAT can't hold them)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'wb') as fh:
                fh.write(tf.extractfile(m).read())

with zipfile.ZipFile(os.path.join(cache, cmuWhl)) as z:
    for n in z.namelist():
        if n.startswith('cmu_graphics/') and not n.endswith('/'):
            z.extract(n, out)
EOF

# pygame's default font is a symlink into a package the console lacks: ship a real file.
FONT=""
[ -n "$ROOTFS" ] && FONT="$ROOTFS/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
[ -f "$FONT" ] || FONT=$(fc-match -f '%{file}' 'DejaVu Sans:bold' 2>/dev/null || true)
if [ -f "$FONT" ]; then cp "$FONT" "$STAGE/cmu_libs/pygame/freesansbold.ttf"
else warn "no DejaVu Sans Bold found: pygame.font.Font(None) won't work (cmu_graphics doesn't need it)"; fi

cp "$HERE/payload/fastpath.py" "$STAGE/cmu_libs/cmu_graphics/fastpath.py"
cat >> "$STAGE/cmu_libs/cmu_graphics/__init__.py" <<'EOF'

# Speed patches for slow handhelds (see fastpath.py). CMU_FAST=0 disables them.
if os.environ.get('CMU_FAST', '1') != '0':
    from cmu_graphics import fastpath as _fastpath

    _fastpath.install(globals())
EOF
cp "$HERE/payload/launch.sh" "$HERE/payload/controls.gptk" "$HERE/payload/controls-mouse.gptk" "$STAGE/cmu_libs/"
echo "r36s-cmu $(date +%Y-%m-%d): cmu_graphics 1.1.44 + fastpath, pygame 2.6.1, pycairo 1.27.0" > "$STAGE/cmu_libs/VERSION"
python3 -m py_compile "$STAGE/cmu_libs/cmu_graphics/fastpath.py" && rm -rf "$STAGE/cmu_libs/cmu_graphics/__pycache__"

# ---------------------------------------------------------------- install
say "installing to $CARD/ports/cmu_libs"
rm -rf "$CARD/ports/cmu_libs"
cp -r "$STAGE/cmu_libs" "$CARD/ports/cmu_libs"
# test port from the first manual install, replaced by the demos below
rm -rf "$CARD/ports/CMU Graphics Test.sh" "$CARD/ports/cmutest"

MAN="$CACHE/setup.md5"
manifest "$CARD" ports/cmu_libs > "$MAN"
verify_on_card "$MAN"

if [ "$DEMOS" = 1 ]; then
  "$HERE/port.sh" "$HERE/games/cmu_fps_demo" --name "CMU FPS Demo" --card "$CARD"
  "$HERE/port.sh" "$HERE/games/cmu_swarm" --name "CMU Swarm" --card "$CARD"
fi

say "done: $(cat "$CARD/ports/cmu_libs/VERSION")"
[ "$EJECT" = 1 ] && eject_card
say "next: ./port.sh /path/to/your/game   (see README.md)"
[ "$EJECT" = 1 ] || say "card left mounted: re-run with --eject, or eject it from your file manager"
