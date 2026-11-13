# cmu_graphics on the R36S

Run CMU 15-112 `cmu_graphics` games on an R36S handheld running dArkOS.
`setup.sh` installs pygame, pycairo and a sped-up cmu_graphics onto the SD card.
`port.sh` copies a game onto the card and adds it to the console's **Ports** menu.
Everything is done from a Linux PC with the SD card plugged in. You don't need a
terminal on the console, and you don't need Wi-Fi.

## Quick start

```bash
./setup.sh --demos                 # once per card (re-run any time to update)
```

```bash
./port.sh ~/path/to/my-game --eject   # once per game, or after you change it
```

Both scripts leave the card mounted unless you pass `--eject`, so you can run several
in a row and add `--eject` to the last one. Then put the card in the console **while it is powered off**, turn it on, and open
**Ports → your game**. Each game writes its output and errors to
`EASYROMS/ports/<game>/log.txt`.

> **Always shut the console down fully (Start → Quit → Shutdown) before taking the
> card out or putting it back in.** If you swap the card while the console is only
> asleep, the console later writes its stale copy of the folder listing back to the card,
> and your newly copied files disappear.

## Controls

The R36S can run a game in one of two modes. `port.sh` picks the mode for each game:

* **Joystick mode** is used when the game imports `joystick.py` from the
  [CMU arcade box](https://github.com/Mohamed-Waiel-Shikfa/arcade-box-15112).
  The gamepad behaves like the arcade box, so `onJoyPress`, `onDigitalJoyAxis`
  and the rest of that API receive the same button numbers they get on the arcade
  box. The game needs no changes.
* **Keyboard mode** is used for every other game. The gamepad types keys, so
  `onKeyPress` / `onKeyHold` games work unchanged.

To override the choice, pass `--input joystick` or `--input keyboard` to `port.sh`.

**Select + Start quits any game, in either mode.**

### Joystick mode: R36S → arcade box

R36S face buttons follow the Nintendo layout: A on the right, B at the bottom,
X at the top, Y on the left.

| R36S | Arcade box | `button` in `onJoyPress` |
|---|---|---|
| B | b | `'0'` |
| A | a | `'1'` |
| X | x | `'2'` |
| Y | y | `'3'` |
| L1 | Insert money | `'4'` |
| R1 | **P1** (most arcade games quit on it) | `'5'` |
| Select | Select | `'8'` |
| Start | Start | `'9'` |
| D-pad | Joystick | `onDigitalJoyAxis`: `(0,-1)` left, `(0,1)` right, `(1,-1)` up, `(1,1)` down |
| Left stick | Joystick | the same axes 0/1 (analog; `joystick.py` turns them into digital directions), plus `onJoyAxis` |

The translation happens inside pygame's event stream. On the R36S the B, A, X, Y,
L1 and R1 buttons already have the arcade numbers 0–5. Select and Start move to
8 and 9, as on the arcade box. The D-pad becomes the arcade joystick (digital
axes 0 and 1), just as the arcade box's own stick does. It does not send hat
buttons `H0`–`H3`, because the arcade box doesn't either.

**Console-only inputs** have no arcade box equivalent. Arcade games ignore them,
but your own code can use them:

| R36S | Seen by the game as |
|---|---|
| L2 | button `'6'` |
| R2 | button `'7'` |
| L3 (press the left stick) | button `'10'` |
| R3 (press the right stick) | button `'11'` |
| Right stick | `onJoyAxis` axes `2` (left/right) and `3` (up/down), values −1…1; `onDigitalJoyAxis` reports `(2,±1)` and `(3,±1)` |
| Select + Start (together) | quits the game |
| FN, volume, power | used by the system; games never see them |

### Keyboard mode

| R36S | Key |
|---|---|
| D-pad | arrow keys (`up`, `down`, `left`, `right`) |
| Left stick | `w` `a` `s` `d` |
| Right stick | `i` `j` `k` `l` |
| A | `space` |
| B | `z` |
| X | `x` |
| Y | `c` |
| L1 / R1 | `q` / `e` |
| L2 / R2 | `r` / `p` (common restart / pause keys) |
| L3 / R3 | `1` / `2` |
| Start | `enter` |
| Select | `escape` |
| Select + Start | quits the game |

### Mouse games (keyboard mode)

If a keyboard-mode game defines `onMousePress`, `onMouseDrag`, `onMouseMove` or
`onMouseRelease`, `port.sh` gives it this map instead. A cursor is drawn on screen,
and mouse positions are converted to the game's own coordinates.

| R36S | Input |
|---|---|
| Left stick | moves the mouse |
| A / B | left / right click |
| X / Y | `space` / `z` |
| Right stick | `w` `a` `s` `d` |
| everything else | as in the keyboard table |

The cursor speed is the `mouse_scale` line (a higher number means a slower cursor).

To change a game's keys, put a `controls.gptk` in the game's own folder before
porting. `port.sh` uses it instead of the defaults, so it survives re-porting.
You can also edit `EASYROMS/ports/<game>/controls.gptk` directly. Each line
has the form `button = key` (PortMaster gptokeyb format).

## The scripts

`./setup.sh [--card DIR] [--demos] [--eject]`
* Downloads pinned, checksum-verified builds (cached in `~/.cache/r36s-cmu`):
  * pygame 2.6.1 and pycairo 1.27.0, the Debian 13 arm64 packages that match the
    console's Python 3.13
  * cmu_graphics 1.1.44 from PyPI
* Installs them, along with `fastpath.py` (the speed patches) and the shared
  launcher, into `EASYROMS/ports/cmu_libs`. The console's system partition isn't
  modified.
* Re-reads every file from the card after a remount to check the copy. With
  `--eject` it then unmounts the card and powers it off.
* `--demos` also installs two benchmark games:
  * **CMU FPS Demo**: a light scene, keyboard mode
  * **CMU Swarm**: about 420 shapes per frame at 800×600, joystick mode. It plays
    itself until you touch a control and shows its FPS on screen.

`./port.sh GAME_FOLDER [--name "Name"] [--main FILE] [--input auto|keyboard|joystick] [--card DIR] [--eject]`
* Copies the project to `EASYROMS/ports/<name>/`. It leaves out `.git`, virtual
  environments, `build/`, `dist/`, `__pycache__`, `.exe` files, and any bundled
  `cmu_graphics` folder (the card's faster copy is used instead). To leave out more,
  add a `.portignore` file to the game folder (rsync patterns, one per line, e.g.
  `*.zip` or `Demo video.mp4`). Files the game writes on the console (saves, high
  scores) are kept when you re-port if they match a pattern in the game's `.portkeep`
  file (globs relative to the game folder, one per line, e.g. `saved games/*.txt`).
* Finds the start file: `main.py` if it exists, otherwise the single file that
  calls `runApp`. Use `--main` if there are several.
* For packages in `requirements.txt`, downloads the aarch64 wheels into the game's
  `_libs/` folder. This needs `uv` or `pip` on the PC. cmu_graphics, pygame,
  Pillow and pycairo are skipped because the card already has them.
* Writes `EASYROMS/ports/<Name>.sh` (the menu entry) and verifies the copy on
  the card.

Per-game tweaks go in an optional `EASYROMS/ports/<game>/cmu.env` file that the
launcher sources. For example:

```bash
export CMU_ANTIALIAS=fast   # cheaper shape edges for very heavy games
export CMU_STATIC_LAYER=0   # turn off static-background caching
export CMU_FAST=0           # run stock cmu_graphics (for comparison)
```

## Screen size

Games of any size are scaled to fit the 640×480 screen and letterboxed if needed.
The arcade box's 800×600 is the same 4:3 shape, so those games fill the whole
screen. Scaling happens while drawing (cairo draws straight at screen
resolution), so it costs nothing extra; an 800×600 game actually renders fewer
pixels.

## Performance

These are the speed patches in `payload/fastpath.py`. Every patch keeps stock
behaviour, and calls with arguments a patch doesn't handle go through the
original code, so error messages don't change.

1. **`redrawAll` runs once per frame.** Stock cmu_graphics re-runs it after every
   handler (`onStep`, `onKeyHold`, each key press) and throws away all but the last
   result. Frames where nothing changed are skipped.
2. **Opaque blit.** The frame goes to the screen as RGBX instead of RGBA, so it
   is copied instead of being alpha-blended pixel by pixel (28× faster).
3. **Direct drawing.** `drawRect/Oval/Circle/Line/Polygon/RegularPolygon/Star/Arc/Label/Image`
   with ordinary arguments, including rotation, dashes, opacity and alignment,
   skip cmu's validated shape objects and draw directly with cairo, at the moment
   they are called.
4. **Caches.** Rendered text, resized and rotated sprites, colours, `rgb()`
   objects and translation lookups are all cached.
5. **Static background caching.** If a frame starts with the same draw calls as
   the previous frame (a background, a tile map, a grid), that part is pasted from
   a saved image instead of being drawn again. The output stays exact: when a call
   differs, the skipped calls are drawn first.
6. **Clocks.** The launcher pins CPU, memory and GPU to their top clocks while a
   game runs. dArkOS already runs the CPU at 1.512 GHz (stock firmware: 1.3 GHz)
   at its highest voltage, so there's no safe overclock beyond that.

Measured on a laptop with frame rates uncapped (the console is roughly 12–13×
slower):

| Scene | Stock | Fast | |
|---|---|---|---|
| CMU FPS Demo (46 shapes, keys held) | 22.1 ms | 2.3 ms | 9.7× |
| CMU Swarm (~420 shapes, rotated sprites, polygons) | 48 ms | 5–7 ms | 7–9× |

On the console, the FPS demo went from about **4 FPS** to **30 FPS** (it is capped
by its own step rate). CMU Swarm shows its FPS on screen and in its `log.txt`.

Tips for writing fast games:
* Draw static things (backgrounds, tile maps, borders) **first** in `redrawAll`,
  always in the same order with the same arguments. That's what background
  caching (item 5) detects.
* Use `rgb(...)` freely; it's cached. Avoid `gradient(...)` fills on many shapes,
  because gradients use the slower original code.
* Prefer pre-sized images over `width=`/`height=` that change every frame. Each
  new size gets resampled once and cached.
* Keep game logic in `onStep` light. Plain Python runs about 12× slower on the
  console than on a laptop.

## Testing on the PC first

`tools/console_sim.py` runs a game headless with the console's exact settings: 640×480
fit, gamepad translation, no stdin, and the same cmu_graphics plus speed patches. You
can press buttons on given frames, save screenshots, and compare against stock
cmu_graphics. It reports startup time, peak memory, CPU per frame, and an estimated
console FPS (laptop CPU time × 11, the ratio measured on CMU Swarm).

```bash
uv run --with pygame --with pycairo --with pillow python tools/console_sim.py ../my-game \
    --press 40:start,90:a --hold 100-200:right --snap 150 --out /tmp/sim
```

Add `--with <package>` for each extra package the game needs, `--stock` for the
unpatched baseline, and `--profile` to see where the time goes. Keep the peak memory
well under ~400 MB: the console has 1 GB in total.

## Limitations

* No touch screen. Mouse games work through the stick-driven cursor above.
  `app.getTextInput()` and `app.showMessage()` open desktop dialogs and won't work.
* The card has cmu_graphics **1.1.44**. Games that depend on behaviour of other
  versions may fail. For example, older versions accepted pygame `Surface`
  objects in `drawImage`; 1.1.44 needs a file path, URL or `CMUImage(PIL image)`.
* Extra pip packages need prebuilt aarch64 wheels for Python 3.13. Pure-Python
  packages always work.
* Sounds load the first time they're played. Files over 1 MB (music) stream from
  disk through `pygame.mixer.music`, so only one long track plays at a time.
* If a game freezes, Select + Start may not get through. Holding the power button
  restarts the console.

## Files

```
setup.sh, port.sh, lib.sh    the scripts (lib.sh = shared helpers)
payload/fastpath.py          speed patches, installed as cmu_graphics/fastpath.py
payload/launch.sh            shared launcher every game's menu entry uses
payload/controls.gptk        default keyboard-mode button map
payload/controls-mouse.gptk  default map for mouse-driven games
tools/console_sim.py         run a game on the PC as the console would, and measure it
games/cmu_fps_demo           light benchmark (keyboard mode)
games/cmu_swarm              heavy benchmark (joystick mode, uses joystick.py)
```
