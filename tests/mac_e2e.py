"""Drives a real window on a Mac through the Controller, and checks what macOS delivered to it.
Needs the Accessibility permission. Usage: mac_e2e.py screenshot.png"""
import asyncio
import os
import queue
import subprocess
import sys
import threading
import time

from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSEvent, NSMenu, NSMenuItem,
                    NSEventModifierFlagCommand, NSEventTypeKeyDown, NSEventTypeLeftMouseDown, NSEventTypeLeftMouseDragged,
                    NSEventTypeLeftMouseUp, NSEventTypeRightMouseDown, NSEventTypeScrollWheel, NSMakeRect, NSScreen,
                    NSTextField, NSWindow, NSWindowStyleMaskTitled)
from PyObjCTools import AppHelper

from phonepad import input as ui, input_macos as mac

seen = []
failures = []


def check(name, ok, detail=''):
    print('PASS' if ok else 'FAIL', name, detail, flush=True)
    if not ok:
        failures.append(name)


def on_main(function):
    """Runs [function] on the main thread and returns its result."""
    answer = queue.Queue(1)
    AppHelper.callAfter(lambda: answer.put(function()))
    return answer.get(timeout=5)


def record(event):
    seen.append(event)
    return event


def events(kind):
    return [e for e in list(seen) if e.type() == kind]


def scenario(window, field, ctl, q):
    async def run():
        time.sleep(1.5)
        print('app active', on_main(lambda: bool(NSApp.isActive())), 'key window', on_main(lambda: bool(window.isKeyWindow())), flush=True)
        on_main(lambda: window.makeFirstResponder_(field))
        time.sleep(.3)

        # Over the window, in an empty area. CoreGraphics counts down from the top, AppKit up from the bottom.
        frame = on_main(window.frame)
        height = on_main(lambda: NSScreen.screens()[0].frame().size.height)
        x, y = q.location()
        ctl.move(int(frame.origin.x + 80 - x), int(height - frame.origin.y - 60 - y))
        time.sleep(.3)
        check('pointer reached the window', abs(q.location()[0] - frame.origin.x - 80) < 2, str(q.location()))

        await ctl.type_text('Hello é😀 ñ')
        time.sleep(.5)
        text = on_main(lambda: str(field.stringValue()))
        check('typed unicode text', text == 'Hello é😀 ñ', repr(text))

        await ctl.combo('ctrl+a')       # the phone's All button: Command+A here
        time.sleep(.2)
        await ctl.type_text('x')
        time.sleep(.3)
        text = on_main(lambda: str(field.stringValue()))
        check('shortcut selects all, typing replaces', text == 'x', repr(text))
        keys = events(NSEventTypeKeyDown)
        check('Command flag reached the app', any(e.modifierFlags() & NSEventModifierFlagCommand for e in keys))
        check('Command flag did not stick', not keys[-1].modifierFlags() & NSEventModifierFlagCommand)

        await ctl.combo('backspace')
        time.sleep(.3)
        check('backspace', on_main(lambda: str(field.stringValue())) == '')
        check('nothing left held', not ctl.held_keys and not ctl.keyboard.flags)

        seen.clear()
        ctl.click()
        ctl.click()
        time.sleep(.3)
        check('double click', [e.clickCount() for e in events(NSEventTypeLeftMouseDown)] == [1, 2],
              str([e.clickCount() for e in events(NSEventTypeLeftMouseDown)]))

        time.sleep(.6)
        seen.clear()
        ctl.button(ui.BTN_LEFT, True)
        ctl.move(10, 5)
        ctl.move(10, 5)
        ctl.button(ui.BTN_LEFT, False)
        time.sleep(.3)
        check('drag', len(events(NSEventTypeLeftMouseDragged)) >= 1 and len(events(NSEventTypeLeftMouseUp)) == 1,
              f'{len(events(NSEventTypeLeftMouseDragged))} dragged')

        seen.clear()
        ctl.click(ui.BTN_RIGHT)
        time.sleep(.3)
        check('right click', len(events(NSEventTypeRightMouseDown)) == 1)

        seen.clear()
        ctl.scroll(0, 30)
        time.sleep(.3)
        deltas = [e.scrollingDeltaY() for e in events(NSEventTypeScrollWheel)]
        check('scroll down', bool(deltas) and sum(deltas) < 0, str(deltas))

        ctl.release_all()
        subprocess.run(['/usr/sbin/screencapture', '-x', sys.argv[1]])

    asyncio.run(run())
    print('E2E', 'FAILED: ' + ', '.join(failures) if failures else 'OK', flush=True)
    os._exit(1 if failures else 0)


def main():
    q = mac.Quartz()
    ctl = ui.Controller(mac.Keyboard(q), mac.Mouse(q), mac.SHORTCUTS)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    # Command shortcuts reach a text field through the menu, as in any Mac app.
    menu = NSMenu.alloc().init()
    holder = NSMenuItem.alloc().init()
    menu.addItem_(holder)
    edit = NSMenu.alloc().initWithTitle_('Edit')
    edit.addItemWithTitle_action_keyEquivalent_('Select All', 'selectAll:', 'a')
    holder.setSubmenu_(edit)
    app.setMainMenu_(menu)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(300, 300, 600, 300), NSWindowStyleMaskTitled, NSBackingStoreBuffered, False)
    window.setTitle_('PhonePad end-to-end test')
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 20, 560, 30))
    window.contentView().addSubview_(field)
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    mask = (1 << NSEventTypeKeyDown) | (1 << NSEventTypeLeftMouseDown) | (1 << NSEventTypeLeftMouseUp) | \
        (1 << NSEventTypeLeftMouseDragged) | (1 << NSEventTypeRightMouseDown) | (1 << NSEventTypeScrollWheel)
    monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, record)
    threading.Thread(target=scenario, args=(window, field, ctl, q), daemon=True).start()
    AppHelper.runEventLoop()
    return monitor


main()
