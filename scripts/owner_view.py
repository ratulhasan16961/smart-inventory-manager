"""A read-only web page for the owner's phone: today's sales, profit, expenses, money owed, low stock.

    python3 -m scripts.owner_view                      # this computer only
    python3 -m scripts.owner_view --host 0.0.0.0       # other devices on the same Wi-Fi

It prints a link with a secret token. Without the token the page answers 403. The page is plain HTTP (not
encrypted): use it on a trusted network, or through a VPN such as Tailscale. Never expose the port to the internet.
Add ?day=2026-10-22 to the link for another day.
"""
from __future__ import annotations

import argparse
import hmac
import html
import os
import secrets
import sqlite3
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from pos_erp import config
from pos_erp.errors import POSError
from pos_erp.services import digest_service

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>body{{font-family:-apple-system,Segoe UI,sans-serif;margin:0;padding:16px;background:#f5f6f8;color:#212529}}
pre{{background:#fff;padding:14px;border-radius:8px;white-space:pre-wrap;font-size:14px;line-height:1.45}}a{{color:#0d6efd;margin-right:14px}}</style>
</head><body><h3>{title}</h3><pre>{body}</pre><p>{links}</p></body></html>"""


def render_page(db_path, day: str | None = None) -> tuple[str, str]:
    """(subject, body) of the daily summary, read through a connection that cannot write."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute("SELECT value FROM settings WHERE key = 'currency_symbol'").fetchone()
        if row:
            config.CURRENCY_SYMBOL = row["value"]
        return digest_service.build(conn, digest_service.system_session(conn), day)
    finally:
        conn.close()


def make_server(db_path, token: str, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    expected = token.encode()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, text: str) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            given = (query.get("token") or [""])[0].encode()
            if url.path != "/" or not hmac.compare_digest(given, expected):
                self._send(403, "Forbidden")
                return
            day = (query.get("day") or [None])[0]
            try:
                title, body = render_page(db_path, day)
            except POSError as exc:
                self._send(400, PAGE.format(title="Problem", body=html.escape(str(exc)), links=""))
                return
            except Exception:
                self._send(500, PAGE.format(title="Problem", links="",
                                            body="The data could not be read. Open the app once so it can upgrade "
                                                 "its database, then reload."))
                return
            shown = date.fromisoformat(day) if day else date.today()
            safe = quote(token)
            links = (f'<a href="/?token={safe}&day={shown - timedelta(days=1)}">&larr; previous day</a>'
                     f'<a href="/?token={safe}">today</a>')
            self._send(200, PAGE.format(title=html.escape(title), body=html.escape(body), links=links))

        def log_message(self, *_args) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only owner's page (sales, profit, dues, low stock).")
    parser.add_argument("--db", default=str(config.DB_PATH))
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open it from a phone on the same Wi-Fi")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("POS_OWNER_TOKEN"), help="secret (default: random)")
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        print(f"Database not found: {args.db}")
        return 1
    token = args.token or secrets.token_urlsafe(16)
    server = make_server(args.db, token, args.host, args.port)
    print(f"Owner page: http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/?token={token}")
    if args.host != "127.0.0.1":
        print("WARNING: plain HTTP. Use only on a trusted network or a VPN. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
