import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from src.config import LIGAS_DISPONIBLES, REGION_LIGA
from src.services.data_loader import (
    cargar_datos_liga,
    obtener_perfil_completo_equipo,
    obtener_historial_h2h,
    obtener_contexto_clasificacion,
    refrescar_cache_temporada_actual,
)
from src.services.espn_client import obtener_cartelera_hoy
from src.services.learning_engine import (
    registrar_prediccion,
    auditar_y_recalibrar,
    obtener_factores_liga,
)
from src.services.ml_engine import predecir_probabilidades_ml
from src.domain.poisson_engine import generar_prediccion_2026


def _evaluar_riesgo_visita_cero(stats_l: dict, stats_v: dict, esperados: dict) -> str | None:
    """Detecta escenarios donde '+0.5 Goles Visita' tiene riesgo alto de quedarse en 0."""
    clean_sheet_local = float((stats_l.get("local") or {}).get("pct_clean_sheet") or 0)
    tiros_visita = float((stats_v.get("visita") or {}).get("prom_tiros_arco_favor") or 0)
    goles_visita_esp = float((esperados or {}).get("goles_visita") or 0)

    motivos = []
    if clean_sheet_local >= 32.0:
        motivos.append(f"local con {clean_sheet_local}% de portería a cero en casa")
    if tiros_visita < 3.8:
        motivos.append(f"visita con {tiros_visita} tiros al arco fuera")
    if goles_visita_esp < 1.05:
        motivos.append(f"xG visitante bajo ({goles_visita_esp})")

    if motivos:
        return (
            "Riesgo de visitante sin gol (" + ", ".join(motivos) + "). "
            "Evita '+0.5 Goles Visita' a cuota baja; prioriza líneas de partido, córners o tarjetas."
        )
    return None


def _enriquecer_mercados(
    probs_ordenadas: list[dict],
    stats_l: dict,
    stats_v: dict,
    esperados: dict,
    alerta_visita: str | None,
    margen_seguridad: float = 0.06,
) -> list[dict]:
    """Añade cuota justa, cuota mínima para EV+ y clasificación de perfil rentable."""
    nombre_visita = str((stats_v or {}).get("equipo") or "").lower()
    enriquecidos = []

    for item in probs_ordenadas:
        mercado = item["mercado"]
        prob = float(item["probabilidad"])
        alerta_item = None

        if alerta_visita and "+0.5 goles" in mercado.lower() and nombre_visita and nombre_visita in mercado.lower():
            prob = round(prob * 0.90, 1)
            alerta_item = alerta_visita

        prob_dec = max(min(prob / 100.0, 0.99), 0.01)
        cuota_justa = round(1.0 / prob_dec, 2)
        prob_objetivo = max(prob_dec - margen_seguridad, 0.05)
        cuota_minima_ev = round(1.0 / prob_objetivo, 2)

        if cuota_minima_ev < 1.25:
            perfil = "CUOTA_BAJA"
        elif 1.35 <= cuota_minima_ev <= 1.85 and prob >= 65.0 and not alerta_item:
            perfil = "DOBLE_RENTABLE"
        elif cuota_minima_ev > 1.85 and prob >= 53.0 and not alerta_item:
            perfil = "SIMPLE_VALOR"
        else:
            perfil = "ESTANDAR"

        enriquecidos.append({
            "mercado": mercado,
            "probabilidad": prob,
            "cuota_justa": cuota_justa,
            "cuota_minima_ev": cuota_minima_ev,
            "perfil_ticket": perfil,
            "alerta": alerta_item,
        })

    enriquecidos.sort(key=lambda x: (x["alerta"] is None, x["probabilidad"]), reverse=True)
    return enriquecidos


def _calcular_prediccion_completa(liga: str, local: str, visita: str, ultimos_n: int = 0):
    """Ejecuta el motor híbrido (Poisson calibrado + Machine Learning) para un encuentro."""
    df = cargar_datos_liga(liga)
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_l = executor.submit(obtener_perfil_completo_equipo, df, local, True, ultimos_n)
        fut_v = executor.submit(obtener_perfil_completo_equipo, df, visita, False, ultimos_n)
        stats_l = fut_l.result()
        stats_v = fut_v.result()

    if not stats_l or not stats_v:
        return None

    historial_h2h = obtener_historial_h2h(df, local, visita, liga=liga, cargar_liga_fn=cargar_datos_liga)
    contexto_tabla = obtener_contexto_clasificacion(df, local, visita)
    factores_liga = obtener_factores_liga(liga)

    prediccion = generar_prediccion_2026(
        stats_l,
        stats_v,
        historial_h2h,
        contexto_tabla,
        liga=liga,
        factores_aprendidos=factores_liga,
    )

    # Fusión híbrida: 60% Poisson calibrado + 40% Machine Learning (GradientBoosting)
    probs_ml = predecir_probabilidades_ml(liga, df, stats_l, stats_v)
    probs_finales = dict(prediccion["probabilidades"])
    if probs_ml:
        for mercado, prob_ml in probs_ml.items():
            if mercado in probs_finales:
                probs_finales[mercado] = round((0.60 * probs_finales[mercado]) + (0.40 * prob_ml), 1)
        if "BTTS (Ambos Anotan - Sí)" in probs_finales:
            probs_finales["BTTS (Ambos Anotan - No)"] = round(100.0 - probs_finales["BTTS (Ambos Anotan - Sí)"], 1)

    probs_ordenadas = [
        {"mercado": k, "probabilidad": v}
        for k, v in sorted(probs_finales.items(), key=lambda x: x[1], reverse=True)
    ]

    claves = dict(prediccion["claves_deportivas"])
    alerta_visita = _evaluar_riesgo_visita_cero(stats_l, stats_v, prediccion["esperados"])
    if alerta_visita:
        claves["alerta_riesgo_visita"] = alerta_visita

    mercados_enriquecidos = _enriquecer_mercados(
        probs_ordenadas,
        stats_l,
        stats_v,
        prediccion["esperados"],
        alerta_visita,
    )

    return {
        "stats_local": stats_l,
        "stats_visita": stats_v,
        "esperados": prediccion["esperados"],
        "mercados": mercados_enriquecidos,
        "h2h": historial_h2h,
        "contexto": contexto_tabla,
        "claves_deportivas": claves,
        "calibracion_activa": bool(factores_liga),
        "ml_activo": bool(probs_ml),
    }


def _registrar_cartelera_en_segundo_plano(partidos: list):
    """Registra en memoria todos los partidos de la cartelera del día."""
    for p in partidos:
        try:
            liga = p["liga"]
            local = p["local_csv"]
            visita = p["visita_csv"]
            fecha = (p.get("hora_utc") or "")[:10]

            res = _calcular_prediccion_completa(liga, local, visita, ultimos_n=0)
            if res:
                registrar_prediccion(liga, local, visita, res["esperados"], res["mercados"], fecha)
            else:
                registrar_prediccion(liga, local, visita, None, None, fecha)
        except Exception:
            continue


def _tarea_arranque_asincrona():
    """Evita bloquear el arranque del servidor y las primeras peticiones HTTP."""
    try:
        partidos_hoy = obtener_cartelera_hoy(cargar_datos_liga)
        auditar_y_recalibrar()
        _registrar_cartelera_en_segundo_plano(partidos_hoy)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_tarea_arranque_asincrona, daemon=True).start()
    yield


app = FastAPI(title="PitchLab API • Predictor Futbolístico 2026", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def servir_interfaz():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/refrescar-cache")
def refrescar_cache(incluir_ml: bool = False, x_api_key: str = Header(default="")):
    clave_esperada = os.getenv("ADMIN_KEY", "pitchlab2026")
    if x_api_key != clave_esperada:
        raise HTTPException(status_code=401, detail="Clave inválida")

    eliminados = refrescar_cache_temporada_actual(incluir_ml=incluir_ml)
    return {
        "status": "ok",
        "archivos_eliminados": eliminados,
        "ml_reentrenara_si_borrado": incluir_ml,
    }


@app.get("/api/cartelera")
def listar_cartelera_hoy(background_tasks: BackgroundTasks, fecha: str = None):
    partidos = obtener_cartelera_hoy(cargar_datos_liga, fecha)
    background_tasks.add_task(auditar_y_recalibrar)
    background_tasks.add_task(_registrar_cartelera_en_segundo_plano, partidos)
    return {"partidos": partidos}


@app.get("/api/auditar")
def disparar_auditoria():
    """Ejecuta la auditoría de partidos completados y recalibra métricas."""
    return auditar_y_recalibrar()


@app.get("/api/ticket-optimo")
def generar_ticket_optimo(fecha: str = None):
    """Construye combinadas seguras (1 selección por partido) priorizando cuotas rentables sin alerta."""
    partidos = obtener_cartelera_hoy(cargar_datos_liga, fecha)
    candidatos = []

    for p in partidos:
        if p.get("completado"):
            continue
        try:
            liga = p["liga"]
            local = p["local_csv"]
            visita = p["visita_csv"]
            res = _calcular_prediccion_completa(liga, local, visita, ultimos_n=0)
            if not res:
                continue

            # Descartar partidos donde ambos clubes tienen muestra sintética
            if res["stats_local"].get("pj", 1) == 0 and res["stats_visita"].get("pj", 1) == 0:
                continue

            # Buscar mercado sin alerta y con cuota competitiva (>= 1.32) evitando cuotas muy bajas
            mejor_pick = None
            for m in res["mercados"]:
                prob = m["probabilidad"]
                nom = m["mercado"].lower()
                cuota_est = m.get("cuota_minima_ev") or (round(100.0 / prob, 2) if prob > 0 else 0)
                if (
                    not m.get("alerta")
                    and 62.0 <= prob <= 82.0
                    and cuota_est >= 1.32
                    and "12 (" not in nom
                ):
                    mejor_pick = {**m, "cuota_estimada": cuota_est}
                    break

            if mejor_pick:
                candidatos.append({
                    "partido": f"{p['local_display']} vs {p['visita_display']}",
                    "local_csv": local,
                    "visita_csv": visita,
                    "liga": liga,
                    "hora_utc": p.get("hora_utc", ""),
                    "mercado": mejor_pick["mercado"],
                    "probabilidad": mejor_pick["probabilidad"],
                    "cuota_estimada": mejor_pick["cuota_estimada"],
                    "perfil_ticket": mejor_pick.get("perfil_ticket", "ESTANDAR"),
                    "marcador_previsto": res["esperados"]["marcador_probable"],
                })
        except Exception:
            continue

    candidatos.sort(key=lambda x: x["probabilidad"], reverse=True)

    def _armar_combo(lista_picks: list, max_legs: int):
        seleccionados = []
        conteo_cat = {"goles": 0, "corners": 0, "tiros": 0, "resultado": 0}
        for item in lista_picks:
            nom = item["mercado"].lower()
            cat = (
                "corners" if "córner" in nom
                else "tiros" if "tiros" in nom
                else "goles" if ("gol" in nom or "btts" in nom)
                else "resultado"
            )
            if conteo_cat[cat] >= 2:
                continue
            conteo_cat[cat] += 1
            seleccionados.append(item)
            if len(seleccionados) == max_legs:
                break

        if not seleccionados:
            return None

        prob_conjunta = 1.0
        cuota_total = 1.0
        for s in seleccionados:
            prob_conjunta *= (s["probabilidad"] / 100.0)
            cuota_total *= s["cuota_estimada"]

        return {
            "selecciones": seleccionados,
            "probabilidad_conjunta": round(prob_conjunta * 100, 1),
            "cuota_combinada_est": round(cuota_total, 2),
        }

    return {
        "total_analizados": len(candidatos),
        "ticket_seguro_2": _armar_combo(candidatos, 2),
        "ticket_seguro_3": _armar_combo(candidatos, 3),
        "ticket_equilibrado_4": _armar_combo(candidatos, 4),
    }


@app.get("/api/cerebro")
def estado_cerebro():
    return auditar_y_recalibrar()


@app.get("/api/ligas")
def listar_ligas():
    return {
        "ligas": [
            {"nombre": k, "region": REGION_LIGA.get(k, "Europa")}
            for k in LIGAS_DISPONIBLES.keys()
        ]
    }


@app.get("/api/equipos")
def listar_equipos(liga: str):
    try:
        df = cargar_datos_liga(liga)
        df_actual = df[df["TempKey"].astype(str).isin(["2627", "2026", "2526", "2025"])]
        if df_actual.empty:
            df_actual = df
        equipos = sorted(
            {str(eq).strip() for eq in df_actual["HomeTeam"].dropna().unique() if str(eq).strip()}
        )
        return {"equipos": equipos}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/predecir")
def predecir_partido(liga: str, local: str, visita: str, ultimos_n: int = 0, fecha: str = ""):
    if local == visita:
        raise HTTPException(status_code=400, detail="El equipo local y visitante deben ser distintos.")
    try:
        res = _calcular_prediccion_completa(liga, local, visita, ultimos_n)
        if not res:
            raise HTTPException(status_code=404, detail="No hay suficientes datos para uno de los equipos.")

        registrar_prediccion(liga, local, visita, res["esperados"], res["mercados"], fecha)

        return {
            "partido": f"{local} vs {visita}",
            "liga": liga,
            **res,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))