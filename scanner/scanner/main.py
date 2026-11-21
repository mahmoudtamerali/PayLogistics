"""Container entrypoint: start clamd, wait until it answers, then run the worker.

If clamd stops, this process exits non-zero and ECS restarts the task. Both run
as the same non-root user; everything written goes under /tmp (a writable
volume in the task definition, because the root filesystem is read-only).
"""
import os
import subprocess
import sys
import time

from . import clamd_client, config, obs, worker

CLAMD_CONF = "/tmp/clamd.conf"
DB_DIR = os.environ.get("CLAMD_DB_DIR", "/var/lib/clamav")


def write_conf():
    # StreamMaxLength equals MAX_BYTES so clamd and the worker agree.
    conf = f"""\
Foreground yes
LocalSocket {config.CLAMD_SOCKET}
DatabaseDirectory {DB_DIR}
TemporaryDirectory /tmp
StreamMaxLength {config.MAX_BYTES}
MaxFileSize {config.MAX_BYTES}
LogTime yes
LogClean no
SelfCheck 3600
"""
    with open(CLAMD_CONF, "w") as f:
        f.write(conf)


def wait_for_clamd(proc, seconds=300):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            obs.log("clamd_exited", code=proc.returncode)
            raise SystemExit(3)
        if clamd_client.ping(timeout=2):
            return
        time.sleep(2)
    obs.log("clamd_timeout")
    proc.terminate()
    raise SystemExit(4)


def main():
    write_conf()
    proc = subprocess.Popen(["clamd", "--config-file", CLAMD_CONF])
    wait_for_clamd(proc)
    obs.log("clamd_ready")
    try:
        worker.run_forever()
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
