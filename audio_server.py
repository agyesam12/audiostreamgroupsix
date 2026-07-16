#!/usr/bin/env python3
"""
audio_server.py
---------------
Receives microphone audio from clients via RTP/UDP and plays it
through the server's speakers in real time.

This demonstrates the CLIENT -> SERVER direction of audio streaming:
  mic_client.py (client) ---RTP/UDP:7000---> audio_server.py (server) -> speakers

Run:    python audio_server.py
Client: python mic_client.py [server_ip]

Requires one of: pip install sounddevice   OR   pip install pyaudio
"""

import socket
import struct
import sys
import os
import time
import json

HOST        = '0.0.0.0'
MIC_PORT    = 7000          # UDP port clients send mic audio to
RATE        = 44100
CHANNELS    = 1
CHUNK       = 1024
GAIN        = 4.0           # amplify quiet phone mic audio (increase if still too quiet)

STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'audio_server_status.json')


def _amplify(raw: bytes, gain: float) -> bytes:
    """Multiply every 16-bit PCM sample by gain and clip to int16 range."""
    n = len(raw) // 2
    samples = struct.unpack(f'<{n}h', raw[:n * 2])
    clipped = [max(-32767, min(32767, int(s * gain))) for s in samples]
    return struct.pack(f'<{n}h', *clipped)


def _parse_rtp(data):
    """Return (seq, payload) from raw RTP bytes, or (None, None) on bad packet."""
    if len(data) < 12:
        return None, None
    if (data[0] >> 6) != 2:       # version must be 2
        return None, None
    seq = struct.unpack('!H', data[2:4])[0]
    return seq, data[12:]


def _write_status(d: dict):
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(d, f)
    except Exception:
        pass


def _open_output():
    """Return a write callable play(bytes) using sounddevice, pyaudio, or silent."""
    try:
        import sounddevice as sd
        import numpy as np
        buf = []

        def _cb(outdata, frames, _t, _s):
            if buf:
                chunk = buf.pop(0)
                arr = np.frombuffer(chunk, dtype=np.int16)
                if len(arr) < frames:
                    arr = np.pad(arr, (0, frames - len(arr)))
                outdata[:, 0] = arr[:frames]
            else:
                outdata.fill(0)

        stream = sd.OutputStream(samplerate=RATE, channels=CHANNELS,
                                 dtype='int16', blocksize=CHUNK, callback=_cb)
        stream.start()
        print('[AUDIO SERVER] Audio output: sounddevice', flush=True)

        def play(raw):
            buf.append(raw)

        return play

    except ImportError:
        pass
    except Exception as e:
        print(f'[AUDIO SERVER] sounddevice error: {e}', flush=True)

    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        pa_out = pa.open(format=pyaudio.paInt16, channels=CHANNELS,
                         rate=RATE, output=True, frames_per_buffer=CHUNK)
        print('[AUDIO SERVER] Audio output: pyaudio', flush=True)

        def play(raw):
            pa_out.write(raw)

        return play

    except ImportError:
        pass
    except Exception as e:
        print(f'[AUDIO SERVER] pyaudio error: {e}', flush=True)

    print('[AUDIO SERVER] WARNING: No audio output library found.', flush=True)
    print('[AUDIO SERVER] Install one:  pip install sounddevice', flush=True)
    print('[AUDIO SERVER] Continuing in packet-count-only mode.', flush=True)

    def play(raw):
        pass   # silent — still counts packets so dashboard works

    return play


def main():
    play = _open_output()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, MIC_PORT))
    sock.settimeout(1.0)

    print(f'[AUDIO SERVER] Listening on UDP port {MIC_PORT}', flush=True)
    print('[AUDIO SERVER] Waiting for microphone audio from clients...', flush=True)

    packets   = 0
    bytes_in  = 0
    last_addr = None

    _write_status({'running': True, 'packets': 0, 'bytes': 0, 'client_ip': None})

    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue

            seq, payload = _parse_rtp(data)
            if payload is None:
                continue

            play(_amplify(payload, GAIN))
            packets  += 1
            bytes_in += len(payload)

            if addr != last_addr:
                print(f'[AUDIO SERVER] Stream from {addr[0]}:{addr[1]}', flush=True)
                last_addr = addr

            if packets % 100 == 0:
                print(f'[AUDIO SERVER] Packets: {packets}  Bytes: {bytes_in}', flush=True)
                _write_status({
                    'running':   True,
                    'packets':   packets,
                    'bytes':     bytes_in,
                    'client_ip': addr[0],
                })

    except KeyboardInterrupt:
        pass
    finally:
        _write_status({'running': False, 'packets': packets,
                       'bytes': bytes_in, 'client_ip': None})
        sock.close()
        print(f'[AUDIO SERVER] Stopped. Received {packets} packets, {bytes_in} bytes.',
              flush=True)


if __name__ == '__main__':
    main()
