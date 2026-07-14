#!/usr/bin/env python3
"""
mic_client.py
-------------
Captures microphone audio and streams it to the server via RTP/UDP.
This demonstrates the CLIENT -> SERVER direction of audio streaming.

Usage:
    python mic_client.py                  # connects to 127.0.0.1:7000
    python mic_client.py 192.168.1.10     # connects to remote server

The server must be running:  python audio_server.py

Requires one of: pip install sounddevice   OR   pip install pyaudio
"""

import socket
import struct
import sys
import os
import time
import json
import random

SERVER_IP   = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
SERVER_PORT = 7000
RATE        = 44100
CHANNELS    = 1
CHUNK       = 1024
SSRC        = random.randint(1, 0xFFFFFFFF)

STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'mic_client_status.json')


def _build_rtp(payload: bytes, seq: int, ts: int) -> bytes:
    """Wrap raw PCM payload in a 12-byte RTP header (RFC 3550)."""
    return struct.pack('!BBHII',
        0x80,             # V=2, P=0, X=0, CC=0
        96,               # M=0, PT=96 (dynamic — L16)
        seq & 0xFFFF,
        ts  & 0xFFFFFFFF,
        SSRC,
    ) + payload


def _write_status(d: dict):
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(d, f)
    except Exception:
        pass


def _stream_sounddevice(sock, server):
    import sounddevice as sd
    import numpy as np

    seq = 0
    ts  = 0
    packets_sent = 0
    bytes_sent   = 0

    def callback(indata, frames, time_info, status):
        nonlocal seq, ts, packets_sent, bytes_sent
        raw = indata.tobytes()
        pkt = _build_rtp(raw, seq, ts)
        sock.sendto(pkt, server)
        seq = (seq + 1) & 0xFFFF
        ts  = (ts  + frames) & 0xFFFFFFFF
        packets_sent += 1
        bytes_sent   += len(raw)
        if packets_sent % 100 == 0:
            print(f'[MIC CLIENT] Sent {packets_sent} packets  ({bytes_sent} bytes)',
                  flush=True)
            _write_status({'running': True, 'packets': packets_sent,
                           'bytes': bytes_sent, 'server_ip': SERVER_IP})

    with sd.InputStream(callback=callback, samplerate=RATE,
                        channels=CHANNELS, dtype='int16', blocksize=CHUNK):
        print('[MIC CLIENT] Recording (sounddevice). Speak into your mic.', flush=True)
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass

    return packets_sent, bytes_sent


def _stream_pyaudio(sock, server):
    import pyaudio
    pa    = pyaudio.PyAudio()
    pa_in = pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                    rate=RATE, input=True, frames_per_buffer=CHUNK)

    seq = 0
    ts  = 0
    packets_sent = 0
    bytes_sent   = 0

    print('[MIC CLIENT] Recording (pyaudio). Speak into your mic.', flush=True)
    try:
        while True:
            raw = pa_in.read(CHUNK, exception_on_overflow=False)
            pkt = _build_rtp(raw, seq, ts)
            sock.sendto(pkt, server)
            seq = (seq + 1) & 0xFFFF
            ts  = (ts  + CHUNK) & 0xFFFFFFFF
            packets_sent += 1
            bytes_sent   += len(raw)
            if packets_sent % 100 == 0:
                print(f'[MIC CLIENT] Sent {packets_sent} packets  ({bytes_sent} bytes)',
                      flush=True)
                _write_status({'running': True, 'packets': packets_sent,
                               'bytes': bytes_sent, 'server_ip': SERVER_IP})
    except KeyboardInterrupt:
        pass
    finally:
        pa_in.stop_stream()
        pa_in.close()
        pa.terminate()

    return packets_sent, bytes_sent


def main():
    server = (SERVER_IP, SERVER_PORT)
    sock   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f'[MIC CLIENT] Target server: {SERVER_IP}:{SERVER_PORT}', flush=True)
    _write_status({'running': True, 'packets': 0,
                   'bytes': 0, 'server_ip': SERVER_IP})

    packets_sent = 0
    bytes_sent   = 0

    try:
        try:
            import sounddevice
            packets_sent, bytes_sent = _stream_sounddevice(sock, server)
        except ImportError:
            try:
                import pyaudio
                packets_sent, bytes_sent = _stream_pyaudio(sock, server)
            except ImportError:
                print('[MIC CLIENT] ERROR: No audio capture library found.', flush=True)
                print('[MIC CLIENT] Fix:  pip install sounddevice', flush=True)
                sys.exit(1)
    finally:
        _write_status({'running': False, 'packets': packets_sent,
                       'bytes': bytes_sent, 'server_ip': SERVER_IP})
        sock.close()
        print(f'[MIC CLIENT] Stopped. Sent {packets_sent} packets, {bytes_sent} bytes.',
              flush=True)


if __name__ == '__main__':
    main()
