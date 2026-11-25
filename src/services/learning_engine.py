import os
import json
import requests
from datetime import datetime, timezone, timedelta
from src.config import DATA_DIR

MEMORIA_PATH = os.path.join(DATA_DIR, "memoria_pitchlab.json")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_ESTRUCTURA_BASE = {
    "partidos": {},
    "factores_liga": {},
    "metricas": {
        "partidos_auditados": 0,
        "aciertos_top_mercado": 0,
        "total_mercados_evaluados": 0,
    },
}


def _headers_supabase() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def _cargar_memoria() -> dict:
    # 1. Intentar leer desde Supabase (persistencia permanente en la nube)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/pitchlab_estado?id=eq.principal&select=datos"
            resp = requests.get(url, headers=_headers_supabase(), timeout=6)
            if resp.status_code == 200:
                filas = resp.json()
                if filas and isinstance(filas[0].get("datos"), dict):
                    datos = filas[0]["datos"]
                    with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
                        json.dump(datos, f, ensure_ascii=False, indent=2)
                    return datos
        except Exception:
            pass

    # 2. Respaldo: leer archivo local si existe
    if os.path.exists(MEMORIA_PATH):
        try:
            with open(MEMORIA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return json.loads(json.dumps(_ESTRUCTURA_BASE))


def _guardar_memoria(memoria: dict):
    # Guardar localmente como caché rápida
    os.makedirs(os.path.dirname(MEMORIA_PATH), exist_ok=True)
    with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)

    # Sincronizar en Supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/pitchlab_estado"
            payload = {
                "id": "principal",
                "datos": memoria,
                "actualizado_en": datetime.now(timezone(timedelta(hours=-5))).isoformat(),
            }
            requests.post(url, headers=_headers_supabase(), json=payload, timeout=6)
        except Exception:
            pass