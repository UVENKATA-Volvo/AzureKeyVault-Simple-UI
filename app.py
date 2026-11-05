"""Loopback-only Key Vault Desk. Python standard library; Azure CLI authentication."""

import argparse
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parent
API_VERSION = "7.4"
MAX_VALUE_BYTES = 25 * 1024
MAX_BODY_BYTES = 160 * 1024
VAULT = re.compile(r"[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]\Z")
LOG = logging.getLogger("keyvault_desk")


class AppError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def load_config(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read configuration: {path}. Copy config.example.json first.") from exc
    if isinstance(data, dict) and set(data) == {"nonprod", "prod"}:
        data = [{"name": name, "env": "prod" if env == "prod" else "test"}
                for env, name in data.items()]
    if not isinstance(data, list) or not data:
        raise ValueError("Configuration must be a non-empty array of vault objects with name, optional nickname and env.")
    vaults = {}
    for entry in data:
        if (not isinstance(entry, dict) or not {"name", "env"} <= set(entry)
                or set(entry) - {"name", "nickname", "env"}):
            raise ValueError("Each vault requires name and env; only nickname is optional.")
        name = entry["name"]
        if not isinstance(name, str) or not VAULT.fullmatch(name) or "--" in name:
            raise ValueError("Vault names must be valid Azure Key Vault names (3-24 characters).")
        env = entry["env"]
        if not isinstance(env, str) or env not in ("prod", "test", "dev", "other"):
            raise ValueError("Vault env must be prod, test, dev or other.")
        nickname = entry.get("nickname", "")
        if (not isinstance(nickname, str) or len(nickname) > 80
                or any(ord(char) < 32 or ord(char) == 127 for char in nickname)):
            raise ValueError("Vault nickname must be text up to 80 characters without control characters.")
        name = name.lower()
        if name in vaults:
            raise ValueError("Each vault name must be unique (case-insensitive).")
        vaults[name] = {"name": name, "nickname": nickname.strip(), "env": env}
    return vaults


class AzureCLI:
    def __init__(self):
        self.executable = shutil.which("az")
        if not self.executable:
            raise ValueError("Azure CLI is missing. Install it, then run az login.")
        self._lock = threading.Lock()
        self._token = None
        self._expires = 0
        self._disabled = False

    def clear(self):
        with self._lock:
            self._token = None
            self._expires = 0

    def lock(self):
        with self._lock:
            self._disabled = True
            self._token = None
            self._expires = 0

    def token(self):
        with self._lock:
            if self._disabled:
                raise AppError(401, "session_locked", "Session locked. Restart Key Vault Desk.")
            if self._token and time.time() < self._expires - 120:
                LOG.debug("Azure CLI token cache hit")
                return self._token
        LOG.debug("Azure CLI token acquisition started resource=https://vault.azure.net")
        started = time.monotonic()
        # Azure CLI uses az.cmd on Windows. List arguments avoid shell interpolation.
        try:
            result = subprocess.run(
                [self.executable, "account", "get-access-token", "--resource",
                 "https://vault.azure.net", "--output", "json", "--only-show-errors"],
                capture_output=True, text=True, encoding="utf-8", timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            LOG.debug("Azure CLI token acquisition timed out")
            raise AppError(504, "login_timeout", "Azure CLI timed out. Check az login in your terminal.") from exc
        except OSError as exc:
            LOG.debug("Azure CLI launch failed exception=%s", type(exc).__name__)
            raise AppError(503, "cli_unavailable", "Cannot start Azure CLI. Check your installation.") from exc
        if result.returncode:
            LOG.debug("Azure CLI token acquisition failed exit_code=%s", result.returncode)
            raise AppError(401, "login_required", "Run az login in your terminal, then retry. Check the selected tenant.")
        try:
            payload = json.loads(result.stdout)
            token = payload["accessToken"]
            expires = float(payload["expires_on"])
            if not isinstance(token, str) or not token or expires <= time.time():
                raise ValueError("Invalid token")
        except (ValueError, KeyError, TypeError) as exc:
            LOG.debug("Azure CLI token response invalid exception=%s", type(exc).__name__)
            raise AppError(502, "cli_response", "Azure CLI returned an invalid token. Update Azure CLI and sign in again.") from exc
        with self._lock:
            if self._disabled:
                raise AppError(401, "session_locked", "Session locked. Restart Key Vault Desk.")
            self._token, self._expires = token, expires
        LOG.debug("Azure CLI token acquired duration_ms=%.0f expires_in_seconds=%.0f",
                  (time.monotonic() - started) * 1000, expires - time.time())
        return token


class KeyVault:
    def __init__(self, vaults, identity):
        self.vaults = vaults
        self.identity = identity
        # Do not forward tokens through proxy redirects or a malicious nextLink.
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, env, path, method="GET", body=None, absolute=False):
        base = f"https://{self.vaults[env]['name']}.vault.azure.net"
        expected_host = f"{self.vaults[env]['name']}.vault.azure.net"
        reason = None
        try:
            if not isinstance(path, str) or not path or "\\" in path or any(
                    ord(char) < 33 or ord(char) == 127 for char in path):
                raise ValueError("Invalid URL characters")
            url = urllib.parse.urljoin(f"{base}/secrets", path) if absolute else f"{base}{path}"
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https":
                reason = "https_required"
            elif parsed.hostname != expected_host:
                reason = "host_mismatch"
            elif parsed.port not in (None, 443):
                reason = "port_not_https"
            elif parsed.username is not None or parsed.password is not None:
                reason = "userinfo_not_allowed"
            elif parsed.fragment:
                reason = "fragment_not_allowed"
            elif (absolute and parsed.path not in ("/secrets", "/secrets/")) or (
                    not absolute and parsed.path != "/secrets" and not parsed.path.startswith("/secrets/")):
                reason = "unexpected_path"
        except ValueError:
            reason = "malformed_url"
        if reason:
            LOG.debug("Azure URL rejected env=%s continuation=%s expected_host=%s reason=%s "
                      "(returned URL omitted; queries may contain continuation tokens)",
                      env, absolute, expected_host, reason)
            raise AppError(502, "invalid_next_link", "Azure returned an unexpected inventory continuation URL.")
        operation = "inventory" if parsed.path in ("/secrets", "/secrets/") else "secret"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(2):
            LOG.debug("Azure request env=%s method=%s operation=%s host=%s continuation=%s attempt=%s",
                      env, method, operation, expected_host, absolute, attempt + 1)
            token = self.identity.token()
            request = urllib.request.Request(
                url, data=data, method=method,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                         "Accept": "application/json"},
            )
            started = time.monotonic()
            try:
                with self.opener.open(request, timeout=30) as response:
                    payload = json.load(response)
                LOG.debug("Azure response env=%s operation=%s status=success duration_ms=%.0f",
                          env, operation, (time.monotonic() - started) * 1000)
                return payload
            except urllib.error.HTTPError as exc:
                status = exc.code
                LOG.debug("Azure response env=%s operation=%s status=%s duration_ms=%.0f",
                          env, operation, status, (time.monotonic() - started) * 1000)
                exc.close()
                if status == 401 and attempt == 0:
                    LOG.debug("Azure rejected token; refreshing once env=%s", env)
                    self.identity.clear()
                    continue
                messages = {
                    400: ("azure_validation", "Azure rejected the request. Check the secret's properties and whether it is managed by a certificate."),
                    401: ("login_required", "Azure session expired. Run az login, then retry."),
                    403: ("forbidden", "Access denied. Check Key Vault RBAC, tenant, firewall and private-network access."),
                    404: ("not_found", "Vault or secret not found. Refresh the inventory."),
                    409: ("conflict", "This name may be soft-deleted or conflicting. Resolve it in Azure Portal."),
                    429: ("throttled", "Azure is throttling requests. Wait before retrying."),
                }
                code, message = messages.get(status, ("azure_error", "Azure could not complete the request. Retry or check Azure Portal."))
                raise AppError(status if status in messages else 502, code, message) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                LOG.debug("Azure transport failure env=%s operation=%s exception=%s cause=%s duration_ms=%.0f",
                          env, operation, type(exc).__name__, type(cause).__name__,
                          (time.monotonic() - started) * 1000)
                raise AppError(504, "network_error", "Cannot reach Key Vault. Check VPN, DNS, firewall and network connectivity.") from exc
            except (ValueError, UnicodeError) as exc:
                LOG.debug("Azure response invalid env=%s operation=%s exception=%s",
                          env, operation, type(exc).__name__)
                raise AppError(502, "azure_response", "Azure returned an invalid response.") from exc

    def inventory(self, env):
        url = f"/secrets?api-version={API_VERSION}&maxresults=25"
        items = []
        seen = set()
        for page in range(1000):
            if url in seen:
                raise AppError(502, "pagination_error", "Azure returned a repeated inventory page.")
            seen.add(url)
            payload = self._request(env, url, absolute=page > 0)
            for item in payload["value"]:
                name = urllib.parse.unquote(urllib.parse.urlsplit(item["id"]).path.split("/")[2])
                attributes = item.get("attributes", {})
                items.append({
                    "name": name, "enabled": attributes.get("enabled", True),
                    "updated": attributes.get("updated"), "expires": attributes.get("exp"),
                    "notBefore": attributes.get("nbf"),
                })
            url = payload.get("nextLink")
            LOG.debug("Inventory page env=%s page=%s page_items=%s total_items=%s has_next=%s",
                      env, page + 1, len(payload["value"]), len(items), bool(url))
            if not url:
                return sorted(items, key=lambda item: item["name"].lower())
        raise AppError(502, "inventory_limit", "Inventory exceeded 25,000 entries. Use Azure CLI for this vault.")

    def read(self, env, name):
        payload = self._request(env, f"/secrets/{name}?api-version={API_VERSION}")
        return {"name": name, "value": payload["value"]}

    def save(self, env, name, value):
        # Preserve existing secret properties; PUT without attributes can reset them.
        properties = {}
        try:
            current = self._request(env, f"/secrets/{name}?api-version={API_VERSION}")
        except AppError as exc:
            if exc.status != 404:
                raise
        else:
            properties = {key: current[key] for key in ("tags", "contentType") if key in current}
            attributes = current.get("attributes", {})
            properties["attributes"] = {
                key: attributes[key] for key in ("enabled", "exp", "nbf") if key in attributes
            }
        self._request(env, f"/secrets/{name}?api-version={API_VERSION}",
                      method="PUT", body={"value": value, **properties})
        return {"name": name}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeskServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, vaults, service):
        super().__init__(address, Handler)
        self.vaults = vaults
        self.service = service
        self.session = secrets.token_urlsafe(32)
        self.locked = False
        self.session_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "KeyVaultDesk"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Never log URLs, request bodies, tokens, secret names or values.
        pass

    def _respond(self, status, body, content_type="application/json; charset=utf-8"):
        self.response_status = status
        LOG.debug("Local response id=%s env=%s operation=%s status=%s code=%s duration_ms=%.0f",
                  self.request_id, self.request_environment, self.request_operation, status,
                  body.get("error", {}).get("code", "-") if isinstance(body, dict) else "-",
                  (time.monotonic() - self.request_started) * 1000)
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _check_request(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            raise AppError(403, "invalid_host", "Open the exact local address printed in the terminal.")
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            raise AppError(403, "invalid_origin", "Cross-origin requests are not allowed.")
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            raise AppError(403, "invalid_origin", "Cross-origin requests are not allowed.")

    def _check_session(self):
        supplied = self.headers.get("X-Desk-Token", "")
        with self.server.session_lock:
            if self.server.locked:
                raise AppError(401, "session_locked", "Session locked. Restart Key Vault Desk.")
            if not hmac.compare_digest(supplied.encode("utf-8"), self.server.session.encode("utf-8")):
                raise AppError(403, "invalid_session", "Invalid local session. Reload the page.")

    def _json_body(self):
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            raise AppError(400, "invalid_body", "Send an application/json body with Content-Length.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AppError(400, "invalid_body", "Invalid Content-Length.") from exc
        if not 0 < length <= MAX_BODY_BYTES:
            raise AppError(413, "body_too_large", "Request body is empty or too large.")
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise AppError(400, "invalid_json", "Request must contain valid JSON.") from exc
        if not isinstance(data, dict):
            raise AppError(400, "invalid_body", "Request must contain a JSON object.")
        return data

    def _dispatch(self):
        self.connection.settimeout(15)
        self._check_request()
        path = urllib.parse.urlsplit(self.path).path
        if self.command == "GET" and path in ("/", "/app.js", "/style.css", "/favicon.svg"):
            filename, mime = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
                "/favicon.svg": ("favicon.svg", "image/svg+xml"),
            }[path]
            body = (ROOT / "static" / filename).read_bytes()
            if path == "/":
                body = body.replace(b"__DESK_TOKEN__", self.server.session.encode("ascii"))
            self._respond(200, body, mime)
            return
        self._check_session()
        if path == "/api/session" and self.command == "GET":
            self._respond(200, {"vaults": self.server.vaults, "authentication": "Azure CLI"})
            return
        if path == "/api/lock" and self.command == "POST":
            with self.server.session_lock:
                self.server.locked = True
            self.server.service.identity.lock()
            self._respond(200, {"locked": True})
            return
        match = re.fullmatch(r"/api/([a-zA-Z0-9-]{1,24})/secrets(?:/([a-zA-Z0-9-]{1,127}))?", path)
        if not match:
            raise AppError(404, "not_found", "Endpoint not found.")
        env, name = match.groups()
        if env not in self.server.vaults:
            raise AppError(404, "not_found", "Vault is not configured.")
        if self.command == "GET":
            result = self.server.service.read(env, name) if name else {"secrets": self.server.service.inventory(env)}
        elif self.command == "PUT" and name:
            data = self._json_body()
            if set(data) != {"value", "confirmProduction"} or not isinstance(data["value"], str):
                raise AppError(400, "invalid_value", "A string value and production confirmation are required.")
            try:
                size = len(data["value"].encode("utf-8"))
            except UnicodeError as exc:
                raise AppError(400, "invalid_value", "Secret value must contain valid Unicode text.") from exc
            if not 0 < size <= MAX_VALUE_BYTES:
                raise AppError(400, "invalid_value", "Secret values must contain 1-25,600 UTF-8 bytes.")
            if not isinstance(data["confirmProduction"], bool):
                raise AppError(400, "invalid_confirmation", "Production confirmation must be true or false.")
            if self.server.vaults[env]["env"] == "prod" and data["confirmProduction"] is not True:
                raise AppError(400, "production_confirmation", "Explicit confirmation is required for production writes.")
            if not self.server.write_lock.acquire(blocking=False):
                raise AppError(409, "write_in_progress", "Another save is running. Wait before retrying.")
            try:
                self._check_session()
                result = self.server.service.save(env, name, data["value"])
            finally:
                self.server.write_lock.release()
        else:
            raise AppError(405, "method_not_allowed", "Operation not supported.")
        # A lock action invalidates pending responses, including secret values.
        self._check_session()
        self._respond(200, result)

    def _handle(self):
        self.request_started = time.monotonic()
        request_id = secrets.token_hex(4)
        route = re.fullmatch(r"/api/([a-zA-Z0-9-]{1,24})/secrets(?:/[a-zA-Z0-9-]{1,127})?",
                             self.path.split("?", 1)[0])
        vault = self.server.vaults.get(route.group(1)) if route else None
        environment = vault["env"] if vault else "-"
        operation = "secret" if route and "/secrets/" in route.group(0) else "inventory" if route else "local"
        self.request_id = request_id
        self.request_environment = environment
        self.request_operation = operation
        LOG.debug("Local request id=%s env=%s operation=%s", request_id, environment, operation)
        try:
            self._dispatch()
        except AppError as exc:
            LOG.warning("Local request failed id=%s env=%s operation=%s status=%s code=%s",
                        request_id, environment, operation, exc.status, exc.code)
            self._respond(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
        except (TimeoutError, ConnectionError):
            LOG.warning("Local request timed out id=%s", request_id)
            self._respond(408, {"error": {"code": "request_timeout", "message": "Local request timed out."}})
        except Exception as exc:
            LOG.error("Unexpected request failure id=%s exception=%s; enable --debug for code locations",
                      request_id, type(exc).__name__)
            for frame in traceback.extract_tb(exc.__traceback__):
                LOG.debug("Failure location id=%s file=%s line=%s function=%s",
                          request_id, Path(frame.filename).name, frame.lineno, frame.name)
            self._respond(500, {"error": {"code": "internal_error", "message": "Unexpected server error. Check the server terminal."}})

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_OPTIONS = _handle


def main():
    parser = argparse.ArgumentParser(description="Key Vault Desk: local UI using your Azure CLI identity.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--debug", action="store_true", help="Print sanitized diagnostic logs to the terminal")
    parser.add_argument("--open", action="store_true", help="Open the app in the default browser after start")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        vaults = load_config(args.config)
        server = DeskServer(("127.0.0.1", args.port), vaults, KeyVault(vaults, AzureCLI()))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Cannot start Key Vault Desk: {exc}\n")
    print(f"Key Vault Desk: {server.origin}\nAuthentication: existing az login session.")
    for vault in vaults.values():
        print(f"Vault: {vault['name']} | Environment: {vault['env']}")
    print("Local use only. Ctrl+C stops the server. No secret values are written to app storage.")
    if args.debug:
        print("Debug enabled: sanitized diagnostics on stderr. Tokens, values, names and URL queries are omitted.")
    if args.open:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Key Vault Desk.")
    finally:
        server.service.identity.lock()
        server.server_close()


if __name__ == "__main__":
    main()
