"""Runs the server on its own thread, so the window stays responsive, and owns the virtual devices."""
import asyncio
import os
import re
import secrets
import tempfile
import threading

from . import input as uinput
from . import qr
from .network import lan_interface
from .server import PhoneServer

NO_ACCESS = ('PhonePad can\'t open /dev/uinput, which it needs to act as a keyboard and mouse.\n\n'
             'Installing the PhonePad package fixes this (log out and back in afterwards). When running from\n'
             'the source folder, install packaging/60-phonepad-uinput.rules into /etc/udev/rules.d/.')
NO_NETWORK = 'This computer isn\'t connected to a local network. Join your Wi-Fi, then try again.'


def token_path():
    config = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(config, 'phonepad', 'token')


def new_token(path):
    """Makes a pairing code and saves it where only this user can read it. If it can't be saved, the
    code still works until PhonePad closes."""
    token = secrets.token_hex(16)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path))  # mkstemp makes it private to this user
        with os.fdopen(fd, 'w') as file:
            file.write(token)
        os.replace(temporary, path)
    except OSError:
        pass
    return token


def load_token(path):
    """The saved pairing code, or a new one if there is none or it is damaged."""
    try:
        with open(path, encoding='ascii') as file:
            token = file.read().strip()
        os.chmod(path, 0o600)
    except (OSError, ValueError):
        return new_token(path)
    return token if re.fullmatch('[0-9a-f]{32}', token) else new_token(path)


class StartError(Exception):
    """A problem to show to the user as it is."""


class Service:
    def __init__(self, on_status=lambda text: None):
        self.on_status = on_status
        self.loop = None
        self.thread = None
        self.server = None
        self.controller = None

    def start(self, new_code=False):
        """Starts serving, and returns the pairing link. Raises StartError."""
        iface = lan_interface()
        if iface is None:
            raise StartError(NO_NETWORK)
        try:
            self.controller = uinput.Controller(*uinput.open_devices())
        except OSError as e:
            raise StartError(NO_ACCESS) from e
        token = new_token(token_path()) if new_code else load_token(token_path())
        self.server = PhoneServer(self.controller, iface, self.on_status, token=token)
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self.server.start())
        except OSError as e:
            self.controller.close()
            self.loop.close()
            self.loop = None
            raise StartError(str(e)) from e
        self.thread = threading.Thread(target=self.loop.run_forever, name='phonepad-server', daemon=True)
        self.thread.start()
        return self.server.url

    async def shutdown(self):
        await self.server.stop()
        leftovers = asyncio.all_tasks() - {asyncio.current_task()}  # half-open connections
        for task in leftovers:
            task.cancel()
        await asyncio.gather(*leftovers, return_exceptions=True)

    def stop(self):
        if not self.thread:
            return
        asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()
        self.controller.close()
        self.thread = self.loop = None


def run_in_terminal():
    service = Service(lambda text: print(text, flush=True))
    try:
        url = service.start()
    except StartError as e:
        print(e, flush=True)
        return 1
    print(qr.to_terminal(qr.encode(url)))
    print(f'\nScan the code, or open {url}\nPress Ctrl+C to stop.', flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0
