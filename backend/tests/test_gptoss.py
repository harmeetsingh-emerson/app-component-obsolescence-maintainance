"""Diagnose gpt-oss Ollama response."""
import requests
import json

# 1. List models
r = requests.get("http://localhost:11434/api/tags")
models = [m["name"] for m in r.json().get("models", [])]
print("Available models:")
for m in models:
    print(" ", m)
print()

# 2. Simple chat test
print("Testing gpt-oss:latest raw response...")
payload = {
    "model": "gpt-oss:latest",
    "messages": [{"role": "user", "content": 'Reply ONLY with valid JSON: {"answer": 42}'}],
    "stream": False,
    "options": {"temperature": 0, "num_predict": 512}
}
r2 = requests.post("http://localhost:11434/api/chat", json=payload, timeout=120)
print("HTTP:", r2.status_code)
body = r2.json()
print("Keys:", list(body.keys()))
msg = body.get("message", {})
print("Role:", msg.get("role"))
content = msg.get("content", "")
print(f"Content (len={len(content)}): [{content!r}]")
if not content:
    print()
    print("Full body:")
    print(json.dumps(body, indent=2)[:1000])
