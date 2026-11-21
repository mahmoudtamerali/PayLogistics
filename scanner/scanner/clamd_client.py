"""Minimal clamd client using the INSTREAM command (no files on disk).

Protocol: send "zINSTREAM\0", then for each chunk a 4-byte big-endian length
followed by the data, then four zero bytes. clamd replies with one line ending
in \0, for example:
    stream: OK
    stream: Eicar-Signature FOUND
    INSTREAM size limit exceeded. ERROR
"""
import socket
import struct
from dataclasses import dataclass
from typing import Iterable, Optional

from . import config

CHUNK = 64 * 1024


class ClamdError(Exception):
    """clamd could not give a verdict (down, timeout, or its own error)."""


@dataclass(frozen=True)
class Verdict:
    status: str  # "clean" | "infected" | "too_large"
    signature: Optional[str] = None


def _connect(timeout: float) -> socket.socket:
    try:
        if config.CLAMD_HOST:
            s = socket.create_connection((config.CLAMD_HOST, config.CLAMD_PORT), timeout=timeout)
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(config.CLAMD_SOCKET)
        return s
    except OSError as err:
        raise ClamdError(f"cannot connect to clamd: {err}") from err


def _read_reply(s: socket.socket) -> str:
    buf = b""
    try:
        while not buf.endswith(b"\0") and not buf.endswith(b"\n"):
            part = s.recv(4096)
            if not part:
                break
            buf += part
    except OSError as err:
        raise ClamdError(f"clamd read failed: {err}") from err
    return buf.rstrip(b"\0\n").decode("utf-8", "replace")


def ping(timeout: float = 5.0) -> bool:
    try:
        s = _connect(timeout)
        try:
            s.sendall(b"zPING\0")
            return _read_reply(s) == "PONG"
        finally:
            s.close()
    except ClamdError:
        return False


def scan_stream(chunks: Iterable[bytes], timeout: float = None) -> Verdict:
    s = _connect(timeout or config.CLAMD_TIMEOUT)
    try:
        try:
            s.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                if chunk:
                    s.sendall(struct.pack("!I", len(chunk)) + chunk)
            s.sendall(struct.pack("!I", 0))
        except (BrokenPipeError, ConnectionResetError):
            # clamd may close early after "size limit exceeded"; read its reply.
            pass
        except OSError as err:
            raise ClamdError(f"clamd write failed: {err}") from err
        reply = _read_reply(s)
    finally:
        s.close()
    return parse_reply(reply)


def parse_reply(reply: str) -> Verdict:
    if reply.endswith("OK"):
        return Verdict("clean")
    if reply.endswith("FOUND"):
        sig = reply.split(":", 1)[-1].strip()[: -len("FOUND")].strip()
        return Verdict("infected", sig or "unknown")
    if "size limit exceeded" in reply:
        return Verdict("too_large")
    raise ClamdError(f"unexpected clamd reply: {reply[:200]!r}")
