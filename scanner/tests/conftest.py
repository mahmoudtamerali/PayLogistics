import json
import os
import socket
import struct
import threading

os.environ["AWS_REGION"] = "eu-west-1"
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["UPLOADS_BUCKET"] = "t-uploads"
os.environ["CLEAN_BUCKET"] = "t-clean"
os.environ["QUARANTINE_BUCKET"] = "t-quarantine"
os.environ["POLL_WAIT_SECONDS"] = "0"

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from scanner import config, worker  # noqa: E402

VIRUS_MARKER = b"FAKE-VIRUS-MARKER"  # our fake clamd treats this as malware


class FakeClamd(threading.Thread):
    """Speaks just enough of the clamd protocol for the tests."""

    def __init__(self, max_len=None, fail=False):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.max_len = max_len
        self.fail = fail
        self.running = True

    def run(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                self.handle(conn)

    def handle(self, conn):
        cmd = b""
        while not cmd.endswith(b"\0"):
            part = conn.recv(1)
            if not part:
                return
            cmd += part
        if cmd == b"zPING\0":
            conn.sendall(b"PONG\0")
            return
        if self.fail:
            conn.sendall(b"internal error ERROR\0")
            return
        data = b""
        while True:
            hdr = b""
            while len(hdr) < 4:
                p = conn.recv(4 - len(hdr))
                if not p:
                    return
                hdr += p
            (n,) = struct.unpack("!I", hdr)
            if n == 0:
                break
            chunk = b""
            while len(chunk) < n:
                chunk += conn.recv(n - len(chunk))
            data += chunk
        if self.max_len is not None and len(data) > self.max_len:
            conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
        elif VIRUS_MARKER in data:
            conn.sendall(b"stream: Fake.Test.Signature FOUND\0")
        else:
            conn.sendall(b"stream: OK\0")

    def stop(self):
        self.running = False
        self.sock.close()


@pytest.fixture
def clamd(monkeypatch):
    server = FakeClamd()
    server.start()
    monkeypatch.setattr(config, "CLAMD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "CLAMD_PORT", server.port)
    yield server
    server.stop()


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        worker.reset_clients()
        s3 = boto3.client("s3", region_name="eu-west-1")
        for b in ("t-uploads", "t-clean", "t-quarantine"):
            s3.create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
        sqs = boto3.client("sqs", region_name="eu-west-1")
        url = sqs.create_queue(QueueName="t-scan", Attributes={"VisibilityTimeout": "0"})["QueueUrl"]
        monkeypatch.setattr(config, "QUEUE_URL", url)
        yield s3, sqs
        worker.reset_clients()


def s3_event(key, bucket="t-uploads", name="ObjectCreated:Put"):
    return json.dumps(
        {"Records": [{"eventName": name, "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}
    )


def keys(s3, bucket):
    return [o["Key"] for o in s3.list_objects_v2(Bucket=bucket).get("Contents", [])]
