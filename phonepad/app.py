"""The desktop window: the QR code, the connection status and a way to change the code."""
import gi

gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import qr  # noqa: E402
from .service import Service, StartError  # noqa: E402

APP_ID = 'io.github.crash101g.PhonePad'
NEW_CODE = 'New code (phones must scan again)'
CSS = b'.status { font-weight: bold; } .qr { background: white; border-radius: 12px; }'


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='PhonePad', default_width=420, default_height=600, icon_name='phonepad')
        self.service = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=20, margin_bottom=20,
                      margin_start=20, margin_end=20)
        self.set_child(box)
        box.append(Gtk.Label(label='Scan with your phone\'s camera. It must be on the same Wi-Fi.', wrap=True))
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True, css_classes=['qr'])
        box.append(self.picture)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=['status'])
        box.append(self.status)
        self.link = Gtk.Label(wrap=True, wrap_mode=2, justify=Gtk.Justification.CENTER, css_classes=['dim-label'])
        box.append(self.link)
        self.button = Gtk.Button(label=NEW_CODE, halign=Gtk.Align.CENTER)
        self.button.connect('clicked', self.on_button)
        box.append(self.button)
        hint = 'The phone stays connected while this window is open, even minimized. Close it to stop.'
        box.append(Gtk.Label(label=hint, wrap=True, css_classes=['dim-label']))

        self.connect('close-request', self.on_close)
        self.restart()

    def show_status(self, text):
        GLib.idle_add(self.status.set_label, text)  # called from the server's thread

    def on_button(self, _button):
        # After a failed start this is "Try again", which must not change the code phones are paired with.
        self.restart(new_code=self.picture.get_paintable() is not None)

    def restart(self, new_code=False):
        self.stop()
        self.service = Service(self.show_status)
        try:
            url = self.service.start(new_code)
        except StartError as e:
            self.picture.set_paintable(None)
            self.link.set_label('')
            self.status.set_label(str(e))
            self.button.set_label('Try again')
            return
        pixels, width = qr.to_rgba(qr.encode(url))
        texture = Gdk.MemoryTexture.new(width, width, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(pixels), width * 4)
        self.picture.set_paintable(texture)
        self.link.set_label(url)
        self.status.set_label('Waiting for a phone')
        self.button.set_label(NEW_CODE)

    def stop(self):
        if self.service:
            self.service.stop()
            self.service = None

    def on_close(self, _window):
        self.stop()
        return False


def run():
    app = Gtk.Application(application_id=APP_ID)  # a second launch just shows the running window

    def activate(application):
        window = application.get_active_window()
        if window is None:
            css = Gtk.CssProvider()
            css.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            window = Window(application)
        window.present()

    app.connect('activate', activate)
    return app.run([])
