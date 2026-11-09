import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web, ClientSession, ClientTimeout, Fingerprint, WSMsgType, ClientError, TraceConfig

def parse_link(raw):
    try:
        raw = raw.strip()
        if not raw.startswith('https://'):
            raw = 'https://' + raw
        u = urlsplit(raw)
        assert u.hostname and u.hostname.endswith('.trycloudflare.com')
        assert re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', u.hostname)
        return 'https://' + u.hostname + '/', u.hostname, ''
    except Exception:
        raise web.HTTPBadRequest(text='Link Internet non valido. Copia il link completo generato da OFFICE.')

def make_link(values):
    return 'https://' + base64.urlsafe_b64encode(json.dumps(values, separators=(',', ':')).encode()).decode().rstrip('=')

class InternetLink:
    def __init__(self, station, assets):
        self.station, self.assets = station, assets
        self.state = 'off'
        self.error = ''
        self.link = ''
        self.runner = None
        self.session = None
        self.sockets = set()
        self.clients = set()

    @property
    def keep_alive(self):
        return self.state in ('starting', 'on')

    @property
    def status(self):
        return dict(state=self.state, error=self.error, link=self.link, target=self.station.target if self.state == 'on' else '')

    async def connect(self, r):
        # Gestione connessione tunnel proxy
        raise NotImplementedError

    async def gateway(self):
        # Gateway locale per il tunnel
        pass

    async def watch(self, future):
        # Monitoraggio del processo cloudflared
        pass

    async def start(self):
        # Avvio del binario cloudflared
        pass

    async def client_endpoint(self, link):
        return parse_link(link)

    async def clear_clients(self):
        self.clients.clear()

    async def stop(self):
        self.state = 'off'
        self.link = ''
        if self.runner:
            await self.runner.cleanup()
            self.runner = None