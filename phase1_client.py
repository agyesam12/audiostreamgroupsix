"""
PHASE 1: Raw Socket Audio Client
----------------------------------
Connects to the Phase 1 server and plays the received audio stream.

Usage:
    python phase1_client.py [server_ip]

    Default server IP: 127.0.0.1 (localhost)

Requirements:
    pip install pyaudio
"""

import socket
import wave
import sys
import io

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_IP   = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
SERVER_PORT = 5004
CHUNK_SIZE  = 4096
SAVE_FILE   = 'received_audio.wav'    # Also saves to disk for verification
# ─────────────────────────────────────────────────────────────────────────────


def receive_and_play():
    print(f"[CLIENT] Connecting to {SERVER_IP}:{SERVER_PORT} ...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_IP, SERVER_PORT))
    print(f"[CLIENT] Connected!")

    # ── Step 1: Read metadata line ────────────────────────────────────────────
    meta_buf = b''
    while b'\n' not in meta_buf:
        meta_buf += sock.recv(1)

    meta_line = meta_buf.decode().strip()
    channels, sampwidth, framerate, n_frames = map(int, meta_line.split(','))

    print(f"[CLIENT] Audio info → {channels}ch | {sampwidth*8}-bit | {framerate}Hz | {n_frames} frames")

    # ── Step 2: Try to use PyAudio for live playback ──────────────────────────
    player = None
    try:
        import pyaudio
        pa = pyaudio.PyAudio()

        fmt_map = {1: pyaudio.paInt8, 2: pyaudio.paInt16, 4: pyaudio.paInt32}
        pa_format = fmt_map.get(sampwidth, pyaudio.paInt16)

        player = pa.open(
            format=pa_format,
            channels=channels,
            rate=framerate,
            output=True,
            frames_per_buffer=CHUNK_SIZE
        )
        print(f"[CLIENT] PyAudio ready — playing audio in real time...")

    except ImportError:
        print("[CLIENT] PyAudio not installed — will save audio to disk instead.")
        print("[CLIENT]   Install with: pip install pyaudio")

    # ── Step 3: Receive chunks and play/save ──────────────────────────────────
    all_frames = []
    chunk_num  = 0
    total_bytes = 0

    try:
        while True:
            data = sock.recv(CHUNK_SIZE)
            if not data:
                break

            all_frames.append(data)
            total_bytes += len(data)
            chunk_num += 1

            # Play live if PyAudio is available
            if player:
                player.write(data)

            # Progress indicator
            if chunk_num % 20 == 0:
                kb = total_bytes / 1024
                print(f"[CLIENT] Received {chunk_num} chunks ({kb:.1f} KB)...", end='\r')

    except KeyboardInterrupt:
        print("\n[CLIENT] Playback interrupted by user.")

    # ── Step 4: Save received audio to WAV for verification ──────────────────
    if all_frames:
        print(f"\n[CLIENT] Saving received audio → {SAVE_FILE}")
        with wave.open(SAVE_FILE, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(framerate)
            wf.writeframes(b''.join(all_frames))
        print(f"[CLIENT] Saved! Open '{SAVE_FILE}' to verify the audio.")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if player:
        player.stop_stream()
        player.close()
        pa.terminate()

    sock.close()
    print(f"[CLIENT] Done. Received {total_bytes / 1024:.1f} KB total.")


if __name__ == '__main__':
    receive_and_play()