"""Read Windows network configuration; never alter routes, DNS or firewall rules."""
import asyncio[cite: 20]
import ctypes[cite: 20]
import ipaddress[cite: 20]
import re[cite: 20]
import socket[cite: 20]
import struct[cite: 20]
import sys[cite: 20]
import time[cite: 20]
import json[cite: 20]
from java import jclass[cite: 20]

PRIVATE = tuple(ipaddress.ip_network(n) for n in
                ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','169.254.0.0/16'))[cite: 20]
_indices={}[cite: 20]
_indices_at=0[cite: 20]


def address(value):
    ip=ipaddress.ip_address(value)
    if ip.is_multicast or ip.is_unspecified:raise ValueError('Not a station address')
    if '%' in str(ip) and not str(ip).split('%')[1].isdigit():raise ValueError('Invalid interface scope')
    return str(ip)[cite: 20]


def local_address(value):
    try:
        ip=ipaddress.ip_address(address(value))
        return (ip.version==4 and any(ip in n for n in PRIVATE)) or (ip.version==6 and ip.is_private and not ip.is_loopback)
    except ValueError:return False[cite: 20]


def peer_address(addr):
    value=addr[0]
    if len(addr)>3 and addr[3] and ipaddress.ip_address(value).is_link_local:
        value=value.split('%')[0]+'%'+str(addr[3])
    return address(value)[cite: 20]


def url_host(ip):
    return '['+ip+']' if ':' in ip else ip[cite: 20]


def sockaddr(ip,port):
    if ':' not in ip:return ip,port
    host,_,scope=ip.partition('%')
    return host,port,0,int(scope or 0)[cite: 20]


def snapshot():
    return json.loads(str(jclass('it.hgxyzn.office.Native').networkSnapshot()))[cite: 20]

def adapters():return snapshot()['adapters'][cite: 20]
def routes():return snapshot()['routes'][cite: 20]
def dns_config(active):
    s=snapshot()
    return s['dns'],s['suffixes'][cite: 20]


def v4_links(topology):
    return {a['ip']:ipaddress.ip_interface(a['ip']+'/'+str(a['prefix'])).network
            for c in topology['adapters'] for a in c['addresses'] if ':' not in a['ip'] and 0<a['prefix']<=32}[cite: 20]


def routed_networks(topology):
    active={c['index'] for c in topology['adapters']}
    result=set()
    links=v4_links(topology)
    own=set(links)|{str(n.broadcast_address) for n in links.values() if n.prefixlen<31}
    for row in topology['routes']:
        net=ipaddress.ip_network(row['network'])
        if row['index'] in active and net.prefixlen and str(net.network_address) not in own and any(net.subnet_of(block) for block in PRIVATE):
            result.add(net)
    return result[cite: 20]


def paths_for(ip,topology):
    """Keep equally valid adapters (including overlapping VPN/LAN prefixes)."""
    target=ipaddress.ip_address(ip)
    if target.version==6:
        scope=str(target).partition('%')[2]
        return [a['ip'] for c in topology['adapters'] for a in c['addresses']
                if ':' in a['ip'] and (not scope or str(c['index'])==scope)
                and ipaddress.ip_address(a['ip']).is_link_local==target.is_link_local][:4] or [None]
    cards=topology['adapters']
    onlink=[a['ip'] for c in cards for a in c['addresses'] if ':' not in a['ip']
            and target in ipaddress.ip_interface(a['ip']+'/'+str(a['prefix'])).network]
    if onlink:return onlink[:4]
    matching=[r for r in topology['routes'] if target in ipaddress.ip_network(r['network'])]
    matching.sort(key=lambda r:(-ipaddress.ip_network(r['network']).prefixlen,r['metric']))
    result=[]
    for row in matching:
        for c in cards:
            if c['index']!=row['index']:continue
            for a in c['addresses']:
                if ':' not in a['ip'] and a['ip'] not in result:result.append(a['ip'])
    return result[:4] or [None][cite: 20]


async def open_tcp(ip,port,source=None,limit=8192):
    global _indices,_indices_at
    family=socket.AF_INET6 if ':' in ip else socket.AF_INET
    sock=socket.socket(family,socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        if source:
            if sys.platform=='win32' and family==socket.AF_INET:
                if time.monotonic()-_indices_at>3:
                    _indices={a['ip']:c['index'] for c in adapters() for a in c['addresses']}
                    _indices_at=time.monotonic()
                index=_indices.get(source)
                if index:sock.setsockopt(socket.IPPROTO_IP,31,socket.htonl(index))
            sock.bind(sockaddr(source,0))
        await asyncio.get_running_loop().sock_connect(sock,sockaddr(ip,port))
        return await asyncio.open_connection(sock=sock,limit=limit)
    except BaseException:
        sock.close()
        raise[cite: 20]


async def dns_addresses(names,topology):
    """Resolve remembered PC names and optional office DNS-SD records; no DNS writes."""
    import dns.asyncresolver
    resolvers=[dns.asyncresolver.Resolver()]
    local_servers=[s for s in topology['dns'] if local_address(s)]
    for server in local_servers[:4]:
        resolver=dns.asyncresolver.Resolver(configure=False)
        resolver.nameservers=[server]
        resolvers.append(resolver)
    result=set()
    gate=asyncio.Semaphore(12)
    async def query(resolver,name,kind):
        async with gate:
            try:return list(await resolver.resolve(name,kind,lifetime=1.2,search=False))
            except Exception:return []
    valid={n for n in names if isinstance(n,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,250}',n)}
    for name in list(valid):
        if '.' not in name:
            valid.update(name+'.'+suffix for suffix in topology['suffixes'])
    for resolver in resolvers:
        for suffix in topology['suffixes']:
            for ptr in (await query(resolver,'_hgxyzn-office._tcp.'+suffix,'PTR'))[:32]:
                for srv in (await query(resolver,str(ptr.target),'SRV'))[:2]:
                    if srv.port==48624:valid.add(str(srv.target).rstrip('.'))
    async def resolve(resolver,name,kind):
        for item in await query(resolver,name,kind):
            value=str(item)
            if local_address(value):result.add(value)
    await asyncio.gather(*(resolve(r,n,t) for r in resolvers for n in sorted(valid)[:64] for t in ('A','AAAA')))
    return result[cite: 20]