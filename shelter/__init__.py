"""避難所AI（避難所PC側）。仕様: docs/spec/避難所AI_仕様書_v1.0.md

起動:
    powershell -ExecutionPolicy Bypass -File scripts\\run_shelter.ps1
    （または） python -m uvicorn shelter.main:app --host 0.0.0.0 --port 8000
"""
