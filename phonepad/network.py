"""Finds the address a phone should use, and decides who may connect."""
import ipaddress
import json
import subprocess

_VIRTUAL = ('lo', 'docker', 'br-', 'veth', 'virbr', 'tun', 'tap', 'wg', 'tailscale', 'vnet')


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


def lan_interface():
    """This computer's address on the local network, or None when it isn't on one."""
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
