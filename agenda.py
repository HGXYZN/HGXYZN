import asyncio
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from aiohttp import web


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 220000).hex()


def new_event(d, author):
    try:
        title, body, when, lead = (
            str(d['title']).strip(),
            str(d.get('body', '')),
            float(d['at']),
            int(d['lead']),
        )
        assert 1 <= len(title) <= 120
        assert len(body) <= 2000 and 946684800 <= when <= 4102444800
        assert lead in (-1, 0, 5, 15, 30, 60, 1440)
    except Exception:
        raise web.HTTPBadRequest(text='Controlla titolo, data e preavviso.')
    return dict(id=uuid.uuid4().hex, title=title, body=body, at=when, lead=lead, author=author)


class Agenda:
    def __init__(self, home):
        self.folders = home / 'Calendari e note'
        self.db = sqlite3.connect(home / 'calendari-locali.sqlite')
        self.db.executescript(
            """
        PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS snapshot(scope TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS pending(seq INTEGER PRIMARY KEY,scope TEXT,method TEXT,id TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS cleared_local(scope TEXT,id TEXT,PRIMARY KEY(scope,id));
        CREATE TABLE IF NOT EXISTS versions(scope TEXT PRIMARY KEY,epoch TEXT NOT NULL);
        """
        )
        self.lock = asyncio.Lock()

    def folder(self, scope):
        if not re.fullmatch(r'[a-f0-9]{64}/[0-4]', scope):
            raise ValueError('Spazio calendario non valido.')
        room, channel = scope.split('/')
        return self.folders / room / ('Stanza comune' if channel == '0' else f'Canale {channel}')

    def write_copy(self, scope):
        folder = self.folder(scope)
        dest = folder / 'Calendario e note.txt'
        events = self.read(scope)['events']
        if not events:
            dest.unlink(missing_ok=True)
            return
        rows = []
        for event in events:
            when = dt.datetime.fromtimestamp(event['at']).strftime('%d/%m/%Y %H:%M')
            lead = event['lead']
            reminder = (
                'Nota'
                if lead < 0
                else ('Promemoria all’orario indicato' if lead == 0 else f'Promemoria {lead} min prima')
            )
            rows.append(
                f"{when} · {event['title']}\n{event.get('author', '')} · {reminder}\n{event.get('body', '')}".rstrip()
            )
        content = ('\n\n'.join(rows) + '\n').replace('\n', '\r\n').encode('utf-8-sig')
        if dest.exists() and dest.read_bytes() == content:
            return
        folder.mkdir(parents=True, exist_ok=True)
        temp = dest.with_suffix('.tmp')
        temp.write_bytes(content)
        os.replace(temp, dest)

    def visible(self, scope, events):
        cleared = {row[0] for row in self.db.execute('SELECT id FROM cleared_local WHERE scope=?', (scope,))}
        return [row for row in events if row['id'] not in cleared]

    def version(self, scope):
        row = self.db.execute('SELECT epoch FROM versions WHERE scope=?', (scope,)).fetchone()
        return row[0] if row else ''

    def reset(self, scope, epoch):
        with self.db:
            for table in ('snapshot', 'pending', 'cleared_local'):
                self.db.execute('DELETE FROM ' + table + ' WHERE scope=?', (scope,))
            self.db.execute('INSERT OR REPLACE INTO versions VALUES(?,?)', (scope, epoch))
            self.write_copy(scope)

    def clear_local(self, scope, ids):
        with self.db:
            self.db.executemany(
                'INSERT OR IGNORE INTO cleared_local VALUES(?,?)', ((scope, eid) for eid in ids)
            )
            row = self.db.execute('SELECT payload FROM snapshot WHERE scope=?', (scope,)).fetchone()
            if row:
                state = json.loads(row[0])
                state['events'] = self.visible(scope, state['events'])
                self.db.execute('UPDATE snapshot SET payload=? WHERE scope=?', (json.dumps(state), scope))
            self.write_copy(scope)

    def read(self, scope):
        row = self.db.execute('SELECT payload FROM snapshot WHERE scope=?', (scope,)).fetchone()
        data = json.loads(row[0]) if row else dict(events=[], files=[], live=None, mine='', seq=0)
        queue = self.db.execute(
            'SELECT method,id,payload FROM pending WHERE scope=? ORDER BY seq', (scope,)
        ).fetchall()
        byid = {e['id']: e for e in data['events']}
        for method, eid, payload in queue:
            if method == 'POST':
                byid[eid] = json.loads(payload)
            else:
                byid.pop(eid, None)
        data['events'] = sorted(self.visible(scope, byid.values()), key=lambda e: e['at'], reverse=True)
        data['pending'] = len(queue)
        return data

    def change(self, scope, method, eid, payload):
        with self.db:
            self.db.execute(
                'INSERT INTO pending(scope,method,id,payload) VALUES(?,?,?,?)',
                (scope, method, eid, json.dumps(payload)),
            )
            self.write_copy(scope)

    async def sync(self, s, channel=None):
        channel = channel or s.channel or '0'
        if not s.active or not s.target:
            return
        async with self.lock:
            if not s.target:
                return
            scope, headers, fp, cfg = (
                f'{s.room_fp}/{channel}',
                s.headers,
                s.fingerprint(),
                s.cfg,
            )
            epoch = self.version(scope)
            access = {'key': ''} if channel == '0' else cfg.get('access', {}).get(channel)
            if not access:
                return
            headers.update({'X-Channel': channel, 'X-Channel-Key': access['key'], 'X-Office-Epoch': epoch})
            if channel == '0':
                with self.db:
                    self.db.execute("DELETE FROM pending WHERE