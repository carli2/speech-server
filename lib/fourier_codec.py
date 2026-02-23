"""Fourier audio codec — Python port with multi-profile support.

Encodes 1024-sample audio frames via FFT into compact binary packets.
Profiles control frequency range (bin count) and bit precision.

Compatible with the JavaScript codec (codec.js) — same header layout,
bit packing, and quantisation so frames can cross between Python and JS.
"""
from __future__ import annotations

import math
import struct
from typing import Dict, Tuple

import numpy as np

FRAME_SAMPLES = 1024
SAMPLE_RATE = 48_000
FFT_SIZE = 1024
HEADER_SIZE = 12
VERSION = 2


# ---------------------------------------------------------------------------
#  Bit-weight functions (ISO 226-inspired psychoacoustic weighting)
# ---------------------------------------------------------------------------

def _low_bits(freq: float) -> int:
    """4-12 bits — telephone quality."""
    if freq < 50:
        return 5
    if freq < 125:
        return 12
    if freq < 250:
        return 11
    if freq < 500:
        return 10
    if freq < 1000:
        return 9
    if freq < 3000:
        return 8
    if freq < 7000:
        return 7
    if freq < 9000:
        return 6
    if freq < 13000:
        return 5
    return 4


def _medium_bits(freq: float) -> int:
    """6-14 bits — good speech quality."""
    if freq < 50:
        return 7
    if freq < 125:
        return 14
    if freq < 250:
        return 13
    if freq < 500:
        return 12
    if freq < 1000:
        return 11
    if freq < 3000:
        return 10
    if freq < 7000:
        return 9
    if freq < 9000:
        return 8
    if freq < 13000:
        return 7
    return 6


def _high_bits(freq: float) -> int:
    """8-16 bits — near-CD quality."""
    if freq < 50:
        return 9
    if freq < 125:
        return 16
    if freq < 250:
        return 15
    if freq < 500:
        return 14
    if freq < 1000:
        return 13
    if freq < 3000:
        return 12
    if freq < 7000:
        return 11
    if freq < 9000:
        return 10
    if freq < 13000:
        return 9
    return 8


def _full_bits(_freq: float) -> int:
    """16 bits uniform — uncompressed."""
    return 16


# ---------------------------------------------------------------------------
#  Profile definitions
# ---------------------------------------------------------------------------

def _build_weights(bin_count: int, bit_fn) -> np.ndarray:
    w = np.empty(bin_count, dtype=np.uint8)
    for i in range(bin_count):
        freq = (i * SAMPLE_RATE) / FFT_SIZE
        w[i] = bit_fn(freq)
    return w


class _Profile:
    __slots__ = ("name", "bin_count", "profile_id", "weights", "total_bits", "payload_bytes")

    def __init__(self, name: str, bin_count: int, profile_id: int, bit_fn):
        self.name = name
        self.bin_count = bin_count
        self.profile_id = profile_id
        self.weights = _build_weights(bin_count, bit_fn)
        self.total_bits = int(self.weights.sum()) * 2  # real + imag per bin
        self.payload_bytes = math.ceil(self.total_bits / 8)


PROFILES: Dict[str, _Profile] = {}
PROFILES_BY_ID: Dict[int, _Profile] = {}

for _name, _bc, _pid, _fn in [
    ("low",    160, 0, _low_bits),
    ("medium", 256, 1, _medium_bits),
    ("high",   384, 2, _high_bits),
    ("full",   512, 3, _full_bits),
]:
    _p = _Profile(_name, _bc, _pid, _fn)
    PROFILES[_name] = _p
    PROFILES_BY_ID[_pid] = _p

PROFILE_NAMES = list(PROFILES.keys())


# ---------------------------------------------------------------------------
#  Bit-level I/O helpers
# ---------------------------------------------------------------------------

def _write_bits(buf: bytearray, base: int, bit_idx: int, value: int, bits: int) -> int:
    for i in range(bits - 1, -1, -1):
        bit = (value >> i) & 1
        byte_off = base + (bit_idx >> 3)
        shift = 7 - (bit_idx & 7)
        buf[byte_off] |= bit << shift
        bit_idx += 1
    return bit_idx


def _read_bits(buf, base: int, bit_idx: int, bits: int) -> int:
    value = 0
    for i in range(bits):
        byte_off = base + ((bit_idx + i) >> 3)
        shift = 7 - ((bit_idx + i) & 7)
        bit = (buf[byte_off] >> shift) & 1
        value = (value << 1) | bit
    return value


# ---------------------------------------------------------------------------
#  Encode / Decode
# ---------------------------------------------------------------------------

_frame_counter = 0


def encode_frame(samples: np.ndarray, profile: str = "low") -> bytes:
    """Encode a 1024-sample float32 frame into a compact binary packet.

    Parameters
    ----------
    samples : float32 array of length 1024
    profile : profile name ("low", "medium", "high", "full")

    Returns
    -------
    bytes — header (12 bytes) + bit-packed FFT coefficients
    """
    global _frame_counter
    if len(samples) != FRAME_SAMPLES:
        raise ValueError(f"Expected {FRAME_SAMPLES} samples, got {len(samples)}")

    prof = PROFILES[profile]

    # Forward FFT
    spectrum = np.fft.fft(samples.astype(np.float32), n=FFT_SIZE)
    real = spectrum.real
    # Negate imaginary: numpy uses e^(-jω) but JS codec uses e^(+jω).
    # Storing -imag makes the binary format match the JS convention.
    imag = -spectrum.imag

    # Find max amplitude across encoded bins for normalisation
    max_abs = 0.0
    for i in range(prof.bin_count):
        a = max(abs(real[i]), abs(imag[i]))
        if a > max_abs:
            max_abs = a
    if max_abs < 1e-9:
        max_abs = 1e-9

    # Allocate output buffer
    buf = bytearray(HEADER_SIZE + prof.payload_bytes)

    # Header
    buf[0] = VERSION
    buf[1] = prof.bin_count & 0xFF
    buf[2] = prof.profile_id
    buf[3] = 0  # reserved
    struct.pack_into("<f", buf, 4, max_abs)
    struct.pack_into("<I", buf, 8, _frame_counter & 0xFFFFFFFF)
    _frame_counter += 1

    # Quantise and pack bins
    bit_idx = 0
    for i in range(prof.bin_count):
        bits = int(prof.weights[i])
        max_quant = (1 << bits) - 1
        r = max(-max_abs, min(max_abs, real[i]))
        im = max(-max_abs, min(max_abs, imag[i]))
        rq = round(((r / max_abs) + 1) * 0.5 * max_quant)
        iq = round(((im / max_abs) + 1) * 0.5 * max_quant)
        rq = max(0, min(max_quant, rq))
        iq = max(0, min(max_quant, iq))
        bit_idx = _write_bits(buf, HEADER_SIZE, bit_idx, rq, bits)
        bit_idx = _write_bits(buf, HEADER_SIZE, bit_idx, iq, bits)

    return bytes(buf)


def decode_frame(data: bytes) -> Tuple[np.ndarray, str]:
    """Decode a binary packet back to 1024 float32 samples.

    The profile is read from the header, making frames self-describing.

    Returns
    -------
    (float32[1024], profile_name)
    """
    if len(data) < HEADER_SIZE:
        raise ValueError("Frame too small")

    version = data[0]
    if version != VERSION:
        raise ValueError(f"Unsupported codec version {version}")

    bin_count_byte = data[1]
    profile_id = data[2]
    scale = struct.unpack_from("<f", data, 4)[0]

    # Look up profile by ID (provides correct weights and bin count).
    # Header byte 1 only holds bin_count & 0xFF which wraps at 256,
    # so always prefer the profile lookup.
    if profile_id in PROFILES_BY_ID:
        prof = PROFILES_BY_ID[profile_id]
    else:
        prof = PROFILES_BY_ID[0]

    real = np.zeros(FFT_SIZE, dtype=np.float32)
    imag = np.zeros(FFT_SIZE, dtype=np.float32)

    bit_idx = 0
    count = prof.bin_count
    for i in range(count):
        bits = int(prof.weights[i])
        max_quant = (1 << bits) - 1
        rq = _read_bits(data, HEADER_SIZE, bit_idx, bits)
        bit_idx += bits
        iq = _read_bits(data, HEADER_SIZE, bit_idx, bits)
        bit_idx += bits

        r = ((rq / max_quant) * 2 - 1) * scale
        im = ((iq / max_quant) * 2 - 1) * scale

        real[i] = r
        imag[i] = im
        # Mirror for real-valued signal reconstruction
        if i != 0:
            mirror = FFT_SIZE - i
            real[mirror] = r
            imag[mirror] = -im

    # Inverse FFT — negate imag to convert from JS convention (e^+jω)
    # back to numpy convention (e^-jω) before calling ifft.
    spectrum = real - 1j * imag
    samples = np.fft.ifft(spectrum).real.astype(np.float32)
    return samples[:FRAME_SAMPLES], prof.name


# ---------------------------------------------------------------------------
#  PCM conversion helpers
# ---------------------------------------------------------------------------

def pcm_s16le_to_float32(pcm: bytes) -> np.ndarray:
    """Convert raw s16le PCM bytes to float32 array in [-1, 1]."""
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def float32_to_pcm_s16le(samples: np.ndarray) -> bytes:
    """Convert float32 array to raw s16le PCM bytes."""
    clamped = np.clip(np.round(samples * 32767.0), -32768, 32767).astype(np.int16)
    return clamped.tobytes()


def frame_size_bytes(profile: str = "low") -> int:
    """Total encoded frame size in bytes (header + payload)."""
    return HEADER_SIZE + PROFILES[profile].payload_bytes
