"""
client/rtsp_client.py
----------------------
A simplified RTSP/1.0 client that:
  1. Connects to the RTSP server over TCP
  2. Sends DESCRIBE → SETUP → PLAY commands
  3. Receives RTP packets over UDP
  4. Decodes and plays the audio (or saves to disk)

Usage:
    python client/rtsp_client.py [server_ip]

    Default: 127.0.0.1

Requires:
    pip install pyaudio   (for live playback)
"""

import socket
import threading
import wave
import time
import sys
import os
import re


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rtp_packet import RTPPacket

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_IP    = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
RTSP_PORT    = 8554
LOCAL_RTP_PORT = 6970      # Client listens for RTP on this UDP port
SAVE_FILE    = 'received_rtsp_audio.wav'
# ─────────────────────────────────────────────────────────────────────────────


class RTSPClient:
    """
    Manages the RTSP control connection (TCP) and RTP data reception (UDP).
    """

    def __init__(self, server_ip: str, server_port: int):
        self.server_ip   = server_ip
        self.server_port = server_port
        self.cseq        = 0           # Increments with each request
        self.session_id  = None

        # RTSP control socket (TCP)
        self.rtsp_sock   = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # RTP receive socket (UDP)
        self.rtp_sock    = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp_sock.bind(('0.0.0.0', LOCAL_RTP_PORT))
        self.rtp_sock.settimeout(5.0)

        # Audio info (filled in from SDP)
        self.channels   = 1
        self.sampwidth  = 2
        self.framerate  = 44100

        # Received audio frames (in order)
        self.frames     = []
        self._stop      = threading.Event()

    # ── RTSP Message Helpers ──────────────────────────────────────────────────

    def _next_cseq(self) -> int:
        self.cseq += 1
        return self.cseq

    def _send_request(self, method: str, url: str, extra_headers: dict = None) -> str:
        """Send an RTSP request and return the raw response string."""
        cseq = self._next_cseq()
        lines = [
            f"{method} {url} RTSP/1.0",
            f"CSeq: {cseq}",
            "User-Agent: PythonRTSPClient/1.0",
        ]
        if self.session_id:
            lines.append(f"Session: {self.session_id}")
        if extra_headers:
            for k, v in extra_headers.items():
                lines.append(f"{k}: {v}")

        lines.append('')   # blank line = end of headers
        lines.append('')

        request = '\r\n'.join(lines)
        print(f"\n[CLIENT] → {method}")
        self.rtsp_sock.sendall(request.encode())

        # Read response — simple approach, good enough for this project
        response = b''
        while b'\r\n\r\n' not in response:
            chunk = self.rtsp_sock.recv(4096)
            if not chunk:
                break
            response += chunk

        # Also read body if Content-Length is present
        resp_str = response.decode('utf-8', errors='ignore')
        if 'Content-Length:' in resp_str:
            match = re.search(r'Content-Length:\s*(\d+)', resp_str)
            if match:
                content_len = int(match.group(1))
                body_start  = resp_str.find('\r\n\r\n') + 4
                existing    = len(resp_str) - body_start
                remaining   = content_len - existing
                if remaining > 0:
                    extra = self.rtsp_sock.recv(remaining)
                    resp_str += extra.decode('utf-8', errors='ignore')

        status_line = resp_str.split('\r\n')[0]
        print(f"[CLIENT] ← {status_line}")
        return resp_str

    def _parse_sdp(self, response: str):
        """Extract audio parameters from SDP body."""
        # Look for: a=rtpmap:96 L16/44100/1
        match = re.search(r'a=rtpmap:\d+\s+L16/(\d+)/(\d+)', response)
        if match:
            self.framerate = int(match.group(1))
            self.channels  = int(match.group(2))
            print(f"[CLIENT] SDP → L16 | {self.framerate}Hz | {self.channels}ch")
        else:
            print("[CLIENT] SDP parse: using default audio params (44100Hz, mono)")

    def _parse_session(self, response: str):
        """Extract Session ID from response headers."""
        match = re.search(r'Session:\s*(\S+)', response)
        if match:
            self.session_id = match.group(1).split(';')[0]
            print(f"[CLIENT] Session ID: {self.session_id}")

    # ── RTP Receiver ─────────────────────────────────────────────────────────

    def _receive_rtp(self):
        """
        Runs in a background thread.
        Receives RTP UDP packets, strips the header, collects payload.
        """
        print(f"[RTP] Listening on UDP port {LOCAL_RTP_PORT}...")

        # Optionally set up PyAudio for live playback
        player = None
        pa     = None
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            fmt = {1: pyaudio.paInt8, 2: pyaudio.paInt16, 4: pyaudio.paInt32}.get(
                self.sampwidth, pyaudio.paInt16
            )
            player = pa.open(
                format=fmt,
                channels=self.channels,
                rate=self.framerate,
                output=True,
                frames_per_buffer=1024,
            )
            print("[RTP] PyAudio ready — playing in real time!")
        except ImportError:
            print("[RTP] PyAudio not installed — saving to disk only.")

        packet_count = 0
        last_seq     = -1

        while not self._stop.is_set():
            try:
                data, _ = self.rtp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                pkt = RTPPacket.from_bytes(data)
            except ValueError as e:
                print(f"[RTP] Bad packet: {e}")
                continue

            # Detect out-of-order or duplicate packets
            expected = (last_seq + 1) & 0xFFFF
            if last_seq >= 0 and pkt.sequence_number != expected:
                print(f"[RTP] ⚠ Seq jump: expected {expected}, got {pkt.sequence_number}")

            last_seq = pkt.sequence_number
            self.frames.append(pkt.payload)
            packet_count += 1

            if player:
                player.write(pkt.payload)

            if packet_count % 50 == 0:
                kb = sum(len(f) for f in self.frames) / 1024
                print(f"[RTP] Received {packet_count} packets ({kb:.1f} KB)", end='\r')

        print(f"\n[RTP] Done. {packet_count} packets received.")

        if player:
            player.stop_stream()
            player.close()
            pa.terminate()

    # ── Main RTSP Flow ────────────────────────────────────────────────────────

    def connect(self):
        print(f"[CLIENT] Connecting to RTSP server {self.server_ip}:{self.server_port}...")
        self.rtsp_sock.connect((self.server_ip, self.server_port))
        print(f"[CLIENT] Connected!")

    def describe(self):
        url  = f"rtsp://{self.server_ip}:{self.server_port}/stream"
        resp = self._send_request('DESCRIBE', url)
        self._parse_sdp(resp)

    def setup(self):
        url  = f"rtsp://{self.server_ip}:{self.server_port}/stream"
        transport = f"RTP/AVP;unicast;client_port={LOCAL_RTP_PORT}-{LOCAL_RTP_PORT+1}"
        resp = self._send_request('SETUP', url, {'Transport': transport})
        self._parse_session(resp)

    def play(self):
        url  = f"rtsp://{self.server_ip}:{self.server_port}/stream"
        self._send_request('PLAY', url)

        # Start receiving RTP in background
        self._rtp_thread = threading.Thread(target=self._receive_rtp, daemon=True)
        self._rtp_thread.start()

    def pause(self):
        url = f"rtsp://{self.server_ip}:{self.server_port}/stream"
        self._send_request('PAUSE', url)

    def teardown(self):
        url = f"rtsp://{self.server_ip}:{self.server_port}/stream"
        self._send_request('TEARDOWN', url)
        self._stop.set()
        self.rtsp_sock.close()
        self.rtp_sock.close()

    def save_audio(self, filepath: str):
        """Save all received frames to a WAV file."""
        if not self.frames:
            print("[CLIENT] No audio data to save.")
            return

        print(f"\n[CLIENT] Saving audio → {filepath}")
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.sampwidth)
            wf.setframerate(self.framerate)
            wf.writeframes(b''.join(self.frames))
        total_kb = sum(len(f) for f in self.frames) / 1024
        print(f"[CLIENT] Saved {total_kb:.1f} KB of audio to '{filepath}'")


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    client = RTSPClient(SERVER_IP, RTSP_PORT)

    try:
        client.connect()

        # Full RTSP handshake
        client.describe()   # Get stream description
        client.setup()      # Negotiate RTP transport
        client.play()       # Start streaming

        print(f"\n[CLIENT] Streaming... Press Ctrl+C to stop.\n")

        # Wait for stream to finish (or user interrupt)
        while client._rtp_thread.is_alive():
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[CLIENT] Interrupted by user.")

    finally:
        client.teardown()
        client.save_audio(SAVE_FILE)


if __name__ == '__main__':
    main()