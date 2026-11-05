"""Finds the address a phone should use, and decides who may connect."""
import ipaddress
import json
import re
import subprocess
import sys

_VIRTUAL = ('lo', 'docker', 'br-', 'veth', 'virbr', 'tun', 'tap', 'wg', 'tailscale', 'vnet',
            'utun', 'awdl', 'llw', 'bridge', 'anpi', 'gif', 'stf', 'ap')


def _ip_json(*args):
    return json.loads(subprocess.run(['ip', '-j', *args], capture_output=True, text=True, timeout=3, check=True).stdout or '[]')


def pick_interface(addresses, default_dev=None):
    """From `ip -j -4 addr` output: the private address on the default route, else the first private one.

    Skips VM, container and VPN interfaces, whose addresses a phone can't reach. Returns an
    ipaddress.IPv4Interface or None.
    """
    found = []
    for link in addresses:
        name = link.get('ifname', '')
        if name.startswith(_VIRTUAL) or link.get('operstate') == 'DOWN':
            continue
        for addr in link.get('addr_info', []):
            iface = ipaddress.ip_interface(f"{addr['local']}/{addr['prefixlen']}")
            if iface.is_private and not iface.is_loopback:
                found.append((name != default_dev, iface))
    found.sort(key=lambda item: item[0])
    return found[0][1] if found else None


def parse_ifconfig(text):
    """macOS `ifconfig` output, in the same form as `ip -j -4 addr`."""
    links = []
    for line in text.splitlines():
        header = re.match(r'(\S+): flags=\w+<([^>]*)>', line)
        inet = re.match(r'\s+inet (\S+) netmask 0x([0-9a-f]+)', line)
        if header:
            links.append({'ifname': header[1], 'operstate': 'UP' if 'UP' in header[2].split(',') else 'DOWN', 'addr_info': []})
        elif inet and links:
            links[-1]['addr_info'].append({'local': inet[1], 'prefixlen': bin(int(inet[2], 16)).count('1')})
    return links


def parse_default_route(text):
    """The interface in macOS `route -n get default` output, or None."""
    match = re.search(r'^\s*interface: (\S+)', text, re.M)
    return match[1] if match else None


def _run(*command):
    return subprocess.run(command, capture_output=True, text=True, timeout=3).stdout


def lan_interface():
    """This computer's address on the local network, or None when it isn't on one."""
    if sys.platform == 'darwin':
        try:
            return pick_interface(parse_ifconfig(_run('/sbin/ifconfig')), parse_default_route(_run('/sbin/route', '-n', 'get', 'default')))
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    try:
        addresses = _ip_json('-4', 'addr')
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    try:
        default_dev = _ip_json('route', 'get', '1.1.1.1')[0]['dev']
    except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError):
        default_dev = None  # no internet is fine: a phone hotspot or offline router still works
    return pick_interface(addresses, default_dev)


def is_lan_peer(peer, iface):
    """True for another device on [iface]'s subnet. This computer itself is refused, so that local
    programs can't drive the keyboard through this server."""
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return address != iface.ip and address in iface.network
