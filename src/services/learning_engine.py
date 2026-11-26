import os
import json
import requests
from datetime import datetime, timezone, timedelta
from src.config import ESPN_TODAS_LIGAS

_RAIZ_PROYECTO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(_RAIZ_PROYECTO, "data")
MEMORIA_PATH = os.path.join(DATA_DIR, "memoria_pitchlab.json")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_TZ_LIMA = timezone(timedelta(hours=-5))

_ESTRUCTURA_BASE = {
    "partidos": {},
    "factores_liga": {},
    "metricas": {
        "partidos_auditados": 0,
        "aciertos_top_mercado": 0,
        "total_mercados_evaluados": 0,
    },
}


def _ahora_lima_iso() -> str:
    return datetime.now(_TZ_LIMA).isoformat()


def _headers_supabase() -> dict:
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    return headers


def _normalizar_memoria(datos: dict) -> dict:
    if not isinstance(datos, dict):
        datos = json.loads(json.dumps(_ESTRUCTURA_BASE))
    datos.setdefault("partidos", {})
    datos.setdefault("factores_liga", {})
    datos.setdefault(
        "metricas",
        {
            "partidos_auditados": 0,
            "aciertos_top_mercado": 0,
            "total_mercados_evaluados": 0,
        },
    )
    return datos


def _cargar_memoria() -> dict:
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/pitchlab_estado?id=eq.principal&select=datos"
            resp = requests.get(url, headers=_headers_supabase(), timeout=6)
            if resp.status_code == 200:
                filas = resp.json()
                if filas and isinstance(filas[0].get("datos"), dict):
                    datos = _normalizar_memoria(filas[0]["datos"])
                    os.makedirs(os.path.dirname(MEMORIA_PATH), exist_ok=True)
                    with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
                        json.dump(datos, f, ensure_ascii=False, indent=2)
                    return datos
            else:
                print(f"[SUPABASE GET ERROR] {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[SUPABASE LOAD ERROR] {e}")

    if os.path.exists(MEMORIA_PATH):
        try:
            with open(MEMORIA_PATH, "r", encoding="utf-8") as f:
                return _normalizar_memoria(json.load(f))
        except Exception:
            pass

    return json.loads(json.dumps(_ESTRUCTURA_BASE))


def _guardar_memoria(memoria: dict):
    memoria = _normalizar_memoria(memoria)
    os.makedirs(os.path.dirname(MEMORIA_PATH), exist_ok=True)
    with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)

    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/pitchlab_estado?on_conflict=id"
            payload = {
                "id": "principal",
                "datos": memoria,
                "actualizado_en": _ahora_lima_iso(),
            }
            r = requests.post(url, headers=_headers_supabase(), json=payload, timeout=8)
            if r.status_code not in (200, 201, 204):
                print(f"[SUPABASE ERROR] {r.status_code}: {r.text}")
        except Exception as e:
            print(f"[SUPABASE EXCEPTION] {e}")


def _armar_clave(liga: str, local: str, visita: str, fecha: str = "") -> str:
    f_corto = str(fecha or "")[:10]
    return f"{str(liga).strip()}|{str(local).strip()}|{str(visita).strip()}|{f_corto}"


def registrar_prediccion(
    liga: str,
    local: str,
    visita: str,
    esperados: dict = None,
    mercados: list = None,
    fecha: str = "",
):
    """Guarda el pronostico generado para un partido y persiste la memoria."""
    clave = _armar_clave(liga, local, visita, fecha)
    if not clave.strip("|"):
        return

    memoria = _cargar_memoria()
    entry = memoria["partidos"].setdefault(
        clave,
        {
            "liga": liga,
            "local": local,
            "visita": visita,
            "fecha": str(fecha or "")[:10],
            "registrado_en": _ahora_lima_iso(),
            "auditado": False,
        },
    )
    if esperados is not None:
        entry["esperados"] = esperados
    if mercados is not None:
        entry["mercados"] = mercados
        entry["top_mercado"] = mercados[0] if mercados else None
    entry["actualizado_en"] = _ahora_lima_iso()
    _guardar_memoria(memoria)


def _buscar_marcador_espn(liga: str, local: str, visita: str, fecha: str):
    slug = ESPN_TODAS_LIGAS.get(liga)
    if not slug:
        return None

    fecha_param = fecha[:10].replace("-", "") if fecha else datetime.now(_TZ_LIMA).strftime("%Y%m%d")
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={fecha_param}"
        resp = requests.get(url, timeout=8).json()
        for ev in resp.get("events", []):
            comp = ev.get("competitions", [{}])[0]
            if not comp.get("status", {}).get("type", {}).get("completed", False):
                continue
            competidores = comp.get("competitors", [])
            if len(competidores) < 2:
                continue

            h_obj = next((c for c in competidores if c.get("homeAway") == "home"), competidores[0])
            a_obj = next((c for c in competidores if c.get("homeAway") == "away"), competidores[1])

            h_name = h_obj.get("team", {}).get("displayName", "").lower()
            a_name = a_obj.get("team", {}).get("displayName", "").lower()

            l_ok = local.lower() in h_name or h_name in local.lower()
            v_ok = visita.lower() in a_name or a_name in visita.lower()

            if l_ok and v_ok:
                goles_local = int(float(h_obj.get("score", 0) or 0))
                goles_visita = int(float(a_obj.get("score", 0) or 0))
                return goles_local, goles_visita
    except Exception:
        pass
    return None


def auditar_partidos_completados() -> dict:
    """Audita los partidos pendientes que ya finalizaron en ESPN y actualiza metricas."""
    memoria = _cargar_memoria()
    hubo_cambios = False

    for _, info in memoria["partidos"].items():
        if info.get("auditado"):
            continue

        marcador = _buscar_marcador_espn(
            info.get("liga", ""),
            info.get("local", ""),
            info.get("visita", ""),
            info.get("fecha", ""),
        )
        if marcador is None:
            continue

        g_local, g_visita = marcador
        total_goles = g_local + g_visita
        acierto = total_goles >= 2

        info["auditado"] = True
        info["marcador"] = f"{g_local}-{g_visita}"
        info["acierto_top_mercado"] = acierto
        info["auditado_en"] = _ahora_lima_iso()

        memoria["metricas"]["partidos_auditados"] += 1
        memoria["metricas"]["total_mercados_evaluados"] += 1
        if acierto:
            memoria["metricas"]["aciertos_top_mercado"] += 1

        liga = info.get("liga", "General")
        fl = memoria["factores_liga"].setdefault(liga, {"auditados": 0, "aciertos": 0, "factor": 1.0})
        fl["auditados"] += 1
        if acierto:
            fl["aciertos"] += 1
        fl["factor"] = round(0.85 + 0.30 * (fl["aciertos"] / max(1, fl["auditados"])), 3)
        hubo_cambios = True

    if hubo_cambios:
        _guardar_memoria(memoria)

    return memoria


def obtener_resumen_cerebro() -> dict:
    memoria = _cargar_memoria()
    metricas = memoria.get("metricas", {})
    auditados = metricas.get("partidos_auditados", 0)
    aciertos = metricas.get("aciertos_top_mercado", 0)
    evaluados = metricas.get("total_mercados_evaluados", 0)
    hit_rate = round((aciertos / evaluados) * 100, 2) if evaluados > 0 else 0.0

    ultimos = [
        {
            "partido": f"{v.get('local', '')} vs {v.get('visita', '')}",
            "liga": v.get("liga", ""),
            "marcador": v.get("marcador", ""),
            "acierto": v.get("acierto_top_mercado", False),
            "auditado_en": v.get("auditado_en", ""),
        }
        for v in memoria.get("partidos", {}).values()
        if v.get("auditado")
    ][-10:]

    return {
        "metricas": {
            "partidos_auditados": auditados,
            "aciertos_top_mercado": aciertos,
            "total_mercados_evaluados": evaluados,
            "hit_rate_pct": hit_rate,
        },
        "factores_por_liga": memoria.get("factores_liga", {}),
        "ultimos_auditados": ultimos,
        "total_en_memoria": len(memoria.get("partidos", {})),
    }


def auditar_y_recalibrar() -> dict:
    """Audita partidos completados y devuelve el estado actual del cerebro."""
    auditar_partidos_completados()
    return obtener_resumen_cerebro()


def obtener_factores_liga(liga: str = None) -> dict:
    """Devuelve los factores calibrados por liga (o el de una liga especifica)."""
    memoria = _cargar_memoria()
    factores = memoria.get("factores_liga", {})
    if liga:
        return factores.get(liga, {})
    return factores