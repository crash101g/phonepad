"""Virtual keyboard and mouse, through the kernel's /dev/uinput.

This sits below the display server, so it works on Wayland (COSMIC, GNOME, KDE) and X11 alike, and
needs no compositor support. Keys are physical key codes, so the text tables assume a US layout.
"""
import asyncio
import fcntl
import os
import struct

UINPUT = '/dev/uinput'
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_DEV_SETUP = 0x405c5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0, 1, 6, 8, 11, 12
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
KEY_LEFTSHIFT, KEY_LEFTCTRL, KEY_LEFTALT, KEY_LEFTMETA = 42, 29, 56, 125
KEY_ENTER, KEY_TAB, KEY_BACKSPACE = 28, 15, 14

NOTCH = 120          # high-resolution wheel units in one wheel click (a kernel convention)
SCROLL_GAIN = 4      # high-resolution units for each phone pixel
KEY_GAP = 0.002      # seconds between key events, so applications see each one
MAX_DELTA = 2000
MAX_REPEAT = 500

_ROWS = {
    2: '1!', 3: '2@', 4: '3#', 5: '4$', 6: '5%', 7: '6^', 8: '7&', 9: '8*', 10: '9(', 11: '0)', 12: '-_', 13: '=+',
    26: '[{', 27: ']}', 39: ';:', 40: '\'"', 41: '`~', 43: '\\|', 51: ',<', 52: '.>', 53: '/?', 57: ' ',
}
_LETTERS = {16: 'qwertyuiop', 30: 'asdfghjkl', 44: 'zxcvbnm'}

# character -> (key code, needs shift)
CHARS = {'\n': (KEY_ENTER, False), '\t': (KEY_TAB, False)}
for code, pair in _ROWS.items():
    for shifted, char in enumerate(pair):
        CHARS[char] = (code, bool(shifted))
for start, letters in _LETTERS.items():
    for offset, char in enumerate(letters):
        CHARS[char] = (start + offset, False)
        CHARS[char.upper()] = (start + offset, True)

# What phone keyboards produce in place of plain punctuation.
SUBSTITUTES = {'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '…': '...', ' ': ' '}

NAMED = {
    'enter': KEY_ENTER, 'tab': KEY_TAB, 'backspace': KEY_BACKSPACE, 'esc': 1, 'space': 57, 'delete': 111, 'insert': 110,
    'up': 103, 'down': 108, 'left': 105, 'right': 106, 'home': 102, 'end': 107, 'pageup': 104, 'pagedown': 109,
    'mute': 113, 'voldown': 114, 'volup': 115, 'next': 163, 'playpause': 164, 'prev': 165,
    **{f'f{n}': 58 + n for n in range(1, 11)}, 'f11': 87, 'f12': 88,
}
MODIFIERS = {'ctrl': KEY_LEFTCTRL, 'alt': KEY_LEFTALT, 'shift': KEY_LEFTSHIFT, 'super': KEY_LEFTMETA}

KEYBOARD_KEYS = sorted({code for code, _ in CHARS.values()} | set(NAMED.values()) | set(MODIFIERS.values()))
MOUSE_BUTTONS = [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE]
MOUSE_AXES = [REL_X, REL_Y, REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES]


def parse_combo(combo):
    """'ctrl+shift+t' -> ([modifier codes], key code). A lone modifier ('super') is allowed. None if unknown."""
    parts = combo.lower().split('+')
    mods, last = parts[:-1], parts[-1]
    if not all(m in MODIFIERS for m in mods):
        return None
    codes = [MODIFIERS[m] for m in mods]
    if last in MODIFIERS:
        return codes, MODIFIERS[last]
    if last in NAMED:
        return codes, NAMED[last]
    if len(last) == 1 and last in CHARS:
        code, shifted = CHARS[last]
        return codes + ([KEY_LEFTSHIFT] if shifted else []), code
    return None


class Device:
    """One uinput device. Needs write access to /dev/uinput, or raises OSError."""

    def __init__(self, name, keys=(), axes=()):
        self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if keys:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
                for key in keys:
                    fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
            if axes:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
                for axis in axes:
                    fcntl.ioctl(self.fd, UI_SET_RELBIT, axis)
            setup = struct.pack('<HHHH80sI', 0x06, 0x1209, 0x0001, 1, name.encode(), 0)  # BUS_VIRTUAL
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise

    def emit(self, events):
        """Sends (type, code, value) events followed by a sync, so they arrive as one report."""
        data = b''.join(struct.pack('@qqHHi', 0, 0, t, c, v) for t, c, v in [*events, (EV_SYN, 0, 0)])
        try:
            os.write(self.fd, data)
        except BlockingIOError:
            pass  # the kernel's queue is full; dropping one report beats stalling the pointer

    def close(self):
        if self.fd >= 0:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = -1


def open_devices():
    """The real keyboard and mouse. Raises OSError if /dev/uinput can't be opened."""
    keyboard = Device('PhonePad Keyboard', keys=KEYBOARD_KEYS)
    try:
        return keyboard, Device('PhonePad Mouse', keys=MOUSE_BUTTONS, axes=MOUSE_AXES)
    except OSError:
        keyboard.close()
        raise


class Controller:
    """Turns phone commands into input events. Remembers what is held, so nothing can stay stuck down."""

    def __init__(self, keyboard, mouse):
        self.keyboard = keyboard
        self.mouse = mouse
        self.held_keys = set()
        self.held_buttons = set()
        self.wheel = [0, 0]  # x, y: high-resolution units not yet a whole notch

    def move(self, dx, dy):
        self.mouse.emit([(EV_REL, REL_X, dx), (EV_REL, REL_Y, dy)])

    def scroll(self, dx, dy):
        """Positive dy scrolls down, positive dx scrolls right. Smooth where the application supports it."""
        events = []
        hi_x, hi_y = dx * SCROLL_GAIN, -dy * SCROLL_GAIN
        for i, hi, hi_axis, axis in ((0, hi_x, REL_HWHEEL_HI_RES, REL_HWHEEL), (1, hi_y, REL_WHEEL_HI_RES, REL_WHEEL)):
            if not hi:
                continue
            events.append((EV_REL, hi_axis, hi))
            self.wheel[i] += hi
            notches = int(self.wheel[i] / NOTCH)  # int() rounds towards zero in both directions
            if notches:
                self.wheel[i] -= notches * NOTCH
                events.append((EV_REL, axis, notches))
        if events:
            self.mouse.emit(events)

    def button(self, code, down):
        (self.held_buttons.add if down else self.held_buttons.discard)(code)
        self.mouse.emit([(EV_KEY, code, int(down))])

    def click(self, code=BTN_LEFT):
        self.button(code, True)
        self.button(code, False)

    def _key(self, code, down):
        (self.held_keys.add if down else self.held_keys.discard)(code)
        self.keyboard.emit([(EV_KEY, code, int(down))])

    async def tap(self, mods, code):
        try:
            for mod in mods:
                self._key(mod, True)
                await asyncio.sleep(KEY_GAP)
            self._key(code, True)
            await asyncio.sleep(KEY_GAP)
            self._key(code, False)
            await asyncio.sleep(KEY_GAP)
        finally:
            for mod in reversed(mods):
                self._key(mod, False)

    async def combo(self, combo, repeat=1):
        """Presses a key or shortcut. False if the name is unknown."""
        parsed = parse_combo(combo)
        if parsed is None:
            return False
        for _ in range(max(1, min(repeat, MAX_REPEAT))):
            await self.tap(*parsed)
        return True

    async def zoom(self, direction):
        """Ctrl + wheel, which browsers and most document viewers treat as zoom."""
        self._key(KEY_LEFTCTRL, True)
        try:
            await asyncio.sleep(KEY_GAP)
            self.scroll(0, -NOTCH // SCROLL_GAIN if direction > 0 else NOTCH // SCROLL_GAIN)
            await asyncio.sleep(KEY_GAP)
        finally:
            self._key(KEY_LEFTCTRL, False)

    async def type_text(self, text):
        """Types [text]. Returns how many characters can't be typed and were skipped."""
        skipped = 0
        for char in text:
            for plain in SUBSTITUTES.get(char, char):
                entry = CHARS.get(plain)
                if entry is None:
                    skipped += 1
                    continue
                code, shifted = entry
                await self.tap([KEY_LEFTSHIFT] if shifted else [], code)
        return skipped

    def release_all(self):
        for code in list(self.held_keys):
            self._key(code, False)
        for code in list(self.held_buttons):
            self.button(code, False)

    def close(self):
        self.release_all()
        self.keyboard.close()
        self.mouse.close()
