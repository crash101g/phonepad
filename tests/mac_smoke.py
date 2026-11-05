"""Checks the real CoreGraphics calls on a Mac. Run with: python3 tests/mac_smoke.py

Events are built and read back without being posted, because that needs no permission. The final part
posts a tiny pointer move and only reports what happened, since a test machine may not allow it. Clicks and
keys are left to mac_e2e.py: here they would land on whatever is on screen, such as the Apple menu.
"""
import platform
import sys
import time

from phonepad import input_macos as mac, network, service

assert sys.platform == 'darwin' and platform.machine() == 'arm64', platform.platform()
print('platform', platform.platform())

q = mac.Quartz()
cg = q.cg
FIELD_KEYCODE = 9

for code, want_flags in ((8, 0x100000), (36, 0)):
    event = cg.CGEventCreateKeyboardEvent(q.source, code, True)
    assert event
    cg.CGEventSetFlags(event, want_flags)
    assert cg.CGEventGetIntegerValueField(event, FIELD_KEYCODE) == code
    assert cg.CGEventGetFlags(event) & 0x1f0000 == want_flags
    q.release(event)

event = cg.CGEventCreateMouseEvent(q.source, mac.MOUSE_MOVED, mac.CGPoint(123.0, 45.0), 0)
assert event
point = cg.CGEventGetLocation(event)
assert (point.x, point.y) == (123.0, 45.0), (point.x, point.y)
cg.CGEventSetIntegerValueField(event, mac.FIELD_CLICK_STATE, 2)
assert cg.CGEventGetIntegerValueField(event, mac.FIELD_CLICK_STATE) == 2
q.release(event)

assert cg.CGEventCreateScrollWheelEvent2(q.source, mac.UNITS_PIXEL, 2, -30, 10, 0)
print('event building ok')

print('displays', q.displays())
assert q.displays(), 'no display found'
print('pointer', q.location())

allowed = mac.accessibility_allowed(prompt=False)
assert isinstance(allowed, bool)
print('accessibility allowed:', allowed)

iface = network.lan_interface()
print('network interface:', iface)

mouse = mac.Mouse(q)
before = q.location()
mouse.move(3, 3)
time.sleep(.3)
print('real post moved pointer:', before, '->', q.location())

svc = service.Service(print)
try:
    print('service started:', svc.start().split('?')[0])
    svc.stop()
except service.StartError as e:
    print('service refused to start:', str(e).splitlines()[0])
    assert not allowed or iface is None
print('SMOKE OK')
