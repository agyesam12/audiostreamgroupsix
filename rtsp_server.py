"""
server/rtsp_server.py
----------------------
A simplified RTSP/1.0 server that handles the core RTSP command flow
and streams audio via RTP over UDP.

RTSP Command Flow (RFC 2326):
    Client --> DESCRIBE  --> Server returns SDP (session description)
    Client --> SETUP     --> Server allocates RTP/UDP ports
    Client --> PLAY      --> Server starts streaming RTP audio packets
    Client --> PAUSE     --> Server pauses stream
    Client --> TEARDOWN  --> Server closes session

Run:
    python server/rtsp_server.py

Requires:
    sample.wav in the project root (or it generates a test tone)
"""

import socket
import threading
import wave
import time
import os
import sys
import math
import struct
import random

# Allow shared/ imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from rtp_packet import RTPPacket, PT_DYNAMIC

# ── Config ────────────────────────────────────────────────────────────────────
RTSP_HOST   = '0.0.0.0'
RTSP_PORT   = 8554          # Standard RTSP port
AUDIO_FILE  = os.path.join(os.path.dirname(__file__), '..', 'sample.wav')
CHUNK_FRAMES = 1024         # Audio frames per RTP packet
# ─────────────────────────────────────────────────────────────────────────────


# ── Session State ─────────────────────────────────────────────────────────────
class StreamSession:
    """Holds state for a single RTSP client session."""

    def __init__(self, session_id: str):
        self.session_id     = session_id
        self.state          = 'INIT'          # INIT -> READY -> PLAYING -> PAUSED
        self.client_rtp_ip  = None
        self.client_rtp_port = None
        self.rtp_socket     = None
        self.stream_thread  = None
        self.stop_event     = threading.Event()
        self.sequence_num   = 0
        self.timestamp      = 0

    def cleanup(self):
        self.stop_event.set()
        if self.rtp_socket:
            try:
                self.rtp_socket.close()
            except Exception:
                pass


# ── Audio Helpers ─────────────────────────────────────────────────────────────

def generate_sample_wav(filename: str):
    """Generate a 5-second 440Hz sine wave WAV if no file exists."""
    sample_rate = 44100
    duration    = 5
    frequency   = 440.0
    num_samples = sample_rate * duration
    amplitude   = 32767

    os.makedirs(os.path.dirname(filename), exist_ok=True) if os.path.dirname(filename) else None

    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = []
        for i in range(num_samples):
            s = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
            frames.append(struct.pack('<h', s))
        wf.writeframes(b''.join(frames))

    print(f"[SERVER] Generated test WAV: {filename}")


def get_audio_info(filepath: str) -> dict:
    """Return metadata dict for a WAV file."""
    with wave.open(filepath, 'rb') as wf:
        return {
            'channels':   wf.getnchannels(),
            'sampwidth':  wf.getsampwidth(),
            'framerate':  wf.getframerate(),
            'n_frames':   wf.getnframes(),
            'duration':   wf.getnframes() / wf.getframerate(),
        }


def build_sdp(audio_info: dict, server_ip: str) -> str:
    """
    Build an SDP (Session Description Protocol) response.
    SDP tells the client exactly what is being streamed and how to decode it.
    """
    duration = audio_info['duration']
    rate     = audio_info['framerate']
    channels = audio_info['channels']

    sdp = (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {server_ip}\r\n"
        "s=Audio Stream\r\n"
        f"c=IN IP4 {server_ip}\r\n"
        f"t=0 0\r\n"
        "a=recvonly\r\n"
        f"m=audio 0 RTP/AVP {PT_DYNAMIC}\r\n"
        f"a=rtpmap:{PT_DYNAMIC} L16/{rate}/{channels}\r\n"
        f"a=control:streamid=0\r\n"
    )
    return sdp


# ── RTP Streaming ─────────────────────────────────────────────────────────────

def stream_rtp(session: StreamSession, audio_file: str):
    """
    Runs in its own thread.
    Reads audio frames and sends RTP packets to the client's UDP port.
    """
    info = get_audio_info(audio_file)
    framerate  = info['framerate']
    sampwidth  = info['sampwidth']
    channels   = info['channels']

    # How many bytes per chunk
    chunk_bytes = CHUNK_FRAMES * channels * sampwidth

    # Timestamp increment per chunk (based on sample rate clock)
    ts_increment = CHUNK_FRAMES

    print(f"[RTP] Streaming -> {session.client_rtp_ip}:{session.client_rtp_port}")

    with wave.open(audio_file, 'rb') as wf:
        while not session.stop_event.is_set():
            if session.state == 'PAUSED':
                time.sleep(0.05)
                continue

            data = wf.readframes(CHUNK_FRAMES)
            if not data:
                print("[RTP] End of audio file - stream complete.")
                break

            pkt = RTPPacket(
                payload=data,
                sequence_number=session.sequence_num,
                timestamp=session.timestamp,
                payload_type=PT_DYNAMIC,
            )

            try:
                session.rtp_socket.sendto(
                    pkt.to_bytes(),
                    (session.client_rtp_ip, session.client_rtp_port)
                )
            except OSError:
                print("[RTP] Send error - client may have disconnected.")
                break

            session.sequence_num = (session.sequence_num + 1) & 0xFFFF
            session.timestamp    = (session.timestamp + ts_increment) & 0xFFFFFFFF

            # Pace the stream to match real-time audio
            time.sleep(CHUNK_FRAMES / framerate)

    session.state = 'READY'
    print("[RTP] Stream thread exited.")


# ── RTSP Request Parser ───────────────────────────────────────────────────────

def parse_rtsp_request(raw: str) -> dict:
    """
    Parse a raw RTSP request string into a dict.

    Example request:
        DESCRIBE rtsp://localhost:8554/stream RTSP/1.0\r\n
        CSeq: 1\r\n
        \r\n
    """
    lines = raw.strip().split('\r\n')
    if not lines:
        return {}

    parts  = lines[0].split(' ', 2)
    method = parts[0] if len(parts) > 0 else ''
    url    = parts[1] if len(parts) > 1 else ''

    headers = {}
    for line in lines[1:]:
        if ':' in line:
            key, _, val = line.partition(':')
            headers[key.strip()] = val.strip()

    return {'method': method, 'url': url, 'headers': headers}


# ── RTSP Response Builder ─────────────────────────────────────────────────────

def rtsp_response(status_code: int, status_msg: str, cseq: str,
                  extra_headers: dict = None, body: str = '') -> str:
    lines = [
        f"RTSP/1.0 {status_code} {status_msg}",
        f"CSeq: {cseq}",
        "Server: PythonRTSP/1.0",
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            lines.append(f"{k}: {v}")
    if body:
        lines.append(f"Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")

    lines.append('')   # blank line separates headers from body
    if body:
        lines.append(body)

    return '\r\n'.join(lines)


# ── RTSP Client Handler ───────────────────────────────────────────────────────

def handle_client(conn: socket.socket, addr, audio_file: str):
    """
    Handles one RTSP client connection in a dedicated thread.
    Processes DESCRIBE -> SETUP -> PLAY -> PAUSE -> TEARDOWN.
    """
    print(f"\n[RTSP] New connection from {addr}")

    session_id = f"{random.randint(100000, 999999)}"
    session    = StreamSession(session_id)

    server_ip = conn.getsockname()[0]

    try:
        while True:
            raw = conn.recv(4096).decode('utf-8', errors='ignore')
            if not raw:
                break

            req    = parse_rtsp_request(raw)
            method = req.get('method', '')
            cseq   = req['headers'].get('CSeq', '0')

            print(f"[RTSP] << {method}  (CSeq {cseq})", flush=True)

            # ── OPTIONS ──────────────────────────────────────────────────────
            if method == 'OPTIONS':
                resp = rtsp_response(200, 'OK', cseq, {
                    'Public': 'OPTIONS, DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN'
                })

            # ── DESCRIBE ─────────────────────────────────────────────────────
            elif method == 'DESCRIBE':
                if not os.path.exists(audio_file):
                    generate_sample_wav(audio_file)

                info = get_audio_info(audio_file)
                sdp  = build_sdp(info, server_ip)
                resp = rtsp_response(200, 'OK', cseq, body=sdp)
                session.state = 'READY'

            # ── SETUP ────────────────────────────────────────────────────────
            elif method == 'SETUP':
                # Parse client RTP port from Transport header
                # e.g. Transport: RTP/AVP;unicast;client_port=6970-6971
                transport_hdr = req['headers'].get('Transport', '')
                client_port   = 6970   # default fallback

                for part in transport_hdr.split(';'):
                    if part.startswith('client_port='):
                        ports = part.split('=')[1].split('-')
                        client_port = int(ports[0])
                        break

                session.client_rtp_ip   = addr[0]
                session.client_rtp_port = client_port

                # Create UDP socket for RTP sending
                session.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

                server_rtp_port = 6000   # fixed server-side RTP port

                resp = rtsp_response(200, 'OK', cseq, {
                    'Transport': (
                        f"RTP/AVP;unicast;"
                        f"client_port={client_port}-{client_port+1};"
                        f"server_port={server_rtp_port}-{server_rtp_port+1}"
                    ),
                    'Session': session_id,
                })
                session.state = 'READY'

            # ── PLAY ─────────────────────────────────────────────────────────
            elif method == 'PLAY':
                if session.state in ('READY', 'PAUSED'):
                    session.state = 'PLAYING'
                    session.stop_event.clear()

                    session.stream_thread = threading.Thread(
                        target=stream_rtp,
                        args=(session, audio_file),
                        daemon=True,
                    )
                    session.stream_thread.start()

                resp = rtsp_response(200, 'OK', cseq, {
                    'Session': session_id,
                    'RTP-Info': f"seq={session.sequence_num};rtptime={session.timestamp}",
                })

            # ── PAUSE ────────────────────────────────────────────────────────
            elif method == 'PAUSE':
                session.state = 'PAUSED'
                resp = rtsp_response(200, 'OK', cseq, {'Session': session_id})

            # ── TEARDOWN ─────────────────────────────────────────────────────
            elif method == 'TEARDOWN':
                session.cleanup()
                resp = rtsp_response(200, 'OK', cseq, {'Session': session_id})
                conn.sendall(resp.encode())
                print(f"[RTSP] Session {session_id} torn down.", flush=True)
                break

            else:
                resp = rtsp_response(501, 'Not Implemented', cseq)

            print(f"[RTSP] >> {resp.splitlines()[0]}", flush=True)
            conn.sendall(resp.encode())

    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        print(f"[RTSP] Client {addr} disconnected abruptly.", flush=True)

    except Exception as e:
        print(f"[RTSP] Handler error ({type(e).__name__}): {e}", flush=True)

    finally:
        session.cleanup()
        conn.close()
        print(f"[RTSP] Connection closed: {addr}", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_server():
    audio_path = os.path.abspath(AUDIO_FILE)

    if not os.path.exists(audio_path):
        print(f"[SERVER] No WAV found at {audio_path} - generating test tone...")
        generate_sample_wav(audio_path)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((RTSP_HOST, RTSP_PORT))
    server_sock.listen(5)

    print(f"[SERVER] RTSP Server started  ->  rtsp://{RTSP_HOST}:{RTSP_PORT}/stream", flush=True)
    print(f"[SERVER] Streaming file       ->  {audio_path}", flush=True)
    print(f"[SERVER] Waiting for clients...\n", flush=True)

    while True:
        conn, addr = server_sock.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr, audio_path), daemon=True)
        t.start()


if __name__ == '__main__':
    run_server()