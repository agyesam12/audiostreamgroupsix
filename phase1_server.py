"""
PHASE 1: Raw Socket Audio Server
---------------------------------
Streams a WAV audio file to a connected client over TCP.
No RTSP yet — just raw socket streaming to prove the concept.

Usage:
    python phase1_server.py
"""

import socket
import wave
import time
import sys
import os

# ── Config ────────────────────────────────────────────────────────────────────
HOST = '0.0.0.0'       # Listen on all interfaces
PORT = 5004            # Arbitrary port for raw streaming
CHUNK_SIZE = 4096      # Bytes sent per packet (4KB)
AUDIO_FILE = 'sample.wav'
# ─────────────────────────────────────────────────────────────────────────────


def generate_sample_wav(filename: str):
    """
    Creates a simple test WAV file (a sine wave tone) if none exists.
    Requires no external libraries — uses only stdlib `wave` and `struct`.
    """
    import struct
    import math

    sample_rate = 44100
    duration = 5          # seconds
    frequency = 440.0     # Hz  (A4 note)
    num_samples = sample_rate * duration
    amplitude = 32767     # max for 16-bit audio

    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)           # Mono
        wf.setsampwidth(2)           # 16-bit samples
        wf.setframerate(sample_rate)

        frames = []
        for i in range(num_samples):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
            frames.append(struct.pack('<h', sample))  # little-endian signed short

        wf.writeframes(b''.join(frames))

    print(f"[SERVER] Generated test audio: {filename}")


def stream_audio(conn: socket.socket, addr):
    """Reads the WAV file and streams raw PCM chunks to the client."""
    print(f"[SERVER] Client connected: {addr}")

    if not os.path.exists(AUDIO_FILE):
        print(f"[SERVER] '{AUDIO_FILE}' not found — generating test tone...")
        generate_sample_wav(AUDIO_FILE)

    with wave.open(AUDIO_FILE, 'rb') as wf:
        channels   = wf.getnchannels()
        sampwidth  = wf.getsampwidth()
        framerate  = wf.getframerate()
        n_frames   = wf.getnframes()

        print(f"[SERVER] Streaming: {channels}ch | {sampwidth*8}-bit | {framerate}Hz | {n_frames} frames")

        # Send audio metadata first so client knows how to play it
        metadata = f"{channels},{sampwidth},{framerate},{n_frames}\n"
        conn.sendall(metadata.encode())

        # Stream audio in chunks
        chunk_num = 0
        while True:
            data = wf.readframes(CHUNK_SIZE)
            if not data:
                break

            try:
                conn.sendall(data)
                chunk_num += 1

                # Throttle to avoid flooding — simulate real-time pacing
                time.sleep(CHUNK_SIZE / (framerate * channels * sampwidth))

            except (BrokenPipeError, ConnectionResetError):
                print(f"[SERVER] Client disconnected during stream.")
                break

    print(f"[SERVER] Stream complete. Sent {chunk_num} chunks.")
    conn.close()


def run_server():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(1)

    print(f"[SERVER] Listening on {HOST}:{PORT} ...")
    print(f"[SERVER] Waiting for a client to connect...")

    while True:
        conn, addr = server_sock.accept()
        stream_audio(conn, addr)   # Handle one client at a time for now


if __name__ == '__main__':
    run_server()