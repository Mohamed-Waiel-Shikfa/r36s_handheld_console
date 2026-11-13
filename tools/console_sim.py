"""Run a game on this PC the way the R36S launcher runs it, and measure it.

    uv run --with pygame --with pycairo --with pillow [--with <game reqs>] \\
        python tools/console_sim.py GAME_DIR [options]

Same settings as the console: 640x480 screen with CMU_FIT scaling, R36S gamepad
translation (joystick mode), no stdin, the card's cmu_graphics + fastpath. Uses
SDL's dummy video/audio drivers, so nothing opens on screen.

Options:
  --main FILE           start file (default main.py)
  --input MODE          joystick | keyboard | auto (default auto, like port.sh)
  --frames N            stop after N displayed frames (default 300)
  --seconds S           ...or after S seconds of wall time (default 60)
  --press F:BTN,...     tap an R36S button at frame F (a b x y l1 r1 l2 r2 l3 r3
                        start select up down left right). Joystick mode sends raw
                        R36S events; keyboard mode sends the key from controls.gptk.
  --hold F1-F2:BTN,...  hold a button from frame F1 to F2
  --click F:X,Y,...     mouse click at screen pixel X,Y (640x480 space) on frame F
  --snap F,...          save screenshots of these frames
  --out DIR             where screenshots go (default /tmp/console_sim)
  --stock               run stock cmu_graphics (CMU_FAST=0) for comparison
  --profile [N]         cProfile from frame 30 to the end; print the top N functions

Prints: time to first frame, peak memory, frame times, an estimated console FPS
(laptop frame time x CONSOLE_FACTOR, calibrated on the real console), and any errors.
"""
import argparse
import os
import re
import resource
import shutil
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
R36S = os.path.dirname(HERE)
CONSOLE_FACTOR = 11  # measured: CMU Swarm 4.8 ms laptop -> 53 ms on the R36S
CONSOLE_RAM_BUDGET_MB = 450  # ~1 GB console, minus the OS and zram headroom

RAW = {'b': 0, 'a': 1, 'x': 2, 'y': 3, 'l1': 4, 'r1': 5, 'l2': 6, 'r2': 7, 'up': 8, 'down': 9,
       'left': 10, 'right': 11, 'select': 12, 'start': 13, 'l3': 14, 'r3': 15}
GPTK_NAME = {'select': 'back'}
KEYNAMES = {'esc': 'escape', 'enter': 'return'}


def simLib():
    """cmu_graphics as setup.sh installs it (cached wheel + current fastpath)."""
    stage = os.path.expanduser('~/.cache/r36s-cmu/stage/cmu_libs/cmu_graphics')
    if not os.path.isdir(stage):
        sys.exit('run r36s/setup.sh once first (it builds ~/.cache/r36s-cmu/stage)')
    import tempfile
    lib = tempfile.mkdtemp(prefix='console_sim_')  # private: several sims may run at once
    dst = os.path.join(lib, 'cmu_graphics')
    shutil.copytree(stage, dst, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(os.path.join(R36S, 'payload', 'fastpath.py'), os.path.join(dst, 'fastpath.py'))
    return lib


def gptkMap(game):
    path = os.path.join(game, 'controls.gptk')
    if not os.path.exists(path):
        path = os.path.join(R36S, 'payload', 'controls.gptk')
    m = {}
    for line in open(path):
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = (s.strip() for s in line.split('=', 1))
            m[k] = KEYNAMES.get(v, v)
    return m


def parseList(s, conv):
    out = []
    for item in filter(None, (s or '').split(',')):
        out.append(conv(item))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game')
    ap.add_argument('--main', default='main.py')
    ap.add_argument('--input', default='auto')
    ap.add_argument('--frames', type=int, default=300)
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--press', default='')
    ap.add_argument('--hold', default='')
    ap.add_argument('--click', default='')
    ap.add_argument('--snap', default='')
    ap.add_argument('--out', default='/tmp/console_sim')
    ap.add_argument('--stock', action='store_true')
    ap.add_argument('--profile', type=int, nargs='?', const=25, default=0)
    a = ap.parse_args()

    game = os.path.abspath(a.game)
    src = '\n'.join(open(os.path.join(d, f), errors='ignore').read()
                    for d, _, fs in os.walk(game) if 'venv' not in d for f in fs if f.endswith('.py'))
    mode = a.input
    if mode == 'auto':
        mode = 'joystick' if re.search(r'^\s*(import joystick|from joystick import)', src, re.M) else 'keyboard'
    isCmu = 'cmu_graphics' in src

    os.environ.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy', CI='1', CMU_FIT='1',
                      CMU_R36S_JOY='1', PYGAME_HIDE_SUPPORT_PROMPT='1')
    if a.stock:
        os.environ['CMU_FAST'] = '0'
    sys.path.insert(0, simLib())
    sys.path.insert(0, os.path.join(game, '_libs'))
    sys.path.insert(0, game)
    os.chdir(game)
    os.makedirs(a.out, exist_ok=True)

    import pygame
    pygame.display.get_desktop_sizes = lambda: [(640, 480)]

    events = {}  # frame -> [pygame events]

    def add(f, e):
        events.setdefault(f, []).append(e)

    keymap = gptkMap(game)

    def button(f, name, down):
        if mode == 'joystick' or not isCmu:
            t = pygame.JOYBUTTONDOWN if down else pygame.JOYBUTTONUP
            add(f, pygame.event.Event(t, joy=0, instance_id=0, button=RAW[name]))
        else:
            key = keymap.get(GPTK_NAME.get(name, name))
            if not key or key.startswith('mouse'):
                return
            # key codes are looked up when posted: key_code() needs pygame.init()
            t = pygame.KEYDOWN if down else pygame.KEYUP
            add(f, ('key', t, key))

    for f, b in parseList(a.press, lambda s: s.split(':')):
        button(int(f), b, True)
        button(int(f) + 2, b, False)
    for span, b in parseList(a.hold, lambda s: s.split(':')):
        f1, f2 = (int(x) for x in span.split('-'))
        button(f1, b, True)
        button(f2, b, False)
    for f, xy in parseList(a.click, lambda s: s.split(':')):
        x, y = (int(v) for v in xy.split(';' if ';' in xy else ' ')) if (';' in xy or ' ' in xy) else (0, 0)
        add(int(f), pygame.event.Event(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0), touch=False))
        add(int(f), pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1, touch=False))
        add(int(f) + 1, pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(x, y), button=1, touch=False))
    snaps = set(parseList(a.snap, int))

    t0 = time.perf_counter()
    st = {'n': 0, 'first': None, 'times': [], 'cpu': [], 'last': None, 'lastCpu': None}
    prof = {'p': None}

    def report(reason):
        n = st['n']
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"\n=== console_sim: {os.path.basename(game)} ({'stock' if a.stock else 'fast'}, {mode} input) ===")
        print(f"stopped: {reason}")
        if st['first'] is None:
            print('no frame was ever displayed')
        else:
            print(f"first frame after {st['first']:.1f} s; {n} frames displayed")
        skip = min(10, len(st['times']) // 4)
        warm, cpu = st['times'][skip:], st['cpu'][skip:]
        if warm:
            med, cmed = statistics.median(warm), statistics.median(cpu)
            p90 = sorted(cpu)[int(len(cpu) * 0.9) - 1] if len(cpu) > 1 else cmed
            print(f"frame interval: median {med:.1f} ms wall (capped by the game's step rate)")
            print(f"CPU per frame: median {cmed:.1f} ms, p90 {p90:.1f} ms")
            est = CONSOLE_FACTOR * cmed
            print(f"console estimate: ~{est:.0f} ms of work per frame -> at most ~{1000 / max(est, 1):.0f} FPS "
                  f"(also capped by the game's step rate; p90 ~{CONSOLE_FACTOR * p90:.0f} ms)")
        if a.profile and prof['p'] is not None:
            import pstats
            prof['p'].disable()
            print(f'--- profile (frames 30-{n}), by own time ---')
            pstats.Stats(prof['p']).sort_stats('tottime').print_stats(a.profile)
            print('--- by cumulative time ---')
            pstats.Stats(prof['p']).sort_stats('cumulative').print_stats(a.profile)
        flag = '  <-- too much for the console' if rss > CONSOLE_RAM_BUDGET_MB else ''
        print(f"peak memory: {rss:.0f} MB (console budget ~{CONSOLE_RAM_BUDGET_MB} MB){flag}")
        sys.stdout.flush()

    origFlip = pygame.display.flip
    origUpdate = pygame.display.update

    def onFrame():
        now = time.perf_counter()
        cpuNow = time.process_time()
        st['n'] += 1
        n = st['n']
        if st['first'] is None:
            st['first'] = now - t0
        elif st['last'] is not None:
            st['times'].append(1000 * (now - st['last']))
            st['cpu'].append(1000 * (cpuNow - st['lastCpu']))
        st['last'] = now
        st['lastCpu'] = cpuNow
        for e in events.get(n, []):
            if isinstance(e, tuple):
                _, t, key = e
                code = pygame.key.key_code(key) if len(key) > 1 else ord(key)
                e = pygame.event.Event(t, key=code, mod=0, unicode=key if len(key) == 1 else '', scancode=0)
            pygame.event.post(e)
        if a.profile and n == 30:
            import cProfile
            prof['p'] = cProfile.Profile()
            prof['p'].enable()
        if n in snaps:
            surf = pygame.display.get_surface()
            if surf is not None:
                pygame.image.save(surf, os.path.join(a.out, f'{os.path.basename(game)}_f{n:04d}.png'))
        if n >= a.frames or now - t0 > a.seconds:
            report(f'{n} frames' if n >= a.frames else f'{a.seconds:.0f} s limit')
            os._exit(0)

    def flip(*args, **kwargs):
        r = origFlip(*args, **kwargs)
        onFrame()
        return r

    def update(*args, **kwargs):
        r = origUpdate(*args, **kwargs)
        onFrame()
        return r

    pygame.display.flip = flip
    pygame.display.update = update

    # joystick.py games: register the pad the way JOYDEVICEADDED would (no real pad here)
    if os.path.exists(os.path.join(game, 'joystick.py')):
        try:
            import joystick
            for name, v in (('_allJoyButtonsDown', set()), ('_allDigitalJoyAxisDown', set()),
                            ('_digitalJoyAxisSendEmpty', False)):
                if hasattr(joystick, name):
                    getattr(joystick, name)[0] = v
        except Exception as e:
            print('note: could not pre-register joystick:', e)

    try:
        # cmu_graphics reads the game's globals from __main__: run the game in it.
        path = os.path.join(game, a.main)
        g = sys.modules['__main__'].__dict__
        g['__file__'] = path
        exec(compile(open(path).read(), path, 'exec'), g)
        report('game exited')
    except SystemExit:
        report('game called sys.exit')
    except BaseException:
        import traceback
        traceback.print_exc()
        report('CRASH (traceback above)')
    os._exit(0)


if __name__ == '__main__':
    main()
