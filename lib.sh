# Shared helpers for setup.sh and port.sh (sourced, not run).

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# dev_by_label LABEL -> /dev/... of the partition with that filesystem label
dev_by_label() {
  lsblk -rpno NAME,LABEL 2>/dev/null | awk -v l="$1" '$2 == l {print $1; exit}'
}

need() {
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "'$c' is required but not installed"; done
}

# find_card [path]  -> sets CARD to the mounted EASYROMS partition
find_card() {
  CARD="${1:-}"
  if [ -z "$CARD" ]; then
    local dev
    dev=$(dev_by_label EASYROMS)
    [ -n "$dev" ] && CARD=$(findmnt -rn -o TARGET -S "$dev" 2>/dev/null | head -1 || true)
    if [ -z "$CARD" ] && [ -n "$dev" ] && command -v udisksctl >/dev/null; then
      udisksctl mount -b "$dev" >/dev/null 2>&1 || true
      CARD=$(findmnt -rn -o TARGET -S "$dev" 2>/dev/null | head -1 || true)
    fi
    for guess in "/run/media/$USER/EASYROMS" "/media/$USER/EASYROMS" "/media/EASYROMS"; do
      [ -z "$CARD" ] && [ -d "$guess/ports" ] && CARD="$guess"
    done
  fi
  [ -n "$CARD" ] || die "SD card not found. Plug it in (partition label EASYROMS) or pass --card /path/to/EASYROMS"
  [ -d "$CARD/ports" ] || die "$CARD has no ports/ folder: is this the R36S EASYROMS partition?"
  [ -d "$CARD/ports/PortMaster" ] || die "$CARD/ports/PortMaster missing: this setup needs a dArkOS card with PortMaster"
  CARD=$(cd "$CARD" && pwd)
}

# fetch URL SHA256 OUT [FALLBACK_URL...]  (cached; checksum always verified)
fetch() {
  local url=$1 sha=$2 out=$3; shift 3
  if [ -f "$out" ] && echo "$sha  $out" | sha256sum -c --quiet - 2>/dev/null; then return 0; fi
  for u in "$url" "$@"; do
    if curl -fsSL --retry 3 -o "$out.part" "$u" && echo "$sha  $out.part" | sha256sum -c --quiet - 2>/dev/null; then
      mv "$out.part" "$out"; return 0
    fi
    warn "download failed or checksum mismatch: $u"
  done
  rm -f "$out.part"
  die "could not download $(basename "$out")"
}

# manifest DIR REL...  -> md5 list (paths relative to DIR) on stdout
manifest() {
  local dir=$1; shift
  (cd "$dir" && find "$@" -type f -not -path '*/__pycache__/*' -print0 | sort -z | xargs -0 md5sum)
}

# verify_on_card MANIFEST_FILE: flush, remount (so we re-read the card, not the cache), compare
verify_on_card() {
  local man=$1 dev
  sync
  dev=$(findmnt -n -o SOURCE --target "$CARD" 2>/dev/null || true)
  if [ -n "$dev" ] && command -v udisksctl >/dev/null; then
    udisksctl unmount -b "$dev" >/dev/null && udisksctl mount -b "$dev" >/dev/null \
      || die "could not remount $dev to verify"
    CARD=$(findmnt -rn -o TARGET -S "$dev" | head -1)
  else
    warn "udisksctl not available: verifying from the page cache, not the card itself"
  fi
  (cd "$CARD" && md5sum -c --quiet "$man") || die "files on the card don't match what was written"
  say "verified $(wc -l < "$man") files on the card"
}

# eject: unmount every partition of the card's disk and power it off
eject_card() {
  local dev disk
  sync
  dev=$(findmnt -n -o SOURCE --target "$CARD" 2>/dev/null || true)
  if [ -z "$dev" ] || ! command -v udisksctl >/dev/null; then
    warn "eject the card from your file manager before removing it"; return 0
  fi
  disk=/dev/$(lsblk -no PKNAME "$dev")
  for p in $(lsblk -lnpo NAME "$disk" | tail -n +2); do
    findmnt -rn -S "$p" >/dev/null && udisksctl unmount -b "$p" >/dev/null
  done
  udisksctl power-off -b "$disk" >/dev/null 2>&1 || true
  say "card ejected: safe to remove. Insert it into the R36S while the console is OFF."
}
