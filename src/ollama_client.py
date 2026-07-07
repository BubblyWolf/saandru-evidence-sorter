"""Tiny local Ollama client — talks to the offline server on localhost:11434.
No API keys, no cloud. Just embeddings (matcher) + generate (brain)."""
import ctypes
import json
import os
import urllib.request

BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

# Hardware auto-tiering: the 3b chat model at q4 quantization only needs ~2.3GB of RAM to
# run, so even a typical 8GB no-GPU college office PC can run it acceptably (slower, but
# usable) -- and 3b's measured accuracy should be kept for nearly all college machines.
# The 1.5b model is reserved for genuinely weak machines (below 8GB); its accuracy is being
# measured separately and is NOT yet validated. Three inputs decide the final CHAT_MODEL,
# in priority order: env override > installed-model reality > RAM tier.
_HIGH_RAM_MODEL = "qwen2.5:3b-instruct"  # unchanged default -- do not rename this string,
# the doc cache key (doc_cache.make_key) includes CHAT_MODEL, so any accidental change here
# invalidates every cached classification on every machine that already ran the old default.
_LOW_RAM_MODEL = "qwen2.5:1.5b"
_RAM_TIER_CUTOFF_GB = 8


def get_total_ram_gb():
    """Total physical RAM in GB via the Windows API (ctypes, no psutil dependency).
    Any failure (non-Windows, API error, ...) falls back to 16GB -- i.e. "assume a normal
    machine and keep today's behaviour" rather than silently downgrading everyone's model."""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # noqa: SLF001
        if not ok:
            return 16.0
        return stat.ullTotalPhys / (1024.0 ** 3)
    except Exception:
        return 16.0


def _installed_model_names():
    """Query Ollama's own /api/tags for what's actually pulled on this machine. Empty list
    (never a crash) if Ollama isn't reachable yet -- the tiering logic below treats "unknown"
    the same as "can't confirm, use the preferred name and let the first real call fail with
    Ollama's own clear error" rather than guessing."""
    try:
        req = urllib.request.Request(BASE + "/api/tags")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def _choose_chat_model(ram_gb=None, installed=None):
    """Pick CHAT_MODEL + a one-line human-readable reason. Priority:
    1. SAANDRU_MODEL env var (preferred), or the older PRAMAN_MODEL alias -- explicit
       operator override, wins over everything.
    2. RAM tier (>=8GB -> 3b, <8GB -> prefer 1.5b) reconciled against what Ollama actually
       has installed -- a preferred model that isn't pulled yet is worse than the other tier
       IF that other tier happens to be installed instead.
    3. If neither/both/unclear, fall back to the RAM-preferred name as-is (Ollama's own error
       on the first real call is clearer than the tool silently guessing further).
    """
    env_override = os.environ.get("SAANDRU_MODEL") or os.environ.get("PRAMAN_MODEL")
    if env_override:
        return env_override, "env override"

    ram_gb = get_total_ram_gb() if ram_gb is None else ram_gb
    installed = _installed_model_names() if installed is None else installed

    if ram_gb >= _RAM_TIER_CUTOFF_GB:
        preferred, other = _HIGH_RAM_MODEL, _LOW_RAM_MODEL
        reason_default = f"default 3b (RAM {ram_gb:.0f}GB)"
    else:
        preferred, other = _LOW_RAM_MODEL, _HIGH_RAM_MODEL
        reason_default = f"low-RAM tier 1.5b (RAM {ram_gb:.0f}GB, below 8GB)"

    if not installed:
        # couldn't ask Ollama (not running yet, etc) -- keep the RAM-based preference, the
        # first real call will surface Ollama's own "model not found" error if it's wrong.
        return preferred, reason_default
    if preferred in installed:
        return preferred, reason_default
    if other in installed:
        print(f"[Saandru] {preferred} is not installed; using {other} instead (already pulled).")
        return other, f"fallback to installed {other}"
    # neither tier is installed -- keep the preferred name; Ollama's own error is clearer
    # than the tool inventing a third guess.
    return preferred, reason_default


CHAT_MODEL, CHAT_MODEL_REASON = _choose_chat_model()


def _post(path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def embed(text, model=EMBED_MODEL):
    """Return a vector for one string."""
    # keep_alive="10m" -- without it Ollama unloads the model between calls and pays a
    # multi-second reload on the next one; keeping it warm for the whole batch run is free.
    out = _post("/api/embeddings", {"model": model, "prompt": text, "keep_alive": "10m"})
    return out["embedding"]


def generate_json(prompt, model=CHAT_MODEL, temperature=0.0):
    """Ask the model and force a JSON object back (Ollama format=json)."""
    out = _post("/api/generate", {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": "10m",  # see embed() -- avoid reload latency between documents
        # num_predict caps the reply length: our JSON answers are tiny (a letter, a float,
        # a short quote), so an uncapped model can ramble and pay tail latency for nothing.
        "options": {"temperature": temperature, "num_predict": 160},
    })
    raw = out.get("response", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": True, "_raw": raw}


if __name__ == "__main__":
    v = embed("Green audit report on campus energy and water conservation")
    print(f"embed OK -> vector length {len(v)}; first 3 = {[round(x,3) for x in v[:3]]}")
    ans = generate_json(
        'Classify this into one word. Return JSON {"topic": "..."}. '
        'Text: "The college conducted a tree plantation and energy audit."'
    )
    print("generate OK ->", ans)
