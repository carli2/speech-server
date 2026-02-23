from __future__ import annotations

import logging
import socket
import struct
import threading
import uuid
from queue import Queue, Empty
from typing import Optional

_LOGGER = logging.getLogger("audiosocket")

# AudioSocket protocol (Asterisk chan_audiosocket / app_audiosocket)
# Frame: 1 byte type + 2 bytes length (network order) + payload
TYPE_UUID = 0x01
TYPE_AUDIO = 0x10
TYPE_SILENCE = 0x02
TYPE_HANGUP = 0x00
TYPE_ERROR = 0xFF


class AudioSocketSession:
    """Manages an Asterisk AudioSocket connection.

    Listens on a TCP port. Asterisk connects and streams bidirectional
    PCM audio (signed linear 16-bit, mono). No SIP library needed —
    Asterisk handles all SIP/RTP, we just get raw audio over TCP.

    Usage:
        session = AudioSocketSession(port=9092)
        session.start()  # starts TCP listener, blocks until Asterisk connects
        # Then use rx_queue / tx_queue for audio frames
        session.hangup()
    """

    def __init__(self, port: int = 9092, sample_rate: int = 8000) -> None:
        self.port = port
        self.sample_rate = sample_rate
        self.uuid = str(uuid.uuid4())
        self.rx_queue: Queue[bytes] = Queue(maxsize=500)
        self.tx_queue: Queue[bytes] = Queue(maxsize=500)
        self.connected = threading.Event()
        self.hungup = threading.Event()
        self._server_sock: Optional[socket.socket] = None
        self._client_sock: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start TCP listener and wait for Asterisk to connect."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("0.0.0.0", self.port))
        self._server_sock.listen(1)
        self._server_sock.settimeout(60)
        _LOGGER.info("AudioSocket listening on port %d (uuid=%s)", self.port, self.uuid)

    def wait_for_connection(self, timeout: float = 60) -> bool:
        """Block until Asterisk connects. Returns True on success."""
        try:
            self._client_sock, addr = self._server_sock.accept()
            _LOGGER.info("AudioSocket connection from %s", addr)
        except socket.timeout:
            _LOGGER.warning("AudioSocket: no connection within %ds", timeout)
            return False

        # Read UUID frame
        try:
            frame_type, payload = self._read_frame()
            if frame_type == TYPE_UUID:
                remote_uuid = payload.decode("utf-8", errors="replace").strip("\x00")
                _LOGGER.info("AudioSocket UUID: %s", remote_uuid)
        except Exception as e:
            _LOGGER.warning("AudioSocket UUID read error: %s", e)

        self.connected.set()

        # Start RX/TX threads
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()
        return True

    def hangup(self) -> None:
        self.hungup.set()
        self.connected.set()  # unblock waiters
        # Send hangup frame
        if self._client_sock:
            try:
                self._send_frame(TYPE_HANGUP, b"")
            except Exception:
                pass
            try:
                self._client_sock.close()
            except Exception:
                pass
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    def _read_frame(self):
        """Read one AudioSocket frame. Returns (type, payload)."""
        header = self._recv_exact(3)
        if not header:
            raise ConnectionError("AudioSocket: connection closed")
        frame_type = header[0]
        length = struct.unpack("!H", header[1:3])[0]
        payload = self._recv_exact(length) if length > 0 else b""
        return frame_type, payload

    def _send_frame(self, frame_type: int, payload: bytes) -> None:
        header = struct.pack("!BH", frame_type, len(payload))
        self._client_sock.sendall(header + payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._client_sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("AudioSocket: connection closed")
            buf += chunk
        return buf

    def _rx_loop(self) -> None:
        """Read audio frames from Asterisk, put into rx_queue."""
        try:
            while not self.hungup.is_set():
                try:
                    frame_type, payload = self._read_frame()
                except (ConnectionError, OSError):
                    break

                if frame_type == TYPE_HANGUP:
                    _LOGGER.info("AudioSocket: hangup received")
                    break
                elif frame_type == TYPE_ERROR:
                    _LOGGER.warning("AudioSocket: error frame received")
                    break
                elif frame_type == TYPE_AUDIO:
                    if payload:
                        try:
                            self.rx_queue.put_nowait(payload)
                        except Exception:
                            pass  # drop if full
                # Ignore other frame types (silence, uuid)
        except Exception as e:
            _LOGGER.warning("AudioSocket RX error: %s", e)
        finally:
            self.hungup.set()
            self.connected.set()

    def _tx_loop(self) -> None:
        """Read from tx_queue, send audio frames to Asterisk."""
        try:
            while not self.hungup.is_set():
                try:
                    data = self.tx_queue.get(timeout=0.5)
                except Empty:
                    continue
                if data:
                    try:
                        self._send_frame(TYPE_AUDIO, data)
                    except (ConnectionError, OSError):
                        break
        except Exception as e:
            _LOGGER.warning("AudioSocket TX error: %s", e)
