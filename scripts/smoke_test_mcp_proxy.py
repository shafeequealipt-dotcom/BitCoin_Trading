"""One-off smoke test for mcp_stdio_proxy.py.

Runs the proxy as a subprocess, sends a minimal MCP handshake
(initialize + notifications/initialized + tools/list), and prints the
server's responses. Used during Phase 1 implementation to verify that
the stdio→SSE proxy round-trips messages against the running SSE server
at http://127.0.0.1:8080/sse.

Not part of the production code path; delete after Phase 1 verification.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT)


def _load_env() -> None:
    env_path = PROJECT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    _load_env()
    proc = subprocess.Popen(
        [str(PROJECT / ".venv/bin/python"), str(PROJECT / "mcp_stdio_proxy.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT),
    )

    def send(payload: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def read() -> str:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        return line.strip()

    t0 = time.time()
    send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0.1"},
        },
    })
    init_response = read()
    t_init = (time.time() - t0) * 1000
    print(f"INIT_RESP ({t_init:.0f}ms): {init_response[:240]}")

    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    t_list0 = time.time()
    list_response = read()
    t_list = (time.time() - t_list0) * 1000
    print(f"LIST_RESP ({t_list:.0f}ms, {len(list_response)} chars): {list_response[:240]}")

    # Count tools in the response
    try:
        payload = json.loads(list_response)
        tools = payload.get("result", {}).get("tools", [])
        print(f"TOOLS_COUNT: {len(tools)}")
        if tools:
            print(f"FIRST_TOOL: {tools[0].get('name')}")
    except Exception as e:
        print(f"PARSE_ERROR: {e}")

    proc.stdin.close()
    t_close = time.time()
    try:
        proc.wait(timeout=15)
        print(f"SHUTDOWN_MS: {(time.time() - t_close) * 1000:.0f}")
    except subprocess.TimeoutExpired:
        print(f"HUNG_AFTER_STDIN_CLOSE_ms: {(time.time() - t_close) * 1000:.0f}")
        proc.kill()
        proc.wait(timeout=2)
    print(f"EXIT_CODE: {proc.returncode}")
    stderr = proc.stderr.read()
    if stderr.strip():
        print("STDERR_TAIL:", stderr[-400:])
    return 0 if init_response and list_response else 1


if __name__ == "__main__":
    sys.exit(main())
