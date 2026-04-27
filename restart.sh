#!/bin/bash
cd "$(dirname "$0")"
pkill -f "src.main" 2>/dev/null
sleep 2
source venv/bin/activate
python -m src.main
