#!/usr/bin/env bash
# port_all.sh: install cmu_graphics (setup.sh) and port every game in ../ to the card.
#   ./port_all.sh [--eject]
# Echoes of the Labyrinth is not included: it was already on the card and working.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
G="$HERE/.."
EJECT=${1:-}
"$HERE/setup.sh"
"$HERE/port.sh" "$G/Salems Adventure"         --name "Salem's Adventure"
"$HERE/port.sh" "$G/Tarnival-StreetFighter.git" --name "Tarnival StreetFighter"
"$HERE/port.sh" "$G/Professor-Invaders.git"   --name "Professor Invaders"
"$HERE/port.sh" "$G/APOAPSIS.git"             --name "APOAPSIS"
"$HERE/port.sh" "$G/wordweaver"               --name "WordWeaver" --main Game.py
"$HERE/port.sh" "$G/f1"                       --name "F1 Racer" --main f1_racer.py
"$HERE/port.sh" "$G/gridlock-defense"         --name "Gridlock Defense"
"$HERE/port.sh" "$G/Insomnia.git"             --name "Insomnia"
if [ "$EJECT" = "--eject" ]; then "$HERE/port.sh" "$G/CMU3D.git" --name "CMU3D" --eject
else "$HERE/port.sh" "$G/CMU3D.git" --name "CMU3D"; fi
