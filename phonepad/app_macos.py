"""The Mac window: the QR code, the connection status and a way to change the code. Needs PyObjC."""
import objc
from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSBezelStyleRounded,
                    NSButton, NSImage, NSImageScaleProportionallyUpOrDown, NSImageView, NSMakeRect, NSObject,
                    NSTextAlignmentCenter, NSTextField, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskTitled)
from Foundation import NSData
from PyObjCTools import AppHelper

from . import qr
from .service import Service, StartError

WIDTH, HEIGHT = 420, 600
NEW_CODE = 'New code (phones must scan again)'


def label(frame, text, size=13):
    field = NSTextField.wrappingLabelWithString_(text)
    field.setFrame_(NSMakeRect(*frame))
    field.setAlignment_(NSTextAlignmentCenter)
    return field


class Delegate(NSObject):
    def applicationDidFinishLaunching_(self, _note):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, WIDTH, HEIGHT), style, NSBackingStoreBuffered, False)
        window.setTitle_('PhonePad')
        window.setReleasedWhenClosed_(False)
        content = window.contentView()
        content.addSubview_(label((20, 548, 380, 40), "Scan with your phone's camera. It must be on the same Wi-Fi."))
        self.picture = NSImageView.alloc().initWithFrame_(NSMakeRect(60, 240, 300, 300))
        self.picture.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        content.addSubview_(self.picture)
        self.status = label((20, 150, 380, 80), '')
        content.addSubview_(self.status)
        self.link = label((20, 100, 380, 44), '')
        content.addSubview_(self.link)
        self.button = NSButton.buttonWithTitle_target_action_(NEW_CODE, self, 'buttonPressed:')
        self.button.setBezelStyle_(NSBezelStyleRounded)
        self.button.setFrame_(NSMakeRect(60, 64, 300, 30))
        content.addSubview_(self.button)
        content.addSubview_(label((20, 14, 380, 44), 'The phone stays connected while this window is open, even minimized. Close it to stop.'))
        self.window = window
        self.service = None
        self.restart(False)
        window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def buttonPressed_(self, _sender):
        # After a failed start this is "Try again", which must not change the code phones are paired with.
        self.restart(self.picture.image() is not None)

    def showStatus_(self, text):
        self.status.setStringValue_(text)

    @objc.python_method
    def restart(self, new_code):
        self.stop()
        self.service = Service(lambda text: AppHelper.callAfter(self.showStatus_, text))
        try:
            url = self.service.start(new_code)
        except StartError as e:
            self.picture.setImage_(None)
            self.link.setStringValue_('')
            self.status.setStringValue_(str(e))
            self.button.setTitle_('Try again')
            return
        png = qr.to_png(qr.encode(url))
        image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(png, len(png)))
        self.picture.setImage_(image)
        self.link.setStringValue_(url)
        self.status.setStringValue_('Waiting for a phone')
        self.button.setTitle_(NEW_CODE)

    @objc.python_method
    def stop(self):
        if self.service:
            self.service.stop()
            self.service = None

    def applicationShouldTerminateAfterLastWindowClosed_(self, _app):
        return True

    def applicationWillTerminate_(self, _note):
        self.stop()


def run():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
    return 0
