import asyncio
import ctypes
import ipaddress
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import time
from collections import deque
import network_topology as network
from return_link import OFFER, RECEIPT, ReturnLinks, decode_peer

DISCOVERY = 48624
QUERY = b'HGXYZN-OFFICE/1 DISCOVER '
GROUP = '239.255.72.24'
GROUP6 = 'ff12::be10'
PRIVATE = network.PRIVATE


def note_request(station, ip):
    if len(station.discovery_leads) >= 64 and ip not in station.discovery_leads:
        station.discovery_leads.pop(next(iter(station.discovery_leads)))
    station.discovery_leads[ip] = time.monotonic()


def presence(station, request, backlink=''):
    if not request.startswith(QUERY) or not re.fullmatch(rb'[a-f0-9]{24}', request[len(QUERY):]):
        return None
    hosting = station.room_runner is not None
    role = 'host' if hosting else ('guest' if station.target else 'idle')
    hints = list(getattr(station, 'advertised_addresses', []))[:8]
    for peer in station.peers.values():
        if peer.get('available') and len(hints) < 12:
            hints.extend(
                a for a in peer.get('addresses', [])[:4]
                if network.local_address(a) and '%' not in a and a not in hints
            )
    return json.dumps(
        dict(
            v=2,
            backlink=backlink,
            receipt=request[len(QUERY):].decode(),
            query=station.cfg['id'],
            addresses=station.cfg['name'],
            id=socket.gethostname(),
            name=hints[:12],
            hostname=role,
            available=hosting,
            role=station.port if hosting else 0,
            port=station.fp if hosting else '',
        )
    ).encode()


class Beacon(asyncio.DatagramProtocol):
    def __init__(self, station):
        self.station = station

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.startswith(RECEIPT):
            self.station.links.accept_receipt(data, addr)
            return
        nonce = data[len(QUERY):]
        if not data.startswith(QUERY) or not re.fullmatch(rb'[a-f0-9]{24}', nonce):
            return
        reply = presence(self.station, data, self.station.links.backlink(addr, nonce.decode()))
        if reply:
            note_request(self.station, network.peer_address(addr))
            self.transport.sendto(reply, addr)


async def listen(station):
    loop = asyncio.get_running_loop()
    station.discovery_leads = {}
    station.multicast_interfaces = set()
    station.multicast6_interfaces = set()
    station.beacon = station.beacon6 = None
    station.discovery_errors = []
    station.topology = {'adapters': [], 'routes': [], 'dns': [], 'suffixes': []}
    station.dns_leads = set()
    station.dns_task = None
    station.links = ReturnLinks(station, DISCOVERY, lambda s, addr: presence(s, QUERY + addr))
    station.discovery_cache = {}
    try:
        raw = json.loads((station.home / 'discovery-cache.json').read_text('utf8'))
        if isinstance(raw, dict):
            station.discovery_cache = {
                k: v for k, v in list(raw.items())[:128]
                if isinstance(v, dict) and time.monotonic() - v.get('seen', 0) < 604800
            }
    except (OSError, ValueError, TypeError):
        pass

    async def identify(reader, writer):
        try:
            try:
                data = await asyncio.wait_for(reader.readline(), 1.5)
                if data.strip() == OFFER + station.cfg['id'].encode('ascii'):
                    await station.links.accept_offer(reader, writer)
                    return
                reply = presence(station, data.strip())
                if reply:
                    note_request(station, network.peer_address(writer.get_extra_info('peername')))
                    writer.write(reply + b'\n')
                    await writer.drain()
            except (OSError, ValueError, asyncio.TimeoutError):
                pass
        finally:
            writer.close()

    configs = [
        (
            socket.AF_INET6 if ':' in station.discovery_bind else socket.AF_INET,
            station.discovery_bind,
            'beacon',
            'discovery_tcp',
        ),
        (socket.AF_INET6, '::', 'beacon6', 'discovery_tcp6'),
    ]
    for family, bind, udp_attr, tcp_attr in configs:
        if bind == '::' and station.discovery_bind != '0.0.0.0':
            continue
        for kind in ('UDP', 'TCP'):
            sock = None
            try:
                sock = socket.socket(
                    family, socket.SOCK_DGRAM if kind == 'UDP' else socket.SOCK_STREAM
                )
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                if sys.platform != 'win32':
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(network.sockaddr(bind, DISCOVERY))
                sock.setblocking(False)
                if kind == 'UDP':
                    transport, _ = await loop.create_datagram_endpoint(
                        lambda: Beacon(station), sock=sock
                    )
                    setattr(station, udp_attr, transport)
                else:
                    sock.listen(64)
                    setattr(
                        station,
                        tcp_attr,
                        await asyncio.start_server(identify, sock=sock, limit=4096),
                    )
                sock = None
            except OSError as e:
                station.discovery_errors.append(
                    f"{kind} IPv{6 if family == socket.AF_INET6 else 4}: {e}"
                )
            finally:
                if sock:
                    sock.close()


def nearby_addresses():
    if sys.platform == 'win32':
        size = ctypes.c_ulong(0)
        table = ctypes.windll.iphlpapi.GetIpNetTable
        if table(None, ctypes.byref(size), False) not in (0, 122) or not (4 <= size.value <= 4_000_000):
            return []
        buffer = ctypes.create_string_buffer(size.value)
        if table(buffer, ctypes.byref(size), False) != 0:
            return []
        raw = buffer.raw
        count = min(struct.unpack_from('<I', raw, 0)[0], (len(raw) - 4) // 24)
        return [
            socket.inet_ntoa(raw[4 + i * 24 + 16 : 4 + i * 24 + 20])
            for i in range(count)
            if struct.unpack_from('<I', raw, 4 + i * 24 + 20)[0] in (3, 4)
        ]
    else:
        try:
            out = subprocess.check_output(['/usr/sbin/arp', '-an'], text=True, timeout=2)
            return re.findall(r'\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+(?!\(incomplete\))', out)
        except Exception:
            return []


def interfaces():
    return network.v4_links(network.snapshot())


def join_multicast(station, links):
    if station.beacon:
        sock = station.beacon.get_extra_info('socket')
        for source in station.multicast_interfaces - set(links):
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_DROP_MEMBERSHIP,
                    socket.inet_aton(GROUP) + socket.inet_aton(source),
                )
            except OSError:
                pass
            station.multicast_interfaces.discard(source)
        for source in set(links) - station.multicast_interfaces:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    socket.inet_aton(GROUP) + socket.inet_aton(source),
                )
            except OSError:
                continue
            station.multicast_interfaces.add(source)
    if station.beacon6:
        sock = station.beacon6.get_extra_info('socket')
        indices = {
            c['index']
            for c in station.topology['adapters']
            if any(':' in a['ip'] for a in c['addresses'])
        }
        for idx in station.multicast6_interfaces ^ indices:
            opt = (
                socket.IPV6_LEAVE_GROUP
                if idx in station.multicast6_interfaces
                else socket.IPV6_JOIN_GROUP
            )
            try:
                sock.setsockopt(
                    socket.IPPROTO_IPV6,
                    opt,
                    socket.inet_pton(socket.AF_INET6, GROUP6) + struct.pack('@I', idx),
                )
            except OSError:
                continue
            if opt == socket.IPV6_JOIN_GROUP:
                station.multicast6_interfaces.add(idx)
            else:
                station.multicast6_interfaces.discard(idx)


def page_addresses(station, nets, budget, links, label):
    cursors = getattr(station, label, {})
    cursors = {net: cursors.get(net, 0) for net in nets}
    setattr(station, label, cursors)
    result = set()
    page = max(8, budget // max(1, len(nets)))
    for net, cursor in cursors.items():
        if links is not None:
            local = min(
                (int(ipaddress.ip_address(src)) for src, n in links.items() if n == net),
                default=int(net.network_address),
            )
            cursor = (local & -256) - int(net.network_address) + (cursor % 256)
            count = min(page, 256)
        else:
            count = min(page, net.num_addresses)
        for i in range(count):
            addr = net[(cursor + i) % net.num_addresses]
            if net.prefixlen < 31 and addr in (net.network_address, net.broadcast_address):
                continue
            if str(addr) not in links:
                result.add(str(addr))
        cursors[net] = (cursor + count) % net.num_addresses
    return result


def targets(station, links, routed):
    now = time.monotonic()
    manual = {
        src: {'255.255.255.255', str(net.broadcast_address)}
        for src, net in links.items()
    }
    if not manual:
        manual[station.discovery_bind] = {'255.255.255.255'}
    sources = {addr for addr, t in station.discovery_leads.items() if now - t < 90}
    leads = (
        sources
        | set(station.cfg.get('last_hosts', []))
        | {p['ip'] for p in station.peers.values() if 'ip' in p}
    )
    leads.update(getattr(station, 'dns_leads', set()))
    for p in getattr(station, 'discovery_cache', {}).values():
        leads.update(p.get('addresses', [])[:4])
    leads.update(nearby_addresses()[:128])
    candidates = set()
    for addr in leads:
        try:
            ip = ipaddress.ip_address(network.clean_ip(addr))
        except ValueError:
            continue
        if addr not in links and (
            network.local_address(addr)
            or (ip.version == 4 and any(ip in net for net in links.values()))
        ):
            candidates.add(addr)
    station.priority_targets = set(candidates)
    routed_all = set(links.values()) | routed
    cached = {net for net in routed_all if any(net.subnet_of(block) for block in PRIVATE)}
    candidates.update(
        page_addresses(station, cached, 96 if sources else 128, links, 'discovery_cursors')
    )
    sites = {
        ipaddress.ip_network(f'{src}/16', strict=False)
        for src in links
        if any(ipaddress.ip_address(src) in b for b in PRIVATE[:2])
    }
    invalid = {
        net for net in sites if not any(net.subnet_of(c) for c in routed_all)
    }
    candidates.update(
        page_addresses(station, invalid, 32 if sources else 96, None, 'site_cursors')
    )
    direct = {
        str(addr)
        for block in links.values()
        if block.prefixlen < 31
        for addr in (block.network_address, block.broadcast_address)
    }
    candidates -= direct | set(links)
    return manual, candidates


async def refresh_dns(station):
    names = {
        p.get('hostname', '') for p in station.discovery_cache.values()
    } | {p.get('hostname', '') for p in station.peers.values()}
    try:
        station.dns_leads = await asyncio.wait_for(
            network.dns_addresses(names, station.topology), 1.5
        )
        station.dns_error = ''
    except Exception as exc:
        station.dns_error = f'{type(exc).__name__}: {exc}'


def save_cache(station):
    for peer in station.peers.values():
        if peer.get('available'):
            station.discovery_cache[peer['id']] = {
                'addresses': peer.get('addresses', [])[:8],
                'hostname': peer.get('hostname', ''),
                'seen': time.monotonic(),
            }
    station.discovery_cache = dict(
        sorted(
            station.discovery_cache.items(),
            key=lambda item: item[1]['seen'],
            reverse=True,
        )[:128]
    )
    try:
        path = station.home / 'discovery-cache.json'
        tmp = path.with_suffix('.new')
        tmp.write_text(json.dumps(station.discovery_cache), encoding='utf8')
        os.replace(tmp, path)
    except OSError:
        pass