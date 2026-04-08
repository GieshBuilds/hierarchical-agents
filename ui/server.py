#!/usr/bin/env python3
"""Hierarchy Management UI — Flask + WebSocket server.

Usage:
    python -m ui.server [--port PORT] [--ws-port WS_PORT]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

from flask import Flask, send_from_directory

from ui.config import HOST, HTTP_PORT, WS_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ui.server")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


# CORS for local dev
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response


# Serve SPA
@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


# ---------------------------------------------------------------------------
# Register API blueprints
# ---------------------------------------------------------------------------

from ui.api.profiles import bp as profiles_bp
from ui.api.org_tree import bp as org_tree_bp
from ui.api.files import bp as files_bp
from ui.api.gateways import bp as gateways_bp
from ui.api.messages import bp as messages_bp
from ui.api.workers import bp as workers_bp
from ui.api.chains import bp as chains_bp
from ui.api.memory import bp as memory_bp
from ui.api.dashboard import bp as dashboard_bp
from ui.api.setup import bp as setup_bp

app.register_blueprint(profiles_bp)
app.register_blueprint(org_tree_bp)
app.register_blueprint(files_bp)
app.register_blueprint(gateways_bp)
app.register_blueprint(messages_bp)
app.register_blueprint(workers_bp)
app.register_blueprint(chains_bp)
app.register_blueprint(memory_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(setup_bp)


# ---------------------------------------------------------------------------
# WebSocket + real-time
# ---------------------------------------------------------------------------

def start_realtime(ws_port: int) -> None:
    """Start the real-time subsystem in background threads."""
    from ui.realtime.event_bus import event_bus
    from ui.realtime.db_watcher import DatabaseWatcher
    from ui.realtime.ws_server import start_ws_server

    watcher = DatabaseWatcher(event_bus)
    watcher.start()
    logger.info("Database watcher started")

    ws_thread = threading.Thread(
        target=start_ws_server,
        args=(ws_port, event_bus),
        daemon=True,
        name="ws-server",
    )
    ws_thread.start()
    logger.info("WebSocket server starting on port %d", ws_port)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Hierarchy Management UI")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help="HTTP port")
    parser.add_argument("--ws-port", type=int, default=WS_PORT, help="WebSocket port")
    parser.add_argument("--no-realtime", action="store_true", help="Disable real-time updates")
    args = parser.parse_args()

    if not args.no_realtime:
        start_realtime(args.ws_port)

    logger.info("Starting UI at http://localhost:%d", args.port)
    logger.info("WebSocket at ws://localhost:%d/ws", args.ws_port)
    app.run(host=HOST, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
