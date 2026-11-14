"""PCStation Game Launcher (back end).

Finds installed games (Steam, Epic Games, GOG), keeps the library you choose in a JSON file, and serves
a fullscreen "big picture" interface on 127.0.0.1 that you drive with your phone controllers.
The interface (pcstation_launcher_ui.py) runs in a Chromium-based browser window (Edge/Chrome), which
reads the phones' virtual Xbox pads through the browser Gamepad API.

Safety: the server only listens on this PC (127.0.0.1), every request needs a random per-run token, and
it can only launch things that are in your library.
"""
import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from pcstation_launcher_ui import HTML as UI_HTML

try:
    import winreg
except ImportError:          # not Windows (development / tests)
    winreg = None

APP_DIR_NAME = "PCStationGamepad"
PORTS = range(8766, 8776)
LAUNCHABLE = {".exe", ".lnk", ".bat", ".cmd", ".url"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ART_KINDS = {"cover": "art", "hero": "hero", "logo": "logo"}       # URL kind -> key stored on a game
USER_COLORS = ["#2f7bff", "#ff4d5a", "#3fb950", "#f08ad8", "#f5a623", "#22c1c3", "#9b6bff", "#ff7a45"]
DEFAULT_USERS = ["Player 1", "Player 2", "Player 3", "Player 4"]
DEFAULT_SETTINGS = {"sound": True, "intro": True, "art_download": True, "auto_open": False}
STEAM_SKIP_IDS = {"228980"}  # Steamworks Common Redistributables
STEAM_SKIP_PREFIXES = ("Steam Linux Runtime", "Proton", "Steamworks Common")


# ------------------------------------------------------------------ storage

def data_dir(base=None):
    base = base or os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, APP_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def load_settings(directory=None):
    return _read_json(os.path.join(directory or data_dir(), "settings.json"), {})


def save_settings(settings, directory=None):
    _write_json(os.path.join(directory or data_dir(), "settings.json"), settings)


class Library:
    """The games you picked. Saved to library.json in %APPDATA%\\PCStationGamepad."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.games = _read_json(path, {}).get("games", [])

    def _save(self):
        _write_json(self.path, {"version": 1, "games": self.games})

    def get(self, gid):
        with self.lock:
            return next((g for g in self.games if g["id"] == gid), None)

    def ids(self):
        with self.lock:
            return {g["id"] for g in self.games}

    def add(self, entries):
        added = 0
        with self.lock:
            have = {g["id"] for g in self.games}
            for e in entries:
                if e["id"] not in have:
                    self.games.append(dict(e, added=int(time.time()), last_played=0))
                    have.add(e["id"])
                    added += 1
            if added:
                self._save()
        return added

    def remove(self, gid):
        with self.lock:
            n = len(self.games)
            self.games = [g for g in self.games if g["id"] != gid]
            if len(self.games) != n:
                self._save()
            return len(self.games) != n

    def rename(self, gid, name):
        name = " ".join(str(name).split())[:80]
        with self.lock:
            g = self.get(gid)
            if g and name:
                g["name"] = name
                self._save()
                return True
        return False

    def touch(self, gid):
        with self.lock:
            g = self.get(gid)
            if g:
                g["last_played"] = int(time.time())
                self._save()

    def public(self):
        with self.lock:
            return [public(g) for g in self.games]


    def set_art(self, gid, **kinds):
        """kinds: cover= / hero= / logo= file paths."""
        with self.lock:
            g = self.get(gid)
            if not g:
                return False
            for kind, path in kinds.items():
                if path and kind in ART_KINDS:
                    g[ART_KINDS[kind]] = path
            self._save()
            return True


class Profile:
    """Users (like console accounts) and which user each controller belongs to. Saved to profile.json."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        d = _read_json(path, {})
        self.users = d.get("users") or [{"id": f"u{i + 1}", "name": n, "color": USER_COLORS[i]} for i, n in enumerate(DEFAULT_USERS)]
        self.controllers = d.get("controllers", {})        # key -> {"user": id, "alias": str}

    def _save(self):
        _write_json(self.path, {"version": 1, "users": self.users, "controllers": self.controllers})

    def user(self, uid):
        return next((u for u in self.users if u["id"] == uid), None)

    def add_user(self, name, color=None):
        name = " ".join(str(name).split())[:24] or f"Player {len(self.users) + 1}"
        with self.lock:
            used = {u["color"] for u in self.users}
            color = color if color in USER_COLORS else next((c for c in USER_COLORS if c not in used), USER_COLORS[len(self.users) % len(USER_COLORS)])
            u = {"id": "u" + secrets.token_hex(3), "name": name, "color": color}
            self.users.append(u)
            self._save()
            return u

    def rename_user(self, uid, name):
        name = " ".join(str(name).split())[:24]
        with self.lock:
            u = self.user(uid)
            if u and name:
                u["name"] = name
                self._save()

    def recolor_user(self, uid, color):
        with self.lock:
            u = self.user(uid)
            if u and color in USER_COLORS:
                u["color"] = color
                self._save()

    def remove_user(self, uid):
        with self.lock:
            if len(self.users) <= 1 or not self.user(uid):
                return False
            self.users = [u for u in self.users if u["id"] != uid]
            for c in self.controllers.values():              # their controllers become unassigned again
                if c.get("user") == uid:
                    c["user"] = ""
            self._save()
            return True

    def set_controller(self, key, user=None, alias=None, forget=False):
        key = str(key)[:120]
        with self.lock:
            if forget:
                self.controllers.pop(key, None)
            else:
                c = self.controllers.setdefault(key, {"user": "", "alias": ""})
                if user is not None and (user == "" or self.user(user)):
                    c["user"] = user
                if alias is not None:
                    c["alias"] = " ".join(str(alias).split())[:32]
            self._save()

    def public(self):
        with self.lock:
            return {"users": list(self.users), "controllers": dict(self.controllers)}


def public(g):
    return {"id": g["id"], "name": g["name"], "source": g.get("source", "custom"), "art": bool(g.get("art")),
            "hero": bool(g.get("hero")), "logo": bool(g.get("logo")), "played": g.get("last_played", 0)}


def make_entry(source, key, name, launch, art="", hero="", logo="", art_urls=None):
    e = {"id": f"{source}:{key}", "name": name, "source": source, "launch": launch, "art": art or "", "hero": hero or "", "logo": logo or ""}
    if art_urls:
        e["art_urls"] = art_urls                               # pictures to download later (when online)
    return e


def pretty_name(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"[_]+", " ", base).strip() or base
    return base.title() if base.islower() else base


# ------------------------------------------------------------------ game detection

_ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def parse_vdf(text):
    """Tiny parser for Valve's text KeyValues format. Keys are lower-cased."""
    text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))
    toks = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    pos = 0

    def unesc(s):
        return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(1)), s)

    def block():
        nonlocal pos
        d = {}
        while pos < len(toks):
            s, br = toks[pos]
            if br == "}":
                pos += 1
                return d
            key = unesc(s).lower()
            pos += 1
            if pos >= len(toks):
                break
            s2, br2 = toks[pos]
            if br2 == "{":
                pos += 1
                d[key] = block()
            else:
                d[key] = unesc(s2)
                pos += 1
        return d

    return block()


def steam_root():
    if winreg:
        for hive, sub, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    v = winreg.QueryValueEx(k, val)[0]
                if v and os.path.isdir(v):
                    return os.path.normpath(v)
            except OSError:
                pass
    for p in (os.path.expandvars(r"%ProgramFiles(x86)%\Steam"), r"C:\Program Files (x86)\Steam"):
        if os.path.isdir(p):
            return p
    return None


def steam_libraries(root):
    libs = [os.path.join(root, "steamapps")]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            data = parse_vdf(f.read()).get("libraryfolders", {})
    except OSError:
        data = {}
    for k, v in data.items():
        if not k.isdigit():
            continue
        path = v.get("path") if isinstance(v, dict) else v      # new format has a block, old has a string
        if path:
            p = os.path.join(path, "steamapps")
            if os.path.isdir(p) and p not in libs:
                libs.append(p)
    return libs


def find_steam_art(root, appid):
    """Pictures Steam already cached on this PC: {'cover','hero','logo'} (missing ones are '')."""
    cache = os.path.join(root, "appcache", "librarycache")
    found = {"cover": "", "hero": "", "logo": ""}
    old = {"cover": f"{appid}_library_600x900.jpg", "hero": f"{appid}_library_hero.jpg", "logo": f"{appid}_logo.png"}
    for kind, n in old.items():                                 # older Steam layout
        p = os.path.join(cache, n)
        if os.path.isfile(p):
            found[kind] = p
    want = {"library_600x900.jpg": "cover", "library_hero.jpg": "hero", "logo.png": "logo"}
    header = ""
    for dirpath, _, files in os.walk(os.path.join(cache, appid)):   # newer layout: librarycache/<appid>/<hash>/
        for fn in files:
            if fn in want and not found[want[fn]]:
                found[want[fn]] = os.path.join(dirpath, fn)
            elif fn == "header.jpg" and not header:
                header = os.path.join(dirpath, fn)
    if not header:
        h = os.path.join(cache, f"{appid}_header.jpg")
        header = h if os.path.isfile(h) else ""
    if not found["cover"]:
        found["cover"] = header
    if not found["hero"]:
        found["hero"] = header
    return found


def scan_steam(root=None):
    root = root or steam_root()
    if not root:
        return []
    out = []
    for lib in steam_libraries(root):
        try:
            names = os.listdir(lib)
        except OSError:
            continue
        for fn in names:
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            try:
                with open(os.path.join(lib, fn), encoding="utf-8", errors="replace") as f:
                    st = parse_vdf(f.read()).get("appstate", {})
                appid, name = st.get("appid"), st.get("name")
                flags = int(st.get("stateflags") or 0)
            except (OSError, ValueError):
                continue
            if not appid or not name or not (flags & 4):          # 4 = fully installed
                continue
            if appid in STEAM_SKIP_IDS or name.startswith(STEAM_SKIP_PREFIXES):
                continue
            art = find_steam_art(root, appid)
            out.append(make_entry("steam", appid, name, {"type": "uri", "value": f"steam://rungameid/{appid}"},
                                  art["cover"], art["hero"], art["logo"]))
    return out


def epic_catalog_images(path=None):
    """Epic's launcher caches store pictures (as web links) in catcache.bin: {CatalogItemId: {cover, hero, logo}}."""
    path = path or os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Epic", "EpicGamesLauncher", "Data", "Catalog", "catcache.bin")
    try:
        with open(path, "rb") as f:
            items = json.loads(base64.b64decode(f.read()))
    except (OSError, ValueError):
        return {}
    out = {}
    for it in items if isinstance(items, list) else []:
        imgs = {i.get("type"): i.get("url") for i in it.get("keyImages", []) if str(i.get("url", "")).startswith("https://")}
        tall = imgs.get("DieselGameBoxTall") or imgs.get("OfferImageTall")
        wide = imgs.get("DieselGameBox") or imgs.get("OfferImageWide") or imgs.get("DieselStoreFrontWide")
        urls = {"cover": tall or wide, "hero": wide or tall, "logo": imgs.get("DieselGameBoxLogo")}
        urls = {k: v for k, v in urls.items() if v}
        if urls and it.get("id"):
            out[it["id"]] = urls
    return out


def scan_epic(manifests=None, catcache=None):
    manifests = manifests or os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                                          "Epic", "EpicGamesLauncher", "Data", "Manifests")
    out = []
    try:
        names = os.listdir(manifests)
    except OSError:
        return out
    images = epic_catalog_images(catcache)
    for fn in names:
        if not fn.endswith(".item"):
            continue
        j = _read_json(os.path.join(manifests, fn), {})
        app, name = j.get("AppName"), j.get("DisplayName")
        if not app or not name or j.get("bIsIncompleteInstall"):
            continue
        if j.get("MainGameAppName", app) != app:                   # skip DLC / add-ons
            continue
        out.append(make_entry("epic", app, name,
                              {"type": "uri", "value": f"com.epicgames.launcher://apps/{app}?action=launch&silent=true"},
                              art_urls=images.get(j.get("CatalogItemId"))))
    return out


def scan_gog():
    if not winreg:
        return []
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games") as games:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(games, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(games, sub) as k:
                        def val(n):
                            try:
                                return winreg.QueryValueEx(k, n)[0]
                            except OSError:
                                return ""
                        name, exe, path = val("gameName"), val("exe"), val("path")
                        if name and exe and os.path.isfile(exe):
                            out.append(make_entry("gog", val("gameID") or sub, name,
                                                  {"type": "exe", "value": exe, "cwd": val("workingDir") or path or os.path.dirname(exe),
                                                   "args": val("launchParam")}))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def scan_all(scanners=None):
    found, seen = [], set()
    for fn in (scanners or (scan_steam, scan_epic, scan_gog)):
        try:
            for e in fn():
                if e["id"] not in seen:
                    seen.add(e["id"])
                    found.append(e)
        except Exception:                                              # one broken launcher must not hide the rest
            continue
    found.sort(key=lambda e: e["name"].lower())
    return found


# ------------------------------------------------------------------ file browser (controller friendly)

def quick_places():
    home = os.path.expanduser("~")
    places = [("Desktop", os.path.join(home, "Desktop")), ("Downloads", os.path.join(home, "Downloads")),
              ("Program Files", os.environ.get("ProgramFiles", "")), ("Program Files (x86)", os.environ.get("ProgramFiles(x86)", ""))]
    return [{"name": n, "path": p, "type": "dir", "tag": "place"} for n, p in places if p and os.path.isdir(p)]


def list_dir(path, kind=""):
    wanted = IMAGE_EXTS if kind == "image" else LAUNCHABLE
    if not path:                                                       # top level: drives + quick places
        if os.name == "nt":
            roots = [{"name": f"{c}:\\", "path": f"{c}:\\", "type": "dir", "tag": "drive"}
                     for c in "CDEFGHIJKLMNOPQRSTUVWXYZAB" if os.path.isdir(f"{c}:\\")]
        else:
            roots = [{"name": "/", "path": "/", "type": "dir", "tag": "drive"}]
        return {"path": "", "parent": None, "entries": quick_places() + roots}
    path = os.path.abspath(path)
    entries = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith((".", "$")):
                    continue
                try:
                    if e.is_dir():
                        entries.append({"name": e.name, "path": e.path, "type": "dir"})
                    elif os.path.splitext(e.name)[1].lower() in wanted:
                        entries.append({"name": e.name, "path": e.path, "type": "file"})
                except OSError:
                    continue
    except OSError:
        pass
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    parent = os.path.dirname(path)
    return {"path": path, "parent": "" if parent == path else parent, "entries": entries[:600]}


# ------------------------------------------------------------------ launching

def open_target(target, cwd=None, args=""):
    """Start a program or URI the way double-clicking it would (handles admin prompts too)."""
    if hasattr(os, "startfile"):
        kw = {}
        if args:
            kw["arguments"] = args
        if cwd:
            kw["cwd"] = cwd
        try:
            os.startfile(target, **kw)
        except TypeError:                                              # Python < 3.10
            os.startfile(target)
    else:                                                              # development fallback
        subprocess.Popen(["xdg-open", target])


def launch_entry(entry, opener=open_target):
    l = entry["launch"]
    if l["type"] == "uri":
        opener(l["value"])
    else:
        opener(l["value"], l.get("cwd") or os.path.dirname(l["value"]), l.get("args", ""))


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def download_file(url, dest, limit=12 * 1024 * 1024):
    """Best-effort picture download (https only, images only, size limited). Returns True on success."""
    if not url.startswith("https://"):
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PCStationGamepad/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            if not str(r.headers.get("Content-Type", "")).startswith("image/"):
                return False
            data = r.read(limit + 1)
        if not data or len(data) > limit:
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def art_urls_for(entry):
    """Where to get pictures for a game that has none cached locally."""
    if entry.get("source") == "steam":
        base = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{entry['id'].split(':', 1)[1]}/"
        return {"cover": base + "library_600x900.jpg", "hero": base + "library_hero.jpg", "logo": base + "logo.png"}
    return entry.get("art_urls") or {}


def find_browser():
    cands = []
    if os.name == "nt":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                          os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")]
    for n in ("msedge", "chrome", "google-chrome", "chromium", "brave"):
        w = shutil.which(n)
        if w:
            cands.append(w)
    return next((c for c in cands if os.path.isfile(c)), None)


# ------------------------------------------------------------------ server

class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket bind a port that is already in use, which silently
    # hijacks it (a stale or second instance keeps swallowing connections). Never allow that.
    allow_reuse_address = False
    daemon_threads = True


class Launcher:
    def __init__(self, get_players=None, directory=None, opener=open_target, scanners=None, identify=None, download=download_file):
        self.dir = directory or data_dir()
        self.art_dir = os.path.join(self.dir, "art")
        self.library = Library(os.path.join(self.dir, "library.json"))
        self.profile = Profile(os.path.join(self.dir, "profile.json"))
        self.settings = dict(DEFAULT_SETTINGS, **load_settings(self.dir))
        self.identify = identify or (lambda pid: False)
        self.download = download
        self.get_players = get_players or (lambda: [])
        self.opener = opener
        self.scanners = scanners
        self.token = secrets.token_urlsafe(16)
        self.candidates = {}
        self.port = None
        self.httpd = None
        self.browser_proc = None

    def save_settings(self):
        save_settings(self.settings, self.dir)

    def fetch_art_async(self, entries):
        """Download missing pictures in the background (needs internet; silently skipped when offline)."""
        if not self.settings.get("art_download", True):
            return
        todo = [dict(e) for e in entries]
        threading.Thread(target=self._fetch_art, args=(todo,), daemon=True).start()

    def _fetch_art(self, entries):
        for e in entries:
            urls = art_urls_for(e)
            got = {}
            for kind, url in urls.items():
                if self.library.get(e["id"]) is None or self.library.get(e["id"]).get(ART_KINDS[kind]):
                    continue                                                    # removed meanwhile, or picture already there
                ext = os.path.splitext(url.split("?")[0])[1].lower()
                dest = os.path.join(self.art_dir, f"{safe_name(e['id'])}_{kind}{ext if ext in IMAGE_EXTS else '.jpg'}")
                if self.download(url, dest):
                    got[kind] = dest
            if got:
                self.library.set_art(e["id"], **got)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self):
        handler = make_handler(self)
        for port in PORTS:
            try:
                self.httpd = _Server(("127.0.0.1", port), handler)
                self.port = port
                break
            except OSError:
                continue
        if not self.httpd:
            raise OSError("no free port for the launcher (8766-8775)")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def open_ui(self, fullscreen=True):
        if self.browser_proc is not None and self.browser_proc.poll() is None:
            return                                                     # already open
        browser = find_browser()
        if not browser:
            webbrowser.open(self.url)                                  # press F11 for fullscreen
            return
        args = [browser, f"--app={self.url}", f"--user-data-dir={os.path.join(self.dir, 'browser-profile')}",
                "--no-first-run", "--no-default-browser-check", "--disable-features=Translate",
                "--autoplay-policy=no-user-gesture-required", "--overscroll-history-navigation=0"]
        if fullscreen:
            args.append("--start-fullscreen")
        self.browser_proc = subprocess.Popen(args)

    def close_ui(self):
        p = self.browser_proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def stop(self):
        self.close_ui()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()                                  # release the port


def make_handler(L):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):                                             # blocks DNS-rebinding tricks
            return self.headers.get("Host", "") in (f"127.0.0.1:{L.port}", f"localhost:{L.port}")

        def token_ok(self, q):
            given = self.headers.get("X-Token") or (q.get("t") or [""])[0]
            return secrets.compare_digest(given, L.token)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def route(self, method):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self.host_ok():
                return self.send(403, {"error": "bad host"})
            if method == "GET" and u.path == "/":
                return self.send(200, UI_HTML, "text/html; charset=utf-8", {
                    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
            if not self.token_ok(q):
                return self.send(403, {"error": "bad token"})
            if method == "GET" and u.path.startswith("/art/"):
                gid = unquote(u.path[5:])
                e = L.library.get(gid) or L.candidates.get(gid)
                p = e.get(ART_KINDS.get((q.get("kind") or ["cover"])[0], "art")) if e else ""
                if p and os.path.isfile(p):
                    with open(p, "rb") as f:
                        data = f.read()
                    return self.send(200, data, mimetypes.guess_type(p)[0] or "image/jpeg", {"Cache-Control": "private, max-age=3600"})
                return self.send(404, {"error": "no art"})
            if not u.path.startswith("/api/"):
                return self.send(404, {"error": "not found"})
            api = u.path[5:]
            if method == "GET":
                if api == "library":
                    return self.send(200, {"games": L.library.public()})
                if api == "players":
                    return self.send(200, {"players": L.get_players()})
                if api == "scan":
                    have = L.library.ids()
                    found = [e for e in scan_all(L.scanners) if e["id"] not in have]
                    L.candidates = {e["id"]: e for e in found}
                    return self.send(200, {"games": [public(e) for e in found]})
                if api == "fs":
                    return self.send(200, list_dir((q.get("path") or [""])[0], (q.get("kind") or [""])[0]))
                if api == "profile":
                    return self.send(200, dict(L.profile.public(), settings=L.settings))
            elif method == "POST":
                b = self.body()
                if api == "add":
                    if b.get("path"):
                        p = os.path.abspath(str(b["path"]))
                        if not os.path.isfile(p) or os.path.splitext(p)[1].lower() not in LAUNCHABLE:
                            return self.send(400, {"error": "that is not a program or shortcut"})
                        key = hashlib.sha1(p.lower().encode()).hexdigest()[:12]
                        ents = [make_entry("custom", key, pretty_name(p), {"type": "exe", "value": p, "cwd": os.path.dirname(p), "args": ""})]
                    else:
                        ents = [L.candidates[i] for i in b.get("ids", []) if i in L.candidates]
                    n = L.library.add(ents)
                    L.fetch_art_async(ents)
                    return self.send(200, {"added": n, "games": L.library.public()})
                if api == "remove":
                    L.library.remove(str(b.get("id", "")))
                    return self.send(200, {"games": L.library.public()})
                if api == "rename":
                    L.library.rename(str(b.get("id", "")), b.get("name", ""))
                    return self.send(200, {"games": L.library.public()})
                if api == "launch":
                    e = L.library.get(str(b.get("id", "")))
                    if not e:
                        return self.send(404, {"error": "game not in library"})
                    try:
                        launch_entry(e, L.opener)
                    except Exception as exc:
                        return self.send(500, {"error": f"Could not start {e['name']}: {exc}"})
                    L.library.touch(e["id"])
                    return self.send(200, {"ok": True})
                if api == "setart":
                    g, p = L.library.get(str(b.get("id", ""))), os.path.abspath(str(b.get("path", "")))
                    ext = os.path.splitext(p)[1].lower()
                    if not g or ext not in IMAGE_EXTS or not os.path.isfile(p) or os.path.getsize(p) > 15 * 1024 * 1024:
                        return self.send(400, {"error": "pick a picture (.jpg, .png or .webp)"})
                    os.makedirs(L.art_dir, exist_ok=True)
                    dest = os.path.join(L.art_dir, f"{safe_name(g['id'])}_custom{ext}")
                    shutil.copyfile(p, dest)
                    L.library.set_art(g["id"], cover=dest, hero=dest)
                    return self.send(200, {"games": L.library.public()})
                if api == "user":
                    act = b.get("action")
                    if act == "add":
                        u = L.profile.add_user(b.get("name", ""), b.get("color"))
                        return self.send(200, dict(L.profile.public(), created=u["id"]))
                    if act == "rename":
                        L.profile.rename_user(str(b.get("id", "")), b.get("name", ""))
                    elif act == "color":
                        L.profile.recolor_user(str(b.get("id", "")), b.get("color"))
                    elif act == "remove":
                        L.profile.remove_user(str(b.get("id", "")))
                    return self.send(200, L.profile.public())
                if api == "controller":
                    L.profile.set_controller(b.get("key", ""), b.get("user"), b.get("alias"), bool(b.get("forget")))
                    return self.send(200, L.profile.public())
                if api == "identify":
                    return self.send(200, {"ok": bool(L.identify(str(b.get("pid", ""))))})
                if api == "settings":
                    for k in ("sound", "intro", "art_download"):
                        if k in b:
                            L.settings[k] = bool(b[k])
                    L.save_settings()
                    return self.send(200, {"settings": L.settings})
                if api == "exit":
                    self.send(200, {"ok": True})
                    threading.Timer(0.3, L.close_ui).start()
                    return
            return self.send(404, {"error": "not found"})

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

    return H
