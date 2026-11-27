import threading
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from src.config import LIGAS_DISPONIBLES, REGION_LIGA
from src.services.data_loader import (
    cargar_datos_liga,
    obtener_perfil_completo_equipo,
    obtener_historial_h2h,
    obtener_contexto_clasificacion,
)
from src.services.espn_client import obtener_cartelera_hoy
from src.services.learning_engine import (
    registrar_prediccion,
    auditar_y_recalibrar,
    obtener_factores_liga,
)
from src.services.ml_engine import predecir_probabilidades_ml
from src.domain.poisson_engine import generar_prediccion_2026


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

    return {
        "stats_local": stats_l,
        "stats_visita": stats_v,
        "esperados": prediccion["esperados"],
        "mercados": probs_ordenadas,
        "h2h": historial_h2h,
        "contexto": contexto_tabla,
        "claves_deportivas": prediccion["claves_deportivas"],
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
    """Construye combinadas seguras (1 selección por partido) filtrando el rango Betano (68%-85.5%)."""
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

            # Buscar el mejor mercado en rango óptimo Betano (68% - 85.5%)
            mejor_pick = None
            for m in res["mercados"]:
                prob = m["probabilidad"]
                nom = m["mercado"].lower()
                if 68.5 <= prob <= 85.0 and "12 (" not in nom:
                    mejor_pick = m
                    break

            if mejor_pick:
                cuota_est = round(100.0 / mejor_pick["probabilidad"], 2)
                candidatos.append({
                    "partido": f"{p['local_display']} vs {p['visita_display']}",
                    "local_csv": local,
                    "visita_csv": visita,
                    "liga": liga,
                    "hora_utc": p.get("hora_utc", ""),
                    "mercado": mejor_pick["mercado"],
                    "probabilidad": mejor_pick["probabilidad"],
                    "cuota_estimada": cuota_est,
                    "marcador_previsto": res["esperados"]["marcador_probable"],
                })
        except Exception:
            continue

    # Ordenar de mayor a menor probabilidad asegurando diversidad de categorías
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
            # Evitar que un ticket de 3-4 selecciones dependa solo de córners
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