# Nuanyu (暖语) — Proactive Emotional Companion Robot

An on-device AI companion robot that runs entirely on an embedded ARM board — speech
recognition, an LLM conversation loop, three interchangeable text-to-speech backends,
facial-expression recognition, ambient sensing and physical motion, all coordinated by a
single service.

Unlike a cloud assistant, the robot is designed to keep working when the network does:
camera, microphone, speaker, sensors and a local dialogue memory live on the board. Only
the LLM and one of the three TTS voices are cloud-backed, and both degrade gracefully.

> **Note on language.** The product is built for Chinese users, so the web UI, the robot's
> persona prompts and its speech-command grammar are Chinese by design. The code,
> comments, configuration and documentation are English.

---

## Hardware

| Component | Model |
|---|---|
| Main board | Fibocom SC171V3 (Qualcomm QCS6490, aarch64) |
| OS | Ubuntu-based vendor image, Python 3.8 |
| NPU / DSP | Qualcomm Hexagon — used by the Fibocom ASR and TTS SDKs |
| Display | 5" HDMI panel, driven as a native Wayland client |
| Speaker | Board ALSA playback (`lahaina-yupikiot` card) |
| Microphone | USB capture device |
| Camera | Board CSI camera, with a host-side JPEG fallback |
| Sensor node | ESP32-S3 over USB CDC (climate, light, air quality, presence, radar) |
| Motion | C07A chassis controller over UART |

The board contributes what it is good at (always-on capture, audio playback, display) and
delegates the two heavy models to a host PC over ADB tunnels. `deploy/` and
`docs/HARDWARE.md` describe the split.

## Architecture

A single process, `app/nuanyu_web.py`, owns the device. It is built on `http.server`
rather than a web framework, because the runtime needs direct control over the UART, the
ALSA device and the camera, and must guarantee that exactly one process owns them.

```
                     ┌───────────────────── app/nuanyu_web.py ─────────────────────┐
  browser /          │  HTTP :5004  ─ routes, session cookies                       │
  desktop shell ────▶│                                                              │
                     │  RuntimeServices ── ASR worker · Vision worker · Sensors     │
                     │                     Memory · Persona · Tools · Proactive     │
                     │  TTS router ──▶ drizzle │ stream │ surge                     │
                     └───────┬──────────────────────────┬───────────────────────────┘
                             │                          │
                     board speaker (ALSA)        ADB reverse tunnels ──▶ host PC
                                                                       ZipVoice, cloud proxy
```

A file lock (`/run/nuanyu_web_5004.lock`) makes a second instance exit immediately rather
than fight over the UART and the camera. `deploy/start_nuanyu_runtime.sh` supervises the
process and restarts it on a memory leak, a wedged request lock, a runaway thread count,
or a TTS DSP timeout.

### Repository layout

```
app/
  nuanyu_web.py          main service: HTTP routes, conversation loop, TTS routing
  fibo_tts.py            board speaker routing + ALSA mixer handling
  src/
    asr/                 ASR worker and its Whisper-Tiny CPU backend
    connectivity/        C07A motion controller, optional L610 4G module
    core/                runtime service registry, DeepSeek client, reminder gate
    display/             Wayland expression renderer for the HDMI panel
    domain/              shared event and model types
    memory/              SQLite conversation store, LLM context assembly
    persona/             per-user character configuration
    ports/               interfaces the services implement
    sensors/             USB-CDC sensor reader and state
    services/            memory, persona, tool, proactive and cloud-sync services
    tools/               tool-calling coordinator
    tts/                 the three TTS backends + streaming pipeline
    vision/              face detection, emotion recognition, expression smoothing
    weather/             Open-Meteo forecast provider
  static/  templates/    web UI (Chinese)
config/                  environment templates — copy to *.env and fill in
deploy/                  systemd unit and the board supervisor script
docs/                    architecture, hardware, models, TTS, motion, known issues
tests/                   unit and contract tests
tools/                   host-side helper servers
```

## The voice pipeline

1. **Wake and capture.** The ASR worker holds the microphone open and applies voice
   activity detection with a deliberate pre-roll, so a wake word is never clipped.
2. **Recognition.** Whisper-Tiny runs on the board CPU and is the **default** backend. The
   board also carries a Fibocom DSP speech model, but **no backend for it ships in this
   repository** — nothing in the tree references it. See `docs/MODELS.md`.
3. **Conversation.** The transcript joins the user's memory context and persona, and goes
   to DeepSeek as a streaming request.
4. **Speech.** The reply is segmented on punctuation and synthesised sentence by sentence,
   so playback of sentence *n* overlaps synthesis of sentence *n+1*. The frontend shows the
   measured first-audio latency rather than a fabricated number.

### TTS backends

The backend is selected with `NUANYU_TTS_BACKEND`. All three share one speaker path
(`fibo_tts.py`), so exactly one component configures the mixer.

| Backend | Model | Where it runs | Character |
|---|---|---|---|
| `drizzle` (default) | Matcha-TTS, with Piper fallback | Board | Fully offline, low latency |
| `stream` | Doubao cloud TTS | Cloud | Highest quality, needs network |
| `surge` | ZipVoice (zero-shot voice cloning) | Host PC | Clone a user's voice from a short sample |

See `docs/TTS.md` for the latency work behind the streaming pipeline and the concurrency
limit, and for the failure modes that were fixed.

## Vision, sensing and motion

- **Expression recognition.** A face detector plus an ONNX emotion classifier, smoothed
  over time so the robot's reaction does not flicker between frames. Emotion labels are
  Chinese, because they are shown to the user.
- **Expression display.** A separate process renders the animated face on the HDMI panel
  as a raw Wayland client, so it keeps animating independently of the web service.
- **Ambient sensing.** An ESP32-S3 node reports temperature, humidity, light, air quality and
  presence over USB CDC, and — where a radar module is fitted — heart and breath rate with a
  risk level. Sensor absence is explicitly non-fatal.
- **Motion.** The C07A chassis controller accepts actions over UART, with a mock mode for
  bench work without the hardware.
- **4G (optional).** An L610 module can report telemetry to Huawei Cloud IoTDA. This is
  **inactive unless the hardware is attached** — the service logs a clear message and
  exits if no AT port is found.

## Memory and multi-user

Each account gets an isolated memory: a SQLite store for conversation history with full-text
search, a JSON profile for facts the user asked the robot to remember, and a persona record
that includes a custom character name. Sessions are cookie-based.

Passwords are stored as unsalted SHA-256 hashes. **This is not suitable for any deployment
exposed beyond a trusted local network** — see *Security* below.

## Getting started

This repository is the board-side runtime. `deploy/start_nuanyu_runtime.sh` resolves the
deployment root from its own location, so the tree can be run in place — `deploy/`, `app/`
and `config/` as siblings. Set `NUANYU_ROOT` to deploy the runtime elsewhere; the reference
board uses `/userdata_fibo`. See `docs/HARDWARE.md` for the device details.

```bash
# 0. Dependencies, on the board. The vendor SDK (fiboaisdk) is NOT on PyPI and
#    must already be in the board image — see app/requirements.txt.
pip3 install -r app/requirements.txt

# 1. Host-side tunnels (the PC runs ZipVoice and the cloud proxy)
adb forward tcp:5004 tcp:5004
adb reverse tcp:5002 tcp:5002     # host microphone bridge (the `auto` failover)
adb reverse tcp:5018 tcp:5018     # ZipVoice / Surge
adb reverse tcp:5019 tcp:5019     # DeepSeek + Doubao proxy
adb reverse tcp:5016 tcp:5016     # host camera fallback

# 2. Configuration
cp config/nuanyu.env.example   config/nuanyu.env           # then edit
cp config/l610.env.example     config/l610.env             # only if using 4G
cp config/c07a_motion.env.example config/c07a_motion.env   # only if using the chassis

# 3. Models — not included, see docs/MODELS.md

# 4. Run
sh deploy/start_nuanyu_runtime.sh
```

The host-side helpers in `tools/` have their own dependencies
(`pip3 install -r tools/requirements.txt`) and run on the PC, not the board.

Open `http://127.0.0.1:5004` and log in.

### Creating the first account

The runtime only ever *reads* the account file — there is no registration endpoint and no
seeding step, so a fresh install cannot be logged into at all. Accounts live in
`app/memories/_users.json` (git-ignored; **you have to create it**), and the stored password
is an unsalted SHA-256 hex digest. Seed the first account yourself:

```bash
mkdir -p app/memories
python3 - <<'PY'
import hashlib, json, pathlib, datetime
p = pathlib.Path("app/memories/_users.json")
users = json.loads(p.read_text()) if p.exists() else {}
users["alice"] = {
    "password": hashlib.sha256(b"change-me").hexdigest(),
    "created": datetime.datetime.now().isoformat(timespec="seconds"),
}
p.write_text(json.dumps(users, ensure_ascii=False, indent=2))
PY
```

Then log in as `alice` / `change-me`. See *Security* below before doing this anywhere but a
lab — the hashing scheme is not suitable for a real deployment.

`config/*.env` are git-ignored on purpose. **Never commit a populated config file.**

## Models

Model weights are **not distributed with this repository**. The full inventory — names,
sizes, what each is for, and which ones cannot be redistributed because they are vendor
DSP binaries — is in [`docs/MODELS.md`](docs/MODELS.md).

In short: Matcha-TTS, Piper and ZipVoice are open models you can download or convert
yourself; the Fibocom `fibots`/`fiboasr` DSP models are vendor assets that must come from
Fibocom and cannot be published.

## Known issues

Read [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) before deploying anything. The two that
catch people out:

- **The board and this repository can drift apart.** The original deployment is driven by a
  Windows launcher that pushes its own bundled payload over the board's files. An older
  launcher silently downgrades the board. Compare content hashes — and note that the board's
  clock is wrong, so its file timestamps mean nothing.
- **The board's clock is stopped.** Do not use board timestamps as evidence.

The rest of that document covers the lab-grade security posture, the absence of any account
registration path, which optional hardware fails loudly and why, and the legacy oddities that
look like bugs but are load-bearing.

## Security

**This is lab-grade code. Do not put it on an untrusted network.** Specifically:

- **The server binds `0.0.0.0`** (`WEB_HOST` in `app/nuanyu_web.py`), so it listens on every
  interface, not just loopback. The `adb forward` tunnel most people use makes it *look*
  loopback-only on the host, but the board's own listener is reachable from the board's LAN.
- **Several routes answer without a session**, including `/api/status`, `/api/sensors`,
  `/api/weather`, `/api/tts_status`, `/api/tts_audio` and `/display`. Notably
  **`/api/users` enumerates account names and creation dates** to any caller.
- **No transport security** — plain HTTP, no TLS anywhere — and a development-grade session
  model with no rotation or expiry worth the name.
- **No rate limiting** on login.
- **Password hashing is unsalted SHA-256.** Replace it with a memory-hard KDF
  (Argon2 / bcrypt / scrypt) before any real deployment.
- Cloud credentials are read from the environment / `config/*.env`; the templates carry
  placeholders only.

The bind address and the unauthenticated routes are not configurable without changing code,
and were left as-is so the original deployment keeps working. `docs/KNOWN_ISSUES.md` §5 has
the same list with the file references.

## Third-party components

The board runtime depends on the Fibocom AI SDK (ASR/TTS DSP models and its Python
bindings), ONNX Runtime, OpenCV, NumPy and Certifi. The web UI is plain HTML/CSS/JS with
no build step and no bundled framework.

## Credits

**Beijing University of Posts and Telecommunications** (北京邮电大学)

Built by **Bojie Lin** (林渤杰), **Chenyu Liu** (刘晨语) and **Chuyi Liang** (梁楚仪).

The sensor-node firmware and its host-side protocol live in a companion repository.

## License

MIT — see [LICENSE](LICENSE).
