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
import queue as _queue

from django.http import JsonResponse, FileResponse, StreamingHttpResponse
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
        'wav':    'puul.wav',
    },
    'do_better': {
        'title':  'Do Better',
        'artist': 'Kuami Eugene',
        'file':   'Kuami Eugene - Do Better - Kuami Eugene (youtube).mp3',
        'wav':    'do_better.wav',
    },
    'biggest_nathaniel': {
        'title':  'Biggest Nathaniel',
        'artist': 'Lasmid',
        'file':   'Lasmid - Biggest Nathaniel (Official Lyrics Video) - AMB StudiOS (youtube).mp3',
        'wav':    'biggest_nathaniel.wav',
    },
}

_state = {
    # SERVER → CLIENT (RTSP music streaming)
    'server_running':    False,
    'rtsp_state':        'IDLE',
    'session_id':        None,
    'framerate':         44100,
    'channels':          1,
    'packets_received':  0,
    'bytes_received':    0,
    'current_song':      'puul',
    'song_title':        'Puul',
    'song_artist':       'Lasmid',
    # CLIENT → SERVER (microphone streaming)
    'mic_server_running': False,
    'mic_server_pid':     None,
    'mic_client_running': False,
    'mic_client_pid':     None,
    'packets_mic_recv':   0,
    'bytes_mic_recv':     0,
    'packets_mic_sent':   0,
    'bytes_mic_sent':     0,
    'mic_client_ip':      None,
    # shared
    'log':               [],
}

_server_proc     = None
_mic_server_proc = None
_mic_client_proc = None
_rtsp_sock   = None
_rtp_sock    = None
_stop_rtp    = threading.Event()
_cseq        = 0
_lock        = threading.Lock()

MIC_PORT = 7000   # UDP port audio_server.py listens on

RTSP_IP   = '127.0.0.1'
RTSP_PORT = 8554
RTP_PORT  = 6970

# ── Server mic streaming state (laptop mic → phone browsers) ──────────────────
_srv_mic_listeners      = []          # list of queue.Queue, one per connected phone
_srv_mic_listeners_lock = threading.Lock()
_srv_mic_active         = threading.Event()
_srv_mic_thread         = None

# ── Browser mic receive state (phone browser mic → laptop speakers) ───────────
_browser_audio_q      = _queue.Queue(maxsize=400)
_browser_player_thread = None
_browser_player_started = False


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


def _mp3_to_wav(mp3_path, wav_path):
    """Convert mp3_path → wav_path (16-bit mono 44100 Hz). Returns (True, None) or (False, msg)."""
    # Method 1: miniaudio (pure Python, no external tool)
    try:
        import miniaudio
        decoded = miniaudio.decode_file(
            mp3_path,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=44100,
        )
        raw = bytes(decoded.samples)
        with wave.open(wav_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(raw)
        return True, None
    except ImportError:
        pass
    except Exception as e:
        pass  # try next method

    # Method 2: pydub (requires ffmpeg or libav)
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_mp3(mp3_path)
        seg = seg.set_channels(1).set_frame_rate(44100).set_sample_width(2)
        seg.export(wav_path, format='wav')
        return True, None
    except ImportError:
        pass
    except Exception as e:
        pass

    # Method 3: ffmpeg subprocess
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', mp3_path,
             '-ac', '1', '-ar', '44100', '-acodec', 'pcm_s16le', wav_path],
            capture_output=True, timeout=300,
        )
        if result.returncode == 0:
            return True, None
    except FileNotFoundError:
        pass
    except Exception:
        pass

    return False, (
        'MP3 conversion failed. Fix: open a terminal in the project folder and run:\n'
        '  pip install miniaudio\n'
        'then run:  python convert_songs.py'
    )


def _activate_song(song_key):
    """Copy song_key's pre-converted WAV to sample.wav. Returns (True, None) or (False, msg)."""
    import shutil
    song     = SONGS.get(song_key, SONGS['puul'])
    wav_path = os.path.join(BASE_DIR, song['wav'])
    if not os.path.exists(wav_path):
        return False, (
            f'{song["title"]}.wav not found. '
            f'Run:  python convert_songs.py'
        )
    dst = os.path.join(BASE_DIR, 'sample.wav')
    shutil.copy2(wav_path, dst)
    return True, None


def _ensure_all_wavs():
    """Convert every song's MP3 → named WAV (skips songs already converted)."""
    for key, song in SONGS.items():
        wav_path = os.path.join(BASE_DIR, song['wav'])
        if os.path.exists(wav_path):
            _log(f'[SONG] {song["title"]} WAV already ready', 'server')
            continue
        mp3_path = os.path.join(BASE_DIR, song['file'])
        if not os.path.exists(mp3_path):
            _log(f'[SONG] MP3 missing: {song["file"]}', 'error')
            continue
        _log(f'[SONG] Converting "{song["title"]}" to WAV — please wait…', 'server')
        ok, err = _mp3_to_wav(mp3_path, wav_path)
        if ok:
            _log(f'[SONG] "{song["title"]}" WAV ready', 'server')
        else:
            _log(f'[SONG] Convert failed for "{song["title"]}": {err}', 'error')


# ── Server mic helpers ───────────────────────────────────────────────────────

def _wav_header(rate=44100, channels=1, bits=16):
    sz = 0x7FFFF000
    h  = struct.pack('<4sI4s', b'RIFF', sz + 36, b'WAVE')
    h += struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, channels, rate,
                    rate * channels * bits // 8, channels * bits // 8, bits)
    h += struct.pack('<4sI', b'data', sz)
    return h


def _srv_mic_worker():
    CHUNK = 1024

    def _broadcast(raw):
        with _srv_mic_listeners_lock:
            for q in _srv_mic_listeners:
                try:
                    q.put_nowait(raw)
                except _queue.Full:
                    pass

    try:
        import pyaudio
        pa     = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1,
                         rate=44100, input=True, frames_per_buffer=CHUNK)
        _log('[SRV MIC] Laptop mic live — phone users can listen', 'server')
        while _srv_mic_active.is_set():
            raw = stream.read(CHUNK, exception_on_overflow=False)
            _broadcast(raw)
        stream.stop_stream()
        stream.close()
        pa.terminate()
        return
    except ImportError:
        pass
    except Exception as e:
        _log(f'[SRV MIC] pyaudio error: {e}', 'error')

    try:
        import sounddevice as sd
        import numpy as np
        _log('[SRV MIC] Laptop mic live (sounddevice)', 'server')
        with sd.InputStream(samplerate=44100, channels=1,
                            dtype='int16', blocksize=CHUNK) as stream:
            while _srv_mic_active.is_set():
                data, _ = stream.read(CHUNK)
                _broadcast(data.tobytes())
    except Exception as e:
        _log(f'[SRV MIC] sounddevice error: {e}', 'error')


def _browser_audio_player():
    """Background thread — plays PCM chunks received from phone browsers."""
    global _browser_player_started
    try:
        import pyaudio
        pa     = pyaudio.PyAudio()
        pa_out = pa.open(format=pyaudio.paInt16, channels=1,
                         rate=44100, output=True, frames_per_buffer=1024)
        _log('[BROWSER MIC] Ready to play phone mic audio', 'server')
        while True:
            raw = _browser_audio_q.get()
            if raw is None:
                break
            pa_out.write(raw)
        pa_out.stop_stream(); pa_out.close(); pa.terminate()
        return
    except ImportError:
        pass
    except Exception as e:
        _log(f'[BROWSER MIC] pyaudio output error: {e}', 'error')

    try:
        import sounddevice as sd
        import numpy as np
        _log('[BROWSER MIC] Ready to play phone mic audio (sounddevice)', 'server')
        while True:
            raw = _browser_audio_q.get()
            if raw is None:
                break
            arr = np.frombuffer(raw, dtype=np.int16)
            sd.play(arr, samplerate=44100, blocking=True)
    except Exception as e:
        _log(f'[BROWSER MIC] sounddevice output error: {e}', 'error')


def _ensure_browser_player():
    global _browser_player_thread, _browser_player_started
    if _browser_player_started:
        return
    _browser_player_started = True
    _browser_player_thread = threading.Thread(
        target=_browser_audio_player, daemon=True)
    _browser_player_thread.start()


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

        # Convert all MP3s to named WAVs (skips any already done)
        _ensure_all_wavs()

        # Copy active song's WAV to sample.wav (rtsp_server.py reads this)
        song_key = _state.get('current_song', 'puul')
        ok, err = _activate_song(song_key)
        if not ok:
            _log(f'[SERVER] Audio not ready: {err}', 'error')
            return JsonResponse({'error': err}, status=500)
        _log(f'[SERVER] Audio: {SONGS[song_key]["title"]} by {SONGS[song_key]["artist"]}', 'server')

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

    # Pull live mic stats from status files written by subprocesses
    import json as _json
    for fname, recv_key, sent_key in [
        ('audio_server_status.json', 'packets_mic_recv', 'bytes_mic_recv'),
        ('mic_client_status.json',   'packets_mic_sent', 'bytes_mic_sent'),
    ]:
        path = os.path.join(BASE_DIR, fname)
        try:
            with open(path) as f:
                s = _json.load(f)
            if fname == 'audio_server_status.json':
                _state['packets_mic_recv'] = s.get('packets', 0)
                _state['bytes_mic_recv']   = s.get('bytes', 0)
                _state['mic_client_ip']    = s.get('client_ip')
            else:
                _state['packets_mic_sent'] = s.get('packets', 0)
                _state['bytes_mic_sent']   = s.get('bytes', 0)
        except Exception:
            pass

    data.update({
        'packets_mic_recv': _state['packets_mic_recv'],
        'bytes_mic_recv':   _state['bytes_mic_recv'],
        'packets_mic_sent': _state['packets_mic_sent'],
        'bytes_mic_sent':   _state['bytes_mic_sent'],
        'mic_client_ip':    _state['mic_client_ip'],
    })
    return JsonResponse(data)


# ── Microphone streaming (CLIENT → SERVER) ─────────────────────────────────────

@csrf_exempt
def mic_server_start(request):
    """Start audio_server.py — receives mic audio from clients and plays it."""
    global _mic_server_proc
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    with _lock:
        if _mic_server_proc and _mic_server_proc.poll() is None:
            return JsonResponse({'status': 'already_running', 'pid': _mic_server_proc.pid})
        script   = os.path.join(BASE_DIR, 'audio_server.py')
        log_path = os.path.join(BASE_DIR, 'audio_server.log')
        log_fh   = open(log_path, 'w', encoding='utf-8')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8']       = '1'
        _mic_server_proc = subprocess.Popen(
            [sys.executable, '-u', script],
            stdout=log_fh, stderr=log_fh,
            cwd=BASE_DIR, env=env,
        )
        _state['mic_server_running'] = True
        _state['mic_server_pid']     = _mic_server_proc.pid
        _log(f'[MIC SERVER] Audio receiver started  PID={_mic_server_proc.pid}', 'server')
        _log(f'[MIC SERVER] Listening on UDP port {MIC_PORT} for mic audio', 'server')
    return JsonResponse({'status': 'started', 'pid': _mic_server_proc.pid})


@csrf_exempt
def mic_server_stop(request):
    """Stop audio_server.py."""
    global _mic_server_proc
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    with _lock:
        if _mic_server_proc:
            _mic_server_proc.terminate()
            _mic_server_proc = None
        _state['mic_server_running'] = False
        _state['mic_server_pid']     = None
        _state['packets_mic_recv']   = 0
        _state['bytes_mic_recv']     = 0
        _log('[MIC SERVER] Audio receiver stopped.', 'server')
    return JsonResponse({'status': 'stopped'})


@csrf_exempt
def mic_client_start(request):
    """Start mic_client.py — captures microphone and sends to audio_server.
    Accepts optional JSON body: {"server_ip": "192.168.x.x"}
    Defaults to 127.0.0.1 if no IP provided.
    """
    global _mic_client_proc
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        body      = _json.loads(request.body) if request.body else {}
        server_ip = body.get('server_ip', '127.0.0.1').strip() or '127.0.0.1'
    except Exception:
        server_ip = '127.0.0.1'

    with _lock:
        if _mic_client_proc and _mic_client_proc.poll() is None:
            return JsonResponse({'status': 'already_running', 'pid': _mic_client_proc.pid})
        script   = os.path.join(BASE_DIR, 'mic_client.py')
        log_path = os.path.join(BASE_DIR, 'mic_client.log')
        log_fh   = open(log_path, 'w', encoding='utf-8')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8']       = '1'
        _mic_client_proc = subprocess.Popen(
            [sys.executable, '-u', script, server_ip],
            stdout=log_fh, stderr=log_fh,
            cwd=BASE_DIR, env=env,
        )
        _state['mic_client_running'] = True
        _state['mic_client_pid']     = _mic_client_proc.pid
        _log(f'[MIC CLIENT] Mic capture started  PID={_mic_client_proc.pid}', 'client')
        _log(f'[MIC CLIENT] Sending mic audio to {server_ip}:{MIC_PORT}', 'client')
    return JsonResponse({'status': 'started', 'pid': _mic_client_proc.pid,
                         'server_ip': server_ip})


@csrf_exempt
def mic_client_stop(request):
    """Stop mic_client.py."""
    global _mic_client_proc
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    with _lock:
        if _mic_client_proc:
            _mic_client_proc.terminate()
            _mic_client_proc = None
        _state['mic_client_running'] = False
        _state['mic_client_pid']     = None
        _state['packets_mic_sent']   = 0
        _state['bytes_mic_sent']     = 0
        _log('[MIC CLIENT] Mic capture stopped.', 'client')
    return JsonResponse({'status': 'stopped'})


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

    # If WAV not converted yet, try now (on-demand fallback)
    wav_path = os.path.join(BASE_DIR, song['wav'])
    if not os.path.exists(wav_path):
        _log(f'[SONG] Converting "{song["title"]}" on-demand…', 'server')
        ok, err = _mp3_to_wav(os.path.join(BASE_DIR, song['file']), wav_path)
        if not ok:
            _log(f'[SONG] Conversion failed: {err}', 'error')
            return JsonResponse({'error': err}, status=500)

    # Copy WAV → sample.wav so rtsp_server picks it up on the next stream
    ok, err = _activate_song(song_key)
    if not ok:
        _log(f'[SONG] Activate failed: {err}', 'error')
        return JsonResponse({'error': err}, status=500)

    _log(f'[SONG] Switched to "{song["title"]}" by {song["artist"]}', 'info')
    return JsonResponse({'status': 'ok', 'song_key': song_key,
                         'title': song['title'], 'artist': song['artist']})


def audio_serve(request):
    audio_path = os.path.join(BASE_DIR, 'sample.wav')
    if not os.path.exists(audio_path):
        _generate_wav(audio_path)
    return FileResponse(open(audio_path, 'rb'), content_type='audio/wav')
