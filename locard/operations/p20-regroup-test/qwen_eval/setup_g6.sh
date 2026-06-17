#!/bin/bash
# Qwen eval setup on the g6 (run from the unpacked bundle dir)
set -e
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet torch --index-url https://download.pytorch.org/whl/cu121
.venv/bin/pip install --quiet "transformers>=4.49" accelerate qwen-vl-utils pillow
echo "setup done. smoke: .venv/bin/python run_qwen.py --mode=target --limit=2"
echo "full run:        nohup .venv/bin/python run_qwen.py > qwen-run.log 2>&1 &"
