import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure stdout handles Unicode/emojis safely on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

# Load .env if present
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Config:
    # Meta & Webhook Config
    META_VERIFY_TOKEN: str = os.getenv("META_VERIFY_TOKEN", "camellia_verify_secret_2026")
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")
    
    # WhatsApp Cloud API Config
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_API_VERSION: str = os.getenv("WHATSAPP_API_VERSION", "v19.0")
    
    # Instagram Config
    INSTAGRAM_PAGE_ID: str = os.getenv("INSTAGRAM_PAGE_ID", "")
    
    # Human Escalation Contact
    HUMAN_ESCALATION_PHONE: str = os.getenv("HUMAN_ESCALATION_PHONE", "+918796551990")
    HUMAN_ESCALATION_EMAIL: str = os.getenv("HUMAN_ESCALATION_EMAIL", "owner@camelliaartisanal.com")
    
    # Data Storage
    DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "camellia.db"))
    MENU_JSON_PATH: str = os.getenv("MENU_JSON_PATH", str(BASE_DIR / "menu.json"))
    QA_TEMPLATES_PATH: str = os.getenv("QA_TEMPLATES_PATH", str(BASE_DIR / "qa_templates.json"))
    
    # Optional Live Google Sheet CSV URL for Menu Updates (Option A in PRD)
    GOOGLE_SHEET_CSV_URL: str = os.getenv("GOOGLE_SHEET_CSV_URL", "")

    # Ollama Fast & Accurate Model RAG Config
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "10.0"))
    LLM_MODE: str = os.getenv("LLM_MODE", "hybrid") # Options: 'hybrid', 'deterministic', 'ollama_only'
    OLLAMA_ENABLED: bool = os.getenv("OLLAMA_ENABLED", "true").lower() in ("true", "1", "t", "yes")

config = Config()
