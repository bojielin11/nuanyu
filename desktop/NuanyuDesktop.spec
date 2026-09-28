# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

project_root = os.path.dirname(SPECPATH)

datas = [
    (os.path.join(SPECPATH, 'splash.html'), 'desktop'),
    (os.path.join(project_root, 'deploy/nuanyu-runtime.service'), 'deploy'),
    (os.path.join(project_root, 'deploy/start_nuanyu_runtime.sh'), 'deploy'),
    (os.path.join(project_root, 'app/nuanyu_web.py'), 'app'),
    (os.path.join(project_root, 'app/fibo_tts.py'), 'app'),
    (os.path.join(project_root, 'app/templates/main.html'), 'app/templates'),
    (os.path.join(project_root, 'app/templates/login.html'), 'app/templates'),
    (os.path.join(project_root, 'app/static/js/main.js'), 'app/static/js'),
    (os.path.join(project_root, 'app/static/js/nuanyu-visual-state.js'), 'app/static/js'),
    (os.path.join(project_root, 'app/src/asr/asr_worker.py'), 'app/src/asr'),
    (os.path.join(project_root, 'app/src/asr/whisper_tiny_cpu_backend.py'), 'app/src/asr'),
    (os.path.join(project_root, 'app/src/vision/vision_worker.py'), 'app/src/vision'),
    (os.path.join(project_root, 'app/src/tts/audio_router.py'), 'app/src/tts'),
    (os.path.join(project_root, 'app/src/tts/drizzle_backend.py'), 'app/src/tts'),
    (os.path.join(project_root, 'app/src/tts/matcha_worker.py'), 'app/src/tts'),
    (os.path.join(project_root, 'app/src/tts/doubao_provider.py'), 'app/src/tts'),
    (os.path.join(project_root, 'app/src/tts/surge_lite_provider.py'), 'app/src/tts'),
    (os.path.join(project_root, 'app/src/tts/streaming_pipeline.py'), 'app/src/tts'),
    (os.path.join(project_root, 'app/src/services/proactive_adapter.py'), 'app/src/services'),
    (os.path.join(project_root, 'app/src/services/proactive_service.py'), 'app/src/services'),
    (os.path.join(project_root, 'app/src/core/runtime_services.py'), 'app/src/core'),
    (os.path.join(project_root, 'app/src/core/deepseek_client.py'), 'app/src/core'),
    (os.path.join(project_root, 'app/src/connectivity/c07a_motion.py'), 'app/src/connectivity'),
    (os.path.join(project_root, 'app/src/sensors/__init__.py'), 'app/src/sensors'),
    (os.path.join(project_root, 'app/src/sensors/sensor_state.py'), 'app/src/sensors'),
    (os.path.join(project_root, 'app/src/sensors/sensor_reader.py'), 'app/src/sensors'),
    (os.path.join(project_root, 'app/src/sensors/sc171_usb_receiver.py'), 'app/src/sensors'),
    (os.path.join(project_root, 'app/src/sensors/ai_context.py'), 'app/src/sensors'),
    (os.path.join(project_root, 'app/src/weather/__init__.py'), 'app/src/weather'),
    (os.path.join(project_root, 'app/src/weather/open_meteo_provider.py'), 'app/src/weather'),
]

cv2_datas, cv2_binaries, cv2_hidden = collect_all('cv2')
webview_datas, webview_binaries, webview_hidden = collect_all('webview')
datas += cv2_datas + webview_datas

a = Analysis(
    [os.path.join(SPECPATH, 'nuanyu_desktop.py')],
    pathex=[project_root],
    binaries=cv2_binaries + webview_binaries,
    datas=datas,
    hiddenimports=cv2_hidden + webview_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pandas'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='暖语',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, 'nuanyu.ico'),
)
