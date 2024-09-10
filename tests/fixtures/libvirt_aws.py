from typing import (
    Generator,
)

import os
import subprocess
import sys
import tempfile

import libvirt
import pytest


@pytest.fixture(scope="module")
def server(
    libvirt_net: libvirt.virNetwork,
) -> Generator[subprocess.Popen[bytes], None, None]:
    """Starts the server program and returns its process"""
    fileno, dbfile = tempfile.mkstemp()
    os.close(fileno)
    server_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "libvirt_aws",
            "--bind-to=127.0.0.1",
            f"--libvirt-network={libvirt_net.UUIDString()}",
            f"--database={dbfile}",
            f"--port=6666",
        ],
        stdin=None,
        stdout=None,
        stderr=None,
    )
    yield server_process
    server_process.kill()
    os.unlink(dbfile)
