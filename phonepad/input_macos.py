"""The Mac keyboard and mouse, through CoreGraphics events. Needs the Accessibility permission.

Devices take the same Linux-coded events as uinput's, and translate them here. Written with ctypes, so the
same code runs from source with no extra packages.
"""
import ctypes
import ctypes.util
import subprocess
import time
from ctypes import POINTER, Structure, c_bool, c_char_p, c_double, c_int32, c_int64, c_uint16, c_uint32, c_uint64, c_ulong, c_void_p

from . import input as ui

# Page shortcut names are written for Linux. Here is what each one means on a Mac.
SHORTCUTS = {
    'ctrl+c': 'super+c', 'ctrl+v': 'super+v', 'ctrl+z': 'super+z', 'ctrl+a': 'super+a',
    'alt+tab': 'super+tab', 'alt+left': 'super+left', 'super': 'super+space', 'f11': 'ctrl+super+f',
    'zoom-in': 'super+=', 'zoom-out': 'super+-',
}

# Character -> Mac virtual key code (the ANSI layout), joined with the Linux code for the same key below.
_MAC_CHARS = {
    'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7, 'c': 8, 'v': 9, 'b': 11, 'q': 12, 'w': 13, 'e': 14,
    'r': 15, 'y': 16, 't': 17, '1': 18, '2': 19, '3': 20, '4': 21, '6': 22, '5': 23, '=': 24, '9': 25, '7': 26, '-': 27,
    '8': 28, '0': 29, ']': 30, 'o': 31, 'u': 32, '[': 33, 'i': 34, 'p': 35, 'l': 37, 'j': 38, "'": 39, 'k': 40, ';': 41,
    '\\': 42, ',': 43, '/': 44, 'n': 45, 'm': 46, '.': 47, ' ': 49, '`': 50,
}
_MAC_NAMED = {
    'enter': 36, 'tab': 48, 'backspace': 51, 'esc': 53, 'delete': 117, 'insert': 114, 'home': 115, 'end': 119,
    'pageup': 116, 'pagedown': 121, 'left': 123, 'right': 124, 'down': 125, 'up': 126,
    'f1': 122, 'f2': 120, 'f3': 99, 'f4': 118, 'f5': 96, 'f6': 97, 'f7': 98, 'f8': 100, 'f9': 101, 'f10': 109, 'f11': 103, 'f12': 111,
}
FLAGS = {ui.KEY_LEFTSHIFT: 0x20000, ui.KEY_LEFTCTRL: 0x40000, ui.KEY_LEFTALT: 0x80000, ui.KEY_LEFTMETA: 0x100000}
_MAC_MODIFIERS = {ui.KEY_LEFTSHIFT: 56, ui.KEY_LEFTCTRL: 59, ui.KEY_LEFTALT: 58, ui.KEY_LEFTMETA: 55}

KEYMAP = {**{ui.CHARS[c][0]: mac for c, mac in _MAC_CHARS.items()},
          **{ui.NAMED[name]: mac for name, mac in _MAC_NAMED.items()}, **_MAC_MODIFIERS}

_VOLUME = 'set volume output volume ((output volume of (get volume settings)) %s 6)'
SCRIPTS = {
    ui.NAMED['volup']: _VOLUME % '+',
    ui.NAMED['voldown']: _VOLUME % '-',
    ui.NAMED['mute']: 'set volume output muted (not (output muted of (get volume settings)))',
}

SYSTEM = '/System/Library/Frameworks/%s.framework/%s'
TAP_HID, SOURCE_HID = 0, 1
UNITS_PIXEL = 0
FIELD_CLICK_STATE = 1
DOUBLE_CLICK_SECONDS = 0.4
# button -> (down, up, dragged, CoreGraphics button number)
BUTTONS = {ui.BTN_LEFT: (1, 2, 6, 0), ui.BTN_RIGHT: (3, 4, 7, 1), ui.BTN_MIDDLE: (25, 26, 27, 2)}
MOUSE_MOVED = 5


class CGPoint(Structure):
    _fields_ = [('x', c_double), ('y', c_double)]


class CGRect(Structure):
    _fields_ = [('origin', CGPoint), ('size', CGPoint)]


class Quartz:
    """The CoreGraphics calls this needs. Tests replace this class."""

    def __init__(self):
        cg = ctypes.CDLL(SYSTEM % ('CoreGraphics', 'CoreGraphics'))
        signatures = {
            'CGEventSourceCreate': (c_void_p, [c_int32]),
            'CGEventCreate': (c_void_p, [c_void_p]),
            'CGEventCreateMouseEvent': (c_void_p, [c_void_p, c_uint32, CGPoint, c_uint32]),
            'CGEventCreateKeyboardEvent': (c_void_p, [c_void_p, c_uint16, c_bool]),
            # The "2" form isn't variadic, which ctypes can't call reliably on Apple silicon. macOS 13 and later.
            'CGEventCreateScrollWheelEvent2': (c_void_p, [c_void_p, c_uint32, c_uint32, c_int32, c_int32, c_int32]),
            'CGEventSetFlags': (None, [c_void_p, c_uint64]),
            'CGEventGetFlags': (c_uint64, [c_void_p]),
            'CGEventSetIntegerValueField': (None, [c_void_p, c_uint32, c_int64]),
            'CGEventGetIntegerValueField': (c_int64, [c_void_p, c_uint32]),
            'CGEventKeyboardSetUnicodeString': (None, [c_void_p, c_ulong, c_char_p]),
            'CGEventGetLocation': (CGPoint, [c_void_p]),
            'CGEventPost': (None, [c_uint32, c_void_p]),
            'CGGetActiveDisplayList': (c_int32, [c_uint32, POINTER(c_uint32), POINTER(c_uint32)]),
            'CGDisplayBounds': (CGRect, [c_uint32]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(cg, name)
            function.restype, function.argtypes = result, arguments
        self.cg = cg
        foundation = ctypes.CDLL(SYSTEM % ('CoreFoundation', 'CoreFoundation'))
        foundation.CFRelease.restype, foundation.CFRelease.argtypes = None, [c_void_p]
        self.release = foundation.CFRelease
        self.source = cg.CGEventSourceCreate(SOURCE_HID)

    def _post(self, event, flags=None, click_state=None):
        if flags is not None:
            self.cg.CGEventSetFlags(event, flags)
        if click_state is not None:
            self.cg.CGEventSetIntegerValueField(event, FIELD_CLICK_STATE, click_state)
        self.cg.CGEventPost(TAP_HID, event)
        self.release(event)

    def location(self):
        event = self.cg.CGEventCreate(None)
        point = self.cg.CGEventGetLocation(event)
        self.release(event)
        return point.x, point.y

    def displays(self):
        """Each screen as (left, top, right, bottom)."""
        ids = (c_uint32 * 16)()
        count = c_uint32()
        self.cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count))
        bounds = [self.cg.CGDisplayBounds(ids[i]) for i in range(count.value)]
        return [(b.origin.x, b.origin.y, b.origin.x + b.size.x, b.origin.y + b.size.y) for b in bounds]

    def mouse(self, kind, x, y, button, click_state=None):
        self._post(self.cg.CGEventCreateMouseEvent(self.source, kind, CGPoint(x, y), button), click_state=click_state)

    def scroll(self, vertical, horizontal):
        self._post(self.cg.CGEventCreateScrollWheelEvent2(self.source, UNITS_PIXEL, 2, vertical, horizontal, 0))

    def key(self, code, down, flags):
        self._post(self.cg.CGEventCreateKeyboardEvent(self.source, code, down), flags=flags)

    def text(self, char):
        data = char.encode('utf-16-le')
        for down in (True, False):
            event = self.cg.CGEventCreateKeyboardEvent(self.source, 0, down)
            self.cg.CGEventKeyboardSetUnicodeString(event, len(data) // 2, data)
            self._post(event, flags=0)


class Keyboard:
    def __init__(self, quartz, run_script=None):
        self.quartz = quartz
        self.flags = 0
        self.run_script = run_script or (lambda script: subprocess.Popen(['/usr/bin/osascript', '-e', script]))

    def emit(self, events):
        for _, code, value in events:
            if code in FLAGS:
                self.flags = self.flags | FLAGS[code] if value else self.flags & ~FLAGS[code]
            if code in SCRIPTS:  # there's no key event a program can post to change the volume
                if value:
                    self.run_script(SCRIPTS[code])
            elif code in KEYMAP:
                self.quartz.key(KEYMAP[code], bool(value), self.flags)

    def type_char(self, char):
        self.quartz.text(char)

    def close(self):
        pass


class Mouse:
    def __init__(self, quartz, clock=time.monotonic):
        self.quartz = quartz
        self.clock = clock
        self.held = None
        self.last_click = (None, 0.0, 0)  # button, time, click count

    def emit(self, events):
        dx = dy = 0
        scroll_x = scroll_y = 0
        for kind, code, value in events:
            if kind == ui.EV_KEY:
                self.press(code, value)
            elif code == ui.REL_X:
                dx += value
            elif code == ui.REL_Y:
                dy += value
            elif code == ui.REL_WHEEL_HI_RES:
                scroll_y += value
            elif code == ui.REL_HWHEEL_HI_RES:
                scroll_x += value
        if dx or dy:
            self.move(dx, dy)
        if scroll_x or scroll_y:
            # Back to phone pixels. A Mac's second wheel runs the other way from Linux's.
            self.quartz.scroll(scroll_y // ui.SCROLL_GAIN, -scroll_x // ui.SCROLL_GAIN)

    def clamp(self, x, y):
        """Keeps the pointer on a screen: the nearest one, when a move would leave them all."""
        displays = self.quartz.displays()
        if not displays:
            return x, y
        nearest = (float('inf'), x, y)
        for left, top, right, bottom in displays:
            cx, cy = min(max(x, left), right - 1), min(max(y, top), bottom - 1)
            nearest = min(nearest, ((cx - x) ** 2 + (cy - y) ** 2, cx, cy))
        return nearest[1], nearest[2]

    def move(self, dx, dy):
        x, y = self.quartz.location()
        x, y = self.clamp(x + dx, y + dy)
        if self.held is None:
            self.quartz.mouse(MOUSE_MOVED, x, y, 0)
        else:
            _, _, dragged, number = BUTTONS[self.held]
            self.quartz.mouse(dragged, x, y, number)

    def press(self, code, value):
        down, up, _, number = BUTTONS[code]
        x, y = self.quartz.location()
        if value:
            button, at, count = self.last_click
            now = self.clock()
            count = count + 1 if button == code and now - at < DOUBLE_CLICK_SECONDS else 1
            self.last_click = (code, now, count)
            self.held = code
        self.quartz.mouse(down if value else up, x, y, number, click_state=self.last_click[2])
        if not value and self.held == code:
            self.held = None

    def close(self):
        pass


def accessibility_allowed(prompt=True):
    """Whether this program may control the Mac. With [prompt], macOS offers to open the setting."""
    services = ctypes.CDLL(SYSTEM % ('ApplicationServices', 'ApplicationServices'))
    foundation = ctypes.CDLL(SYSTEM % ('CoreFoundation', 'CoreFoundation'))
    create = foundation.CFDictionaryCreate
    create.restype = c_void_p
    create.argtypes = [c_void_p, POINTER(c_void_p), POINTER(c_void_p), ctypes.c_long, c_void_p, c_void_p]
    key = c_void_p.in_dll(services, 'kAXTrustedCheckOptionPrompt')
    value = c_void_p.in_dll(foundation, 'kCFBooleanTrue' if prompt else 'kCFBooleanFalse')
    keys, values = (c_void_p * 1)(key), (c_void_p * 1)(value)
    options = create(None, keys, values, 1, None, None)
    check = services.AXIsProcessTrustedWithOptions
    check.restype, check.argtypes = c_bool, [c_void_p]
    allowed = check(options)
    foundation.CFRelease.argtypes = [c_void_p]
    foundation.CFRelease(options)
    return allowed


def open_devices():
    """The Mac's keyboard and mouse. Raises OSError without the Accessibility permission."""
    if not accessibility_allowed():
        raise PermissionError('Accessibility permission is off')
    quartz = Quartz()
    return Keyboard(quartz), Mouse(quartz)
