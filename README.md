# Group 6 - RTSP Audio Streaming System

![Python](https://img.shields.io/badge/Python-3.8+-blue?style=flat-square&logo=python)
![Django](https://img.shields.io/badge/Django-5.1-green?style=flat-square&logo=django)
![Protocol](https://img.shields.io/badge/Protocol-RTSP%2FRTP-orange?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square)

> **Course:** DCIT 313 - Distributed Systems
> **Topic:** Common Object Request Broker Architecture (CORBA)
> **Project:** Audio streaming server and client using the Real-Time Streaming Protocol (RTSP)

---

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Project Structure](#project-structure)
- [Protocols Explained](#protocols-explained)
- [Phase 1 - Raw Socket Streaming](#phase-1---raw-socket-streaming)
- [Phase 2 - Full RTSP and RTP Streaming](#phase-2---full-rtsp-and-rtp-streaming)
- [Web UI - Django Dashboard](#web-ui---django-dashboard)
- [RTSP Protocol Flow](#rtsp-protocol-flow)
- [RTP Packet Structure](#rtp-packet-structure)
- [API Reference](#api-reference)
- [CORBA vs RTSP Comparison](#corba-vs-rtsp-comparison)
- [Installation](#installation)
- [Running the Project](#running-the-project)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

---

## Overview

This project implements a complete **audio streaming system** built from scratch in Python.
It demonstrates how real-time media is delivered over a network, progressing from a raw
TCP socket proof-of-concept (Phase 1) through a fully standards-compliant RTSP/RTP server
and client (Phase 2), topped with a professional Django web dashboard that visualises
the protocol live.

The system streams WAV audio from a server to connected clients, replicating the internals
of professional media servers such as VLC, FFmpeg, and QuickTime Streaming Server.

### Key Features

- RTSP/1.0 control channel over TCP (RFC 2326)
- RTP audio delivery over UDP (RFC 3550)
- SDP session negotiation (RFC 4566)
- Live L16 PCM audio at 44,100 Hz mono
- Multi-client support via one thread per connection
- Real-time packet counting and session tracking
- Django web dashboard with animated protocol-flow diagram
- Two-phase development: raw socket proof-of-concept then full RTSP/RTP

---

## System Architecture

```
+---------------------------------------------------------------+
|                       CLIENT MACHINE                          |
|                                                               |
|   Browser ---HTTP:8000---> Django app                         |
|                                  |                            |
|                            player/views.py                    |
|                         (inline RTSP client)                  |
|                                  |                            |
|          TCP:8554 (RTSP control) |                            |
|          UDP:6970 (RTP data)     |                            |
+----------------------------------+----------------------------+
                                   |
+----------------------------------+----------------------------+
|                       SERVER MACHINE                          |
|                                                               |
|   rtsp_server.py                                              |
|   +-- TCP :8554  <-- DESCRIBE / SETUP / PLAY / TEARDOWN       |
|   +-- StreamSession  (one per connected client)               |
|   +-- stream_rtp() thread --> RTPPacket --> UDP:6970           |
|                                                               |
|   sample.wav --> WAV frames --> RTPPacket --> UDP datagram     |
+---------------------------------------------------------------+
```

### Two-Channel Design

| Channel | Protocol | Port | Purpose                                                |
|---------|----------|------|--------------------------------------------------------|
| Control | TCP      | 8554 | RTSP commands: DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN  |
| Data    | UDP      | 6970 | RTP packets carrying raw PCM audio frames              |

The two channels are deliberately separate. TCP gives reliable delivery for commands,
while UDP gives low-latency delivery for audio because a slightly dropped packet
is better than a delayed one.

---

## Project Structure

```
groupsixdistributedsystemfolder/
|
|-- manage.py                      Django entry point
|
|-- streamapp/                     Django project settings
|   |-- __init__.py
|   |-- settings.py                In-memory SQLite, no migrations needed
|   |-- urls.py                    Root URL router
|   +-- wsgi.py
|
|-- player/                        Django app: UI and REST API
|   |-- __init__.py
|   |-- views.py                   API views and inline RTSP client
|   |-- urls.py                    Route definitions
|   +-- templates/
|       +-- player/
|           +-- index.html         Full streaming UI (HTML + CSS + JS)
|
|-- rtsp_server.py                 Phase 2: RTSP/1.0 server (RFC 2326)
|-- rtsp_client.py                 Phase 2: RTSP/1.0 client (RFC 2326)
|-- rtp_packet.py                  RTP packet encoder and decoder (RFC 3550)
|
|-- phase1_server.py               Phase 1: raw TCP audio server
|-- phase1_client.py               Phase 1: raw TCP audio client
|
|-- sample.wav                     Audio source (auto-generated if missing)
|-- rtsp_server.log                Server subprocess output (created at runtime)
|-- requirements.txt               Python dependencies
+-- README.md                      This file
```

---

## Protocols Explained

### RTSP - Real-Time Streaming Protocol (RFC 2326)

RTSP is the **remote control** for media streaming. It negotiates, starts, pauses, and
stops a stream but does **not** carry the audio itself. Think of it like HTTP for media sessions.

**State machine**

```
         DESCRIBE           SETUP
INIT  ------------> READY ---------> READY
                      ^                 |
                      |      PLAY       v
                      |   ---------> PLAYING
                      |                 |
                      |      PAUSE      |
                      +<----------------+
                      |
                 TEARDOWN
```

**Methods supported**

| Method   | Direction        | What It Does                                 |
|----------|------------------|----------------------------------------------|
| OPTIONS  | Client to Server | Query which methods the server supports       |
| DESCRIBE | Client to Server | Fetch SDP stream description                 |
| SETUP    | Client to Server | Negotiate RTP transport and port numbers      |
| PLAY     | Client to Server | Tell the server to start sending audio        |
| PAUSE    | Client to Server | Temporarily suspend the stream                |
| TEARDOWN | Client to Server | End the session and release all resources     |

**Example RTSP response**

```
RTSP/1.0 200 OK
CSeq: 3
Server: PythonRTSP/1.0
Session: 482931
Transport: RTP/AVP;unicast;client_port=6970-6971;server_port=6000-6001
```

Every request and its matching response share the same CSeq number.

---

### RTP - Real-time Transport Protocol (RFC 3550)

RTP carries the actual audio data over UDP. Each packet has a 12-byte fixed header
followed by raw PCM audio payload. UDP is used because a dropped packet is better
than a delayed one for real-time audio.

---

### SDP - Session Description Protocol (RFC 4566)

SDP is carried inside the RTSP DESCRIBE response body. It tells the client exactly
what is being streamed so it can decode it correctly.

**Example SDP returned by this server**

```
v=0
o=- 0 0 IN IP4 127.0.0.1
s=Audio Stream
c=IN IP4 127.0.0.1
t=0 0
a=recvonly
m=audio 0 RTP/AVP 96
a=rtpmap:96 L16/44100/1
a=control:streamid=0
```

| SDP Field               | Meaning                                      |
|-------------------------|----------------------------------------------|
| v=0                     | SDP version                                  |
| s=Audio Stream          | Session name                                 |
| m=audio 0 RTP/AVP 96    | Media: audio, payload type 96 dynamic        |
| a=rtpmap:96 L16/44100/1 | Codec: 16-bit PCM at 44100 Hz mono           |

---

## Phase 1 - Raw Socket Streaming

**Files:** phase1_server.py, phase1_client.py

Phase 1 proves audio can be streamed over a plain TCP socket with no protocol overhead.
The server sends a metadata line then raw PCM bytes. The client reads and plays or saves them.

**Communication flow**

```
Server                                      Client
  |                                           |
  |-- "channels,sampwidth,framerate,n\n" ---->|   Single metadata line
  |-- raw PCM bytes (4096 byte chunks) ------>|
  |-- raw PCM bytes ----------------------->|
  |-- (loops until EOF)                       |
```

**Why Phase 1 is not enough**

| Problem with Phase 1         | How Phase 2 solves it               |
|------------------------------|-------------------------------------|
| No pause or resume           | RTSP PAUSE and PLAY commands        |
| No session management        | Session IDs and state machine       |
| No format negotiation        | SDP exchange in DESCRIBE            |
| Single client only           | One handler thread per connection   |
| Incompatible with VLC etc.   | Standards-compliant RTSP/RTP        |

---

## Phase 2 - Full RTSP and RTP Streaming

**Files:** rtsp_server.py, rtsp_client.py, rtp_packet.py

### rtsp_server.py Components

| Component             | Description                                                      |
|-----------------------|------------------------------------------------------------------|
| StreamSession         | Per-client state: session ID, RTSP state, RTP socket, thread    |
| generate_sample_wav   | Auto-generates a 440 Hz sine wave WAV if sample.wav is missing  |
| get_audio_info        | Reads WAV metadata: channels, sample rate, frame count           |
| build_sdp             | Constructs SDP response body for DESCRIBE                        |
| stream_rtp            | Background thread: reads WAV frames, wraps in RTPPacket, sends UDP |
| parse_rtsp_request    | Parses raw RTSP text into a Python dictionary                    |
| rtsp_response         | Builds a well-formed RTSP response string                        |
| handle_client         | Per-connection handler: runs the full RTSP state machine         |
| run_server            | Main loop: binds TCP:8554, accepts client connections            |

### rtsp_client.py Components

| Component       | Description                                                       |
|-----------------|-------------------------------------------------------------------|
| RTSPClient      | Manages TCP control socket and UDP RTP receive socket             |
| _send_request   | Sends any RTSP method, reads full response including body         |
| _parse_sdp      | Extracts sample rate and channels from SDP                        |
| _parse_session  | Extracts Session ID from SETUP response                           |
| _receive_rtp    | Background thread: receives UDP, decodes RTPPacket, plays audio   |
| connect         | Establishes TCP connection to server                              |
| describe        | Sends DESCRIBE, parses SDP response                               |
| setup           | Sends SETUP with client RTP port                                  |
| play            | Sends PLAY, starts RTP receiver thread                            |
| pause           | Sends PAUSE                                                       |
| teardown        | Sends TEARDOWN, closes all sockets                                |
| save_audio      | Writes all received frames to a WAV file                          |

### rtp_packet.py Usage

```python
# Build a packet ready to send over UDP
pkt = RTPPacket(
    payload=pcm_bytes,
    sequence_number=42,
    timestamp=44100,
    payload_type=96
)
raw = pkt.to_bytes()

# Parse a received UDP datagram
pkt = RTPPacket.from_bytes(raw_udp_data)
print(pkt.sequence_number, pkt.timestamp, len(pkt.payload))
```

**Payload type constants**

| Constant   | Value | Codec                              |
|------------|-------|------------------------------------|
| PT_PCMU    | 0     | G.711 mu-law at 8 kHz              |
| PT_PCMA    | 8     | G.711 A-law at 8 kHz               |
| PT_L16     | 11    | Linear 16-bit PCM at 44.1 kHz     |
| PT_DYNAMIC | 96    | Dynamic - L16 mono used by project |

---

## Web UI - Django Dashboard

### Dashboard Panels

| Panel          | What It Shows                                                          |
|----------------|------------------------------------------------------------------------|
| Server Control | Launch and Stop the RTSP server subprocess, online/offline indicator   |
| Now Streaming  | Animated album art, rotating ring, equalizer bars, progress, volume    |
| Live Stats     | RTSP state badge, packet counter, bytes received, sample rate, session |
| Protocol Flow  | Animated RFC 2326 sequence diagram - each step lights up live          |
| Event Log      | Colour-coded: green=server, cyan=client, purple=RTP, red=error         |

### How the Backend Controls RTSP

```
Browser --> POST /api/rtsp/play --> Django view --> TCP PLAY --> rtsp_server.py
                                               --> spawns RTP receiver thread
                                               <-- UDP RTP packets update counter
```

---

## RTSP Protocol Flow

```
CLIENT                                            SERVER
  |                                                 |
  |-- TCP SYN --------------------------------------->|
  |<- SYN-ACK ----------------------------------------|
  |                                                 |
  |-- DESCRIBE rtsp://host:8554/stream RTSP/1.0 ---->|
  |<- 200 OK + SDP body -----------------------------|
  |                                                 |
  |-- SETUP  Transport:RTP/AVP;client_port=6970 ---->|
  |<- 200 OK + Session:482931 ----------------------|
  |                                                 |
  |-- PLAY ----------------------------------------->|
  |<- 200 OK + RTP-Info ----------------------------|
  |                                                 |
  |<= RTP/UDP seq=0   ts=0      2048 bytes =========|
  |<= RTP/UDP seq=1   ts=1024   2048 bytes =========|  one packet every ~23 ms
  |<= RTP/UDP seq=2   ts=2048   2048 bytes =========|
  |                                                 |
  |-- PAUSE ----------------------------------------->|
  |<- 200 OK ----------------------------------------|
  |                                                 |
  |-- PLAY ----------------------------------------->|
  |<- 200 OK ----------------------------------------|
  |                                                 |
  |-- TEARDOWN --------------------------------------->|
  |<- 200 OK ----------------------------------------|
  |                                                 |
  |-- TCP FIN --------------------------------------->|
```

---

## RTP Packet Structure

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|V=2|P|X|  CC   |M|   PT=96     |       Sequence Number         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           Timestamp                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Synchronization Source (SSRC)              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                  Audio Payload  (L16 PCM bytes)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| Field           | Bits | Value     | Purpose                                                       |
|-----------------|------|-----------|---------------------------------------------------------------|
| V (Version)     | 2    | 2         | RTP version - always 2                                        |
| P (Padding)     | 1    | 0         | No padding appended                                           |
| X (Extension)   | 1    | 0         | No header extension                                           |
| CC (CSRC count) | 4    | 0         | No contributing sources                                       |
| M (Marker)      | 1    | 0         | Not used for continuous audio                                 |
| PT (Payload)    | 7    | 96        | Dynamic type - L16 PCM audio                                  |
| Sequence Number | 16   | 0 to 65535| Increments per packet, wraps at 2^16, detects loss/reordering |
| Timestamp       | 32   | 0 to 2^32 | Increments by 1024 per packet at 44100 Hz clock               |
| SSRC            | 32   | random    | Identifies this stream source uniquely                        |
| Payload         | var  | PCM data  | Raw 16-bit signed little-endian audio samples                 |

**Packet size and bitrate**

```
Header:   12 bytes  (fixed)
Payload:  1024 frames x 1 channel x 2 bytes/sample = 2048 bytes
Total:    2060 bytes per UDP datagram
Bitrate:  approx 710 kbps
```

---

## API Reference

All endpoints served at http://127.0.0.1:8000

| Method | Endpoint            | Description                                                    |
|--------|---------------------|----------------------------------------------------------------|
| GET    | /                   | Serves the streaming dashboard UI                              |
| GET    | /api/audio          | Streams sample.wav for browser playback                        |
| GET    | /api/status         | Returns current state as JSON, polled every 1 second           |
| POST   | /api/server/start   | Launches rtsp_server.py, waits up to 20 s for port 8554        |
| POST   | /api/server/stop    | Terminates server subprocess, resets all state                 |
| POST   | /api/rtsp/connect   | Opens TCP connection to RTSP server, retries up to 4 times     |
| POST   | /api/rtsp/describe  | Sends RTSP DESCRIBE, parses SDP response                       |
| POST   | /api/rtsp/setup     | Sends RTSP SETUP, binds UDP:6970 for RTP                       |
| POST   | /api/rtsp/play      | Sends RTSP PLAY, starts RTP receiver background thread         |
| POST   | /api/rtsp/pause     | Sends RTSP PAUSE, stops RTP receiver                           |
| POST   | /api/rtsp/teardown  | Sends RTSP TEARDOWN, closes all sockets                        |

---

## CORBA vs RTSP Comparison

| Concept               | CORBA                               | RTSP / RTP                             |
|-----------------------|-------------------------------------|----------------------------------------|
| Purpose               | General distributed object RPC      | Real-time media streaming              |
| Interface definition  | IDL - Interface Definition Language | SDP - Session Description Protocol    |
| Method invocation     | Remote method calls via ORB         | RTSP commands: DESCRIBE, PLAY, etc.    |
| Transport             | IIOP over TCP                       | RTSP over TCP plus RTP over UDP        |
| Session concept       | Object reference / IOR              | Session ID assigned during SETUP       |
| Location transparency | corbaname::host/ServiceName         | rtsp://host:8554/stream URL            |
| Client/Server pattern | Stub / Skeleton                     | RTSPClient / RTSPServer handler        |
| Multiplexing          | Multiple objects per ORB            | Multiple sessions per server           |
| Negotiation           | IDL type checking at bind time      | SDP negotiation in DESCRIBE and SETUP  |
| Data separation       | Single channel                      | Two channels: control TCP and data UDP |

Both CORBA and RTSP are **middleware**: they sit between the application and the network,
hiding the complexity of distributed communication from the developer.

---

## Installation

### Requirements

- Python 3.8 or higher
- Django 5.x (already in requirements.txt)
- PyAudio (optional, only for CLI client live playback)

```bash
pip install django
pip install pyaudio
```

> **Windows PyAudio note:** If pip install fails, get the pre-built wheel from
> https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
> then: pip install PyAudio-0.2.14-cpXX-cpXX-win_amd64.whl

No database setup needed - the app uses in-memory SQLite and requires no migrations.

---

## Running the Project

### Option A - Web UI (Recommended for presentations)

```bash
cd groupsixdistributedsystemfolder
python manage.py runserver 8000
```

Open http://127.0.0.1:8000 in your browser, then:

1. Click **Launch RTSP Server** - waits for port 8554 to open (spinner shown)
2. Click the **Play button** - automatically runs CONNECT, DESCRIBE, SETUP, PLAY
3. Watch the Protocol Flow diagram animate and the packet counter rise
4. Click **Pause** to send RTSP PAUSE
5. Click **Stop** to send RTSP TEARDOWN

### Option B - CLI Phase 2 (Full RTSP)

Terminal 1:
```bash
python rtsp_server.py
```

Terminal 2:
```bash
python rtsp_client.py
# or for a remote machine:
python rtsp_client.py 192.168.1.100
```

Press Ctrl+C to stop. Audio saved to received_rtsp_audio.wav.

### Option C - CLI Phase 1 (Raw TCP)

Terminal 1:
```bash
python phase1_server.py
```

Terminal 2:
```bash
python phase1_client.py
```

Audio saved to received_audio.wav.

### Firewall Ports (for cross-machine streaming)

| Port | Protocol | Required For         |
|------|----------|----------------------|
| 8554 | TCP      | RTSP control channel |
| 6970 | UDP      | RTP audio data       |
| 5004 | TCP      | Phase 1 only         |

---

## Configuration

### rtsp_server.py

| Constant     | Default       | Description                       |
|--------------|---------------|-----------------------------------|
| RTSP_HOST    | 0.0.0.0       | Listen on all interfaces          |
| RTSP_PORT    | 8554          | RTSP TCP control port             |
| AUDIO_FILE   | ../sample.wav | Path to the WAV file to stream    |
| CHUNK_FRAMES | 1024          | WAV frames per RTP packet         |

### rtsp_client.py

| Constant        | Default                 | Description                       |
|-----------------|-------------------------|-----------------------------------|
| SERVER_IP       | 127.0.0.1               | RTSP server address               |
| RTSP_PORT       | 8554                    | RTSP server port                  |
| LOCAL_RTP_PORT  | 6970                    | Local UDP port for RTP            |
| SAVE_FILE       | received_rtsp_audio.wav | Output file for received audio    |

### player/views.py

| Constant   | Default   | Description              |
|------------|-----------|--------------------------|
| RTSP_IP    | 127.0.0.1 | RTSP server address      |
| RTSP_PORT  | 8554      | RTSP server port         |
| RTP_PORT   | 6970      | UDP port for RTP         |

---

## Troubleshooting

| Problem                            | Cause                                   | Fix                                                              |
|------------------------------------|-----------------------------------------|------------------------------------------------------------------|
| Connection refused on connect      | Server not ready yet                    | Use the Django UI - it polls port 8554 before enabling controls  |
| SETUP failed: timed out            | Server crashed during startup           | Check rtsp_server.log for the exact traceback                    |
| rtsp_server.log is empty           | Old session before unbuffered mode fix  | Stop server, click Launch again                                  |
| UnicodeEncodeError in server       | Windows cp1252 encoding issue           | Fixed: server now uses PYTHONIOENCODING=utf-8                    |
| Port already in use                | Previous server still running           | netstat -ano then kill the old process                           |
| No audio in CLI client             | PyAudio not installed                   | pip install pyaudio                                              |
| ImportError: relative import       | Script run from wrong directory         | Run python rtsp_server.py from the project root                  |
| Client on another PC fails         | Firewall blocking ports                 | Open TCP 8554 and UDP 6970 inbound                               |
| Audio sounds distorted             | Sample rate mismatch                    | Check SDP a=rtpmap line matches on both sides                    |
| received_rtsp_audio.wav is silent  | UDP filtered by firewall                | Test on same machine first                                       |

### Reading rtsp_server.log

```bash
type rtsp_server.log
```

A healthy startup log:

```
[SERVER] RTSP Server started  ->  rtsp://0.0.0.0:8554/stream
[SERVER] Waiting for clients...
[RTSP] New connection from ('127.0.0.1', 54321)
[RTSP] << DESCRIBE  (CSeq 2)
[RTSP] >> RTSP/1.0 200 OK
[RTSP] << SETUP  (CSeq 3)
[RTSP] >> RTSP/1.0 200 OK
[RTSP] << PLAY  (CSeq 4)
[RTP] Streaming -> 127.0.0.1:6970
[RTSP] >> RTSP/1.0 200 OK
```

---

## Group Members

| Name                 Student ID  | Role         |
|----------------------|------------|---------------
|Samuel Agyemang       | 1693571049 | Lead developer|
|Victoria Akushika Kyei| 1704470797 | Asist developer|   
|Theophilus Adu        | 1704455312 |                |     
|Wiafe Gilbert Paakwesi| 1691668588 |                |      
|Robert Akati lambert  |            |                |
|Priscilla Edinam      |            |                |
|Rejoice Fianyobge     |                             |
|Tassan Luca Kojo      |            |                | 
---

*Group 6 - DCIT 313 Distributed Systems*