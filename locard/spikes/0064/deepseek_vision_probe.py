"""Probe: does the DeepSeek API accept image input, and with which model?"""
import base64
import sys
import httpx
sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
H = {"Authorization": f"Bearer {KEY}"}
BASE = "https://api.deepseek.com/v1"


def b64(path):
    return base64.b64encode(open(path, "rb").read()).decode()

with httpx.Client(timeout=60) as c:
    print("=== available models ===")
    try:
        r = c.get(f"{BASE}/models", headers=H)
        print(r.status_code, [m["id"] for m in r.json().get("data", [])])
    except Exception as e:
        print("models err", e)

    img = b64("eval_set/img/001eb5f8_p13.png")
    msg = [{"role": "user", "content": [
        {"type": "text", "text": "What is on this page in one sentence?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
    ]}]
    for model in ["deepseek-chat", "deepseek-vl2", "deepseek-v4-flash",
                  "deepseek-reasoner", "deepseek-vision"]:
        try:
            r = c.post(f"{BASE}/chat/completions", headers=H,
                       json={"model": model, "messages": msg, "max_tokens": 80})
            if r.status_code == 200:
                print(f"[OK]   {model}: {r.json()['choices'][0]['message']['content'][:160]!r}")
            else:
                print(f"[{r.status_code}] {model}: {r.text[:140]}")
        except Exception as e:
            print(f"[ERR]  {model}: {str(e)[:120]}")
