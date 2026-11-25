import math
from src.config import ESPN_TORNEOS_INTERNACIONALES


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (math.exp(-lam) * (lam ** k)) / math.factorial(k)


def prob_mas_de(linea: float, lam: float) -> float:
    limite_entero = math.floor(linea)
    prob_menos_o_igual = sum(poisson_pmf(k, lam) for k in range(limite_entero + 1))
    return round((1 - prob_menos_o_igual) * 100, 1)


def calcular_matriz_1x2_y_marcadores(lam_local: float, lam_visita: float, max_goles: int = 8) -> dict:
    prob_1 = prob_x = prob_2 = prob_btts = 0.0
    marcadores = []

    for g_l in range(max_goles + 1):
        p_l = poisson_pmf(g_l, lam_local)
        for g_v in range(max_goles + 1):
            p_v = poisson_pmf(g_v, lam_visita)
            prob_m = p_l * p_v

            marcadores.append((f"{g_l}-{g_v}", round(prob_m * 100, 1)))

            if g_l > g_v:
                prob_1 += prob_m
            elif g_l == g_v:
                prob_x += prob_m
            else:
                prob_2 += prob_m

            if g_l > 0 and g_v > 0:
                prob_btts += prob_m

    marcadores.sort(key=lambda x: x[1], reverse=True)

    return {
        "mercados": {
            "1 (Gana Local)": round(prob_1 * 100, 1),
            "X (Empate)": round(prob_x * 100, 1),
            "2 (Gana Visita)": round(prob_2 * 100, 1),
            "1X (Local o Empate)": round((prob_1 + prob_x) * 100, 1),
            "X2 (Visita o Empate)": round((prob_2 + prob_x) * 100, 1),
            "12 (Cualquiera Gana)": round((prob_1 + prob_2) * 100, 1),
            "BTTS (Ambos Anotan - Sí)": round(prob_btts * 100, 1),
            "BTTS (Ambos Anotan - No)": round((1 - prob_btts) * 100, 1),
        },
        "top_marcadores": marcadores[:3],
    }


def _generar_lineas_dinamicas_equipo(nombre_eq: str, lam_corners: float, lam_tiros: float, es_local: bool) -> dict:
    """Ajusta las líneas de córners y tiros al arco al rango comercial real que abre Betano."""
    mercados = {}

    # Escala dinámica de Córners (Betano sube el piso cuando el equipo es dominante)
    if lam_corners >= 5.6:
        lineas_c = [4.5, 5.5, 6.5]
    elif lam_corners >= 4.4:
        lineas_c = [3.5, 4.5, 5.5]
    else:
        lineas_c = [2.5, 3.5] if not es_local else [3.5, 4.5]

    for lc in lineas_c:
        mercados[f"{nombre_eq} +{lc} Córners"] = prob_mas_de(lc, lam_corners)

    # Escala dinámica de Tiros al Arco (Betano abre +6.5/+7.5/+8.5 para ataques élite)
    if lam_tiros >= 6.8:
        lineas_t = [6.5, 7.5, 8.5]
    elif lam_tiros >= 5.3:
        lineas_t = [4.5, 5.5, 6.5]
    elif lam_tiros >= 4.0:
        lineas_t = [3.5, 4.5]
    else:
        lineas_t = [2.5, 3.5]

    for lt in lineas_t:
        mercados[f"{nombre_eq} +{lt} Tiros al arco"] = prob_mas_de(lt, lam_tiros)

    return mercados


def _aplicar_filtro_coherencia(probabilidades: dict, lam_goles_l: float, lam_goles_v: float, stats_l: dict, stats_v: dict) -> dict:
    """Elimina mercados contradictorios o distorsionados por muestras insuficientes."""
    probs = dict(probabilidades)
    diff_goles = lam_goles_l - lam_goles_v
    muestras_l = stats_l.get("partidos_jugados", 10)
    muestras_v = stats_v.get("partidos_jugados", 10)

    # Si un equipo tiene dominio claro en goles esperados, bloquear la doble oportunidad del rival
    if diff_goles >= 0.75:
        probs.pop("X2 (Visita o Empate)", None)
        probs.pop("2 (Gana Visita)", None)
    elif diff_goles <= -0.75:
        probs.pop("1X (Local o Empate)", None)
        probs.pop("1 (Gana Local)", None)

    # Si alguno de los dos equipos casi no tiene muestra real en CSV (<3 partidos), evitar 1X/X2 engañoso
    if muestras_l < 3 or muestras_v < 3:
        if abs(diff_goles) < 0.45:
            probs.pop("1X (Local o Empate)", None)
            probs.pop("X2 (Visita o Empate)", None)

    return probs


def _redactar_claves_deportivas(
    stats_l: dict,
    stats_v: dict,
    esp: dict,
    h2h: dict,
    ctx: dict,
    es_torneo_intl: bool,
    factores_aprendidos: dict = None,
) -> dict:
    ctx_l = (ctx or {}).get("local")
    ctx_v = (ctx or {}).get("visita")

    if ctx_l and ctx_v:
        linea_tabla = (
            f"{stats_l['equipo']} marcha {ctx_l['pos']}.° ({ctx_l['pts']} pts, DG {ctx_l['dg']}) en {ctx_l['zona'].lower()}. "
            f"{stats_v['equipo']} ocupa el puesto {ctx_v['pos']}.° ({ctx_v['pts']} pts, DG {ctx_v['dg']}) en {ctx_v['zona'].lower()}."
        )
    else:
        linea_tabla = f"Duelo estadístico entre {stats_l['equipo']} ({stats_l['pct_ganar']}% victorias en casa) y {stats_v['equipo']} ({stats_v['pct_ganar']}% fuera)."

    dinamica = (
        f"{stats_l['equipo']} promedia {stats_l['prom_goles_favor']} goles a favor en casa frente a una defensa visitante que encaja "
        f"{stats_v['prom_goles_contra']} por salida, proyectando {esp['goles_local']} — {esp['goles_visita']} en goles esperados."
    )

    if h2h and h2h.get("total", 0) >= 2:
        clave_h2h = (
            f"En sus últimos {h2h['total']} cruces directos: {h2h['victorias_local']} victorias para {stats_l['equipo']}, "
            f"{h2h['empates']} empates y {h2h['victorias_visita']} para {stats_v['equipo']} (promedio de {h2h['prom_goles']} goles)."
        )
    else:
        clave_h2h = "Sin historial directo reciente en esta categoría; el modelo prioriza la forma actual por condición."

    tendencia = ""
    alertas = []

    if stats_l.get("partidos_jugados", 10) < 3 or stats_v.get("partidos_jugados", 10) < 3:
        alertas.append(
            "⚠️ Muestra limitada en CSV para uno de los clubes; el modelo prioriza líneas de goles y estadísticas del equipo con historial completo."
        )

    if factores_aprendidos and factores_aprendidos.get("muestras", 0) >= 2:
        alertas.append(
            f"🧠 Calibración Activa ({factores_aprendidos['muestras']} partidos auditados en esta liga): pesos ajustados según rendimiento real."
        )

    if esp["goles_local"] >= 1.35 and esp["goles_visita"] >= 1.35:
        alertas.append(
            "🔥 Escenario Ideal BTTS: Ambos equipos superan 1.35 goles esperados y ofrecen buena cuota para 'Ambos Anotan - Sí'."
        )

    if esp.get("tiene_stats_avanzadas"):
        tendencia = (
            f"Proyección de {esp['corners_total']} córners totales ({esp['corners_local']} vs {esp['corners_visita']}), "
            f"{esp['tiros_total']} tiros al arco y {esp['tarjetas_total']} tarjetas esperadas."
        )

        if esp["corners_total"] >= 9.5 and esp["goles_total"] >= 2.45:
            alertas.append(
                "⚠️ Alerta Bet Builder: Alta proyección de córners y ritmo ofensivo; evita combinar con 'Menos de 3.5 / 4.5 Goles'."
            )

    return {
        "clasificacion": linea_tabla,
        "dinamica": dinamica,
        "historial": clave_h2h,
        "tendencia": tendencia,
        "alerta_correlacion": " | ".join(alertas),
    }


def generar_prediccion_2026(
    stats_local: dict,
    stats_visita: dict,
    h2h: dict = None,
    contexto: dict = None,
    liga: str = "",
    factores_aprendidos: dict = None,
) -> dict:
    es_torneo_intl = liga in ESPN_TORNEOS_INTERNACIONALES

    lam_goles_l = (stats_local["prom_goles_favor"] + stats_visita["prom_goles_contra"]) / 2
    lam_goles_v = (stats_visita["prom_goles_favor"] + stats_local["prom_goles_contra"]) / 2

    if stats_visita.get("pct_clean_sheet", 0) >= 42:
        lam_goles_l *= 0.90
    if stats_local.get("pct_clean_sheet", 0) >= 42:
        lam_goles_v *= 0.90

    # Solo penalizar al visitante en torneo internacional si no es claramente superior al local
    if es_torneo_intl and lam_goles_v <= (lam_goles_l * 1.25):
        lam_goles_v *= 0.88

    if contexto:
        if contexto.get("local"):
            lam_goles_l *= contexto["local"].get("factor_urgencia", 1.0)
        if contexto.get("visita"):
            lam_goles_v *= contexto["visita"].get("factor_urgencia", 1.0)

    if factores_aprendidos:
        lam_goles_l *= factores_aprendidos.get("mult_goles_local", 1.0)
        lam_goles_v *= factores_aprendidos.get("mult_goles_visita", 1.0)

    ajuste_h2h_aplicado = False
    if h2h and h2h.get("total", 0) >= 3:
        lam_goles_l = (0.85 * lam_goles_l) + (0.15 * h2h["prom_goles_equipo_local"])
        lam_goles_v = (0.85 * lam_goles_v) + (0.15 * h2h["prom_goles_equipo_visita"])
        ajuste_h2h_aplicado = True

    lam_goles_total = lam_goles_l + lam_goles_v
    matriz = calcular_matriz_1x2_y_marcadores(lam_goles_l, lam_goles_v)

    probabilidades = {
        **matriz["mercados"],
        "Más de 1.5 Goles": prob_mas_de(1.5, lam_goles_total),
        "Más de 2.5 Goles": prob_mas_de(2.5, lam_goles_total),
        "Menos de 3.5 Goles": round(100 - prob_mas_de(3.5, lam_goles_total), 1),
        f"{stats_local['equipo']} +0.5 Goles": prob_mas_de(0.5, lam_goles_l),
        f"{stats_local['equipo']} +1.5 Goles": prob_mas_de(1.5, lam_goles_l),
        f"{stats_visita['equipo']} +0.5 Goles": prob_mas_de(0.5, lam_goles_v),
        f"{stats_visita['equipo']} +1.5 Goles": prob_mas_de(1.5, lam_goles_v),
    }

    esperados = {
        "goles_local": round(lam_goles_l, 2),
        "goles_visita": round(lam_goles_v, 2),
        "goles_total": round(lam_goles_total, 2),
        "marcador_probable": matriz["top_marcadores"][0][0],
        "marcador_alternativo": matriz["top_marcadores"][1][0],
        "tiene_stats_avanzadas": stats_local["tiene_stats_avanzadas"],
        "ajuste_h2h_aplicado": ajuste_h2h_aplicado,
    }

    if stats_local["tiene_stats_avanzadas"] and stats_visita["tiene_stats_avanzadas"]:
        lam_corners_l = (stats_local["prom_corners_favor"] + stats_visita["prom_corners_contra"]) / 2
        lam_corners_v = (stats_visita["prom_corners_favor"] + stats_local["prom_corners_contra"]) / 2

        lam_tiros_l = (stats_local["prom_tiros_arco_favor"] + stats_visita["prom_tiros_arco_contra"]) / 2
        lam_tiros_v = (stats_visita["prom_tiros_arco_favor"] + stats_local["prom_tiros_arco_contra"]) / 2

        if es_torneo_intl and lam_goles_v <= (lam_goles_l * 1.25):
            cs_local_ratio = min(stats_local.get("pct_clean_sheet", 0) / 100.0, 0.60)
            factor_asfixia_local = max(0.82, 1.0 - (cs_local_ratio * 0.25))
            lam_corners_v *= 0.80 * factor_asfixia_local
            lam_tiros_v *= 0.80 * factor_asfixia_local

        if factores_aprendidos:
            lam_corners_l *= factores_aprendidos.get("mult_corners_local", 1.0)
            lam_corners_v *= factores_aprendidos.get("mult_corners_visita", 1.0)
            lam_tiros_l *= factores_aprendidos.get("mult_tiros_local", 1.0)
            lam_tiros_v *= factores_aprendidos.get("mult_tiros_visita", 1.0)

        lam_corners_total = lam_corners_l + lam_corners_v
        lam_tiros_total = lam_tiros_l + lam_tiros_v

        lam_tarjetas_l = stats_local["prom_amarillas"] + stats_local["prom_rojas"]
        lam_tarjetas_v = stats_visita["prom_amarillas"] + stats_visita["prom_rojas"]
        lam_tarjetas_total = lam_tarjetas_l + lam_tarjetas_v

        esperados.update({
            "corners_local": round(lam_corners_l, 2),
            "corners_visita": round(lam_corners_v, 2),
            "corners_total": round(lam_corners_total, 2),
            "tiros_local": round(lam_tiros_l, 2),
            "tiros_visita": round(lam_tiros_v, 2),
            "tiros_total": round(lam_tiros_total, 2),
            "tarjetas_total": round(lam_tarjetas_total, 2),
        })

        probabilidades.update({
            "Más de 7.5 Córners": prob_mas_de(7.5, lam_corners_total),
            "Más de 8.5 Córners": prob_mas_de(8.5, lam_corners_total),
            "Más de 9.5 Córners": prob_mas_de(9.5, lam_corners_total),
            "Más de 2.5 Tarjetas": prob_mas_de(2.5, lam_tarjetas_total),
            "Más de 3.5 Tarjetas": prob_mas_de(3.5, lam_tarjetas_total),
        })

        # Líneas dinámicas ajustadas al nivel real de cada equipo en Betano
        probabilidades.update(_generar_lineas_dinamicas_equipo(stats_local["equipo"], lam_corners_l, lam_tiros_l, True))
        probabilidades.update(_generar_lineas_dinamicas_equipo(stats_visita["equipo"], lam_corners_v, lam_tiros_v, False))

    probabilidades = _aplicar_filtro_coherencia(probabilidades, lam_goles_l, lam_goles_v, stats_local, stats_visita)

    claves_deportivas = _redactar_claves_deportivas(
        stats_local, stats_visita, esperados, h2h, contexto, es_torneo_intl, factores_aprendidos
    )

    return {
        "esperados": esperados,
        "probabilidades": probabilidades,
        "claves_deportivas": claves_deportivas,
    }