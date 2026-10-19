"""minibox web UI: Flask routes and the security checks in front of them.

SECURITY: this server can start and control root processes. It therefore
  - binds to loopback only (non-loopback --host is refused),
  - requires a random per-start token on every /api request,
  - rejects any Host header that is not 127.0.0.1:<port> or localhost:<port>
    (DNS-rebinding defence), and sets no CORS headers.
A container is NOT a hard security boundary (see docs/ui/app.md), so this page
must never be exposed beyond localhost.

Start it with: sudo scripts/run_ui.sh [port]
"""
import argparse
import hmac
import ipaddress
import logging
import os
import re
import secrets
import sys

from flask import Flask, jsonify, render_template, request

import minibox_client as mb
import validation as vd

DEFAULT_PORT = 8080
MAX_BODY = 16 * 1024            # bytes accepted in any request body
MIN_TOKEN_LEN = 16
STATUS_FOR = {                  # exception -> HTTP status
    vd.ValidationError: 400,
    mb.NotFoundError: 404,
    mb.LimitError: 409,
    mb.ConflictError: 409,
    mb.BinaryMissingError: 503,
    mb.MiniboxError: 500,
}


def error_response(code, message, status, field=None):
    """Every error looks the same: {"error": {"code", "message", "field"?}}."""
    body = {"code": code, "message": message}
    if field:
        body["field"] = field
    return jsonify(error=body), status


def create_app(token, port, bind_host="127.0.0.1"):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port,
                     "%s:%d" % (bind_host, port)}

    # ---- checks that run before every request ----
    @app.before_request
    def check_host_and_token():
        if request.host not in allowed_hosts:
            return error_response("bad_host", "unexpected Host header", 400)
        if request.path.startswith("/api/"):
            header = request.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                resp, status = error_response("unauthorized", "missing or wrong token", 401)
                resp.headers["WWW-Authenticate"] = "Bearer"
                return resp, status
        return None

    @app.after_request
    def security_headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return resp

    # ---- error handling ----
    @app.errorhandler(Exception)
    def handle_error(err):
        for exc_type, status in STATUS_FOR.items():
            if isinstance(err, exc_type):
                code = {400: "invalid_input", 404: "not_found", 409: "conflict",
                        503: "unavailable"}.get(status, "minibox_error")
                return error_response(code, str(err), status, getattr(err, "field", None))
        status = getattr(err, "code", 500)
        if not isinstance(status, int):
            status = 500
        names = {404: "not_found", 405: "method_not_allowed", 413: "body_too_large",
                 415: "unsupported_media_type"}
        message = {413: "request body too large (max %d bytes)" % MAX_BODY}.get(
            status, getattr(err, "name", "") if status != 500 else "internal error")
        if status == 500:
            app.logger.error("unhandled error: %r", err)
        return error_response(names.get(status, "error"), message, status)

    def json_body():
        if not request.is_json:
            raise vd.ValidationError("body", "send JSON (Content-Type: application/json)")
        payload = request.get_json(silent=True)
        if payload is None:
            raise vd.ValidationError("body", "invalid JSON")
        return payload

    # ---- routes ----
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/containers")
    def list_containers():
        return jsonify(containers=mb.list_containers())

    @app.post("/api/containers")
    def launch():
        opts = vd.validate_launch(json_body())
        return jsonify(id=mb.run_detached(opts)), 201

    @app.get("/api/containers/<cid>/logs")
    def logs(cid):
        cid = vd.validate_container_id(cid)
        tail = vd.validate_tail(request.args.get("tail"))
        return jsonify(id=cid, **mb.get_logs(cid, tail))

    @app.post("/api/containers/<cid>/stop")
    def stop(cid):
        cid = vd.validate_container_id(cid)
        return jsonify(id=cid, message=mb.stop(cid))

    @app.delete("/api/containers/<cid>")
    def remove(cid):
        cid = vd.validate_container_id(cid)
        mb.remove(cid)
        return jsonify(id=cid, removed=True)

    @app.post("/api/containers/<cid>/exec")
    def exec_command(cid):
        cid = vd.validate_container_id(cid)
        argv = vd.validate_exec(json_body())
        return jsonify(id=cid, **mb.exec_in(cid, argv))

    return app


class _RedactToken(logging.Filter):
    """Keep ?token=... out of the request log lines."""

    def filter(self, record):
        record.msg = re.sub(r"token=[^ &\"]+", "token=<hidden>", record.getMessage())
        record.args = ()
        return True


def check_loopback(host):
    """Return the address to bind to, or exit if `host` is not a loopback address."""
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None or address.version != 4 or not address.is_loopback:
        sys.exit("refusing to listen on '%s': the UI may only bind to 127.0.0.1 "
                 "(it can run commands as root)" % host)
    return host


def main():
    parser = argparse.ArgumentParser(description="minibox web UI (localhost only)")
    parser.add_argument("--host", default="127.0.0.1", help="loopback address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default %d)" % DEFAULT_PORT)
    args = parser.parse_args()

    bind_host = check_loopback(args.host)
    if not 1 <= args.port <= 65535:
        sys.exit("invalid port %d" % args.port)
    if os.geteuid() != 0:
        print("warning: not running as root - starting containers will fail", file=sys.stderr)

    # MINIBOX_UI_TOKEN is only for the test script; normally the token is random.
    token = os.environ.get("MINIBOX_UI_TOKEN") or secrets.token_urlsafe(32)
    if len(token) < MIN_TOKEN_LEN:
        sys.exit("MINIBOX_UI_TOKEN must be at least %d characters" % MIN_TOKEN_LEN)

    app = create_app(token, args.port, bind_host)
    logging.getLogger("werkzeug").addFilter(_RedactToken())
    print("minibox UI: http://%s:%d/?token=%s" % (bind_host, args.port, token), flush=True)
    app.run(host=bind_host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
