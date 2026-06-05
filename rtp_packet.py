"""
shared/rtp_packet.py
---------------------
RTP (Real-time Transport Protocol) Packet — RFC 3550

RTP Header Structure (12 bytes minimum):
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|V=2|P|X|  CC   |M|     PT      |       Sequence Number         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           Timestamp                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Synchronization Source (SSRC)              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                         Payload Data                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Fields:
    V   (2 bits)  — RTP version, always 2
    P   (1 bit)   — Padding flag
    X   (1 bit)   — Extension flag
    CC  (4 bits)  — CSRC count
    M   (1 bit)   — Marker bit (end of frame, etc.)
    PT  (7 bits)  — Payload type (11 = PCMU audio, 96+ = dynamic)
    Seq (16 bits) — Sequence number (increments per packet)
    TS  (32 bits) — Timestamp (based on sample rate clock)
    SSRC(32 bits) — Synchronization Source identifier (random)
"""

import struct
import random


# Payload type constants (RFC 3551)
PT_PCMU   = 0    # G.711 µ-law  (8kHz)
PT_PCMA   = 8    # G.711 A-law  (8kHz)
PT_L16    = 11   # Linear 16-bit PCM (44.1kHz stereo)
PT_DYNAMIC = 96  # Dynamic — used for any format not in the fixed table


class RTPPacket:
    """
    Builds and parses RTP packets for audio streaming.
    """

    HEADER_SIZE = 12   # bytes (fixed header, no CSRC/extension)

    def __init__(
        self,
        payload: bytes,
        sequence_number: int = 0,
        timestamp: int = 0,
        ssrc: int = None,
        payload_type: int = PT_DYNAMIC,
        marker: bool = False,
    ):
        self.version         = 2
        self.padding         = 0
        self.extension       = 0
        self.csrc_count      = 0
        self.marker          = marker
        self.payload_type    = payload_type
        self.sequence_number = sequence_number & 0xFFFF          # wrap at 16-bit
        self.timestamp       = timestamp & 0xFFFFFFFF            # wrap at 32-bit
        self.ssrc            = ssrc if ssrc is not None else random.randint(0, 0xFFFFFFFF)
        self.payload         = payload

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """Pack the RTP packet into bytes ready to send over UDP."""

        # Byte 0: V(2) P(1) X(1) CC(4)
        byte0 = (self.version << 6) | (self.padding << 5) | (self.extension << 4) | self.csrc_count

        # Byte 1: M(1) PT(7)
        byte1 = (int(self.marker) << 7) | (self.payload_type & 0x7F)

        header = struct.pack(
            '!BBHII',       # network byte order: 2×byte, short, 2×int
            byte0,
            byte1,
            self.sequence_number,
            self.timestamp,
            self.ssrc,
        )
        return header + self.payload

    # ── Deserialisation ───────────────────────────────────────────────────────

    @classmethod
    def from_bytes(cls, data: bytes) -> 'RTPPacket':
        """Parse raw bytes back into an RTPPacket object."""
        if len(data) < cls.HEADER_SIZE:
            raise ValueError(f"RTP packet too short: {len(data)} bytes (need ≥{cls.HEADER_SIZE})")

        byte0, byte1, seq, ts, ssrc = struct.unpack('!BBHII', data[:cls.HEADER_SIZE])

        version     = (byte0 >> 6) & 0x03
        padding     = (byte0 >> 5) & 0x01
        extension   = (byte0 >> 4) & 0x01
        csrc_count  = byte0 & 0x0F
        marker      = bool((byte1 >> 7) & 0x01)
        payload_type = byte1 & 0x7F

        payload = data[cls.HEADER_SIZE:]

        pkt = cls(
            payload=payload,
            sequence_number=seq,
            timestamp=ts,
            ssrc=ssrc,
            payload_type=payload_type,
            marker=marker,
        )
        pkt.version   = version
        pkt.padding   = padding
        pkt.extension = extension
        pkt.csrc_count = csrc_count
        return pkt

    def __repr__(self):
        return (
            f"RTPPacket(seq={self.sequence_number}, ts={self.timestamp}, "
            f"pt={self.payload_type}, marker={self.marker}, "
            f"payload={len(self.payload)}B, ssrc={self.ssrc:#010x})"
        )