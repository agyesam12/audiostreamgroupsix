import os
import sys
import subprocess
import threading
import socket
import wave
import re
import math
import struct
import time

from django.http import JsonResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ── Module-level state (safe for Django dev server single-process) ────────────
SONGS = {
    'puul': {
        'title':  'Puul',
        'artist': 'Lasmid',
        'file':   'Lasmid - Puul (Official Video) - Lasmid (youtube).mp3',
    },
    'do_better': {
        'title':  'Do Better',
        'artist': 'Kuami Eugene',
        'file':   'Kuami Eugene - Do Better - Kuami Eugene (youtube).mp3',
    },
    'biggest_nathaniel': {
        'title':  'Biggest Nathaniel',
        'artist': 'Lasmid',
        'file':   'Lasmid - Biggest Nathaniel (Official Lyrics Video) - AMB StudiOS (youtube).mp3',
    },
}

_state = {
    'server_running':    False,
    'rtsp_state':        'IDLE',
    'session_id':        None,
    'framerate':         44100,
    'channels':          1,
    'packets_received':  0,
    'bytes_received':    0,
    'log':               [],
    'current_song':      'puul',
    'song_title':        'Puul',
    'song_artist':       'Lasmid',
}

_server_proc = None
_rtsp_sock   = None
_rtp_sock    = None
_stop_rtp    = threading.Event()
_cseq        = 0
_lock        = threading.Lock()

RTSP_IP   = '127.0.0.1'
RTSP_PORT = 8554
RTP_PORT  = 6970


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg, level='info'):
    _state['log'].append({'msg': msg, 'level': level, 'ts': time.strftime('%H:%M:%S')})
    if len(_state['log']) > 120:
        _state['log'] = _state['log'][-80:]


def _wait_for_port(host, port, timeout=20):
    """Poll until the TCP port is accepting connections or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.8)
            s.connect((host, port))
            s.close()
            return True
        except OSError:
            time.sleep(0.4)
    return False


def _send_rtsp(method, extra_headers=None):
    global _cseq, _rtsp_sock
    if not _rtsp_sock:
        return None, 'Not connected'
    _cseq += 1
    url = f'rtsp://{RTSP_IP}:{RTSP_PORT}/stream'
    lines = [
        f'{method} {url} RTSP/1.0',
        f'CSeq: {_cseq}',
        'User-Agent: Group6RTSPPlayer/1.0',
    ]
    if _state.get('session_id'):
        lines.append(f"Session: {_state['session_id']}")
    if extra_headers:
        for k, v in extra_headers.items():
            lines.append(f'{k}: {v}')
    lines += ['', '']
    try:
        _rtsp_sock.sendall('\r\n'.join(lines).encode())
        data = b''
        _rtsp_sock.settimeout(15)       # generous timeout for slow Windows startup
        while b'\r\n\r\n' not in data:
            chunk = _rtsp_sock.recv(4096)
            if not chunk:
                break
            data += chunk
        # Read any remaining body bytes indicated by Content-Length
        m = re.search(rb'Content-Length:\s*(\d+)', data)
        if m:
            cl      = int(m.group(1))
            bstart  = data.find(b'\r\n\r\n') + 4
            remain  = cl - (len(data) - bstart)
            while remain > 0:
                chunk = _rtsp_sock.recv(remain)
                if not chunk:
                    break
                data   += chunk
                remain -= len(chunk)
        return data.decode('utf-8', errors='ignore'), None
    except Exception as e:
        return None, str(e)


def _rtp_receiver():
    try:
        from rtp_packet import RTPPacket
    except ImportError:
        _log('[RTP] rtp_packet module not found — packet counting disabled.', 'error')
        return
    while not _stop_rtp.is_set():
        try:
            if not _rtp_sock:
                break
            data, _ = _rtp_sock.recvfrom(65535)
            pkt = RTPPacket.from_bytes(data)
            _state['packets_received'] += 1
            _state['bytes_received']   += len(pkt.payload)
        except socket.timeout:
            continue
        except (OSError, ValueError):
            break


def _generate_wav(path, song_key=None, duration=30):
    if song_key is None:
        song_key = _state.get('current_song', 'puul')
    song     = SONGS.get(song_key, SONGS['puul'])
    mp3_path = os.path.join(BASE_DIR, song['file'])

    if os.path.exists(mp3_path):
        try:
            import miniaudio
            decoded = miniaudio.decode_file(
                mp3_path,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=44100,
            )
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(bytes(decoded.samples))
            return
        except Exception as e:
            _log(f'[SERVER] MP3 decode failed ({e}), falling back to test tone.', 'error')

    # Fallback: synthesise a 30-second musical chord
    sr    = 44100
    amp   = 28000
    ns    = sr * duration
    freqs = [261.63, 329.63, 392.00, 523.25]
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        buf = bytearray()
        for i in range(ns):
            env = 0.6 + 0.4 * math.sin(2 * math.pi * 0.3 * i / sr)
            s   = sum(int((amp / len(freqs)) * env *
                         math.sin(2 * math.pi * f * i / sr)) for f in freqs)
            buf += struct.pack('<h', max(-32767, min(32767, s)))
        wf.writeframes(bytes(buf))


# ── Views ─────────────────────────────────────────────────────────────────────

def index(request):
    return render(request, 'player/index.html')


def docs(request):
    return render(request, 'player/docs.html')


def walkthrough(request):
    return render(request, 'player/walkthrough.html')


@csrf_exempt
def server_start(request):
    global _server_proc
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    with _lock:
        if _server_proc and _server_proc.poll() is None:
            return JsonResponse({'status': 'already_running', 'pid': _server_proc.pid})

        # Ensure sample.wav is in the project root so rtsp_server.py finds it
        audio_path = os.path.join(BASE_DIR, 'sample.wav')
        if not os.path.exists(audio_path):
            _log('[SERVER] Generating sample WAV…', 'server')
            _generate_wav(audio_path)

        # Also place a copy one level up (rtsp_server.py looks for '../sample.wav')
        parent_wav = os.path.join(BASE_DIR, '..', 'sample.wav')
        parent_wav = os.path.abspath(parent_wav)
        if not os.path.exists(parent_wav):
            import shutil
            shutil.copy2(audio_path, parent_wav)

        script   = os.path.join(BASE_DIR, 'rtsp_server.py')
        log_path = os.path.join(BASE_DIR, 'rtsp_server.log')
        log_fh   = open(log_path, 'w', encoding='utf-8')

        # -u = unbuffered stdout/stderr so every print() flushes immediately
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8']       = '1'

        _server_proc = subprocess.Popen(
            [sys.executable, '-u', script],
            stdout=log_fh,
            stderr=log_fh,
            cwd=BASE_DIR,
            env=env,
        )
        _log(f'[SERVER] Starting RTSP Server  PID={_server_proc.pid}', 'server')
        _log(f'[SERVER] Output -> rtsp_server.log', 'server')

    # Block (outside lock) until port 8554 is ready — up to 20 s
    _log(f'[SERVER] Waiting for port {RTSP_PORT} to open…', 'server')
    if _wait_for_port(RTSP_IP, RTSP_PORT, timeout=20):
        with _lock:
            _state['server_running'] = True
        _log(f'[SERVER] Ready  →  rtsp://0.0.0.0:{RTSP_PORT}/stream', 'server')
        _log(f'[SERVER] RTP transport  UDP:{RTP_PORT}', 'server')
        return JsonResponse({'status': 'started', 'pid': _server_proc.pid})
    else:
        rc = _server_proc.poll()
        if rc is not None:
            _log(f'[SERVER] Process crashed (exit={rc}). See rtsp_server.log', 'error')
            return JsonResponse({'error': f'Server crashed (exit {rc}). Check rtsp_server.log'}, status=500)
        _log(f'[SERVER] Port {RTSP_PORT} not responding after 20 s.', 'error')
        return JsonResponse({'error': 'Server did not open port 8554 within 20 s'}, status=500)


@csrf_exempt
def server_stop(request):
    global _server_proc, _rtsp_sock, _rtp_sock, _cseq
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    with _lock:
        _stop_rtp.set()
        for s in (_rtsp_sock, _rtp_sock):
            if s:
                try: s.close()
                except Exception: pass
        _rtsp_sock = _rtp_sock = None
        if _server_proc:
            _server_proc.terminate()
            _server_proc = None
        _state.update({
            'server_running': False,
            'rtsp_state': 'IDLE',
            'session_id': None,
            'packets_received': 0,
            'bytes_received': 0,
        })
        _cseq = 0
        _log('[SERVER] Server process terminated.', 'server')
    return JsonResponse({'status': 'stopped'})


@csrf_exempt
def rtsp_connect(request):
    global _rtsp_sock, _cseq
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    last_err = None
    for attempt in range(4):        # retry up to 4 times with delay
        try:
            if _rtsp_sock:
                try: _rtsp_sock.close()
                except Exception: pass
            _rtsp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _rtsp_sock.settimeout(5)
            _rtsp_sock.connect((RTSP_IP, RTSP_PORT))
            _cseq = 0
            _state['rtsp_state'] = 'CONNECTED'
            _log(f'[CLIENT] TCP connection established → {RTSP_IP}:{RTSP_PORT}', 'client')
            return JsonResponse({'status': 'connected'})
        except OSError as e:
            last_err = e
            if attempt < 3:
                _log(f'[CLIENT] Connect attempt {attempt+1} failed — retrying…', 'error')
                time.sleep(1.5)

    _log(f'[ERROR]  TCP connect failed after 4 attempts: {last_err}', 'error')
    return JsonResponse({'error': str(last_err)}, status=500)


@csrf_exempt
def rtsp_describe(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    _log(f'[CLIENT] → DESCRIBE  rtsp://{RTSP_IP}:{RTSP_PORT}/stream  RTSP/1.0', 'client')
    resp, err = _send_rtsp('DESCRIBE')
    if err:
        _log(f'[ERROR]  DESCRIBE failed: {err}', 'error')
        return JsonResponse({'error': err}, status=500)
    status_line = resp.split('\r\n')[0]
    _log(f'[SERVER] ← {status_line}', 'server')
    m = re.search(r'a=rtpmap:\d+\s+L16/(\d+)/(\d+)', resp)
    if m:
        _state['framerate'] = int(m.group(1))
        _state['channels']  = int(m.group(2))
        _log(f'[SDP]    Format=L16  Rate={_state["framerate"]}Hz  Ch={_state["channels"]}', 'info')
    _state['rtsp_state'] = 'DESCRIBED'
    return JsonResponse({'status': 'described', 'framerate': _state['framerate'], 'channels': _state['channels']})


@csrf_exempt
def rtsp_setup(request):
    global _rtp_sock
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        if _rtp_sock:
            try: _rtp_sock.close()
            except Exception: pass
        _rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _rtp_sock.bind(('0.0.0.0', RTP_PORT))
        _rtp_sock.settimeout(2.0)
    except Exception as e:
        _log(f'[ERROR]  RTP socket bind failed (port {RTP_PORT}): {e}', 'error')
        return JsonResponse({'error': str(e)}, status=500)
    transport = f'RTP/AVP;unicast;client_port={RTP_PORT}-{RTP_PORT + 1}'
    _log(f'[CLIENT] → SETUP  Transport: {transport}', 'client')
    resp, err = _send_rtsp('SETUP', {'Transport': transport})
    if err:
        _log(f'[ERROR]  SETUP failed: {err}', 'error')
        return JsonResponse({'error': err}, status=500)
    status_line = resp.split('\r\n')[0]
    _log(f'[SERVER] ← {status_line}', 'server')
    m = re.search(r'Session:\s*(\S+)', resp)
    if m:
        _state['session_id'] = m.group(1).split(';')[0]
        _log(f'[SESSION] ID = {_state["session_id"]}', 'info')
    _state['rtsp_state'] = 'READY'
    return JsonResponse({'status': 'ready', 'session_id': _state['session_id']})


@csrf_exempt
def rtsp_play(request):
    global _stop_rtp
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    _log('[CLIENT] → PLAY', 'client')
    resp, err = _send_rtsp('PLAY')
    if err:
        _log(f'[ERROR]  PLAY failed: {err}', 'error')
        return JsonResponse({'error': err}, status=500)
    status_line = resp.split('\r\n')[0]
    _log(f'[SERVER] ← {status_line}', 'server')
    _log(f'[RTP]    Receiving UDP packets on port {RTP_PORT}…', 'rtp')
    _state['rtsp_state'] = 'PLAYING'
    _stop_rtp.clear()
    threading.Thread(target=_rtp_receiver, daemon=True).start()
    return JsonResponse({'status': 'playing'})


@csrf_exempt
def rtsp_pause(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    _log('[CLIENT] → PAUSE', 'client')
    _stop_rtp.set()
    resp, err = _send_rtsp('PAUSE')
    if err:
        _log(f'[ERROR]  PAUSE failed: {err}', 'error')
        return JsonResponse({'error': err}, status=500)
    status_line = resp.split('\r\n')[0]
    _log(f'[SERVER] ← {status_line}', 'server')
    _state['rtsp_state'] = 'PAUSED'
    return JsonResponse({'status': 'paused'})


@csrf_exempt
def rtsp_teardown(request):
    global _rtsp_sock, _rtp_sock
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    _log('[CLIENT] → TEARDOWN', 'client')
    _stop_rtp.set()
    resp, err = _send_rtsp('TEARDOWN')
    if resp:
        _log(f'[SERVER] ← {resp.split(chr(13))[0]}', 'server')
    with _lock:
        for s in (_rtsp_sock, _rtp_sock):
            if s:
                try: s.close()
                except Exception: pass
        _rtsp_sock = _rtp_sock = None
    _state['rtsp_state'] = 'IDLE'
    _state['session_id'] = None
    _log('[SESSION] Torn down. Connection closed.', 'info')
    return JsonResponse({'status': 'torn_down'})


def api_status(request):
    data = dict(_state)
    data['songs'] = {k: {'title': v['title'], 'artist': v['artist']} for k, v in SONGS.items()}
    return JsonResponse(data)


@csrf_exempt
def song_select(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json
    try:
        body     = json.loads(request.body)
        song_key = body.get('song_key', '').strip()
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if song_key not in SONGS:
        return JsonResponse({'error': f'Unknown song key: {song_key}'}, status=400)

    song = SONGS[song_key]
    mp3_path = os.path.join(BASE_DIR, song['file'])
    if not os.path.exists(mp3_path):
        return JsonResponse({'error': f'Audio file not found: {song["file"]}'}, status=404)

    _state['current_song'] = song_key
    _state['song_title']   = song['title']
    _state['song_artist']  = song['artist']

    # Regenerate the WAV so the next server start uses the new song
    audio_path = os.path.join(BASE_DIR, 'sample.wav')
    try:
        _generate_wav(audio_path, song_key=song_key)
        _log(f'[SONG] Switched to "{song["title"]}" by {song["artist"]}', 'info')
    except Exception as e:
        _log(f'[SONG] WAV generation failed: {e}', 'error')
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'status': 'ok', 'song_key': song_key,
                         'title': song['title'], 'artist': song['artist']})


def audio_serve(request):
    audio_path = os.path.join(BASE_DIR, 'sample.wav')
    if not os.path.exists(audio_path):
        _generate_wav(audio_path)
    return FileResponse(open(audio_path, 'rb'), content_type='audio/wav')
