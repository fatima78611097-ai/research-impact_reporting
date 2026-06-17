"""Qwen2.5-VL-7B eval runner (runs ON the g6). Two modes per page:
  full   - production-shaped: "extract every hero statistic with its caption" -> JSON list
  target - diagnostic: per eval item, "which caption belongs to <value>?"
Saves raw model outputs to qwen-raw.json (scoring happens separately in score_qwen.py).

Usage: python run_qwen.py [--mode=full|target|both] [--limit=N]
"""
import json
import os
import re
import sys
import time

MODE = next((a.split("=")[1] for a in sys.argv if a.startswith("--mode=")), "both")
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)
HERE = os.path.dirname(os.path.abspath(__file__))

import torch
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info

MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
print(f"loading {MODEL}...", flush=True)
t0 = time.time()
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(MODEL, torch_dtype=torch.bfloat16, device_map="cuda")
processor = AutoProcessor.from_pretrained(MODEL, max_pixels=1280 * 28 * 28)
print(f"loaded in {time.time()-t0:.0f}s", flush=True)


def ask(img_path, prompt, max_new=600):
    messages = [{"role": "user", "content": [{"type": "image", "image": f"file://{img_path}"},
                                             {"type": "text", "text": prompt}]}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    imgs, vids = process_vision_info(messages)
    inputs = processor(text=[text], images=imgs, videos=vids, padding=True, return_tensors="pt").to("cuda")
    out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    out = out[:, inputs.input_ids.shape[1]:]
    return processor.batch_decode(out, skip_special_tokens=True)[0]


FULL_PROMPT = (
    "This is a page from a nonprofit annual report. Extract EVERY prominent statistic on the page — "
    "each big/hero number together with the caption or label that belongs to it per the page layout. "
    'Return ONLY a JSON array: [{"value": "...", "caption": "..."}]. Use the caption text as printed.'
)

items = json.load(open(f"{HERE}/eval-set.json"))
if LIMIT:
    items = items[:LIMIT]
pages = sorted({i["img"] for i in items})
raw = {"full": {}, "target": {}}
t0 = time.time()
if MODE in ("full", "both"):
    for n, pg in enumerate(pages, 1):
        try:
            raw["full"][pg] = ask(f"{HERE}/{pg}", FULL_PROMPT)
        except Exception as e:
            raw["full"][pg] = f"ERR:{e}"
        if n % 10 == 0:
            print(f"  full {n}/{len(pages)} ({(time.time()-t0)/n:.1f}s/page)", flush=True)
if MODE in ("target", "both"):
    for n, it in enumerate(items, 1):
        key = f"{it['img']}|{it['value']}"
        try:
            raw["target"][key] = ask(f"{HERE}/{it['img']}",
                                     f"On this report page, find the number {it['value']}. What caption/label does the "
                                     f"page layout pair with it? Reply with ONLY the caption text as printed.", 120)
        except Exception as e:
            raw["target"][key] = f"ERR:{e}"
        if n % 20 == 0:
            print(f"  target {n}/{len(items)}", flush=True)
json.dump(raw, open(f"{HERE}/qwen-raw.json", "w"))
print(f"DONE in {(time.time()-t0)/60:.0f}m -> qwen-raw.json", flush=True)
