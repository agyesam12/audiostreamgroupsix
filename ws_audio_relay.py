#!/usr/bin/env python3
"""
ws_audio_relay.py
-----------------
Lightweight WebSocket server — relays raw PCM audio binary between all
connected browser clients in real time.

Used by the /voice page for bidirectional voice communication:
  Phone browser  ──── ws://laptop:8765 ────  Laptop browser
  (send mic PCM)                             (play received PCM)

Any client's sent audio is forwarded to all OTHER connected clients.

Port: 8765
Run:  python ws_audio_relay.py
Requires: pip install websockets
"""

import asyncio
import sys
import os
import json

RELAY_PORT = 8765
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'ws_relay_status.json')

try:
    import websockets
    import websockets.exceptions
except ImportError:
    print('[WS RELAY] ERROR: websockets package not installed.', flush=True)
    print('[WS RELAY] Fix:  pip install websockets', flush=True)
    sys.exit(1)

_clients = set()


def _write_status(running: bool, count: int = 0):
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump({'running': running, 'clients': count}, f)
    except Exception:
        pass


async def _handler(ws):
    _clients.add(ws)
    peer = ws.remote_address
    count = len(_clients)
    print(f'[WS RELAY] + Connected  {peer[0]}:{peer[1]}  total={count}', flush=True)
    _write_status(True, count)
    try:
        async for msg in ws:
            others = {c for c in _clients if c is not ws}
            if others:
                await asyncio.gather(
                    *[c.send(msg) for c in others],
                    return_exceptions=True,
                )
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        print(f'[WS RELAY] Error: {e}', flush=True)
    finally:
        _clients.discard(ws)
        count = len(_clients)
        print(f'[WS RELAY] - Disconnected  {peer[0]}:{peer[1]}  total={count}', flush=True)
        _write_status(True, count)


async def _main():
    print(f'[WS RELAY] WebSocket audio relay listening on port {RELAY_PORT}', flush=True)
    print(f'[WS RELAY] Browsers connect to  ws://YOUR_IP:{RELAY_PORT}', flush=True)
    _write_status(True, 0)
    async with websockets.serve(_handler, '0.0.0.0', RELAY_PORT):
        await asyncio.Future()  # run forever


if __name__ == '__main__':
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    finally:
        _write_status(False, 0)
        print('[WS RELAY] Stopped.', flush=True)
