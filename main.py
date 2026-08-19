import os
import uvicorn

if __name__ == "__main__":
    raw_port = os.environ.get("PORT", "8000")
    try:
        port = int(raw_port)
    except (ValueError, TypeError):
        port = 8000
    print(f"Starting Camellia Artisanal Bot on 0.0.0.0:{port}...")
    uvicorn.run("app:app", host="0.0.0.0", port=port)
