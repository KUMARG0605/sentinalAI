# sentinel.spec — PyInstaller build spec for SentinelAI v2
#
# Build commands:
#   pyinstaller sentinel.spec                  (recommended)
#   pyinstaller sentinel.spec --clean          (clean rebuild)
#
# Output:
#   dist/SentinelAI/SentinelAI.exe            (main executable)
#   dist/SentinelAI/sentinel_wake.exe         (wake word process — standalone)
#
# IMPORTANT — Wake word process is built as a SEPARATE executable so it
# can run independently and be restarted without touching the main UI.
# ─────────────────────────────────────────────────────────────────────────────

import sys
sys.setrecursionlimit(sys.getrecursionlimit() * 5)
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

PROJECT_ROOT = Path(SPECPATH)

# ─────────────────────────────────────────────────────────────────────────────
#  SHARED ANALYSIS SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

HIDDEN_IMPORTS = [
    # LangChain ecosystem
    "langchain",
    "langchain.agents",
    "langchain.tools",
    "langchain_core",
    "langchain_core.tools",
    "langchain_core.prompts",
    "langchain_core.messages",
    "langchain_community",
    "langchain_community.vectorstores",
    "langchain_community.document_loaders",
    "langchain_text_splitters",
    "langchain_huggingface",
    "langchain_groq",
    "langchain_sambanova",

    # Groq
    "groq",
    "groq._client",

    # Browser automation
    "playwright",
    "playwright.async_api",
    "playwright.sync_api",

    # STT / Audio
    "assemblyai",
    "assemblyai.streaming",
    "assemblyai.streaming.v3",
    "sounddevice",
    "webrtcvad",
    "vosk",

    # ML / Embeddings
    "torch",
    "torch.nn",
    "transformers",
    "sentence_transformers",
    "faiss",

    # Search
    "duckduckgo_search",
    "ddgs",
    "tavily",
    "wikipedia",
    "arxiv",

    # Windows automation
    "pyautogui",
    "pywinauto",
    "pywinauto.application",
    "pywinauto.desktop",
    "pyperclip",

    # Vision / OCR
    "PIL",
    "PIL.Image",
    "pytesseract",
    "cv2",
    "pdf2image",

    # Misc
    "numpy",
    "dotenv",
    "python_dotenv",
    "requests",
    "urllib3",
    "chardet",
    "charset_normalizer",
    "charset_normalizer.md__mypyc",   # compiled extension, missed by static analysis
    "charset_normalizer.legacy",
    "charset_normalizer.utils",
    "winreg",
    "comtypes",
    "openpyxl",

    # Internal modules — force inclusion
    "app",
    "app.src",
    "app.src.agents",
    "app.src.agents.agents",
    "app.src.agents.browser_agent",
    "app.src.agents.ecommerce_agent",
    "app.src.agents.research_agent",
    "app.src.agents.comms_agent",
    "app.src.orchestrator",
    "app.src.scheduler",
    "app.src.dag_builder",
    "app.src.blackboard",
    "app.src.session_memory",
    "app.src.llm_rotation",
    "app.src.stt",
    "app.src.voice_pipeline",
    "app.src.wake_word_worker",
    "app.src.wake_word_standalone",
    "app.src.config",
    "app.src.path_utils",
    "app.src.tools",
    "app.src.browser_filters",
    "app.src.rag",
    "app.src.indexer",
    "app.src.filter_extractor",
    "app.src.selection_context",
    "app.src.background_actions",
    "app.src.app_registry",
    "app.ui",
]

# Collect package data files (models, tokenizer configs, etc.)
DATAS = [
    # App .env defaults
    (str(PROJECT_ROOT / ".env"), "."),
    # App source (for dynamic imports)
    (str(PROJECT_ROOT / "app"), "app"),
]

# Add langchain data files
try:
    DATAS += collect_data_files("langchain")
    DATAS += collect_data_files("langchain_core")
    DATAS += collect_data_files("langchain_community")
    DATAS += collect_data_files("langchain_huggingface")
except Exception:
    pass

# Add playwright/patchright driver
try:
    import patchright
    patchright_path = getattr(patchright, '__path__', [None])[0]
    if patchright_path:
        DATAS.append((os.path.join(patchright_path, 'driver'), 'patchright/driver'))
except ImportError:
    pass

try:
    import playwright
    playwright_path = getattr(playwright, '__path__', [None])[0]
    if playwright_path:
        DATAS.append((os.path.join(playwright_path, 'driver'), 'playwright/driver'))
except ImportError:
    pass

# Add tokenizer/model config files
try:
    DATAS += collect_data_files("transformers")
    DATAS += collect_data_files("sentence_transformers")
    DATAS += collect_data_files("tiktoken_ext")
except Exception:
    pass
# Add vosk shared library
try:
    DATAS += collect_dynamic_libs("vosk")
except Exception:
    pass

# Add charset_normalizer data files (fixes requests RequestsDependencyWarning)
try:
    DATAS += collect_data_files("charset_normalizer")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
#  ANALYSIS — main app
# ─────────────────────────────────────────────────────────────────────────────

a = Analysis(
    [str(PROJECT_ROOT / "sentinel_main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "notebook",
        "IPython",
        "jupyter",
        "sphinx",
        "pytest",
        "test",
        "tests",
        "_tkinter",     # we use our own UI, not tkinter
        "tkinter",
        "tensorflow",   # huge, unused ML framework
        "tensorboard",
        "keras",
        "tf_keras",
        "streamlit",    # web UI frameworks (we use PyQt5)
        "gradio",
        "gradio_client",
        "pandas",       # data analysis, large
        "boto3",        # AWS SDK, large
        "botocore",
        "f5-tts",       # local unrelated packages
        "soprano-tts",
        "seaborn",      # data viz
        "altair",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN EXE — one-dir build
# ─────────────────────────────────────────────────────────────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SentinelAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,           # No console window — GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(PROJECT_ROOT / "version_info.txt")
        if (PROJECT_ROOT / "version_info.txt").exists()
        else None,
    icon=str(PROJECT_ROOT / "resources" / "icons" / "sentinel_icon.ico")
        if (PROJECT_ROOT / "resources" / "icons" / "sentinel_icon.ico").exists()
        else None,
    uac_admin=False,         # Set True if app needs admin rights
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        "vcruntime140.dll",
        "python3*.dll",
        "api-ms-*.dll",
    ],
    name="SentinelAI",
)
