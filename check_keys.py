"""Check that the Nicrron and PRISM keys work."""
import os
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = "openai/gpt-4o-mini"
messages = [{"role": "user", "content": "Say hello in five words."}]

client = OpenAI(base_url="https://api.nicrron.ai/v1", api_key=os.environ["NICRRON_API_KEY"])
start = time.time()
resp = client.chat.completions.create(model=MODEL, messages=messages, max_tokens=30)
latency_ms = int((time.time() - start) * 1000)
reply = resp.choices[0].message.content
print("Model reply:", reply)

host = os.environ.get("PRISMTRACE_HOST", "https://prism.blockconvey.com").rstrip("/")
trace = {
    "project_id": os.environ["PRISMTRACE_PROJECT_ID"],
    "model": MODEL,
    "input_messages": messages,
    "output_message": {"role": "assistant", "content": reply},
    "latency_ms": latency_ms,
    "agent_id": "setup-check",
    "agent_name": "setup-check",
    "session_id": "setup",
}
r = requests.post(
    f"{host}/api/traces",
    json=trace,
    headers={"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"]},
    timeout=15,
)
print("PRISM status code:", r.status_code)
if r.status_code >= 300:
    print("PRISM response:", r.text[:300])
