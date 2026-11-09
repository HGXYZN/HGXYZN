import asyncio
import json
from aiohttp import WSMsgType, web

FRAME_BYTES = 1920


class MediaChannel:
    def __init__(self, kind):
        self.kind = kind
        self.limit = FRAME_BYTES * 64 if kind == 'voice' else 262144
        self.members = {}
        self.names = {}

    async def roster(self):
        if self.kind == 'camera':
            payload = json.dumps(self.names, ensure_ascii=False)
            await asyncio.gather(
                *(
                    asyncio.wait_for(ws.send_str(payload), 0.5)
                    for ws in self.members.values()
                    if ws.prepared and not ws.closed
                ),
                return_exceptions=True,
            )

    async def close(self):
        sockets = list(self.members.values())
        self.members.clear()
        self.names.clear()
        await asyncio.gather(*(ws.close() for ws in sockets), return_exceptions=True)

    async def connect(self, request, client):
        if client in self.members:
            raise web.HTTPConflict(text='Chat vocale già attiva sulla postazione.')
        ws = web.WebSocketResponse(heartbeat=8, max_msg_size=self.limit)
        self.members[client] = ws
        try:
            await ws.prepare(request)
            self.names[client] = request.headers.get('X-Office-Name', 'Partecipante')[:40]
            await self.roster()
            prefix = bytes.fromhex(client)
            async for msg in ws:
                if self.members.get(client) is not ws:
                    break
                if msg.type != WSMsgType.BINARY:
                    continue
                if self.kind == 'voice' and len(msg.data) != FRAME_BYTES:
                    continue
                if self.kind == 'camera' and not msg.data.startswith(b'\xff\xd8'):
                    continue
                others = [
                    other
                    for who, other in self.members.items()
                    if who != client and other.prepared and not other.closed
                ]
                if others:
                    await asyncio.gather(
                        *(asyncio.wait_for(other.send_bytes(prefix + msg.data), 0.5) for other in others),
                        return_exceptions=True,
                    )
        finally:
            if self.members.get(client) is ws:
                self.members.pop(client, None)
                self.names.pop(client, None)
                await self.roster()
            if not ws.closed:
                await ws.close()
        return ws


async def relay(a, b):
    async def forward(source, destination, text=False):
        async for msg in source:
            if msg.type == WSMsgType.BINARY:
                await destination.send_bytes(msg.data)
            elif text and msg.type == WSMsgType.TEXT:
                await destination.send_str(msg.data)
                
    tasks = [
        asyncio.create_task(forward(a, b)),
        asyncio.create_task(forward(b, a, True)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)