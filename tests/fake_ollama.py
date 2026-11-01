"""A fake Ollama server for tests and docs-smoke: answers /api/chat with valid, obviously fake JSON.

    python tests/fake_ollama.py 11500        # then: llm: {provider: ollama, model: fake, base_url: http://127.0.0.1:11500}

It reads which job the prompt is for (fit, junk, merge, gaps, profile) and returns a well-formed reply:
fit scores 85 / 70 / 55 ... by topic position, nothing flagged, nothing merged. No model runs; token counts are made up.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def answer(system: str, user: str) -> dict:
    data = json.loads(user) if user.strip().startswith("{") else {}
    topics = data.get("topics") or data.get("gaps") or []
    ids = [t["id"] for t in topics if isinstance(t, dict) and "id" in t]
    if "decide which trending topics a website should write about" in system:
        scores = [85, 70, 55, 40, 25]
        return {"items": [{"id": i, "fit": scores[n % len(scores)],
                           "reason": "Fake model: fixed score by position, no judgement was made."}
                          for n, i in enumerate(ids)]}
    if "filter a trend feed" in system:
        return {"flagged": []}
    if "group trend topics" in system:
        return {"groups": []}
    if "Search Console gaps into articles" in system:
        return {"items": [{"id": i, "angle": "Fake angle.", "questions": ["Fake question one?", "Fake question two?"]}
                          for i in ids]}
    if "propose the niches" in system:
        return {"niches": []}
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        msgs = body.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
        out = json.dumps({"message": {"role": "assistant", "content": json.dumps(answer(system, user))},
                          "prompt_eval_count": 120, "eval_count": 40, "done": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 11500), Handler).serve_forever()
