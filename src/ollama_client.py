"""Tiny local Ollama client — talks to the offline server on localhost:11434.
No API keys, no cloud. Just embeddings (matcher) + generate (brain)."""
import json, urllib.request

BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "qwen2.5:3b-instruct"


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
