"""The small web server behind the phone page: one page, and one WebSocket carrying commands.

Everything arrives over the single WebSocket, so moves, clicks and typing stay in order. The commands:
    m dx dy     move the pointer            s dx dy     scroll
    c           left click                  r           right click
    M           middle click                2           double click
    d / u       left button down / up       z n         zoom in (n > 0) or out
    t <text>    type text                   k name [n]  press a key or shortcut, n times
    p           keep-alive (answered "p")

The server sends "p", or "! n" when n typed characters could not be produced.
"""
import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import parse_qs, urlsplit

from . import input as uinput
from .network import is_lan_peer

PORTS = (8765, 8766, 8767, 8768)
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_HEAD = 8192
MAX_FRAME = 16384
MAX_RATE = 300          # moves and scrolls a second; more are dropped
IDLE_TIMEOUT = 30       # seconds; the phone sends a keep-alive every 10
HEAD_TIMEOUT = 10
PHONE_PAGE = os.path.join(os.path.dirname(__file__), 'phone.html')
OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 1, 8, 9, 10


class Closed(Exception):
    """The WebSocket has ended, cleanly or not."""


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


async def read_frame(reader):
    """One client frame as (opcode, payload). Unmasked, fragmented, binary or oversized frames end the connection."""
    b0, b1 = await reader.readexactly(2)
    opcode, length = b0 & 0x0f, b1 & 0x7f
    if b0 & 0x70 or not b0 & 0x80 or not b1 & 0x80 or opcode not in (OP_TEXT, OP_CLOSE, OP_PING, OP_PONG):
        raise Closed
    if length == 126:
        length = struct.unpack('>H', await reader.readexactly(2))[0]
    elif length == 127:
        raise Closed
    if length > MAX_FRAME:
        raise Closed
    mask = await reader.readexactly(4)
    payload = bytearray(await reader.readexactly(length))
    for i in range(length):
        payload[i] ^= mask[i & 3]
    return opcode, bytes(payload)


def server_frame(opcode, payload):
    return bytes((0x80 | opcode, len(payload))) + payload  # always short: "p", "! n" or a ping's payload


class RateLimit:
    """A token bucket: [rate] messages a second on average, bursts up to the same number."""

    def __init__(self, rate, clock=time.monotonic):
        self.rate, self.clock = rate, clock
        self.tokens, self.last = rate, clock()

    def allow(self):
        now = self.clock()
        self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class PhoneServer:
    """Serves one phone at a time. A newer valid connection takes over, so a phone that slept can reconnect."""

    def __init__(self, controller, iface, on_status=lambda text: None, token=None, ports=PORTS, allow_peer=None, mac=False):
        self.controller = controller
        self.iface = iface
        self.mac = mac
        self.host = str(iface.ip)
        self.on_status = on_status
        self.token = token or secrets.token_hex(16)
        self.ports = ports
        self.allow_peer = allow_peer or (lambda peer: is_lan_peer(peer, iface))
        self.server = None
        self.port = 0
        self.current = None     # the writer of the connected phone

    @property
    def url(self):
        return f'http://{self.host}:{self.port}/?t={self.token}'

    async def start(self):
        for port in self.ports:
            try:
                self.server = await asyncio.start_server(self.client, self.host, port, backlog=8, limit=MAX_HEAD)
            except OSError:
                continue
            self.port = self.server.sockets[0].getsockname()[1]
            return
        raise OSError(f'Ports {self.ports[0]}-{self.ports[-1]} are all in use')

    async def stop(self):
        if self.server:
            self.server.close()
        if self.current:
            self.current.close()
        if self.server:
            # Lets the phone's handler release what it holds. A half-open connection mustn't delay quitting.
            try:
                await asyncio.wait_for(self.server.wait_closed(), 1)
            except asyncio.TimeoutError:
                pass
            self.server = None

    def token_ok(self, token):
        """Wrong tokens don't lock anyone out. A phone's old tab keeps retrying with an expired token and
        Safari asks for icons with none, so that would block the phone itself, and nobody can guess 128 bits."""
        return isinstance(token, str) and hmac.compare_digest(token.encode(), self.token.encode())

    async def client(self, reader, writer):
        ip = writer.get_extra_info('peername')[0]
        try:
            if self.allow_peer(ip):  # anyone else just sees the connection close
                await self.request(reader, writer, ip)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError, Closed):
            pass
        finally:
            writer.close()

    async def request(self, reader, writer, ip):
        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), HEAD_TIMEOUT)
        lines = head.decode('latin-1').split('\r\n')
        method, target, _version = lines[0].split(' ', 2)
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(':')
            headers[name.strip().lower()] = value.strip()
        url = urlsplit(target)
        if url.path == '/favicon.ico':
            return await self.reply(writer, 404, 'text/plain', b'')
        token = parse_qs(url.query).get('t', [None])[0]
        if not self.token_ok(token):
            await asyncio.sleep(0.3)
            page = b'<!doctype html><meta name=viewport content="width=device-width"><body style="font:18px sans-serif;padding:24px">' \
                   b'This link has expired. Scan the QR code on your computer again.</body>'
            return await self.reply(writer, 403, 'text/html; charset=utf-8', page)
        # The Host check stops a web page reaching this server through a rebound DNS name.
        if headers.get('host') != f'{self.host}:{self.port}':
            return await self.reply(writer, 400, 'text/plain', b'Bad host')
        if method == 'GET' and url.path == '/':
            with open(PHONE_PAGE, 'rb') as page:
                body = page.read()
            if self.mac:
                body = body.replace(b'<body>', b'<body class="mac">')
            return await self.reply(writer, 200, 'text/html; charset=utf-8', body)
        if method == 'GET' and url.path == '/ws':
            return await self.websocket(reader, writer, ip, headers)
        return await self.reply(writer, 404, 'text/plain', b'Not found')

    async def reply(self, writer, code, content_type, body):
        reason = {200: 'OK', 400: 'Bad Request', 403: 'Forbidden', 404: 'Not Found'}[code]
        policy = f"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src ws://{self.host}:{self.port}; frame-ancestors 'none'"
        writer.write((f'HTTP/1.1 {code} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n'
                      f'Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n'
                      f'Content-Security-Policy: {policy}\r\nConnection: close\r\n\r\n').encode() + body)
        await writer.drain()

    async def websocket(self, reader, writer, ip, headers):
        key = headers.get('sec-websocket-key', '')
        origin = headers.get('origin')
        if (headers.get('upgrade', '').lower() != 'websocket' or headers.get('sec-websocket-version') != '13'
                or not key or (origin and origin != f'http://{self.host}:{self.port}')):
            return await self.reply(writer, 400, 'text/plain', b'Bad upgrade')
        if self.current:
            self.current.close()  # its handler releases anything it was holding
        self.current = writer
        writer.write((f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                      f'Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n').encode())
        await writer.drain()
        self.on_status(f'Phone connected ({ip})')
        limit = RateLimit(MAX_RATE)
        try:
            while True:
                opcode, payload = await asyncio.wait_for(read_frame(reader), IDLE_TIMEOUT)
                if opcode == OP_CLOSE:
                    return
                if opcode == OP_PING:
                    writer.write(server_frame(OP_PONG, payload[:125]))
                if opcode == OP_TEXT:
                    message = payload.decode('utf-8', 'replace')
                    # Only moves are dropped in a flood: losing a button release would leave it held.
                    if message[:2] not in ('m ', 's ') or limit.allow():
                        await self.command(message, writer)
                        await writer.drain()
        finally:
            self.controller.release_all()
            if self.current is writer:
                self.current = None
                self.on_status('Waiting for a phone')

    async def command(self, message, writer):
        """Runs one command. Anything malformed is ignored."""
        op, _, rest = message.partition(' ')
        ctl = self.controller
        try:
            if op == 'm':
                dx, dy = self.delta(rest)
                ctl.move(dx, dy)
            elif op == 's':
                dx, dy = self.delta(rest)
                ctl.scroll(dx, dy)
            elif op == 'c':
                ctl.click()
            elif op == 'r':
                ctl.click(uinput.BTN_RIGHT)
            elif op == 'M':
                ctl.click(uinput.BTN_MIDDLE)
            elif op == '2':
                ctl.click()
                await asyncio.sleep(0.03)
                ctl.click()
            elif op == 'd':
                ctl.button(uinput.BTN_LEFT, True)
            elif op == 'u':
                ctl.button(uinput.BTN_LEFT, False)
            elif op == 'z':
                await ctl.zoom(int(rest))
            elif op == 't':
                skipped = await ctl.type_text(rest)
                if skipped:
                    writer.write(server_frame(OP_TEXT, f'! {skipped}'.encode()))
            elif op == 'k':
                name, _, count = rest.partition(' ')
                await ctl.combo(name, int(count or 1))
            elif op == 'p':
                writer.write(server_frame(OP_TEXT, b'p'))
        except ValueError:
            pass

    @staticmethod
    def delta(text):
        dx, dy = text.split()
        limit = uinput.MAX_DELTA
        return max(-limit, min(limit, int(dx))), max(-limit, min(limit, int(dy)))
