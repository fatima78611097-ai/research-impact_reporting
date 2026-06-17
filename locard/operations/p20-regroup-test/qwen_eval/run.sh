#!/bin/bash
# runs ON the g6 from the unpacked qwen_pkg dir (invoked by launch_qwen.py via SSM)
set -x
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cu121
.venv/bin/pip install --quiet "transformers>=4.49" accelerate qwen-vl-utils pillow
echo "=== smoke (2 targeted) ==="
.venv/bin/python run_qwen.py --mode=target --limit=2 || exit 1
echo "=== full eval ==="
.venv/bin/python run_qwen.py --mode=both
echo "=== scoring ==="
.venv/bin/python score_qwen.py | tee score-output.txt
aws s3 cp qwen-raw.json "s3://lavandula-nonprofit-collaterals/logs/qwen/qwen-raw.json"
aws s3 cp score-output.txt "s3://lavandula-nonprofit-collaterals/logs/qwen/score-output.txt"
