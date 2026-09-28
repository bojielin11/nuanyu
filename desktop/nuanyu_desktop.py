#!/usr/bin/env python3
"""Nuanyu Windows desktop shell and resilient board orchestrator."""

from __future__ import annotations

import json
import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview

try:
    import cv2
except Exception:
    cv2 = None

try:
    import urllib3
except Exception:
    urllib3 = None

# ── Upstream HTTPS pool (keep-alive) ─────────────────────────────────
# The proxy used to open a fresh HTTPS connection with `Connection: close`
# for every forwarded request, costing a cold TLS handshake (~250ms) each
# time.  urllib3's PoolManager reuses keep-alive connections per upstream
# host and transparently rebuilds connections the server has closed.
_proxy_pool = None
_proxy_pool_lock = threading.Lock()


def _get_proxy_pool():
    global _proxy_pool
    if urllib3 is None:
        raise RuntimeError("urllib3 not available")
    with _proxy_pool_lock:
        if _proxy_pool is None:
            _proxy_pool = urllib3.PoolManager(
                maxsize=8,
                timeout=urllib3.Timeout(connect=5.0, read=75.0),
                retries=urllib3.Retry(total=1, connect=1, read=0, status=0),
                cert_reqs="CERT_REQUIRED",
            )
        return _proxy_pool


APP_TITLE = "暖语"
BOARD_APP = "/userdata_fibo/app"
WEB_URL = "http://127.0.0.1:5004/"
CREATE_NO_WINDOW = 0x08000000


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


class DesktopBridge:
    """Provides PC camera frames and emergency PC speaker playback."""

    def __init__(self):
        self._camera = None
        self._camera_lock = threading.Lock()
        self._camera_error = ""
        self._servers = []
        self._audio_files = []

    @property
    def camera_ready(self):
        return self._camera is not None and self._camera.isOpened()

    def start(self):
        self._open_camera()
        endpoints = (
            ("127.0.0.1", 5015),
            ("127.0.0.1", 5016),
            ("127.0.0.1", 5019),
            # The ADB-forwarded 5004 socket is loopback-only. Expose a
            # separate, API-only LAN gateway for the WeChat mini program.
            ("0.0.0.0", 5005),
        )
        for host, port in endpoints:
            try:
                server = ThreadingHTTPServer((host, port), self._handler())
                thread = threading.Thread(
                    target=server.serve_forever,
                    daemon=True,
                    name="NuanyuBridge-%s" % port,
                )
                thread.start()
                self._servers.append(server)
            except OSError:
                # An existing compatible bridge is acceptable; health is checked
                # independently by the orchestrator.
                pass

    def _open_camera(self):
        if cv2 is None:
            self._camera_error = "OpenCV unavailable"
            return
        for index in (0, 1, 2):
            try:
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    ok, _ = cap.read()
                    if ok:
                        self._camera = cap
                        self._camera_error = ""
                        return
                cap.release()
            except Exception as exc:
                self._camera_error = str(exc)[:160]
        self._camera_error = self._camera_error or "no PC camera found"

    def camera_jpeg(self):
        with self._camera_lock:
            if not self.camera_ready:
                self._open_camera()
            if not self.camera_ready:
                return None
            ok, frame = self._camera.read()
            if not ok:
                try:
                    self._camera.release()
                except Exception:
                    pass
                self._camera = None
                self._camera_error = "camera read failed"
                return None
            ok, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
            )
            return encoded.tobytes() if ok else None

    def play_wav(self, payload: bytes):
        if not payload:
            return False
        try:
            import winsound

            target = Path(tempfile.gettempdir()) / (
                "nuanyu_audio_%d.wav" % int(time.time() * 1000)
            )
            target.write_bytes(payload)
            self._audio_files.append(target)
            winsound.PlaySound(
                str(target),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
            threading.Thread(
                target=self._cleanup_audio,
                args=(target,),
                daemon=True,
            ).start()
            return True
        except Exception:
            return False

    def _cleanup_audio(self, path):
        time.sleep(60)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def _handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                return

            def _json(self, value, status=200):
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.server.server_port == 5005:
                    self._proxy_board_api("GET")
                    return
                if self.path == "/health":
                    self._json({
                        "ok": True,
                        "camera_ready": bridge.camera_ready,
                        "camera_error": bridge._camera_error,
                    })
                    return
                if self.path.startswith("/camera"):
                    frame = bridge.camera_jpeg()
                    if frame is None:
                        self._json({"ok": False, "error": bridge._camera_error}, 503)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    return
                self._json({"ok": False, "error": "not found"}, 404)

            def do_POST(self):
                if self.server.server_port == 5005:
                    self._proxy_board_api("POST")
                    return
                upstream = {
                    "/v1/chat/completions":
                        "https://api.deepseek.com/v1/chat/completions",
                    "/doubao/tts":
                        "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                }.get(self.path)
                if upstream:
                    self._proxy_post(upstream)
                    return
                if self.path != "/play":
                    self._json({"ok": False, "error": "not found"}, 404)
                    return
                length = min(int(self.headers.get("Content-Length", "0")), 32 << 20)
                payload = self.rfile.read(length)
                ok = bridge.play_wav(payload)
                self._json({"ok": ok}, 200 if ok else 500)

            def do_OPTIONS(self):
                if self.server.server_port != 5005:
                    self._json({"ok": False, "error": "not found"}, 404)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _proxy_board_api(self, method):
                parsed = urlparse(self.path)
                if not parsed.path.startswith("/api/"):
                    self._json({"ok": False, "error": "API only"}, 404)
                    return
                length = min(int(self.headers.get("Content-Length", "0")), 2 << 20)
                payload = self.rfile.read(length) if length else None
                headers = {"Connection": "close"}
                content_type = self.headers.get("Content-Type")
                if content_type:
                    headers["Content-Type"] = content_type
                cookie = self.headers.get("Cookie")
                if cookie:
                    headers["Cookie"] = cookie
                connection = http.client.HTTPConnection("127.0.0.1", 5004, timeout=45)
                try:
                    connection.request(method, self.path, body=payload, headers=headers)
                    response = connection.getresponse()
                    body = response.read()
                    self.send_response(response.status)
                    self.send_header(
                        "Content-Type",
                        response.headers.get("Content-Type", "application/json; charset=utf-8"),
                    )
                    set_cookie = response.headers.get("Set-Cookie")
                    if set_cookie:
                        self.send_header("Set-Cookie", set_cookie)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._json({
                        "ok": False,
                        "error": "暖语板卡服务暂时不可用",
                        "detail": str(exc)[:160],
                    }, 502)
                finally:
                    connection.close()

            def _proxy_post(self, upstream):
                length = min(int(self.headers.get("Content-Length", "0")), 8 << 20)
                payload = self.rfile.read(length)
                allowed = (
                    "Authorization", "Content-Type", "Accept",
                    "X-Api-Key", "X-Api-Resource-Id", "X-Api-Request-Id",
                )
                headers = {
                    name: self.headers[name]
                    for name in allowed if self.headers.get(name)
                }
                parsed = urlparse(upstream)
                try:
                    pool = _get_proxy_pool()
                    target = urlunparse((
                        "https", parsed.netloc, parsed.path or "/",
                        "", parsed.query, ""))
                    response = pool.request(
                        "POST",
                        target,
                        body=payload,
                        headers=headers,
                        preload_content=False,
                    )
                except Exception as exc:
                    self._json({
                        "error": "desktop network proxy unavailable",
                        "detail": str(exc)[:160],
                    }, 502)
                    return

                self.send_response(response.status)
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    # Stream the provider body through chunk by chunk so the
                    # board hears the first SSE token as soon as it arrives.
                    # DeepSeek keeps the TCP connection open after sending
                    # "data: [DONE]", so blindly reading until EOF would block
                    # forever (and pollute the pool with a half-read stream).
                    # Stop as soon as the SSE end marker is seen.
                    sse_done_marker = b"data: [DONE]"
                    tail = b""
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        tail = (tail + chunk)[-64:]
                        if sse_done_marker in tail:
                            break
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    response.release_conn()
                    self.close_connection = True

        return Handler


class Orchestrator:
    def __init__(self, bridge: DesktopBridge):
        self.bridge = bridge
        self.window = None
        self._running = False
        self._zipvoice_process = None
        self._surge_boot_ready = False
        self._monitor_started = False
        self._initial_started = False
        self._adb = self._find_adb()

    def attach(self, window):
        self.window = window

    def retry_startup(self):
        self.start_async()
        return {"ok": True}

    def on_window_loaded(self):
        """The WebView fires loaded again when splash switches to the app."""
        if self._initial_started:
            return
        self._initial_started = True
        self.start_async()

    def start_async(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._startup, daemon=True).start()

    def _emit(self, step, status, note="", title="", detail="", log=""):
        if not self.window:
            return
        payload = json.dumps({
            "id": step, "status": status, "note": note,
            "title": title, "detail": detail, "log": log,
        }, ensure_ascii=False)
        try:
            self.window.evaluate_js("window.nuanyu && window.nuanyu.update(%s)" % payload)
        except Exception:
            pass

    def _startup(self):
        try:
            self._run_startup()
        except Exception as exc:
            self._emit("ready", "fail", "需要处理")
            try:
                self.window.evaluate_js(
                    "window.nuanyu && window.nuanyu.failed(%s)"
                    % json.dumps(str(exc), ensure_ascii=False)
                )
            except Exception:
                pass
        finally:
            self._running = False

    def _run_startup(self):
        self._emit("desktop_bridge", "running", "检查中",
                   "正在准备电脑外设", "检测摄像头与音频回退通道")
        bridge_ok = self._http_json("http://127.0.0.1:5016/health", timeout=3)
        if bridge_ok:
            camera_note = "电脑摄像头可用" if bridge_ok.get("camera_ready") else "摄像头暂不可用"
            self._emit("desktop_bridge", "ok" if bridge_ok.get("camera_ready") else "warn",
                       camera_note)
        else:
            self._emit("desktop_bridge", "warn", "桥接端口被占用")

        self._emit("adb", "running", "查找中",
                   "正在连接广和通板卡", "等待 ADB 设备响应")
        serial = self._wait_for_device(35)
        self._emit("adb", "ok", serial[-8:], log="ADB device: %s" % serial)

        self._emit("port_map", "running", "配置中",
                   "正在建立稳定通信", "配置 Web、摄像头和 TTS 通道")
        self._configure_ports()
        self._emit("port_map", "ok", "6 条通道")

        # Surge availability is captured when the board imports its TTS
        # providers, so make the PC model ready before starting the board app.
        self._emit("surge", "running", "预加载",
                   "正在预加载 Surge / ZipVoice", "GPU 模型与板卡并行准备")
        self._surge_boot_ready = self._ensure_zipvoice()
        self._configure_ports()

        self._emit("board_runtime", "running", "同步中",
                   "正在启动板卡运行时", "校验核心模块并安装可靠守护服务")
        self._sync_board_payload()
        self._install_runtime_service()
        self._emit("board_runtime", "running", "等待模型",
                   "正在加载板卡模型", "首次启动通常需要 30–60 秒")
        health = self._wait_for_web(100)
        self._emit("board_runtime", "ok", "运行正常")

        self._emit("deepseek", "running", "握手中",
                   "正在连接 DeepSeek", "检查对话服务")
        # 板卡经 WiFi 直连 api.deepseek.com（不走电脑代理）。电脑侧探测仅作
        # 参考信息，不再作为硬性门槛——热点/代理切换时电脑可达性不代表板卡状态。
        deep = self._service_ready(health, "deepseek")
        if deep:
            self._emit("deepseek", "ok", "板卡直连已就绪")
        else:
            self._emit("deepseek", "warn", "板卡直连待确认，稍后自动重试")

        self._emit("drizzle", "running", "检查模型",
                   "正在加载 Drizzle", "验证板卡本地语音模型")
        provider = self._post_json(WEB_URL + "api/tts_provider", {})
        available = (provider or {}).get("available", {})
        if not available.get("drizzle"):
            raise RuntimeError("Drizzle 本地模型未加载")
        self._emit("drizzle", "ok", "本地可用")

        self._emit("stream", "running", "连接豆包",
                   "正在连接豆包 Stream", "读取云端音色")
        voices = self._http_json(WEB_URL + "api/stream/voices", timeout=8)
        doubao_network = self._probe_upstream(
            "https://openspeech.bytedance.com"
        )
        if not voices or not voices.get("ok") or not doubao_network:
            self._emit("stream", "warn", "稍后自动重试")
        else:
            self._emit("stream", "ok", "%d 个音色" % len(voices.get("voices", [])))

        self._emit("surge", "running", "加载模型",
                   "正在加载 Surge / ZipVoice", "优先复用本机 GPU 服务")
        surge = self._surge_boot_ready or self._ensure_zipvoice()
        self._configure_ports()
        provider = self._post_json(WEB_URL + "api/tts_provider", {})
        surge_online = bool((provider or {}).get("available", {}).get("surge_online"))
        if surge and surge_online:
            self._emit("surge", "ok", "GPU 服务在线")
        else:
            self._emit("surge", "warn", "不可用，可使用 Drizzle/Stream")

        self._emit("devices", "running", "检查中",
                   "正在确认摄像头与扬声器", "板卡优先，电脑自动兜底")
        audio = self._adb_shell("aplay -l 2>/dev/null | head -20", timeout=6)
        board_audio = "card " in audio.lower() or "card:" in audio.lower()
        camera_local = self._adb_shell(
            "test -e /dev/video2 && echo yes || echo no", timeout=4
        ).strip() == "yes"
        camera_pc = bool(bridge_ok and bridge_ok.get("camera_ready"))
        notes = []
        notes.append("扬声器:板卡" if board_audio else "扬声器:电脑兜底")
        notes.append("摄像头:板卡" if camera_local else (
            "摄像头:电脑" if camera_pc else "摄像头:缺失"
        ))
        device_status = "ok" if (board_audio and (camera_local or camera_pc)) else "warn"
        self._emit("devices", device_status, " · ".join(notes))

        self._emit("ready", "ok", "全部完成",
                   "暖语已经准备好", "正在进入陪伴空间")
        try:
            self.window.evaluate_js("window.nuanyu && window.nuanyu.complete()")
        except Exception:
            pass
        time.sleep(1.2)
        self.window.load_url(WEB_URL)
        if not self._monitor_started:
            self._monitor_started = True
            threading.Thread(target=self._monitor, daemon=True).start()

    def _find_adb(self):
        candidates = [
            shutil.which("adb"),
            r"D:\adb\adb\adb.exe",
            str(resource_path("bin/adb.exe")),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        raise RuntimeError("未找到 adb.exe")

    def _run(self, args, timeout=15, env=None):
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            env=env,
        )
        return proc.returncode, proc.stdout.strip()

    def _adb_cmd(self, *args, timeout=15):
        return self._run([self._adb, *args], timeout=timeout)

    def _adb_shell(self, command, timeout=15):
        code, out = self._adb_cmd("shell", command, timeout=timeout)
        if code:
            raise RuntimeError("板卡命令失败: %s" % out[-300:])
        return out

    def _wait_for_device(self, timeout):
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            _, out = self._adb_cmd("devices", timeout=5)
            last = out
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    # This development board permits root adbd. Root is needed
                    # to install the systemd unit and update the runtime files.
                    self._adb_cmd("root", timeout=10)
                    self._adb_cmd("wait-for-device", timeout=15)
                    return serial
            time.sleep(2)
        raise RuntimeError("未检测到广和通板卡，请检查 USB 与 ADB 授权。%s" % last[-120:])

    def _configure_ports(self):
        commands = [
            ("forward", "tcp:5004", "tcp:5004"),
            ("reverse", "tcp:5002", "tcp:5002"),
            ("reverse", "tcp:5015", "tcp:5015"),
            ("reverse", "tcp:5016", "tcp:5016"),
            ("reverse", "tcp:5018", "tcp:5018"),
            ("reverse", "tcp:5019", "tcp:5019"),
        ]
        for command in commands:
            code, out = self._adb_cmd(*command, timeout=8)
            if code:
                raise RuntimeError("ADB 端口映射失败: %s" % out)

    def _sync_board_payload(self):
        payloads = [
            ("app/nuanyu_web.py", "%s/nuanyu_web.py" % BOARD_APP),
            ("app/fibo_tts.py", "%s/fibo_tts.py" % BOARD_APP),
            ("app/fibo_tts.py", "/userdata_fibo/tts_client/fibo_tts.py"),
            ("app/templates/main.html", "%s/templates/main.html" % BOARD_APP),
            ("app/templates/login.html", "%s/templates/login.html" % BOARD_APP),
            ("app/static/js/main.js", "%s/static/js/main.js" % BOARD_APP),
            ("app/static/js/nuanyu-visual-state.js", "%s/static/js/nuanyu-visual-state.js" % BOARD_APP),
            ("app/src/asr/asr_worker.py", "%s/src/asr/asr_worker.py" % BOARD_APP),
            ("app/src/asr/whisper_tiny_cpu_backend.py", "%s/src/asr/whisper_tiny_cpu_backend.py" % BOARD_APP),
            ("app/src/vision/vision_worker.py", "%s/src/vision/vision_worker.py" % BOARD_APP),
            ("app/src/tts/audio_router.py", "%s/src/tts/audio_router.py" % BOARD_APP),
            ("app/src/tts/drizzle_backend.py", "%s/src/tts/drizzle_backend.py" % BOARD_APP),
            ("app/src/tts/matcha_worker.py", "%s/src/tts/matcha_worker.py" % BOARD_APP),
            ("app/src/tts/doubao_provider.py", "%s/src/tts/doubao_provider.py" % BOARD_APP),
            ("app/src/tts/surge_lite_provider.py", "%s/src/tts/surge_lite_provider.py" % BOARD_APP),
            ("app/src/tts/streaming_pipeline.py", "%s/src/tts/streaming_pipeline.py" % BOARD_APP),
            ("app/src/services/proactive_adapter.py", "%s/src/services/proactive_adapter.py" % BOARD_APP),
            ("app/src/services/proactive_service.py", "%s/src/services/proactive_service.py" % BOARD_APP),
            ("app/src/core/runtime_services.py", "%s/src/core/runtime_services.py" % BOARD_APP),
            ("app/src/core/deepseek_client.py", "%s/src/core/deepseek_client.py" % BOARD_APP),
            ("app/src/connectivity/c07a_motion.py", "%s/src/connectivity/c07a_motion.py" % BOARD_APP),
            ("app/src/sensors/__init__.py", "%s/src/sensors/__init__.py" % BOARD_APP),
            ("app/src/sensors/sensor_state.py", "%s/src/sensors/sensor_state.py" % BOARD_APP),
            ("app/src/sensors/sensor_reader.py", "%s/src/sensors/sensor_reader.py" % BOARD_APP),
            ("app/src/sensors/sc171_usb_receiver.py", "%s/src/sensors/sc171_usb_receiver.py" % BOARD_APP),
            ("app/src/sensors/ai_context.py", "%s/src/sensors/ai_context.py" % BOARD_APP),
            ("app/src/weather/__init__.py", "%s/src/weather/__init__.py" % BOARD_APP),
            ("app/src/weather/open_meteo_provider.py", "%s/src/weather/open_meteo_provider.py" % BOARD_APP),
            ("deploy/start_nuanyu_runtime.sh", "/userdata_fibo/deploy/start_nuanyu_runtime.sh"),
        ]
        self._adb_shell(
            "mkdir -p %s/templates %s/static/js %s/src/asr %s/src/vision %s/src/tts "
            "%s/src/services %s/src/core %s/src/connectivity %s/src/sensors %s/src/weather "
            "/userdata_fibo/deploy" %
            (BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP,
             BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP),
            timeout=8,
        )
        for relative, remote in payloads:
            local = resource_path(relative)
            if not local.exists():
                raise RuntimeError("EXE 缺少运行文件: %s" % relative)
            code, out = self._adb_cmd("push", str(local), remote, timeout=25)
            if code:
                raise RuntimeError("同步板卡代码失败: %s" % out[-200:])
        self._adb_shell(
            "chmod 755 /userdata_fibo/deploy/start_nuanyu_runtime.sh && "
            "python3 -m py_compile "
            "%s/nuanyu_web.py "
            "%s/fibo_tts.py "
            "/userdata_fibo/tts_client/fibo_tts.py "
            "%s/src/asr/asr_worker.py "
            "%s/src/asr/whisper_tiny_cpu_backend.py "
            "%s/src/vision/vision_worker.py "
            "%s/src/tts/audio_router.py "
            "%s/src/tts/drizzle_backend.py "
            "%s/src/tts/matcha_worker.py "
            "%s/src/tts/doubao_provider.py "
            "%s/src/tts/surge_lite_provider.py "
             "%s/src/tts/streaming_pipeline.py "
             "%s/src/services/proactive_adapter.py "
             "%s/src/services/proactive_service.py "
             "%s/src/core/runtime_services.py "
            "%s/src/core/deepseek_client.py "
            "%s/src/connectivity/c07a_motion.py "
            "%s/src/sensors/sensor_state.py "
            "%s/src/sensors/sensor_reader.py "
            "%s/src/sensors/sc171_usb_receiver.py "
            "%s/src/sensors/ai_context.py "
            "%s/src/weather/open_meteo_provider.py" %
             (BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP,
              BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP,
              BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP,
              BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP, BOARD_APP,
              BOARD_APP),
            timeout=30,
        )

    def _install_runtime_service(self):
        unit = resource_path("deploy/nuanyu-runtime.service")
        code, out = self._adb_cmd(
            "push", str(unit), "/etc/systemd/system/nuanyu-runtime.service",
            timeout=15,
        )
        if code:
            raise RuntimeError("安装板卡服务失败: %s" % out[-200:])
        self._adb_shell(
            "systemctl disable --now xiaopei-watchdog.service 2>/dev/null || true; "
            "rm -f /userdata_fibo/.nuanyu_stop /userdata_fibo/.nuanyu_sleeping /userdata_fibo/.nuanyu_quiet; "
            "systemctl daemon-reload; "
            "systemctl enable nuanyu-runtime.service >/dev/null 2>&1; "
            "systemctl restart nuanyu-runtime.service",
            timeout=25,
        )

    def _wait_for_web(self, timeout):
        deadline = time.time() + timeout
        last_error = ""
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            try:
                health = self._http_json(WEB_URL + "api/health", timeout=4)
                if health and health.get("ok") and health.get("health", {}).get("initialized"):
                    return health
            except Exception as exc:
                last_error = str(exc)
            if attempts in (8, 16, 24):
                self._configure_ports()
                active = self._adb_shell(
                    "systemctl is-active nuanyu-runtime.service || true", timeout=5
                )
                if active.strip() != "active":
                    self._adb_shell(
                        "systemctl restart nuanyu-runtime.service", timeout=10
                    )
            time.sleep(3)
        tail = self._adb_shell(
            "tail -n 18 /userdata_fibo/nuanyu_runtime.log 2>/dev/null || true",
            timeout=7,
        )
        raise RuntimeError("板卡运行时启动超时。%s\n%s" % (last_error, tail[-700:]))

    def _service_ready(self, health, name):
        services = health.get("health", {}).get("services", [])
        return any(item.get("name") == name and item.get("ready") for item in services)

    def _ensure_zipvoice(self):
        status = self._http_json("http://127.0.0.1:5018/status", timeout=4)
        if status and status.get("model_ready"):
            return True

        script_candidates = [
            # Current PC-side ZipVoice server.  The old Fibocom_Xiaopei copy
            # is retained only as a last-resort compatibility fallback so a
            # stale desktop checkout cannot silently become the primary TTS
            # process again.
            Path.home() / "zipvoice_server_v2.py",
            Path.home() / "Desktop/Fibocom_Xiaopei/tools/zipvoice_server_v2.py",
            Path.home() / "Fibocom_Xiaopei/tools/zipvoice_server_v2.py",
        ]
        script = next((p for p in script_candidates if p.exists()), None)
        if script is None:
            return False
        python = shutil.which("python") or r"C:\Python314\python.exe"
        env = os.environ.copy()
        env["ZIPVOICE_MODEL_DIR"] = str(Path.home() / "zipvoice")
        env["ZIPVOICE_PORT"] = "5018"
        # The validated competition baseline is sherpa_onnx + CPU.  Do not
        # inherit a stale DirectML setting from an older desktop environment.
        env["ZIPVOICE_PROVIDER"] = "cpu"
        # 扩散步数 3(2026-08-04 用户选定): 2 步偶发吐字不清/胡话, 3 步折中——
        # 音质接近 4 步、时延只比 2 步慢 ~0.6s/段(干净基准 2步2028/3步2627ms)。
        env["ZIPVOICE_NUM_STEPS"] = "3"
        env["ZIPVOICE_NUM_THREADS"] = "8"
        log_path = Path(tempfile.gettempdir()) / "nuanyu_zipvoice.log"
        log_file = open(log_path, "a", encoding="utf-8")
        self._zipvoice_process = subprocess.Popen(
            [python, "-u", str(script)],
            cwd=str(script.parent),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
        deadline = time.time() + 75
        while time.time() < deadline:
            status = self._http_json("http://127.0.0.1:5018/status", timeout=4)
            if status and status.get("model_ready"):
                return True
            if self._zipvoice_process.poll() is not None:
                return False
            time.sleep(3)
        return False

    def _http_json(self, url, timeout=5):
        try:
            req = urllib.request.Request(url, headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def _probe_upstream(self, url):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "NuanyuDesktop/1.0"},
                method="HEAD",
            )
            with urllib.request.urlopen(request, timeout=7):
                return True
        except urllib.error.HTTPError:
            # Any HTTP response proves DNS/TCP/TLS reachability.
            return True
        except Exception:
            return False

    def _post_json(self, url, data, timeout=7):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def _monitor(self):
        failures = 0
        while True:
            time.sleep(7)
            health = self._http_json(WEB_URL + "api/health", timeout=4)
            if health and health.get("ok"):
                failures = 0
                continue
            failures += 1
            if failures < 3 or self._running:
                continue
            try:
                self.window.load_html(resource_path("desktop/splash.html").read_text(
                    encoding="utf-8"
                ))
                time.sleep(0.5)
                self._emit("board_runtime", "warn", "连接中断",
                           "检测到板卡连接中断", "正在自动恢复全部服务")
                self.start_async()
            except Exception:
                pass
            failures = 0


def main():
    bridge = DesktopBridge()
    bridge.start()
    orchestrator = Orchestrator(bridge)
    splash = resource_path("desktop/splash.html").read_text(encoding="utf-8")
    window = webview.create_window(
        APP_TITLE,
        html=splash,
        js_api=orchestrator,
        width=1460,
        height=920,
        min_size=(1060, 700),
        background_color="#f5f2e9",
        confirm_close=False,
    )
    orchestrator.attach(window)
    window.events.loaded += orchestrator.on_window_loaded
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
