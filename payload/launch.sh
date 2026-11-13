#!/bin/bash
# Shared launcher for cmu_graphics games on the R36S (dArkOS + PortMaster).
# Each game's "<Name>.sh" in /roms/ports sets these, then sources this file:
#   GAME_DIR    folder name under /roms/ports
#   GAME_MAIN   entry script inside it (e.g. main.py)
#   INPUT_MODE  keyboard  - gamepad -> keys via gptokeyb (controls.gptk)
#               joystick  - gamepad seen as the CMU arcade box joystick (joystick.py games)

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi
source $controlfolder/control.txt
source $controlfolder/device_info.txt
[ -f "${controlfolder}/mod_${CFW_NAME}.txt" ] && source "${controlfolder}/mod_${CFW_NAME}.txt"
get_controls

CMU_LIBS="/$directory/ports/cmu_libs"
GAMEDIR="/$directory/ports/$GAME_DIR"
cd "$GAMEDIR" || exit 1
> "$GAMEDIR/log.txt" && exec > >(tee "$GAMEDIR/log.txt") 2>&1
echo "cmu launcher: $GAME_DIR/$GAME_MAIN, input=$INPUT_MODE, $(cat "$CMU_LIBS/VERSION" 2>/dev/null)"

export PYTHONPATH="$GAMEDIR/_libs:$CMU_LIBS"
export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
export PYGAME_HIDE_SUPPORT_PROMPT=1
export CI=1            # stops cmu_graphics' stdin console, which would quit the game
export CMU_FIT=1       # scale any app size to the 640x480 screen
export CMU_R36S_JOY=1  # R36S pad -> arcade box numbering; Select+Start quits
[ -f "$GAMEDIR/cmu.env" ] && source "$GAMEDIR/cmu.env"  # per-game overrides

# Pin CPU / memory / GPU at their top clocks (dArkOS already allows 1.512 GHz).
# The menu's perfnorm restores power saving after the game exits.
$ESUDO sh -c 'echo performance > /sys/devices/system/cpu/cpufreq/policy0/scaling_governor
  for g in dmc_performance performance; do echo $g > /sys/devices/platform/dmc/devfreq/dmc/governor && break; done
  for g in simple_performance performance; do echo $g > /sys/devices/platform/ff400000.gpu/devfreq/ff400000.gpu/governor && break; done' 2>/dev/null
echo "cpu: $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor) @ $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq 2>/dev/null) kHz"

if [ "$INPUT_MODE" = "keyboard" ]; then
  GPTK="$GAMEDIR/controls.gptk"
  [ -f "$GPTK" ] || GPTK="$CMU_LIBS/controls.gptk"
  $GPTOKEYB "python3" -c "$GPTK" &
fi

python3 "$GAME_MAIN" < /dev/null

GPID=$(pidof gptokeyb || true)
[ -n "$GPID" ] && $ESUDO kill -9 $GPID
$ESUDO sh -c 'printf "\033c" > /dev/tty0'
