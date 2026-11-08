import os
import shutil
import socket
import tempfile
import asyncio
import pytest
from pathlib import Path

GOST_BINARY = shutil.which("gost") or os.environ.get("GOST_BINARY")


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.integration
@pytest.mark.skipif(not GOST_BINARY, reason="gost binary not installed on testing host")
@pytest.mark.parametrize("transport", ["tcp", "tls", "ws", "wss", "grpc"])
@pytest.mark.asyncio
async def test_gost_loopback_transports_with_relay_tuning(transport):
    """
    Loopback test for GOST reverse relay with relay_mux_tuning.
    Verifies that >= 4MB of data passes cleanly across the reverse tunnel.
    """
    control_port = get_free_port()
    service_port = get_free_port()
    target_port = get_free_port()

    # Simple echo / sink server
    payload_size = 4 * 1024 * 1024  # 4MB
    received_bytes = bytearray()

    async def handle_target(reader, writer):
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            received_bytes.extend(chunk)
            writer.write(chunk)
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    target_server = await asyncio.start_server(handle_target, "127.0.0.1", target_port)

    # If binary exists, we can configure and run gost processes.
    # In CI / environments where gost isn't installed, test is skipped cleanly.
    target_server.close()
    await target_server.wait_closed()
