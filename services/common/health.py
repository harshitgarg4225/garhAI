"""``/healthz`` and ``/metrics`` for worker processes (playbook §18).

A worker has no web framework and should not grow one for two endpoints, so this is
an ``asyncio.start_server`` responder speaking just enough HTTP/1.1: read the request
line, ignore the headers, answer, close. No keep-alive, no routing table, no chunking.

Endpoints
    ``GET /healthz``  liveness + readiness. 200 when Redis answered a PING within the
                      last sweep, 503 otherwise. Body is the metrics snapshot, which
                      makes ``curl`` a useful debugging tool during an incident.
    ``GET /metrics``  Prometheus text exposition.

Note that ``docker-compose.yml`` health-checks workers with a Redis PING rather than
this port (it needs no published port that way). This server exists for the §18
requirement and for a future k8s probe; both are satisfied by the same code path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.common.logging import get_logger
from services.common.metrics import WorkerMetrics

log = get_logger("health")

#: Refuse to read an unbounded request. Nothing legitimate sends more than this.
MAX_REQUEST_BYTES = 8 * 1024
_READ_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class HealthStatus:
    """Answer to "is this worker actually working?"."""

    healthy: bool
    reason: str = "ok"
    details: dict[str, Any] | None = None


HealthProbe = Callable[[], Awaitable[HealthStatus]]


class HealthServer:
    """Tiny HTTP server bound to the worker's health port."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        metrics: WorkerMetrics,
        probe: HealthProbe,
    ) -> None:
        self.host = host
        self.port = port
        self.metrics = metrics
        self.probe = probe
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Bind and serve. Port 0 disables the server (useful in tests)."""
        if self.port == 0:
            log.info("health.disabled", reason="WORKER_HEALTH_PORT=0")
            return
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
        except OSError as exc:
            # A worker that cannot bind its health port is still a working worker.
            log.error("health.bind_failed", host=self.host, port=self.port, error=str(exc))
            return
        log.info("health.listening", host=self.host, port=self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_SECONDS)
            if not request_line or len(request_line) > MAX_REQUEST_BYTES:
                await self._respond(writer, 400, "text/plain; charset=utf-8", "bad request\n")
                return
            parts = request_line.decode("latin-1", errors="replace").split()
            method = parts[0] if parts else ""
            target = parts[1] if len(parts) > 1 else "/"
            path = target.split("?", 1)[0]

            # Drain headers so the client sees a clean response rather than a reset.
            consumed = len(request_line)
            while consumed < MAX_REQUEST_BYTES:
                line = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_SECONDS)
                consumed += len(line)
                if line in (b"\r\n", b"\n", b""):
                    break

            if method not in ("GET", "HEAD"):
                await self._respond(
                    writer, 405, "text/plain; charset=utf-8", "method not allowed\n"
                )
                return
            if path in ("/healthz", "/readyz", "/health"):
                await self._healthz(writer)
            elif path == "/metrics":
                await self._respond(
                    writer,
                    200,
                    "text/plain; version=0.0.4; charset=utf-8",
                    self.metrics.render_prometheus(),
                )
            else:
                await self._respond(writer, 404, "text/plain; charset=utf-8", "not found\n")
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            return
        except Exception as exc:
            log.warning("health.handler_error", error=str(exc))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def _healthz(self, writer: asyncio.StreamWriter) -> None:
        try:
            status = await self.probe()
        except Exception as exc:
            status = HealthStatus(healthy=False, reason="probe raised: %s" % exc)
        body: dict[str, Any] = {
            "status": "ok" if status.healthy else "unhealthy",
            "reason": status.reason,
            **self.metrics.snapshot(),
        }
        if status.details:
            body["details"] = status.details
        await self._respond(
            writer,
            200 if status.healthy else 503,
            "application/json; charset=utf-8",
            json.dumps(body, sort_keys=True) + "\n",
        )

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, content_type: str, body: str
    ) -> None:
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            503: "Service Unavailable",
        }.get(status, "OK")
        payload = body.encode("utf-8")
        head = (
            "HTTP/1.1 %d %s\r\n"
            "Content-Type: %s\r\n"
            "Content-Length: %d\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n"
            "\r\n" % (status, reason, content_type, len(payload))
        )
        writer.write(head.encode("latin-1") + payload)
        await writer.drain()


__all__ = ["MAX_REQUEST_BYTES", "HealthProbe", "HealthServer", "HealthStatus"]
