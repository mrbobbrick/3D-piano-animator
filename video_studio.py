"""
video_studio.py - the 3D Video Studio: a page in your browser for styling the
falling notes (video_style.py), previewing single frames and rendering videos
(render_video.py). The page talks to this small local server, which runs
Blender and ffmpeg on this computer; nothing leaves it.

    python video_studio.py [--port 8765] [--no-browser]

Starting it again while it runs just opens the page. It stops from the
page's Quit button, or by itself after IDLE_EXIT_S with no page open and
nothing rendering.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_video  # noqa: E402
import video_style  # noqa: E402

PORT = 8765
STATIC = os.path.join(HERE, "studio")
RENDERS = os.path.join(HERE, "Renders")
WORK = os.path.join(RENDERS, ".studio")
UPLOADS = os.path.join(RENDERS, "uploads")
SETTINGS = os.path.join(WORK, "settings.json")
IDLE_EXIT_S = 15 * 60
NO_WINDOW = render_video.NO_WINDOW
MIDI_EXT = (".mid", ".midi")
KEEP_PREVIEWS = 60                       # preview frames kept on disk (the page's history)
PREVIEW_FILE = re.compile(r"preview_(\d+)\.json")


def console_python():
    """python.exe next to pythonw.exe (child processes need a real stdout)."""
    exe = sys.executable
    alt = os.path.join(os.path.dirname(exe), "python.exe")
    return alt if os.path.basename(exe).lower() == "pythonw.exe" and os.path.exists(alt) else exe


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(d):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)


def song_dirs():
    home = os.path.expanduser("~")
    dirs = load_settings().get("song_dirs") or [
        os.path.join(HERE, "MIDIs"),
        os.path.join(home, "OneDrive", "Desktop", "Notewright", "My scores"),
        os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "Desktop"),
        os.path.join(home, "Downloads"), os.path.join(home, "Music"), UPLOADS]
    return [d for d in dirs if os.path.isdir(d)]


def list_songs():
    seen, out = set(), []
    for d in song_dirs():
        try:
            names = sorted(os.listdir(d), key=str.lower)
        except OSError:
            continue
        for fn in names:
            p = os.path.normcase(os.path.abspath(os.path.join(d, fn)))
            if fn.lower().endswith(MIDI_EXT) and p not in seen:
                seen.add(p)
                out.append({"path": os.path.join(d, fn), "name": os.path.splitext(fn)[0], "folder": d})
    return out


_song_cache = {}


def song_info(path):
    key = (path, os.path.getmtime(path))
    if key not in _song_cache:
        from midi_loader import load_song
        s = load_song(path)
        dur = max((n.end for n in s.notes), default=0.0)
        bins = 200
        density = {"R": [0] * bins, "L": [0] * bins}          # notes starting in each slice, per hand
        for n in s.notes:
            density.setdefault(n.hand, [0] * bins)[min(bins - 1, int(n.start / max(dur, 1e-6) * bins))] += 1
        _song_cache[key] = {"path": path, "title": s.title, "notes": len(s.notes),
                            "duration": round(dur, 2),
                            "first": round(min((n.start for n in s.notes), default=0.0), 2),
                            "hands": sorted({n.hand for n in s.notes}), "density": density}
    return _song_cache[key]


def list_videos():
    out = []
    if os.path.isdir(RENDERS):
        for fn in os.listdir(RENDERS):
            if fn.lower().endswith(".mp4"):
                p = os.path.join(RENDERS, fn)
                out.append({"name": fn, "size": os.path.getsize(p), "time": os.path.getmtime(p)})
    return sorted(out, key=lambda v: -v["time"])


def first_id():
    """Preview and clip numbers: tenths of a second since 2026, so they keep growing across restarts."""
    return int((time.time() - 1767225600) * 10)


def remove_quietly(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "video"


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
class Worker:
    """The Blender that stays open for previews (blender/preview_worker.py)."""

    def __init__(self):
        self.proc = None

    def _start(self):
        self.proc = subprocess.Popen(
            [render_video.BLENDER, "-b", "--factory-startup", "-P", os.path.join(HERE, "blender", "preview_worker.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=NO_WINDOW)
        self._wait("@@READY")

    def _wait(self, marker):
        tail = []
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.startswith("@@"):
                if line.startswith(marker) or line.startswith("@@DONE"):
                    return
                raise RuntimeError(line[len("@@FAIL "):] or " | ".join(tail[-5:]))
            tail = (tail + [line])[-40:]
        self.proc = None
        raise RuntimeError("Blender closed: " + " | ".join(tail[-5:]))

    def render(self, argv):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        self.proc.stdin.write(json.dumps({"argv": argv}) + "\n")
        self.proc.stdin.flush()
        self._wait("@@DONE")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(5)
            except Exception:
                self.proc.kill()


class Previews:
    """
    One preview at a time; a request while one runs replaces the waiting
    one. The song's hands are solved once (export3d.Export, kept per song)
    and Blender stays open between previews (Worker).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self.state = {"state": "idle"}
        self.seq = first_id()
        self.exports = {}
        self.worker = Worker()
        threading.Thread(target=self._loop, daemon=True).start()

    def request(self, req):
        with self.lock:
            self.seq += 1
            req["id"] = self.seq
            self.pending = req
            if self.state.get("state") != "running":
                self.state = {"state": "queued", "id": self.seq}
        return self.seq

    def status(self):
        with self.lock:
            st = dict(self.state)
            if self.pending:
                st["waiting"] = self.pending["id"]
            if st.get("state") == "running":
                st["elapsed"] = round(time.time() - st["started"], 1)
            return st

    def _export(self, song, clones, pairs=1, lead=3.0):
        import export3d
        key = (song, os.path.getmtime(song), tuple(clones), pairs, lead)
        if key not in self.exports:
            if len(self.exports) > 4:
                self.exports.clear()
            exp = self.exports[key] = export3d.Export(song, clones, pairs)
            exp.lead = lead
            # which notes the hands really press: slow, so worked out in the background for previews
            # (a full render gets it from its own frames)
            threading.Thread(target=exp.pressed_map, daemon=True).start()
        return self.exports[key]

    def _loop(self):
        while True:
            with self.lock:
                req, self.pending = self.pending, None
                if req:
                    self.state = {"state": "running", "id": req["id"], "started": time.time()}
            if not req:
                time.sleep(0.15)
                continue
            os.makedirs(WORK, exist_ok=True)
            out = os.path.join(WORK, f"preview_{req['id']}.png")
            t0 = time.time()
            try:
                t = float(req["t"])
                anim = os.path.join(WORK, "preview.json")
                style = video_style.resolve(req["style"])
                exp = self._export(req["song"], video_style.clone_shifts(style), video_style.shared_pairs(style),
                                   video_style.enter_lead(style))
                exp.save(anim, 30, t - 0.6, t, video_start=exp.default_start(), video_end=exp.default_end(),
                         pressed=getattr(exp, "_pressed", None))
                style_file = os.path.join(WORK, "preview_style.json")
                video_style.dump(style, style_file)
                self.worker.render([anim, "--style", style_file, "--still", out, "--view", req.get("view", "auto"),
                                    "--quality", req.get("quality", "fast"), "--res", req.get("res", "1280x720")])
                if not os.path.exists(out):
                    raise RuntimeError("Blender didn't write the frame")
                st = {"state": "done", "id": req["id"], "url": f"/preview/{os.path.basename(out)}",
                      "seconds": round(time.time() - t0, 1), "t": req["t"]}
                # kept next to the frame, so the page's history survives a reload
                meta = dict(st, song=req["song"], style=req["style"], shape=req.get("shape", "16:9"),
                            view=req.get("view", "auto"), quality=req.get("quality", "fast"), time=time.time())
                with open(out[:-4] + ".json", "w", encoding="utf-8") as fh:
                    json.dump(meta, fh)
            except Exception as e:
                st = {"state": "error", "id": req["id"], "error": str(e)}
                traceback.print_exc()
            with self.lock:
                self.state = st
            self._prune()

    @staticmethod
    def _prune():
        """Keep the newest KEEP_PREVIEWS frames and a few clips."""
        for prefix, ext, keep in (("preview_", ".png", KEEP_PREVIEWS), ("clip_", ".mp4", 4)):
            files = sorted((f for f in os.listdir(WORK) if f.startswith(prefix) and f.endswith(ext)),
                           key=lambda f: os.path.getmtime(os.path.join(WORK, f)))
            for f in files[:-keep]:
                remove_quietly(os.path.join(WORK, f), os.path.join(WORK, f[:-len(ext)] + ".json"))

    @staticmethod
    def history(song=None):
        """The preview frames still on disk (oldest first), for one song or all."""
        out = []
        if not os.path.isdir(WORK):
            return out
        for f in os.listdir(WORK):
            m = PREVIEW_FILE.fullmatch(f)
            if not m or not os.path.exists(os.path.join(WORK, f"preview_{m[1]}.png")):
                continue
            try:
                with open(os.path.join(WORK, f), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            if song is None or os.path.normcase(meta.get("song", "")) == os.path.normcase(song):
                out.append(meta)
        return sorted(out, key=lambda m: m["id"])

    @staticmethod
    def delete(ids):
        for i in ids:
            n = int(i)                                    # only our own numbered files
            remove_quietly(os.path.join(WORK, f"preview_{n}.png"), os.path.join(WORK, f"preview_{n}.json"))


class Render:
    """
    Video renders (render_video.py in a child process), one at a time with
    the rest queued, and progress from the frames folder.
    """

    def __init__(self):
        self.proc = None
        self.info = {"state": "idle"}
        self.queue = []
        self.lock = threading.Lock()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def submit(self, req):
        """Start a render now, or queue it behind the one running. Returns "started" or "queued"."""
        with self.lock:
            if self.running() or self.info.get("state") == "running":
                self.queue.append(req)
                return "queued"
        self.start(req)
        return "started"

    def dequeue(self, i):
        with self.lock:
            if 0 <= i < len(self.queue):
                self.queue.pop(i)

    def _next(self):
        with self.lock:
            req = self.queue.pop(0) if self.queue else None
        if req:
            try:
                self.start(req)
            except Exception as e:
                with self.lock:
                    self.info = {"state": "error", "error": str(e), "name": req.get("name", "")}

    def start(self, req):
        song = req["song"]
        name = safe_name(req.get("name") or os.path.splitext(os.path.basename(song))[0])
        out = os.path.join(RENDERS, name + ".mp4")
        os.makedirs(WORK, exist_ok=True)
        style_file = os.path.join(WORK, f"style_{safe_name(name)}.json")
        video_style.dump(video_style.resolve(req["style"]), style_file)
        cmd = [console_python(), "-u", os.path.join(HERE, "render_video.py"), song, out,
               "--style", style_file, "--quality", req.get("quality", "balanced"),
               "--fps", str(int(req.get("fps", 30))), "--res", req.get("res", "1920x1080"),
               "--view", req.get("view", "auto")]
        if req.get("start") not in (None, ""):
            cmd += ["--start", str(float(req["start"]))]
        if req.get("end") not in (None, ""):
            cmd += ["--end", str(float(req["end"]))]
        log = open(os.path.join(WORK, "render.log"), "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     creationflags=NO_WINDOW, bufsize=1)
        with self.lock:
            self.info = {"state": "running", "stage": "starting", "out": out, "name": os.path.basename(out),
                         "started": time.time(), "quality": req.get("quality"), "log": []}
        threading.Thread(target=self._read, args=(self.proc, log), daemon=True).start()

    def _read(self, proc, log):
        for line in proc.stdout:
            log.write(line)
            log.flush()
            line = line.rstrip()
            with self.lock:
                info = self.info
                if line.startswith("@stage "):
                    info["stage"] = line.split(" ", 1)[1]
                    if info["stage"] == "frames":
                        info["frames_started"] = time.time()
                        info["frames_at_start"] = self._count(info)
                elif line.startswith("@frames "):
                    _, n, folder = line.split(" ", 2)
                    info["total"], info["dir"] = int(n), folder
                elif line.startswith("@done "):
                    info["done_path"] = line.split(" ", 1)[1]
                elif line:
                    info["log"] = (info["log"] + [line])[-30:]
        code = proc.wait()
        log.close()
        with self.lock:
            if self.info.get("state") == "cancelled":
                pass
            elif code == 0:
                self.info["state"] = "done"
            else:
                self.info["state"] = "error"
                self.info["error"] = "\n".join(self.info.get("log", [])[-8:]) or f"exit code {code}"
            self.info["finished"] = time.time()
        self._next()

    @staticmethod
    def _count(info):
        d = info.get("dir")
        if not d or not os.path.isdir(d):
            return 0
        return sum(1 for f in os.listdir(d) if f.endswith(".png"))

    def status(self):
        with self.lock:
            info = {k: v for k, v in self.info.items() if k != "log"}
            info["log_tail"] = self.info.get("log", [])[-3:]
            info["queue"] = [{"name": q.get("name") or os.path.basename(q["song"]), "quality": q.get("quality"),
                              "res": q.get("res"), "fps": q.get("fps")} for q in self.queue]
        if info.get("state") == "running":
            done = self._count(info)
            info["done"] = done
            now = time.time()
            info["elapsed"] = round(now - info["started"])
            fs = info.get("frames_started")
            new = done - info.get("frames_at_start", 0)
            if fs and new >= 3 and info.get("total"):
                rate = (now - fs) / new
                info["per_frame"] = round(rate, 2)
                info["eta"] = round(rate * (info["total"] - done) + 20)
        return info

    def cancel(self):
        if self.running():
            with self.lock:
                self.info["state"] = "cancelled"
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                self.proc.kill()


class Clips:
    """A few seconds of video at preview quality, to see the motion (one at a time; a new one replaces it)."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.state = {"state": "idle"}
        self.seq = first_id()

    def start(self, req):
        self.cancel()
        with self.lock:
            self.seq += 1
            n = self.seq
        os.makedirs(WORK, exist_ok=True)
        out = os.path.join(WORK, f"clip_{n}.mp4")
        style_file = os.path.join(WORK, "clip_style.json")
        video_style.dump(video_style.resolve(req["style"]), style_file)
        t = float(req["t"])
        quality = "flat" if req.get("quality") == "flat" else "preview"
        cmd = [console_python(), "-u", os.path.join(HERE, "render_video.py"), req["song"], out,
               "--style", style_file, "--quality", quality, "--fps", "24", "--res", req.get("res", "1280x720"),
               "--view", req.get("view", "auto"), "--start", str(t), "--end", str(t + float(req.get("seconds", 3)))]
        proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=NO_WINDOW)
        with self.lock:
            self.proc = proc
            self.state = {"state": "running", "id": n, "started": time.time(), "t": t}
        threading.Thread(target=self._wait, args=(proc, n, out), daemon=True).start()
        return n

    def _wait(self, proc, n, out):
        code = proc.wait()
        with self.lock:
            if self.state.get("id") != n:
                return
            if code == 0 and os.path.exists(out):
                self.state = {"state": "done", "id": n, "url": f"/preview/{os.path.basename(out)}",
                              "seconds": round(time.time() - self.state["started"], 1), "t": self.state["t"]}
            elif self.state.get("state") != "cancelled":
                self.state = {"state": "error", "id": n, "error": f"the clip render stopped (code {code})"}

    def cancel(self):
        with self.lock:
            proc = self.proc
            if self.state.get("state") == "running":
                self.state = dict(self.state, state="cancelled")
        if proc and proc.poll() is None and os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)

    def status(self):
        with self.lock:
            st = dict(self.state)
        if st.get("state") == "running":
            st["elapsed"] = round(time.time() - st["started"], 1)
        return st


PREVIEWS = Previews()
RENDER = Render()
CLIPS = Clips()
LAST_SEEN = [time.time()]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "HandThesiaStudio"

    def log_message(self, fmt, *args):
        pass

    # ----- helpers
    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if a and b else size - 1
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    return
                left -= len(chunk)

    # ----- routes
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        p = url.path
        if p.startswith("/api/"):
            LAST_SEEN[0] = time.time()
        try:
            if p == "/":
                return self._file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
            if p.startswith("/static/"):
                f = os.path.normpath(os.path.join(STATIC, p[len("/static/"):]))
                if not f.startswith(STATIC):
                    return self._send(403, {"error": "forbidden"})
                return self._file(f)
            if p == "/api/ping":
                return self._send(200, {"app": "hand-thesia-studio"})
            if p == "/api/state":
                st = load_settings()
                return self._send(200, {
                    "spec": video_style.spec_json(), "groups": video_style.GROUPS,
                    "presets": {k: video_style.preset(k) for k in video_style.PRESETS},
                    "styles": [{"name": n, "style": s} for n, s in video_style.list_styles()],
                    "songs": list_songs(), "videos": list_videos(), "last": st.get("last", {})})
            if p == "/api/song":
                return self._send(200, song_info(q["path"][0]))
            if p == "/api/status":
                return self._send(200, {"preview": PREVIEWS.status(), "render": RENDER.status(),
                                        "clip": CLIPS.status()})
            if p == "/api/previews":
                return self._send(200, Previews.history(q["song"][0] if "song" in q else None))
            if p == "/api/videos":
                return self._send(200, list_videos())
            if p.startswith("/preview/"):
                f = os.path.basename(p)
                return self._file(os.path.join(WORK, f), "video/mp4" if f.endswith(".mp4") else "image/png")
            if p.startswith("/video/"):
                return self._file(os.path.join(RENDERS, os.path.basename(urllib.parse.unquote(p[7:]))), "video/mp4")
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        LAST_SEEN[0] = time.time()
        try:
            if p == "/api/upload":
                name = safe_name(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["name"][0])
                if not name.lower().endswith(MIDI_EXT):
                    return self._send(400, {"error": "Only MIDI files (.mid)"})
                os.makedirs(UPLOADS, exist_ok=True)
                n = int(self.headers.get("Content-Length") or 0)
                path = os.path.join(UPLOADS, name)
                with open(path, "wb") as fh:
                    fh.write(self.rfile.read(n))
                return self._send(200, {"path": path})
            body = self._json()
            if p == "/api/preview":
                return self._send(200, {"id": PREVIEWS.request(body)})
            if p == "/api/render":
                return self._send(200, {"ok": True, "result": RENDER.submit(body)})
            if p == "/api/render/dequeue":
                RENDER.dequeue(int(body["index"]))
                return self._send(200, {"ok": True})
            if p == "/api/previews/delete":
                ids = body.get("ids") or []
                if body.get("all"):
                    ids = [m["id"] for m in Previews.history(body.get("song"))]
                Previews.delete(ids)
                return self._send(200, {"deleted": len(ids)})
            if p == "/api/clip":
                return self._send(200, {"id": CLIPS.start(body)})
            if p == "/api/clip/cancel":
                CLIPS.cancel()
                return self._send(200, {"ok": True})
            if p == "/api/render/cancel":
                RENDER.cancel()
                return self._send(200, {"ok": True})
            if p == "/api/style/save":
                video_style.save_style(body["name"], body["style"])
                return self._send(200, {"ok": True})
            if p == "/api/style/delete":
                video_style.delete_style(body["name"])
                return self._send(200, {"ok": True})
            if p == "/api/last":
                st = load_settings()
                st["last"] = body
                save_settings(st)
                return self._send(200, {"ok": True})
            if p == "/api/open":
                target = RENDERS
                if body.get("video"):
                    target = os.path.join(RENDERS, os.path.basename(body["video"]))
                if os.name == "nt":
                    if os.path.isfile(target):
                        subprocess.Popen(["explorer", "/select,", target])
                    else:
                        os.makedirs(target, exist_ok=True)
                        os.startfile(target)
                return self._send(200, {"ok": True})
            if p == "/api/quit":
                self._send(200, {"ok": True})
                threading.Thread(target=shutdown, daemon=True).start()
                return
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(400, {"error": str(e)})


SERVER = [None]


def shutdown():
    time.sleep(0.3)
    RENDER.queue.clear()
    RENDER.cancel()
    CLIPS.cancel()
    PREVIEWS.worker.stop()
    if SERVER[0]:
        SERVER[0].shutdown()


def watchdog():
    while True:
        time.sleep(30)
        if time.time() - LAST_SEEN[0] > IDLE_EXIT_S and not RENDER.running():
            shutdown()
            return


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.load(r).get("app") == "hand-thesia-studio"
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="3D Video Studio (browser)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    if already_running(args.port):
        if not args.no_browser:
            webbrowser.open(url)
        return
    os.makedirs(WORK, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:                 # pythonw: keep a log instead
        log = open(os.path.join(WORK, "studio.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    SERVER[0] = srv
    threading.Thread(target=watchdog, daemon=True).start()
    print(f"3D Video Studio at {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
