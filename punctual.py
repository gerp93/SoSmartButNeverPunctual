#!/usr/bin/env python3
"""SoSmartButNeverPunctual: keep Samsung appliance clocks in sync via SmartThings.

SmartThings has a "Sync Time" button for Samsung appliances but no way to run
it on a schedule. This script sends the same command through the SmartThings
cloud API, and keeps its own OAuth login alive so it can run unattended
(cron, systemd, Windows Task Scheduler, ...).

Standard library only, Python 3.9+. See README.md.

    python punctual.py setup     # one-time: authorize this script
    python punctual.py select    # pick which appliances to sync
    python punctual.py sync      # set their clocks (this is what you schedule)

Exit codes: 0 ok, 1 error / some device failed, 2 login needed (run setup).
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import getpass
import json
import os
import random
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

__version__ = "0.1.0"

API_URL = "https://api.smartthings.com/v1"
AUTHORIZE_URL = f"{API_URL}/oauth/authorize"
TOKEN_URL = f"{API_URL}/oauth/token"
DEFAULT_SCOPE = "r:devices:* x:devices:*"

# Undocumented, community-discovered command that sets a Samsung appliance clock.
CLOCK_RESOURCE = "/configuration/vs/0"
CLOCK_KEY = "x.com.samsung.da.currentTime"
CLOCK_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Refresh the access token (~24h lifetime) when less than this is left.
REFRESH_MARGIN_SECONDS = 6 * 3600
LOCK_STALE_SECONDS = 600
DPAPI_PREFIX = b"dpapi1:"


class PunctualError(Exception):
    """A failure to report to the user (exit code 1)."""


class AuthRequired(PunctualError):
    """The stored login is missing or no longer valid (exit code 2)."""


TASK_NAME = "SoSmartButNeverPunctual"
LOG_MAX_BYTES = 200_000

# Where log lines go besides stdout. The GUI adds a sink; main() and the GUI set the file.
LOG_SINKS: list = []
_log_path = None


def set_log_file(path) -> None:
    global _log_path
    _log_path = Path(path) if path else None


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    if sys.stdout is not None:  # None under pythonw.exe (scheduled runs)
        with contextlib.suppress(Exception):
            print(line, flush=True)
    if _log_path is not None:
        with contextlib.suppress(OSError):
            _log_path.parent.mkdir(parents=True, exist_ok=True)
            if _log_path.exists() and _log_path.stat().st_size > LOG_MAX_BYTES:
                os.replace(str(_log_path), str(_log_path) + ".1")
            with open(_log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    for sink in LOG_SINKS:
        with contextlib.suppress(Exception):
            sink(line)


# --------------------------------------------------------------------------
# Storage: tokens + device selection live outside the repo, in a per-user dir.
# --------------------------------------------------------------------------

def config_dir() -> Path:
    override = os.environ.get("SSBNP_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "SoSmartButNeverPunctual"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sosmartbutneverpunctual"


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI: only the same Windows user can decrypt."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError(ctypes.GetLastError(), "Windows DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


class Store:
    def __init__(self, directory: Path):
        self.dir = directory
        self.state_path = directory / "state"
        self.config_path = directory / "config.json"

    def _write_private(self, path: Path, data: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))

    def load_state(self):
        if not self.state_path.exists():
            return None
        raw = self.state_path.read_bytes()
        if raw.startswith(DPAPI_PREFIX):
            if sys.platform != "win32":
                raise PunctualError("state file was written on Windows; run setup again here")
            try:
                raw = _dpapi(base64.b64decode(raw[len(DPAPI_PREFIX):]), protect=False)
            except OSError as exc:
                raise PunctualError(
                    f"cannot decrypt {self.state_path} ({exc}). Windows only lets the user who ran "
                    "setup read it; run the scheduled job as that user, or run setup again."
                )
        return json.loads(raw.decode("utf-8"))

    def load_state_safe(self):
        """Like load_state, but None instead of an error (for showing status in a UI)."""
        try:
            return self.load_state()
        except (PunctualError, ValueError):
            return None

    def save_state(self, state: dict) -> None:
        raw = json.dumps(state).encode("utf-8")
        if sys.platform == "win32":
            raw = DPAPI_PREFIX + base64.b64encode(_dpapi(raw, protect=True))
        self._write_private(self.state_path, raw)

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def save_config(self, config: dict) -> None:
        self._write_private(self.config_path, json.dumps(config, indent=2).encode("utf-8"))

    @contextlib.contextmanager
    def lock(self):
        """One run at a time: refresh tokens are single-use, so overlap would lose one."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "lock"
        for attempt in range(2):
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                age = time.time() - path.stat().st_mtime
                if attempt == 0 and age > LOCK_STALE_SECONDS:
                    path.unlink()
                    continue
                raise PunctualError(f"another run is in progress (lock file {path})")
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                path.unlink()


# --------------------------------------------------------------------------
# HTTP + OAuth
# --------------------------------------------------------------------------

def http(method: str, url: str, headers=None, body: bytes | None = None, timeout: int = 30):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PunctualError(f"network error talking to SmartThings: {getattr(exc, 'reason', exc)}")
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {"raw": raw[:300].decode("utf-8", "replace")}
    return status, payload


def describe_error(payload) -> str:
    if not isinstance(payload, dict):
        return str(payload)[:200]
    error = payload.get("error")
    if isinstance(error, dict):
        return json.dumps(error)[:300]
    parts = [str(payload[k]) for k in ("error", "error_description", "message", "raw") if payload.get(k)]
    return "; ".join(parts)[:300] or "no details"


def token_request(client_id: str, client_secret: str, form: dict) -> dict:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    status, payload = http(
        "POST",
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        body=urllib.parse.urlencode(form).encode(),
    )
    if status == 200 and "access_token" in payload:
        return payload
    detail = describe_error(payload)
    if status in (400, 401, 403):
        raise AuthRequired(f"SmartThings rejected the login (HTTP {status}: {detail}). Run: python punctual.py setup")
    raise PunctualError(f"token request failed (HTTP {status}: {detail})")


def apply_tokens(store: Store, state: dict, payload: dict) -> None:
    """Both tokens change on every refresh; save immediately so the new one is never lost."""
    now = time.time()
    state["access_token"] = payload["access_token"]
    state["refresh_token"] = payload.get("refresh_token") or state.get("refresh_token")
    state["expires_at"] = now + float(payload.get("expires_in", 86399))
    state["refreshed_at"] = now
    store.save_state(state)


def refresh(store: Store, state: dict) -> None:
    if not state.get("refresh_token"):
        raise AuthRequired("no refresh token stored. Run: python punctual.py setup")
    payload = token_request(
        state["client_id"],
        state["client_secret"],
        {
            "grant_type": "refresh_token",
            "refresh_token": state["refresh_token"],
            "client_id": state["client_id"],
        },
    )
    apply_tokens(store, state, payload)
    log("refreshed SmartThings login")


def load_logged_in(store: Store) -> dict:
    state = store.load_state()
    if not state or not state.get("client_id"):
        raise AuthRequired("not set up yet. Run: python punctual.py setup")
    if state.get("expires_at", 0) - time.time() < REFRESH_MARGIN_SECONDS:
        refresh(store, state)
    return state


def api(store: Store, state: dict, method: str, path: str, payload=None):
    """Call the SmartThings API; on a 401 refresh once and retry."""
    url = path if path.startswith("http") else f"{API_URL}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {state['access_token']}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, data = http(method, url, headers=headers, body=body)
        if status == 401 and attempt == 0:
            refresh(store, state)
            continue
        return status, data
    return status, data


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------

def list_devices(store: Store, state: dict) -> list:
    devices, url = [], "/devices"
    while url:
        status, data = api(store, state, "GET", url)
        if status != 200:
            raise PunctualError(f"could not list devices (HTTP {status}: {describe_error(data)})")
        for item in data.get("items", []):
            capabilities = {
                cap.get("id")
                for component in item.get("components", [])
                for cap in component.get("capabilities", [])
            }
            devices.append(
                {
                    "id": item["deviceId"],
                    "name": item.get("label") or item.get("name") or item["deviceId"],
                    "execute": "execute" in capabilities,
                    "model": (item.get("ocf") or {}).get("modelNumber", ""),
                }
            )
        url = (data.get("_links") or {}).get("next", {}).get("href")
    return devices


def fetch_devices(store: Store) -> list:
    with store.lock():
        return list_devices(store, load_logged_in(store))


def clock_timestamp(use_utc: bool) -> str:
    now = datetime.now(timezone.utc) if use_utc else datetime.now()
    return now.strftime(CLOCK_FORMAT)


def clock_command(timestamp: str) -> dict:
    return {
        "commands": [
            {
                "component": "main",
                "capability": "execute",
                "command": "execute",
                "arguments": [CLOCK_RESOURCE, {CLOCK_KEY: timestamp}],
            }
        ]
    }


def set_clock(store: Store, state: dict, device_id: str, timestamp: str):
    status, data = api(store, state, "POST", f"/devices/{device_id}/commands", clock_command(timestamp))
    if status != 200:
        return False, f"HTTP {status}: {describe_error(data)}"
    results = data.get("results") or [{}]
    result = results[0].get("status", "UNKNOWN")
    return result in ("COMPLETED", "ACCEPTED"), result


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def parse_redirect(text: str):
    """Accept a full redirected URL or a bare code; return (code, state, error)."""
    text = text.strip()
    if "://" not in text and "?" not in text:
        return text, None, None
    query = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
    first = lambda key: query.get(key, [None])[0]
    return first("code"), first("state"), first("error")


def capture_via_loopback(redirect_uri: str, timeout: int = 300) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path != (parsed.path or "/"):
                self.send_response(404)
                self.end_headers()
                return
            result["url"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h3>Authorized. You can close this tab and go back to the terminal.</h3>")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", parsed.port), Handler)
    server.timeout = 2
    deadline = time.time() + timeout
    try:
        while "url" not in result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "url" not in result:
        raise PunctualError("timed out waiting for the browser redirect")
    return result["url"]


def is_loopback_redirect(redirect_uri: str) -> bool:
    parsed = urllib.parse.urlparse(redirect_uri)
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1") and bool(parsed.port)


DEFAULT_REDIRECT_URI = "http://localhost:8123/callback"
APP_DISPLAY_NAME = "SoSmartButNeverPunctual"


def create_oauth_app(token: str, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE):
    """Create the user's own OAuth-In app with a short-lived personal access token.

    Returns (client_id, client_secret). The token is only used for these calls and
    is never stored. Equivalent to `smartthings apps:create`.
    """
    headers = {"Authorization": f"Bearer {token.strip()}", "Accept": "application/json",
               "Content-Type": "application/json"}
    app_request = {
        "appName": f"sosmart-{uuid.uuid4().hex[:16]}",
        "displayName": APP_DISPLAY_NAME,
        "description": "Keeps appliance clocks in sync",
        "singleInstance": True,
        "appType": "API_ONLY",
        "classifications": ["CONNECTED_SERVICE"],
        "apiOnly": {},
    }
    status, data = http("POST", f"{API_URL}/apps", headers=headers, body=json.dumps(app_request).encode())
    if status not in (200, 201) or "appId" not in data:
        raise PunctualError(f"could not create the SmartThings app (HTTP {status}: {describe_error(data)}). "
                            "Check the token has the Apps permissions, or create the app by hand (README step 1).")
    app_id = data["appId"]
    oauth_request = {"clientName": APP_DISPLAY_NAME, "scope": scope.split(), "redirectUris": [redirect_uri]}
    status, data = http("POST", f"{API_URL}/apps/{app_id}/oauth/generate", headers=headers,
                        body=json.dumps(oauth_request).encode())
    client_id = data.get("oauthClientId") if isinstance(data, dict) else None
    client_secret = data.get("oauthClientSecret") if isinstance(data, dict) else None
    if status not in (200, 201) or not client_id or not client_secret:
        raise PunctualError(f"the app was created but its login details could not be generated "
                            f"(HTTP {status}: {describe_error(data)}). App ID: {app_id}")
    return client_id, client_secret


def authorize(store: Store, client_id: str, client_secret: str, redirect_uri: str = DEFAULT_REDIRECT_URI,
              scope: str = DEFAULT_SCOPE, ask_pasted=None, force_paste: bool = False, notify=log) -> None:
    """Browser login (OAuth authorization code) and save the tokens.

    Uses a one-shot localhost listener when the redirect URI is http://localhost:PORT/...;
    otherwise calls ask_pasted(prompt) to get the redirected URL pasted by the user.
    """
    if not (client_id and client_secret and redirect_uri):
        raise PunctualError("client ID, client secret and redirect URI are all required")

    nonce = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": nonce,
        },
        quote_via=urllib.parse.quote,
    )
    url = f"{AUTHORIZE_URL}?{query}"

    use_loopback = is_loopback_redirect(redirect_uri) and not force_paste
    if not use_loopback and ask_pasted is None:
        raise PunctualError("this redirect URI is not a localhost address, so the redirected URL has to be pasted")
    notify("Opening your browser to approve access. If it does not open, visit: " + url)
    with contextlib.suppress(Exception):
        webbrowser.open(url)

    if use_loopback:
        notify(f"Waiting for the redirect on {redirect_uri} ...")
        redirected = capture_via_loopback(redirect_uri)
    else:
        redirected = ask_pasted(
            "After you approve, your browser is sent to the redirect URI. That page may fail to load;\n"
            "that is fine. Copy the full address from the browser's address bar and paste it here."
        )

    code, returned_state, error = parse_redirect(redirected)
    if error:
        raise PunctualError(f"SmartThings reported: {error}")
    if not code:
        raise PunctualError("no authorization code found in what you pasted")
    if returned_state is not None and returned_state != nonce:
        raise PunctualError("state mismatch; start setup again")

    payload = token_request(
        client_id,
        client_secret,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        },
    )
    state = {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "scope": scope}
    apply_tokens(store, state, payload)
    notify(f"Logged in. Saved to {store.dir}")


def cmd_setup(store: Store, args) -> int:
    print("Authorize this script with your SmartThings OAuth-In app (see README, step 1).\n")
    if args.token:
        print("Creating your SmartThings app with the token you gave ...")
        client_id, client_secret = create_oauth_app(args.token, args.redirect_uri or DEFAULT_REDIRECT_URI, args.scope)
    else:
        client_id = args.client_id or input("Client ID: ").strip()
        client_secret = args.client_secret or getpass.getpass("Client secret (hidden): ").strip()
    redirect_uri = args.redirect_uri or input(f"Redirect URI you registered [{DEFAULT_REDIRECT_URI}]: ").strip() \
        or DEFAULT_REDIRECT_URI
    authorize(store, client_id, client_secret, redirect_uri, args.scope,
              ask_pasted=lambda prompt: input(f"\n{prompt}\n\nRedirected URL (or just the code): "),
              force_paste=args.paste, notify=print)
    print("Next: python punctual.py select")
    return 0


def print_devices(devices: list, selected_ids: set) -> None:
    for index, device in enumerate(devices, 1):
        flags = []
        if device["id"] in selected_ids:
            flags.append("selected")
        if not device["execute"]:
            flags.append("no clock command")
        suffix = f"   [{', '.join(flags)}]" if flags else ""
        print(f"  {index:>2}) {device['name']}{suffix}")


def cmd_devices(store: Store, args) -> int:
    with store.lock():
        state = load_logged_in(store)
        devices = list_devices(store, state)
    selected = {d["id"] for d in store.load_config().get("devices", [])}
    print_devices(devices, selected)
    return 0


def cmd_select(store: Store, args) -> int:
    with store.lock():
        state = load_logged_in(store)
        devices = list_devices(store, state)
    selected = {d["id"] for d in store.load_config().get("devices", [])}
    print_devices(devices, selected)
    print("\nOnly devices that accept the clock command can be synced. Try one first and look at its display.")
    answer = input("Numbers to sync, comma-separated (e.g. 2,3): ").strip()
    chosen = []
    for part in answer.replace(",", " ").split():
        if not part.isdigit() or not 1 <= int(part) <= len(devices):
            raise PunctualError(f"'{part}' is not a number from 1 to {len(devices)}")
        device = devices[int(part) - 1]
        if not device["execute"]:
            print(f"  skipping {device['name']}: it has no 'execute' capability")
            continue
        chosen.append({"id": device["id"], "name": device["name"]})
    if not chosen:
        raise PunctualError("nothing selected")
    config = store.load_config()
    config["devices"] = chosen
    store.save_config(config)
    print("Saved: " + ", ".join(d["name"] for d in chosen))
    print("Test it with: python punctual.py sync --dry-run   then   python punctual.py sync")
    return 0


def resolve_targets(store: Store, state: dict, requested: list | None) -> list:
    if not requested:
        saved = store.load_config().get("devices", [])
        if not saved:
            raise PunctualError("no devices chosen. Run: python punctual.py select")
        return saved
    devices = list_devices(store, state)
    targets = []
    for wanted in requested:
        matches = [d for d in devices if wanted in (d["id"], d["name"])]
        if len(matches) != 1:
            raise PunctualError(f"'{wanted}' matched {len(matches)} devices; use the exact name or ID")
        targets.append({"id": matches[0]["id"], "name": matches[0]["name"]})
    return targets


def record_last_sync(store: Store, ok: bool, summary: str) -> None:
    config = store.load_config()
    config["last_sync"] = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok": ok, "summary": summary}
    store.save_config(config)


def run_sync(store: Store, devices=None, use_utc: bool = False, jitter: float = 0, dry_run: bool = False):
    """Set the clocks. Returns (succeeded_names, failed_names); raises PunctualError if it can't start."""
    with store.lock():
        try:
            state = load_logged_in(store)
            targets = resolve_targets(store, state, devices)
            if jitter and not dry_run:
                delay = random.uniform(0, jitter * 60)
                log(f"waiting {delay / 60:.1f} min (jitter) so runs do not all hit SmartThings at once")
                time.sleep(delay)
            timestamp = clock_timestamp(use_utc)
            succeeded, failed = [], []
            for target in targets:
                if dry_run:
                    log(f"[dry run] would set {target['name']} to {timestamp}")
                    continue
                ok, detail = set_clock(store, state, target["id"], timestamp)
                log(f"{target['name']}: {'ok' if ok else 'FAILED'} ({detail}) -> {timestamp}")
                (succeeded if ok else failed).append(target["name"])
        except PunctualError as exc:
            if not dry_run:
                record_last_sync(store, False, str(exc))
            raise
        if not dry_run:
            summary = f"set {timestamp}: " + ", ".join(succeeded or ["nothing"])
            if failed:
                summary += "; FAILED: " + ", ".join(failed)
            record_last_sync(store, not failed, summary)
    return succeeded, failed


def cmd_sync(store: Store, args) -> int:
    _, failed = run_sync(store, args.device, args.utc, args.jitter, args.dry_run)
    return 1 if failed else 0


# --------------------------------------------------------------------------
# Scheduling: Task Scheduler on Windows, crontab on Linux/macOS.
# --------------------------------------------------------------------------

CRON_MARKER = "# SoSmartButNeverPunctual"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_time(text: str):
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise PunctualError("time must look like 03:17 (24-hour HH:MM)")
    return int(match.group(1)), int(match.group(2))


def sync_command_line(store: Store, default_dir: bool, jitter: int) -> list:
    """The command a scheduler should run: python (windowless on Windows) + this script + sync."""
    python = sys.executable
    if sys.platform == "win32":
        windowless = Path(python).with_name("pythonw.exe")
        if windowless.exists():
            python = str(windowless)
    args = [python, str(Path(__file__).resolve())]
    if not default_dir:
        args += ["--config-dir", str(store.dir)]
    return args + ["sync", "--jitter", str(jitter)]


def _run(command: list):
    return subprocess.run(command, capture_output=True, text=True, creationflags=_NO_WINDOW)


def schedule_exists() -> bool:
    if sys.platform == "win32":
        return _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0
    listing = _run(["crontab", "-l"]) if shutil.which("crontab") else None
    return bool(listing and listing.returncode == 0 and CRON_MARKER in listing.stdout)


def schedule_install(store: Store, at: str = "03:17", jitter: int = 20, task_name: str = TASK_NAME) -> None:
    hour, minute = parse_time(at)
    command = sync_command_line(store, default_dir=(store.dir == config_dir()), jitter=jitter)
    if sys.platform == "win32":
        exe, argument = command[0], subprocess.list2cmdline(command[1:])
        quote = lambda text: text.replace("'", "''")
        script = (
            f"$a = New-ScheduledTaskAction -Execute '{quote(exe)}' -Argument '{quote(argument)}'; "
            f"$t = New-ScheduledTaskTrigger -Daily -At '{hour:02d}:{minute:02d}'; "
            "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2); "
            f"Register-ScheduledTask -TaskName '{quote(task_name)}' -Action $a -Trigger $t -Settings $s -Force | Out-Null"
        )
        result = _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
        if result.returncode != 0:
            raise PunctualError("could not create the scheduled task: " + (result.stderr or result.stdout).strip()[:300])
    else:
        if not shutil.which("crontab"):
            raise PunctualError("crontab not found; install cron or use a systemd timer (see README)")
        existing = _run(["crontab", "-l"])
        lines = existing.stdout.splitlines() if existing.returncode == 0 else []
        lines = [ln for ln in lines if CRON_MARKER not in ln]
        lines.append(f"{minute} {hour} * * * {shlex.join(command)} >> {shlex.quote(str(store.dir / 'punctual.log'))} 2>&1 {CRON_MARKER}")
        result = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True)
        if result.returncode != 0:
            raise PunctualError("could not update crontab: " + result.stderr.strip()[:300])
    config = store.load_config()
    config["schedule"] = {"time": f"{hour:02d}:{minute:02d}"}
    store.save_config(config)


def schedule_remove(store: Store, task_name: str = TASK_NAME) -> None:
    if sys.platform == "win32":
        result = _run(["schtasks", "/Delete", "/TN", task_name, "/F"])
        if result.returncode != 0 and schedule_exists():
            raise PunctualError("could not remove the scheduled task: " + (result.stderr or result.stdout).strip()[:300])
    elif shutil.which("crontab"):
        existing = _run(["crontab", "-l"])
        if existing.returncode == 0:
            lines = [ln for ln in existing.stdout.splitlines() if CRON_MARKER not in ln]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + ("\n" if lines else ""),
                           capture_output=True, text=True)
    config = store.load_config()
    config.pop("schedule", None)
    store.save_config(config)


def cmd_schedule(store: Store, args) -> int:
    schedule_install(store, args.at, args.jitter)
    print(f"Scheduled: sync every day at {args.at} (random delay up to {args.jitter} min). Remove with: unschedule")
    return 0


def cmd_unschedule(store: Store, args) -> int:
    schedule_remove(store)
    print("Daily sync removed.")
    return 0


def cmd_status(store: Store, args) -> int:
    state = store.load_state()
    print(f"Config folder: {store.dir}")
    if not state:
        print("Login: not set up (run: python punctual.py setup)")
        return 2
    left = state.get("expires_at", 0) - time.time()
    age = time.time() - state.get("refreshed_at", 0)
    print(f"Login: set up. Access token {'expires in ' + format_hours(left) if left > 0 else 'expired'}; "
          f"last refreshed {format_hours(age)} ago.")
    print("The refresh token lapses after about 29 days without a run; scheduled runs keep it alive.")
    devices = store.load_config().get("devices", [])
    print("Devices: " + (", ".join(d["name"] for d in devices) if devices else "none chosen (run: select)"))
    config = store.load_config()
    scheduled = config.get("schedule", {}).get("time")
    print("Schedule: " + (f"daily at {scheduled}" if scheduled and schedule_exists() else "not scheduled"))
    last = config.get("last_sync")
    if last:
        print(f"Last sync: {last['at']} - {'ok' if last['ok'] else 'FAILED'} ({last['summary']})")
    return 0


def format_hours(seconds: float) -> str:
    return f"{abs(seconds) / 3600:.1f} h"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="punctual.py",
        description="Keep Samsung appliance clocks in sync through SmartThings.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config-dir", help="where tokens/config are stored (or set SSBNP_CONFIG_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="one-time login with your SmartThings OAuth-In app")
    setup.add_argument("--client-id")
    setup.add_argument("--client-secret", help="prefer the hidden prompt over passing this on the command line")
    setup.add_argument("--token", help="a short-lived SmartThings personal access token; creates the OAuth app for you "
                                       "(instead of --client-id/--client-secret)")
    setup.add_argument("--redirect-uri")
    setup.add_argument("--scope", default=DEFAULT_SCOPE)
    setup.add_argument("--paste", action="store_true", help="paste the redirected URL instead of listening on localhost")
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("devices", help="list your SmartThings devices").set_defaults(func=cmd_devices)
    sub.add_parser("select", help="choose which devices to sync").set_defaults(func=cmd_select)

    sync = sub.add_parser("sync", help="set the clock on the chosen devices")
    sync.add_argument("--device", action="append", help="device name or ID (repeatable); default: the chosen devices")
    sync.add_argument("--utc", action="store_true", help="send UTC instead of this computer's local time")
    sync.add_argument("--jitter", type=float, default=0, metavar="MINUTES",
                      help="wait a random 0..MINUTES before sending (spreads load on SmartThings)")
    sync.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    sync.set_defaults(func=cmd_sync)

    schedule = sub.add_parser("schedule", help="run sync every day (Task Scheduler on Windows, crontab elsewhere)")
    schedule.add_argument("--at", default="03:17", metavar="HH:MM", help="24-hour time (default 03:17)")
    schedule.add_argument("--jitter", type=int, default=20, metavar="MINUTES", help="random delay (default 20)")
    schedule.set_defaults(func=cmd_schedule)
    sub.add_parser("unschedule", help="remove the daily sync").set_defaults(func=cmd_unschedule)

    sub.add_parser("status", help="show login and device state (no network)").set_defaults(func=cmd_status)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(Path(args.config_dir) if args.config_dir else config_dir())
    set_log_file(store.dir / "punctual.log")
    try:
        return args.func(store, args)
    except AuthRequired as exc:
        log(f"error: {exc}")
        return 2
    except PunctualError as exc:
        log(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
