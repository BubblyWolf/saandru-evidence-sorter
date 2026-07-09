# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Saandru -- setup doctor.

Run this once after installing Saandru on a new PC:
    python setup_check.py

It checks everything Saandru needs and prints a simple checklist. For anything
missing, it prints the exact command to fix it. This script never assumes a
technical user is reading it -- every failed line says exactly what to do next.
"""
import importlib
import json
import os
import platform
import sys
import urllib.request

# ---------------------------------------------------------------------------
# Windows consoles are not always UTF-8 -- reconfigure stdout so check-marks
# (✅/❌) print correctly, and fall back to plain ASCII markers if that fails
# rather than crash with UnicodeEncodeError partway through the checklist.
# ---------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OK, X, WARN = "✅", "❌", "⚠️"
except Exception:
    OK, X, WARN = "[OK]", "[X]", "[!]"


def _print(line):
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"))


# ---------------------------------------------------------------------------
# The two Ollama models Saandru actually uses (see src/ollama_client.py) --
# kept as one place so this script and that module never drift apart.
# ---------------------------------------------------------------------------
EMBED_MODEL = "nomic-embed-text"
CHAT_MODELS = ["qwen2.5:3b-instruct", "qwen2.5:1.5b"]  # high-RAM / low-RAM tier
REQUIRED_MODELS = [EMBED_MODEL] + CHAT_MODELS

# import name -> pip package name, for the packages this repo actually imports
PIP_PACKAGES = {
    "streamlit": "streamlit",
    "yaml": "PyYAML",
    "openpyxl": "openpyxl",
    "pdfplumber": "pdfplumber",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "PIL": "Pillow",
    "pytesseract": "pytesseract",
    "reportlab": "reportlab",
}


def check_python_version():
    _print("\n== Python ==")
    major, minor = sys.version_info[0], sys.version_info[1]
    ok = (major, minor) >= (3, 11)
    if ok:
        _print(f"{OK} Python {platform.python_version()} (3.11 or newer -- good)")
    else:
        _print(f"{X} Python {platform.python_version()} is too old.")
        _print("   Fix: install Python 3.11 or newer from https://www.python.org/downloads/")
    return ok


def check_pip_packages():
    _print("\n== Python packages (pip) ==")
    all_ok = True
    for import_name, pip_name in PIP_PACKAGES.items():
        try:
            importlib.import_module(import_name)
            _print(f"{OK} {pip_name}")
        except Exception:
            all_ok = False
            _print(f"{X} {pip_name} is missing.")
            _print(f"   Fix: pip install -r requirements.txt   (or: pip install {pip_name})")
    return all_ok


def check_ollama_reachable():
    _print("\n== Ollama (the local AI engine) ==")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        _print(f"{OK} Ollama is running on this PC.")
        installed = {m.get("name", "") for m in data.get("models", [])}
        return True, installed
    except Exception:
        _print(f"{X} Ollama could not be reached at http://localhost:11434")
        _print("   Fix: install Ollama from https://ollama.com, then make sure it is running")
        _print("        (it usually starts itself and sits in the system tray).")
        return False, set()


def check_models_installed(ollama_up, installed):
    _print("\n== Ollama models ==")
    if not ollama_up:
        _print(f"{WARN} Skipped -- Ollama is not running, so models cannot be checked yet.")
        return False
    all_ok = True
    for model in REQUIRED_MODELS:
        # Ollama tag names sometimes include a ":latest" suffix in the listing --
        # match on a prefix so "qwen2.5:1.5b" still matches "qwen2.5:1.5b" exactly
        # and doesn't false-match a different tag by accident.
        found = any(m == model or m == f"{model}:latest" for m in installed)
        if found:
            _print(f"{OK} {model}")
        else:
            all_ok = False
            _print(f"{X} {model} is not pulled yet.")
            _print(f"   Fix: ollama pull {model}")
    return all_ok


def check_tesseract():
    _print("\n== Tesseract OCR (optional -- only needed for scanned/photo documents) ==")
    try:
        import shutil as _shutil
        found_on_path = _shutil.which("tesseract")
        known_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        found = found_on_path or next((p for p in known_paths if os.path.exists(p)), None)
        if found:
            _print(f"{OK} Tesseract found ({found}).")
        else:
            _print(f"{WARN} Tesseract was not found. This is OPTIONAL.")
            _print("   Note: without it, scanned/photographed documents cannot be read.")
            _print("   Fix (optional): install from https://github.com/UB-Mannheim/tesseract/wiki")
    except Exception as e:
        _print(f"{WARN} Could not check for Tesseract ({e}). This is optional, so continuing.")


def check_ram():
    _print("\n== Memory (RAM) ==")
    # Reuse the ONE cross-platform detector in src/ollama_client.py (Windows ctypes /
    # Linux /proc/meminfo / optional psutil) rather than duplicating a Windows-only copy
    # here -- that copy used to report "could not measure RAM" on every Mac/Linux machine.
    # ollama_client imports only the standard library, so this is safe even on a setup
    # with missing pip packages (which is exactly when this doctor script gets run).
    total_gb = None
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
        from ollama_client import get_total_ram_gb
        total_gb = get_total_ram_gb()
    except Exception:
        total_gb = None

    if total_gb is None:
        _print(f"{WARN} Could not measure RAM on this PC -- skipping this check.")
        return
    # matches src/ollama_client.py: the standard 3b model runs fine from 8 GB up
    # (it only needs ~2.3 GB); the small 1.5b model is only for PCs below 8 GB.
    tier = "a smaller, faster model (below 8 GB RAM)" if total_gb < 8 else "the standard model"
    _print(f"{OK} Total RAM: {total_gb:.1f} GB -- Saandru will automatically use {tier}.")
    if total_gb < 8:
        _print(f"{WARN} 8 GB RAM is the recommended minimum. Saandru may run slowly on this PC.")


def main():
    _print("Saandru setup check")
    _print("=" * 50)
    py_ok = check_python_version()
    pip_ok = check_pip_packages()
    ollama_up, installed = check_ollama_reachable()
    models_ok = check_models_installed(ollama_up, installed)
    check_tesseract()
    check_ram()

    _print("\n" + "=" * 50)
    if py_ok and pip_ok and ollama_up and models_ok:
        _print(f"{OK} Everything looks ready. You can run:  streamlit run app.py")
    else:
        _print(f"{WARN} Some items above need fixing before Saandru will work fully.")
        _print("   Fix the items marked with X above, then run this check again.")


if __name__ == "__main__":
    main()
