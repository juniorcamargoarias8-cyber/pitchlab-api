import os
import json
import time
import unicodedata
import requests
from datetime import datetime, timedelta
from src.config import CACHE_DIR, ESPN_TODAS_LIGAS, ESPN_TORNEOS_INTERNACIONALES

MEMORIA_PATH = os.path.join("data", "memoria_pitchlab.json")
os.makedirs("data", exist_ok=True)

_session = requests.Session()
_session.headers.update({"User-Agent": "PitchLab-Brain/1.0"})


def _norm_eq(s: str) -> str:
    """Normaliza nombres de clubes quitando tildes y prefijos/sufijos comunes."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    for tok in (
        "fc ", " fk", " sk", " cf", "ac ", "as ", "sc ", "cd ",
        "club ", "deportivo ", "vfb ", "ind. ", "independiente ", "futbol ",
    ):
        s = s.replace(tok, " ")
    return " ".join(s.split())


def _cargar_memoria() -> dict:
    if os.path.exists(MEMORIA_PATH):
        try:
            with open(MEMORIA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "predicciones": {},
        "factores_aprendidos": {},
        "metricas_globales": {
            "partidos_auditados": 0,
            "aciertos_top_mercado": 0,
            "total_mercados_evaluados": 0,
            "hit_rate_pct": 0.0,
        },
    }


def _guardar_memoria(data: dict):
    with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _seleccionar_mercados_diversificados(mercados: list) -> list:
    """Elige hasta 3 mercados en rango Betano (68–85.5%), máximo 1 por categoría para evitar correlación."""
    candidatos = [
        m for m in mercados
        if 68.0 <= m["probabilidad"] <= 85.5 and "menos de 4.5 goles" not in m["mercado"].lower()
    ]
    if not candidatos:
        candidatos = [m for m in mercados if "menos de 4.5 goles" not in m["mercado"].lower()]

    top_diversificado = []
    categorias_vistas = set()

    for m in candidatos:
        nom = m["mercado"].lower()
        cat = (
            "corners" if "córner" in nom
            else "tiros" if "tiros" in nom
            else "goles" if ("gol" in nom or "btts" in nom)
            else "resultado"
        )
        if cat not in categorias_vistas:
            categorias_vistas.add(cat)
            top_diversificado.append(m)
        if len(top_diversificado) == 3:
            break

    return top_diversificado or mercados[:3]


def registrar_prediccion(
    liga: str,
    local: str,
    visita: str,
    esperados: dict,
    mercados: list,
    fecha_partido: str = "",
):
    """Guarda en memoria cada partido analizado con mercados diversificados para auditarlo al finalizar."""
    mem = _cargar_memoria()
    hoy_str = fecha_partido or datetime.now().strftime("%Y-%m-%d")
    clave = f"{hoy_str}_{liga}_{local}_vs_{visita}"

    if clave in mem["predicciones"] and mem["predicciones"][clave].get("auditado"):
        return

    top_betano = _seleccionar_mercados_diversificados(mercados)

    mem["predicciones"][clave] = {
        "fecha": hoy_str,
        "liga": liga,
        "local": local,
        "visita": visita,
        "esperados": esperados,
        "mercados_evaluados": top_betano,
        "auditado": False,
        "resultado_real": None,
        "aciertos": [],
        "timestamp": int(time.time()),
    }
    _guardar_memoria(mem)


def _buscar_resultado_real_espn(liga: str, local: str, visita: str, fecha_str: str):
    """Consulta ESPN para verificar si el partido ya terminó y extraer marcador, córners, tiros y tarjetas."""
    slug = ESPN_TODAS_LIGAS.get(liga)
    if not slug:
        return None

    # Consultar la fecha registrada y el día anterior por desfase horario UTC/América
    fechas_a_consultar = []
    try:
        dt_base = datetime.strptime(fecha_str[:10], "%Y-%m-%d")
        fechas_a_consultar = [
            dt_base.strftime("%Y%m%d"),
            (dt_base - timedelta(days=1)).strftime("%Y%m%d"),
        ]
    except Exception:
        fechas_a_consultar = [fecha_str.replace("-", "")[:8]]

    l_n = _norm_eq(local)
    v_n = _norm_eq(visita)

    for fecha_compacta in fechas_a_consultar:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={fecha_compacta}"
            resp = _session.get(url, timeout=6).json()
            for ev in resp.get("events", []):
                comp = ev.get("competitions", [{}])[0]
                if not comp.get("status", {}).get("type", {}).get("completed", False):
                    continue

                comps = comp.get("competitors", [])
                if len(comps) < 2:
                    continue

                h_o = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
                a_o = next((c for c in comps if c.get("homeAway") == "away"), comps[1])

                h_n = _norm_eq(h_o.get("team", {}).get("displayName", ""))
                a_n = _norm_eq(a_o.get("team", {}).get("displayName", ""))

                if (l_n[:5] in h_n or h_n[:5] in l_n) and (v_n[:5] in a_n or a_n[:5] in v_n):
                    event_id = ev.get("id")
                    goles_l = int(float(h_o.get("score", 0) or 0))
                    goles_v = int(float(a_o.get("score", 0) or 0))

                    url_sum = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={event_id}"
                    summary = _session.get(url_sum, timeout=6).json()
                    equipos_box = summary.get("boxscore", {}).get("teams", [])

                    stats_real = {
                        "goles_local": goles_l,
                        "goles_visita": goles_v,
                        "goles_total": goles_l + goles_v,
                        "corners_local": 0,
                        "corners_visita": 0,
                        "corners_total": 0,
                        "tiros_local": 0,
                        "tiros_visita": 0,
                        "tiros_total": 0,
                        "tarjetas_total": 0,
                    }

                    for eq_b in equipos_box:
                        es_home = str(eq_b.get("team", {}).get("id")) == str(h_o.get("team", {}).get("id"))
                        s_map = {}
                        for s in eq_b.get("statistics", []):
                            try:
                                s_map[s["name"]] = float(s.get("displayValue", 0) or 0)
                            except ValueError:
                                pass

                        tiros = int(s_map.get("shotsOnTarget", s_map.get("onGoalShot", 0)))
                        corners = int(s_map.get("wonCorners", s_map.get("cornerKicks", 0)))
                        tarj = int(s_map.get("yellowCards", 0) + s_map.get("redCards", 0))

                        if es_home:
                            stats_real["corners_local"] = corners
                            stats_real["tiros_local"] = tiros
                        else:
                            stats_real["corners_visita"] = corners
                            stats_real["tiros_visita"] = tiros

                        stats_real["corners_total"] += corners
                        stats_real["tiros_total"] += tiros
                        stats_real["tarjetas_total"] += tarj

                    return stats_real
        except Exception:
            continue

    return None


def _evaluar_mercado_acertado(mercado: str, real: dict, local: str, visita: str) -> bool:
    """Evalúa si un mercado recomendado se cumplió en el partido real."""
    m = mercado.lower()
    gl, gv, gt = real["goles_local"], real["goles_visita"], real["goles_total"]
    cl, cv, ct = real["corners_local"], real["corners_visita"], real["corners_total"]
    tl, tv = real["tiros_local"], real["tiros_visita"]
    tarj = real["tarjetas_total"]

    if "1 (gana local)" in m:
        return gl > gv
    if "2 (gana visita)" in m:
        return gv > gl
    if "1x (" in m:
        return gl >= gv
    if "x2 (" in m:
        return gv >= gl
    if "12 (" in m:
        return gl != gv
    if "btts (ambos anotan - sí)" in m:
        return gl > 0 and gv > 0
    if "btts (ambos anotan - no)" in m:
        return not (gl > 0 and gv > 0)
    if "más de 1.5 goles" in m:
        return gt > 1.5
    if "más de 2.5 goles" in m:
        return gt > 2.5
    if "menos de 3.5 goles" in m:
        return gt < 3.5
    if "menos de 4.5 goles" in m:
        return gt < 4.5
    if "más de 6.5 córners" in m:
        return ct > 6.5
    if "más de 7.5 córners" in m:
        return ct > 7.5
    if "más de 8.5 córners" in m:
        return ct > 8.5
    if "más de 9.5 córners" in m:
        return ct > 9.5
    if "más de 2.5 tarjetas" in m:
        return tarj > 2.5
    if "más de 3.5 tarjetas" in m:
        return tarj > 3.5

    # Mercados individuales por equipo
    if local.lower() in m:
        if "+0.5 goles" in m:
            return gl > 0.5
        if "+1.5 goles" in m:
            return gl > 1.5
        if "+2.5 córners" in m:
            return cl > 2.5
        if "+3.5 córners" in m:
            return cl > 3.5
        if "+4.5 córners" in m:
            return cl > 4.5
        if "+5.5 córners" in m:
            return cl > 5.5
        if "+2.5 tiros" in m:
            return tl > 2.5
        if "+3.5 tiros" in m:
            return tl > 3.5
        if "+4.5 tiros" in m:
            return tl > 4.5
        if "+6.5 tiros" in m:
            return tl > 6.5

    if visita.lower() in m:
        if "+0.5 goles" in m:
            return gv > 0.5
        if "+1.5 goles" in m:
            return gv > 1.5
        if "+2.5 córners" in m:
            return cv > 2.5
        if "+3.5 córners" in m:
            return cv > 3.5
        if "+4.5 córners" in m:
            return cv > 4.5
        if "+5.5 córners" in m:
            return cv > 5.5
        if "+2.5 tiros" in m:
            return tv > 2.5
        if "+3.5 tiros" in m:
            return tv > 3.5
        if "+4.5 tiros" in m:
            return tv > 4.5
        if "+6.5 tiros" in m:
            return tv > 6.5

    return False


def auditar_y_recalibrar() -> dict:
    """Revisa predicciones pendientes, compara con resultados reales y ajusta los factores del modelo."""
    mem = _cargar_memoria()
    hubo_cambios = False

    for clave, reg in mem["predicciones"].items():
        if reg.get("auditado"):
            continue

        real = _buscar_resultado_real_espn(reg["liga"], reg["local"], reg["visita"], reg["fecha"])
        if not real:
            continue

        reg["auditado"] = True
        reg["resultado_real"] = real
        evaluaciones = []
        for m_obj in reg.get("mercados_evaluados", []):
            ok = _evaluar_mercado_acertado(m_obj["mercado"], real, reg["local"], reg["visita"])
            evaluaciones.append({"mercado": m_obj["mercado"], "acertado": ok})
        reg["aciertos"] = evaluaciones
        hubo_cambios = True

    if hubo_cambios:
        _recalcular_factores_aprendidos(mem)
        _guardar_memoria(mem)

    return obtener_resumen_aprendizaje(mem)


def _recalcular_factores_aprendidos(mem: dict):
    """Calcula sesgos reales (Real / Esperado) por liga para corregir sobreestimaciones."""
    acum_liga = {}
    total_mercados = 0
    total_aciertos = 0
    partidos_aud = 0

    for reg in mem["predicciones"].values():
        if not reg.get("auditado") or not reg.get("resultado_real"):
            continue

        partidos_aud += 1
        liga = reg["liga"]
        esp = reg["esperados"]
        real = reg["resultado_real"]

        for ev in reg.get("aciertos", []):
            total_mercados += 1
            if ev["acertado"]:
                total_aciertos += 1

        if liga not in acum_liga:
            acum_liga[liga] = {
                "n": 0,
                "esp_gl": 0.0, "real_gl": 0.0,
                "esp_gv": 0.0, "real_gv": 0.0,
                "esp_cl": 0.0, "real_cl": 0.0,
                "esp_cv": 0.0, "real_cv": 0.0,
                "esp_tl": 0.0, "real_tl": 0.0,
                "esp_tv": 0.0, "real_tv": 0.0,
            }

        d = acum_liga[liga]
        d["n"] += 1
        d["esp_gl"] += esp.get("goles_local", 1.0)
        d["real_gl"] += real.get("goles_local", 0)
        d["esp_gv"] += esp.get("goles_visita", 1.0)
        d["real_gv"] += real.get("goles_visita", 0)

        if esp.get("tiene_stats_avanzadas"):
            d["esp_cl"] += esp.get("corners_local", 1.0)
            d["real_cl"] += real.get("corners_local", 0)
            d["esp_cv"] += esp.get("corners_visita", 1.0)
            d["real_cv"] += real.get("corners_visita", 0)
            d["esp_tl"] += esp.get("tiros_local", 1.0)
            d["real_tl"] += real.get("tiros_local", 0)
            d["esp_tv"] += esp.get("tiros_visita", 1.0)
            d["real_tv"] += real.get("tiros_visita", 0)

    factores = {}
    for liga, d in acum_liga.items():
        if d["n"] < 2:
            continue

        def _ratio_suavizado(real_val, esp_val):
            if esp_val <= 0:
                return 1.0
            ratio_puro = real_val / esp_val
            ajustado = 0.5 + (0.5 * ratio_puro)
            return round(max(0.65, min(1.25, ajustado)), 3)

        factores[liga] = {
            "muestras": d["n"],
            "mult_goles_local": _ratio_suavizado(d["real_gl"], d["esp_gl"]),
            "mult_goles_visita": _ratio_suavizado(d["real_gv"], d["esp_gv"]),
            "mult_corners_local": _ratio_suavizado(d["real_cl"], d["esp_cl"]),
            "mult_corners_visita": _ratio_suavizado(d["real_cv"], d["esp_cv"]),
            "mult_tiros_local": _ratio_suavizado(d["real_tl"], d["esp_tl"]),
            "mult_tiros_visita": _ratio_suavizado(d["real_tv"], d["esp_tv"]),
        }

    mem["factores_aprendidos"] = factores
    mem["metricas_globales"] = {
        "partidos_auditados": partidos_aud,
        "aciertos_top_mercado": total_aciertos,
        "total_mercados_evaluados": total_mercados,
        "hit_rate_pct": round((total_aciertos / total_mercados) * 100, 1) if total_mercados > 0 else 0.0,
    }


def obtener_factores_liga(liga: str) -> dict:
    mem = _cargar_memoria()
    return mem.get("factores_aprendidos", {}).get(liga, {})


def obtener_resumen_aprendizaje(mem: dict = None) -> dict:
    if mem is None:
        mem = _cargar_memoria()
    auditados_lista = [
        r for r in mem.get("predicciones", {}).values()
        if r.get("auditado") and r.get("resultado_real")
    ]
    recientes = [
        {
            "partido": f"{r['local']} vs {r['visita']}",
            "liga": r["liga"],
            "marcador": f"{r['resultado_real']['goles_local']}-{r['resultado_real']['goles_visita']}",
            "aciertos": r.get("aciertos", []),
        }
        for r in auditados_lista[-5:]
    ]
    return {
        "metricas": mem.get("metricas_globales", {}),
        "factores_por_liga": mem.get("factores_aprendidos", {}),
        "ultimos_auditados": recientes,
        "total_en_memoria": len(mem.get("predicciones", {})),
    }