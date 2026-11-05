"""Run with: python3 -m unittest discover tests"""
import asyncio
import base64
import ipaddress
import os
import struct
import tempfile
import unittest

from phonepad import input as uinput
from phonepad import network, qr, server, service


class FakeDevice:
    def __init__(self):
        self.events = []

    def emit(self, events):
        self.events += events

    def close(self):
        pass


def controller():
    return uinput.Controller(FakeDevice(), FakeDevice())


def typed(ctl):
    """Rebuilds the text from key-down events, as a US keyboard would produce it."""
    reverse = {entry: char for char, entry in uinput.CHARS.items()}
    shift, out = False, ''
    for _, code, value in ctl.keyboard.events:
        if code == uinput.KEY_LEFTSHIFT:
            shift = bool(value)
        elif value:
            out += reverse[code, shift]
    return out


class Typing(unittest.TestCase):
    def test_every_ascii_character_round_trips(self):
        ctl = controller()
        text = ''.join(map(chr, range(32, 127))) + '\n\t'
        self.assertEqual(asyncio.run(ctl.type_text(text)), 0)
        self.assertEqual(typed(ctl), text)
        self.assertEqual(ctl.held_keys, set())

    def test_smart_punctuation_becomes_plain_and_the_rest_is_counted(self):
        ctl = controller()
        self.assertEqual(asyncio.run(ctl.type_text('it’s…é😀')), 2)
        self.assertEqual(typed(ctl), "it's...")

    def test_combos(self):
        self.assertEqual(uinput.parse_combo('ctrl+c'), ([uinput.KEY_LEFTCTRL], 46))
        self.assertEqual(uinput.parse_combo('super'), ([], uinput.KEY_LEFTMETA))
        self.assertIsNone(uinput.parse_combo('ctrl+nonsense'))
        self.assertIsNone(uinput.parse_combo('hyper+c'))

    def test_every_key_the_page_can_send_is_known_to_the_device(self):
        page = open(server.PHONE_PAGE, encoding='utf-8').read()
        import re
        for name in re.findall(r'data-k="([^"]+)"', page):
            parsed = uinput.parse_combo(name)
            self.assertIsNotNone(parsed, name)
            for code in parsed[0] + [parsed[1]]:
                self.assertIn(code, uinput.KEYBOARD_KEYS, name)


class Scrolling(unittest.TestCase):
    def test_small_scrolls_add_up_to_a_notch(self):
        ctl = controller()
        for _ in range(30):
            ctl.scroll(0, 1)  # 4 units down each, 120 in all
        notches = [e for e in ctl.mouse.events if e[1] == uinput.REL_WHEEL]
        self.assertEqual(notches, [(uinput.EV_REL, uinput.REL_WHEEL, -1)])

    def test_zoom_holds_ctrl_around_a_whole_notch(self):
        ctl = controller()
        asyncio.run(ctl.zoom(1))
        self.assertIn((uinput.EV_REL, uinput.REL_WHEEL, 1), ctl.mouse.events)
        self.assertEqual(ctl.held_keys, set())


class Network(unittest.TestCase):
    ADDRESSES = [
        {'ifname': 'lo', 'operstate': 'UNKNOWN', 'addr_info': [{'local': '127.0.0.1', 'prefixlen': 8}]},
        {'ifname': 'virbr0', 'operstate': 'UP', 'addr_info': [{'local': '192.168.122.1', 'prefixlen': 24}]},
        {'ifname': 'eth1', 'operstate': 'UP', 'addr_info': [{'local': '10.0.0.5', 'prefixlen': 24}]},
        {'ifname': 'wlp1s0', 'operstate': 'UP', 'addr_info': [{'local': '192.168.1.27', 'prefixlen': 24}]},
    ]

    def test_prefers_the_default_route_and_skips_virtual_interfaces(self):
        self.assertEqual(str(network.pick_interface(self.ADDRESSES, 'wlp1s0').ip), '192.168.1.27')
        self.assertEqual(str(network.pick_interface(self.ADDRESSES[:2], None) or ''), '')

    def test_only_other_devices_on_the_subnet_may_connect(self):
        iface = ipaddress.ip_interface('192.168.1.27/24')
        self.assertTrue(network.is_lan_peer('192.168.1.40', iface))
        self.assertFalse(network.is_lan_peer('192.168.1.27', iface))
        self.assertFalse(network.is_lan_peer('127.0.0.1', iface))
        self.assertFalse(network.is_lan_peer('192.168.2.40', iface))
        self.assertFalse(network.is_lan_peer('8.8.8.8', iface))


class QR(unittest.TestCase):
    def test_sizes_and_limit(self):
        self.assertEqual(len(qr.encode('a' * 17)), 21)
        self.assertEqual(len(qr.encode('a' * 106)), 37)
        with self.assertRaises(ValueError):
            qr.encode('a' * 107)

    def test_reed_solomon_matches_the_standard_worked_example(self):
        # ISO/IEC 18004 annex I: "01234567" at version 1-M.
        data = [16, 32, 12, 86, 97, 128, 236, 17, 236, 17, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.reed_solomon(data, 10), [165, 36, 212, 193, 237, 54, 199, 135, 44, 85])


class PairingCode(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, 'config', 'phonepad', 'token')

    def test_the_code_survives_a_restart_and_only_new_code_changes_it(self):
        first = service.load_token(self.path)
        self.assertRegex(first, '^[0-9a-f]{32}$')
        self.assertEqual(service.load_token(self.path), first)
        second = service.new_token(self.path)
        self.assertNotEqual(second, first)
        self.assertEqual(service.load_token(self.path), second)

    def test_only_this_user_can_read_it(self):
        service.load_token(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.path)).st_mode & 0o777, 0o700)
        os.chmod(self.path, 0o644)
        service.load_token(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_damaged_file_gets_a_new_code(self):
        first = service.load_token(self.path)
        for damaged in (b'', b'short', b'\xff\xfe' * 16):
            with open(self.path, 'wb') as file:
                file.write(damaged)
            self.assertRegex(service.load_token(self.path), '^[0-9a-f]{32}$')
            self.assertNotEqual(service.load_token(self.path), first)

    def test_a_code_that_can_not_be_saved_still_works(self):
        blocked = os.path.join(os.path.dirname(os.path.dirname(self.path)), 'file')
        os.makedirs(os.path.dirname(blocked))
        open(blocked, 'w').close()
        self.assertRegex(service.load_token(os.path.join(blocked, 'token')), '^[0-9a-f]{32}$')


TOKEN = 'a' * 32


class Client:
    """A minimal WebSocket client, as a phone would behave."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, srv, token=TOKEN, origin=None, host=None):
        reader, writer = await asyncio.open_connection('127.0.0.1', srv.port)
        key = base64.b64encode(os.urandom(16)).decode()
        head = (f'GET /ws?t={token} HTTP/1.1\r\nHost: {host or f"127.0.0.1:{srv.port}"}\r\nUpgrade: websocket\r\n'
                f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n')
        if origin:
            head += f'Origin: {origin}\r\n'
        writer.write((head + '\r\n').encode())
        status = await reader.readuntil(b'\r\n\r\n')
        return cls(reader, writer), status.split(b' ')[1], status

    def send(self, text):
        data = text.encode()
        mask = b'\x01\x02\x03\x04'
        length = bytes([0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | 126]) + struct.pack('>H', len(data))
        self.writer.write(bytes([0x81]) + length + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))

    async def receive(self):
        _, length = await self.reader.readexactly(2)
        return await self.reader.readexactly(length)


class Server(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ctl = controller()
        iface = ipaddress.ip_interface('127.0.0.1/8')
        self.srv = server.PhoneServer(self.ctl, iface, token=TOKEN, ports=(0,), allow_peer=lambda peer: True)
        await self.srv.start()

    async def asyncTearDown(self):
        await self.srv.stop()

    async def get(self, path, host=None):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.srv.port)
        writer.write(f'GET {path} HTTP/1.1\r\nHost: {host or f"127.0.0.1:{self.srv.port}"}\r\n\r\n'.encode())
        response = await reader.read()
        writer.close()
        return response

    async def test_page_needs_the_token(self):
        self.assertIn(b' 200 ', await self.get(f'/?t={TOKEN}'))
        self.assertIn(b' 403 ', await self.get('/?t=wrong'))
        self.assertIn(b' 403 ', await self.get('/'))

    async def test_wrong_host_is_refused(self):
        self.assertIn(b' 400 ', await self.get(f'/?t={TOKEN}', host='evil.example'))

    async def test_an_old_tab_retrying_does_not_lock_out_a_fresh_scan(self):
        for path in ('/?t=old', '/?t=old', '/?t=old', '/apple-touch-icon.png', '/apple-touch-icon-precomposed.png', '/?t=old'):
            self.assertIn(b' 403 ', await self.get(path))
        self.assertIn(b' 200 ', await self.get(f'/?t={TOKEN}'))

    async def test_commands_reach_the_input_devices_in_order(self):
        client, code, _ = await Client.open(self.srv)
        self.assertEqual(code, b'101')
        client.send('m 5 -3')
        client.send('c')
        client.send('t Hi')
        client.send('k backspace 2')
        client.send('p')
        self.assertEqual(await client.receive(), b'p')
        self.assertEqual(self.ctl.mouse.events, [
            (uinput.EV_REL, uinput.REL_X, 5), (uinput.EV_REL, uinput.REL_Y, -3),
            (uinput.EV_KEY, uinput.BTN_LEFT, 1), (uinput.EV_KEY, uinput.BTN_LEFT, 0)])
        downs = [c for _, c, v in self.ctl.keyboard.events if v]
        self.assertEqual(downs, [uinput.KEY_LEFTSHIFT, 35, 23, uinput.KEY_BACKSPACE, uinput.KEY_BACKSPACE])
        client.writer.close()

    async def test_untypable_characters_are_reported(self):
        client, _, _ = await Client.open(self.srv)
        client.send('t 😀')
        self.assertEqual(await client.receive(), b'! 1')
        client.writer.close()

    async def test_malformed_commands_are_ignored(self):
        client, _, _ = await Client.open(self.srv)
        for bad in ('m', 'm x y', 'm 1', 'z big', 'k nonsense', 'k enter x', '?', ''):
            client.send(bad)
        client.send('p')
        self.assertEqual(await client.receive(), b'p')
        self.assertEqual(self.ctl.mouse.events + self.ctl.keyboard.events, [])
        client.writer.close()

    async def test_disconnecting_releases_a_held_button(self):
        client, _, _ = await Client.open(self.srv)
        client.send('d')
        client.send('p')
        await client.receive()
        self.assertEqual(self.ctl.held_buttons, {uinput.BTN_LEFT})
        client.writer.close()
        for _ in range(100):
            if not self.ctl.held_buttons:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.ctl.held_buttons, set())

    async def test_a_new_connection_replaces_the_old_one(self):
        first, _, _ = await Client.open(self.srv)
        second, code, _ = await Client.open(self.srv)
        self.assertEqual(code, b'101')
        self.assertEqual(await first.reader.read(), b'')
        second.send('p')
        self.assertEqual(await second.receive(), b'p')
        second.writer.close()

    async def test_another_sites_page_can_not_connect(self):
        _, code, _ = await Client.open(self.srv, origin='http://evil.example')
        self.assertEqual(code, b'400')

    async def test_refused_peers_see_a_closed_connection(self):
        self.srv.allow_peer = lambda peer: False
        try:
            response = await self.get(f'/?t={TOKEN}')
        except ConnectionResetError:
            response = b''
        self.assertEqual(response, b'')


if __name__ == '__main__':
    unittest.main()
