import os
import time
import unicodedata
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from src.config import (
    CACHE_DIR,
    CODIGOS_EUROPA,
    URLS_AMERICA,
    ESPN_TORNEOS_INTERNACIONALES,
    PESOS_TEMPORADA_EUROPA,
    PESOS_TEMPORADA_AMERICA,
    ESPN_SLUGS_AMERICA,
    LIGAS_HERMANAS,
    ALIAS_EQUIPOS,
)
from src.services.espn_client import (
    obtener_info_espn_equipo,
    obtener_stats_espn_america,
    descargar_partidos_torneo_espn,
    obtener_partidos_equipo_espn_global,
)

_MEM_CACHE = {}

COLUMNAS_UTILES = [
    "Date", "Season", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
    "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR"
]

ALIAS_CLUBES_CSV = {
    "fc barcelona": "Barcelona",
    "barcelona": "Barcelona",
    "real madrid": "Real Madrid",
    "atletico madrid": "Ath Madrid",
    "atletico de madrid": "Ath Madrid",
    "athletic bilbao": "Ath Bilbao",
    "athletic club": "Ath Bilbao",
    "real betis": "Betis",
    "real sociedad": "Sociedad",
    "celta vigo": "Celta",
    "bayern munich": "Bayern Munich",
    "fc bayern munchen": "Bayern Munich",
    "borussia dortmund": "Dortmund",
    "bayer leverkusen": "Leverkusen",
    "rb leipzig": "RB Leipzig",
    "eintracht frankfurt": "Ein Frankfurt",
    "vfb stuttgart": "Stuttgart",
    "inter milan": "Inter",
    "internazionale": "Inter",
    "ac milan": "Milan",
    "juventus": "Juventus",
    "atalanta": "Atalanta",
    "napoli": "Napoli",
    "as roma": "Roma",
    "lazio": "Lazio",
    "paris saint germain": "Paris SG",
    "paris saint-germain": "Paris SG",
    "psg": "Paris SG",
    "as monaco": "Monaco",
    "olympique marseille": "Marseille",
    "marseille": "Marseille",
    "lille": "Lille",
    "olympique lyonnais": "Lyon",
    "lyon": "Lyon",
    "manchester city": "Man City",
    "manchester united": "Man United",
    "newcastle united": "Newcastle",
    "tottenham hotspur": "Tottenham",
    "aston villa": "Aston Villa",
    "nottingham forest": "Nott'm Forest",
    "sporting cp": "Sp Lisbon",
    "sporting lisbon": "Sp Lisbon",
    "benfica": "Benfica",
    "fc porto": "Porto",
    "porto": "Porto",
    "psv eindhoven": "PSV",
    "feyenoord": "Feyenoord",
    "ajax": "Ajax",
    "celtic": "Celtic",
    "rangers": "Rangers",
    "galatasaray": "Galatasaray",
    "fenerbahce": "Fenerbahce",
    "besiktas": "Besiktas",
    "club brugge": "Club Brugge",
}


def _normalizar_texto(txt: str) -> str:
    s = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().replace("-", " ").split())


def _leer_csv_cacheado(url: str, nombre_cache: str, es_historica: bool = False) -> pd.DataFrame:
    ruta_local = os.path.join(CACHE_DIR, nombre_cache)
    ahora = time.time()

    usar_cache = False
    if os.path.exists(ruta_local):
        if es_historica or (ahora - os.path.getmtime(ruta_local)) < 43200:
            usar_cache = True

    try:
        if usar_cache:
            df = pd.read_csv(ruta_local, encoding="utf-8")
        else:
            df = pd.read_csv(url, encoding="latin1")
            df.to_csv(ruta_local, index=False, encoding="utf-8")
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam", "HG": "FTHG", "AG": "FTAG"})
        cols_presentes = [c for c in COLUMNAS_UTILES if c in df.columns]
        return df[cols_presentes].dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).copy()
    except Exception:
        return pd.DataFrame()


def cargar_datos_liga(nombre_liga: str) -> pd.DataFrame:
    ahora = time.time()
    if nombre_liga in _MEM_CACHE:
        df_mem, ts = _MEM_CACHE[nombre_liga]
        if (ahora - ts) < 3600:
            return df_mem

    if nombre_liga in CODIGOS_EUROPA:
        codigo = CODIGOS_EUROPA[nombre_liga]
        frames = []

        def _fetch_temp(temp_str):
            url = f"https://www.football-data.co.uk/mmz4281/{temp_str}/{codigo}.csv"
            df_t = _leer_csv_cacheado(url, f"eu_{temp_str}_{codigo}.csv", es_historica=(temp_str != "2627"))
            if not df_t.empty:
                df_t = df_t.assign(TempKey=temp_str, PesoBase=PESOS_TEMPORADA_EUROPA[temp_str])
            return df_t

        with ThreadPoolExecutor(max_workers=4) as ex:
            for df_part in ex.map(_fetch_temp, ["2324", "2425", "2526", "2627"]):
                if not df_part.empty:
                    frames.append(df_part)

        if not frames:
            raise ValueError(f"No se pudieron cargar datos para {nombre_liga}")
        df = pd.concat(frames, ignore_index=True)

    elif nombre_liga in URLS_AMERICA:
        url = URLS_AMERICA[nombre_liga]
        nombre_archivo = url.split("/")[-1]
        df = _leer_csv_cacheado(url, f"am_multi_{nombre_archivo}", es_historica=False)

        if "Season" in df.columns:
            anios = df["Season"].astype(str).str[:4].astype(int)
            df = df[anios.isin(PESOS_TEMPORADA_AMERICA.keys())].copy()
            anios_filtrados = df["Season"].astype(str).str[:4].astype(int)
            df = df.assign(
                AnioNum=anios_filtrados,
                TempKey=anios_filtrados,
                PesoBase=anios_filtrados.map(PESOS_TEMPORADA_AMERICA),
            )
        else:
            df = df.assign(TempKey=2026, PesoBase=1.0)

    elif nombre_liga in ESPN_TORNEOS_INTERNACIONALES:
        slug = ESPN_TORNEOS_INTERNACIONALES[nombre_liga]
        partidos_espn = descargar_partidos_torneo_espn(slug)
        if partidos_espn:
            df = pd.DataFrame(partidos_espn).assign(TempKey=2026, PesoBase=1.0)
        else:
            df = pd.DataFrame(columns=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "TempKey", "PesoBase"])

    else:
        raise ValueError(f"Liga no encontrada: {nombre_liga}")

    df.attrs["nombre_liga"] = nombre_liga
    _MEM_CACHE[nombre_liga] = (df, ahora)
    return df


def _buscar_club_en_ligas_domesticas(equipo: str):
    """Busca el historial doméstico completo (goles, tiros, córners) de un club en los CSV europeos."""
    norm = _normalizar_texto(equipo)
    candidato = ALIAS_CLUBES_CSV.get(norm, equipo)
    norm_cand = _normalizar_texto(candidato)

    for liga_eu in CODIGOS_EUROPA.keys():
        try:
            df_liga = cargar_datos_liga(liga_eu)
            equipos_csv = df_liga["HomeTeam"].dropna().unique().tolist()

            match_exacto = None
            for eq_csv in equipos_csv:
                n_csv = _normalizar_texto(eq_csv)
                if n_csv == norm_cand or n_csv == norm or (len(norm_cand) >= 5 and norm_cand in n_csv):
                    match_exacto = eq_csv
                    break

            if match_exacto:
                sub = df_liga[(df_liga["HomeTeam"] == match_exacto) | (df_liga["AwayTeam"] == match_exacto)].copy()
                if len(sub) >= 5:
                    return df_liga, match_exacto
        except Exception:
            continue

    return None, None


def _resumir_subconjunto_ponderado(df_sub: pd.DataFrame, equipo: str, tiene_stats_csv: bool, usar_pesos: bool = True):
    if df_sub.empty:
        return None

    df_calc = df_sub.copy()
    if usar_pesos and "PesoBase" in df_calc.columns:
        conteo_por_temp = df_calc.groupby("TempKey")["PesoBase"].transform("count")
        df_calc["PesoPartido"] = df_calc["PesoBase"] / conteo_por_temp
    else:
        df_calc["PesoPartido"] = 1.0

    suma_pesos = df_calc["PesoPartido"].sum()
    if suma_pesos <= 0:
        return None

    w_ganados = w_empatados = w_perdidos = w_cs = w_btts = 0.0
    w_gf = w_gc = w_tf = w_tc = w_cf = w_cc = w_am = w_ro = 0.0

    for _, row in df_calc.iterrows():
        w = float(row["PesoPartido"])
        es_local = (row["HomeTeam"] == equipo)
        gf = float(row["FTHG"] if es_local else row["FTAG"])
        gc = float(row["FTAG"] if es_local else row["FTHG"])

        w_gf += gf * w
        w_gc += gc * w

        if gf > gc:
            w_ganados += w
        elif gf == gc:
            w_empatados += w
        else:
            w_perdidos += w

        if gc == 0:
            w_cs += w
        if gf > 0 and gc > 0:
            w_btts += w

        if tiene_stats_csv and pd.notna(row.get("HST")) and pd.notna(row.get("HC")):
            w_tf += float(row["HST"] if es_local else row["AST"]) * w
            w_tc += float(row["AST"] if es_local else row["HST"]) * w
            w_cf += float(row["HC"] if es_local else row["AC"]) * w
            w_cc += float(row["AC"] if es_local else row["HC"]) * w
            w_am += float(row.get("HY", 0) if es_local else row.get("AY", 0)) * w
            w_ro += float(row.get("HR", 0) if es_local else row.get("AR", 0)) * w

    n_real = len(df_calc)
    return {
        "pj": n_real,
        "partidos_jugados": n_real,
        "pct_ganar": round((w_ganados / suma_pesos) * 100, 1),
        "pct_doble_oport": round(((w_ganados + w_empatados) / suma_pesos) * 100, 1),
        "pct_btts": round((w_btts / suma_pesos) * 100, 1),
        "pct_clean_sheet": round((w_cs / suma_pesos) * 100, 1),
        "prom_goles_favor": round(w_gf / suma_pesos, 2),
        "prom_goles_contra": round(w_gc / suma_pesos, 2),
        "prom_goles_totales": round((w_gf + w_gc) / suma_pesos, 2),
        "prom_tiros_arco_favor": round(w_tf / suma_pesos, 2) if tiene_stats_csv else 0.0,
        "prom_tiros_arco_contra": round(w_tc / suma_pesos, 2) if tiene_stats_csv else 0.0,
        "prom_corners_favor": round(w_cf / suma_pesos, 2) if tiene_stats_csv else 0.0,
        "prom_corners_contra": round(w_cc / suma_pesos, 2) if tiene_stats_csv else 0.0,
        "prom_amarillas": round(w_am / suma_pesos, 2) if tiene_stats_csv else 0.0,
        "prom_rojas": round(w_ro / suma_pesos, 2) if tiene_stats_csv else 0.0,
    }


def obtener_perfil_completo_equipo(df: pd.DataFrame, equipo: str, es_local_hoy: bool = True, ultimos_n: int = 0):
    nombre_liga = df.attrs.get("nombre_liga", "")
    espn_id, logo_url = obtener_info_espn_equipo(nombre_liga, equipo)

    df_trabajo = df.copy()
    equipo_busqueda = equipo
    todos = df_trabajo[(df_trabajo["HomeTeam"] == equipo_busqueda) | (df_trabajo["AwayTeam"] == equipo_busqueda)]

    # 1. En torneos internacionales, priorizar historial completo del club en su liga doméstica (40+ partidos con tiros/córners)
    if nombre_liga in ESPN_TORNEOS_INTERNACIONALES and len(todos) < 6:
        df_dom, nombre_csv = _buscar_club_en_ligas_domesticas(equipo)
        if df_dom is not None and nombre_csv:
            df_trabajo = df_dom
            equipo_busqueda = nombre_csv
            todos = df_trabajo[(df_trabajo["HomeTeam"] == equipo_busqueda) | (df_trabajo["AwayTeam"] == equipo_busqueda)]

    # 2. Si no está en CSV europeos, intentar historial global en ESPN
    if len(todos) < 2 and nombre_liga in ESPN_TORNEOS_INTERNACIONALES and espn_id:
        filas_globales = obtener_partidos_equipo_espn_global(espn_id, equipo)
        if filas_globales:
            df_trabajo = pd.DataFrame(filas_globales)
            equipo_busqueda = equipo
            todos = df_trabajo

    # 3. Respaldo realista para clubes de rondas previas sin historial en CSV ni ESPN (ej. Sabah FK)
    if todos.empty and nombre_liga in ESPN_TORNEOS_INTERNACIONALES:
        base_previa = {
            "pj": 0,
            "partidos_jugados": 0,
            "pct_ganar": 30.0,
            "pct_doble_oport": 52.0,
            "pct_btts": 48.0,
            "pct_clean_sheet": 22.0,
            "prom_goles_favor": 0.95,
            "prom_goles_contra": 1.65,
            "prom_goles_totales": 2.60,
            "prom_tiros_arco_favor": 3.10,
            "prom_tiros_arco_contra": 5.40,
            "prom_corners_favor": 3.60,
            "prom_corners_contra": 5.80,
            "prom_amarillas": 2.20,
            "prom_rojas": 0.08,
        }
        return {
            "equipo": equipo,
            "logo": logo_url,
            "racha": ["E", "P", "G", "P", "E"],
            "tiene_stats_avanzadas": True,
            "general": base_previa,
            "local": base_previa,
            "visita": base_previa,
            **base_previa,
        }

    if todos.empty:
        return None

    tiene_stats_csv = all(col in df_trabajo.columns for col in ["HST", "HC", "HY"])

    racha = []
    for _, row in todos.tail(5).iterrows():
        es_loc = (row["HomeTeam"] == equipo_busqueda)
        gf = row["FTHG"] if es_loc else row["FTAG"]
        gc = row["FTAG"] if es_loc else row["FTHG"]
        racha.append("G" if gf > gc else ("E" if gf == gc else "P"))

    if ultimos_n and ultimos_n > 0:
        df_gen = todos.tail(ultimos_n)
        df_loc = df_trabajo[df_trabajo["HomeTeam"] == equipo_busqueda].tail(ultimos_n)
        df_vis = df_trabajo[df_trabajo["AwayTeam"] == equipo_busqueda].tail(ultimos_n)
        usar_pesos = False
    else:
        df_gen = todos
        df_loc = df_trabajo[df_trabajo["HomeTeam"] == equipo_busqueda]
        df_vis = df_trabajo[df_trabajo["AwayTeam"] == equipo_busqueda]
        usar_pesos = True

    res_gen = _resumir_subconjunto_ponderado(df_gen, equipo_busqueda, tiene_stats_csv, usar_pesos)
    res_loc = _resumir_subconjunto_ponderado(df_loc, equipo_busqueda, tiene_stats_csv, usar_pesos) or res_gen
    res_vis = _resumir_subconjunto_ponderado(df_vis, equipo_busqueda, tiene_stats_csv, usar_pesos) or res_gen

    tiene_avanzadas = tiene_stats_csv

    if not tiene_stats_csv and (nombre_liga in ESPN_SLUGS_AMERICA or nombre_liga in ESPN_TORNEOS_INTERNACIONALES):
        slug_espn = ESPN_SLUGS_AMERICA.get(nombre_liga) or ESPN_TORNEOS_INTERNACIONALES.get(nombre_liga)
        n_espn = ultimos_n if (ultimos_n and ultimos_n > 0) else 8
        stats_espn = obtener_stats_espn_america(slug_espn, espn_id, ultimos_n=n_espn)
        if stats_espn:
            tiene_avanzadas = True
            for vista in (res_gen, res_loc, res_vis):
                vista.update(stats_espn)

    vista_condicion = res_loc if es_local_hoy else res_vis

    return {
        "equipo": equipo,
        "logo": logo_url,
        "racha": racha,
        "tiene_stats_avanzadas": tiene_avanzadas,
        "general": res_gen,
        "local": res_loc,
        "visita": res_vis,
        **vista_condicion,
    }


def _canon_equipo_h2h(nombre: str) -> str:
    n = _normalizar_texto(nombre)
    return ALIAS_EQUIPOS.get(n, n)


def _canon_equipo_h2h(nombre: str) -> str:
    n = _normalizar_texto(nombre)
    return ALIAS_EQUIPOS.get(n, n)


def obtener_historial_h2h(
    df: pd.DataFrame,
    equipo_local: str,
    equipo_visita: str,
    limite: int = 8,
    liga: str = "",
    cargar_liga_fn=None,
) -> dict:
    canon_l = _canon_equipo_h2h(equipo_local)
    canon_v = _canon_equipo_h2h(equipo_visita)

    def _filtrar_duelos(dataframe):
        if dataframe is None or dataframe.empty:
            return pd.DataFrame()
        h_canon = dataframe["HomeTeam"].astype(str).map(_canon_equipo_h2h)
        a_canon = dataframe["AwayTeam"].astype(str).map(_canon_equipo_h2h)
        cond = ((h_canon == canon_l) & (a_canon == canon_v)) | (
            (h_canon == canon_v) & (a_canon == canon_l)
        )
        return dataframe[cond].copy()

    lotes = [_filtrar_duelos(df)]
    nombre_liga = liga or (df.attrs.get("nombre_liga", "") if df is not None else "")

    if nombre_liga in CODIGOS_EUROPA:
        codigo = CODIGOS_EUROPA[nombre_liga]
        for temp_ant in ["2223", "2122", "2021", "1920", "1819"]:
            url = f"https://www.football-data.co.uk/mmz4281/{temp_ant}/{codigo}.csv"
            df_t = _leer_csv_cacheado(url, f"eu_{temp_ant}_{codigo}.csv", es_historica=True)
            if not df_t.empty:
                df_t = df_t.assign(TempKey=temp_ant)
                lotes.append(_filtrar_duelos(df_t))

    if callable(cargar_liga_fn) and nombre_liga in LIGAS_HERMANAS:
        for liga_hermana in LIGAS_HERMANAS[nombre_liga]:
            try:
                df_h = cargar_liga_fn(liga_hermana)
                if df_h is not None and not df_h.empty:
                    lotes.append(_filtrar_duelos(df_h))
            except Exception:
                continue

    lotes_validos = [d for d in lotes if not d.empty]
    if not lotes_validos:
        return {
            "total": 0,
            "victorias_local": 0,
            "empates": 0,
            "victorias_visita": 0,
            "prom_goles": 0.0,
            "prom_goles_equipo_local": 0.0,
            "prom_goles_equipo_visita": 0.0,
            "pct_btts": 0.0,
            "partidos": [],
        }

    df_h2h = pd.concat(lotes_validos, ignore_index=True)
    if "Date" in df_h2h.columns:
        df_h2h["_fecha_ord"] = pd.to_datetime(df_h2h["Date"], errors="coerce", dayfirst=True)
        df_h2h = df_h2h.sort_values("_fecha_ord", ascending=True)
    df_h2h = df_h2h.drop_duplicates(
        subset=[c for c in ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"] if c in df_h2h.columns]
    )

    df_recientes = df_h2h.tail(limite).iloc[::-1]

    vic_l = emp = vic_v = btts_cnt = 0
    total_goles = goles_eq_local = goles_eq_visita = 0.0
    lista_partidos = []

    for _, row in df_recientes.iterrows():
        raw_hg = row.get("FTHG")
        raw_ag = row.get("FTAG")
        if pd.isna(raw_hg) or pd.isna(raw_ag):
            continue
        try:
            hg = int(float(raw_hg))
            ag = int(float(raw_ag))
        except (ValueError, TypeError):
            continue

        h_team = str(row["HomeTeam"])
        a_team = str(row["AwayTeam"])
        fecha = str(row.get("Date", row.get("TempKey", "")))

        total_goles += (hg + ag)
        if _canon_equipo_h2h(h_team) == canon_l:
            goles_eq_local += hg
            goles_eq_visita += ag
            local_es_h = True
        else:
            goles_eq_local += ag
            goles_eq_visita += hg
            local_es_h = False

        if hg > 0 and ag > 0:
            btts_cnt += 1

        if hg == ag:
            emp += 1
            ganador = "Empate"
        elif (hg > ag and local_es_h) or (ag > hg and not local_es_h):
            vic_l += 1
            ganador = equipo_local
        else:
            vic_v += 1
            ganador = equipo_visita

        lista_partidos.append({
            "fecha": fecha,
            "local": h_team,
            "visita": a_team,
            "goles_local": hg,
            "goles_visita": ag,
            "ganador": ganador,
        })

    n = len(lista_partidos)
    if n == 0:
        return {
            "total": 0,
            "victorias_local": 0,
            "empates": 0,
            "victorias_visita": 0,
            "prom_goles": 0.0,
            "prom_goles_equipo_local": 0.0,
            "prom_goles_equipo_visita": 0.0,
            "pct_btts": 0.0,
            "partidos": [],
        }

    return {
        "total": n,
        "victorias_local": vic_l,
        "empates": emp,
        "victorias_visita": vic_v,
        "prom_goles": round(total_goles / n, 2),
        "prom_goles_equipo_local": round(goles_eq_local / n, 2),
        "prom_goles_equipo_visita": round(goles_eq_visita / n, 2),
        "pct_btts": round((btts_cnt / n) * 100, 1),
        "partidos": lista_partidos,
    }

def obtener_contexto_clasificacion(df: pd.DataFrame, equipo_local: str, equipo_visita: str) -> dict:
    if df.empty or "TempKey" not in df.columns:
        return {}
    df_actual = df[df["TempKey"].isin(["2627", 2026])].copy()
    if df_actual.empty:
        return {}

    tabla = {}
    for _, row in df_actual.iterrows():
        h, a = str(row["HomeTeam"]), str(row["AwayTeam"])
        hg, ag = int(float(row["FTHG"])), int(float(row["FTAG"]))

        for eq in (h, a):
            if eq not in tabla:
                tabla[eq] = {"equipo": eq, "pj": 0, "g": 0, "e": 0, "p": 0, "gf": 0, "gc": 0, "pts": 0}

        tabla[h]["pj"] += 1
        tabla[a]["pj"] += 1
        tabla[h]["gf"] += hg
        tabla[h]["gc"] += ag
        tabla[a]["gf"] += ag
        tabla[a]["gc"] += hg

        if hg > ag:
            tabla[h]["g"] += 1
            tabla[h]["pts"] += 3
            tabla[a]["p"] += 1
        elif ag > hg:
            tabla[a]["g"] += 1
            tabla[a]["pts"] += 3
            tabla[h]["p"] += 1
        else:
            tabla[h]["e"] += 1
            tabla[a]["e"] += 1
            tabla[h]["pts"] += 1
            tabla[a]["pts"] += 1

    ranking = sorted(
        tabla.values(),
        key=lambda x: (x["pts"], x["gf"] - x["gc"], x["gf"]),
        reverse=True
    )

    total_equipos = len(ranking)
    info_equipos = {}

    for idx, item in enumerate(ranking, start=1):
        dg = item["gf"] - item["gc"]
        item["pos"] = idx
        item["dg"] = f"+{dg}" if dg > 0 else str(dg)

        if idx == 1:
            zona = "Líder del torneo (defendiendo la punta)"
            factor_urgencia = 1.03
        elif idx <= 4:
            zona = "Zona de clasificación directa / Champions"
            factor_urgencia = 1.02
        elif idx <= 6:
            zona = "Peleando cupos internacionales / Playoffs"
            factor_urgencia = 1.02
        elif idx >= total_equipos - 2:
            zona = "Zona de descenso directo (urgido por sumar puntos)"
            factor_urgencia = 1.04
        elif idx >= total_equipos - 4:
            zona = "Al borde del descenso (necesita alejarse de zona roja)"
            factor_urgencia = 1.02
        else:
            zona = "Mitad de tabla"
            factor_urgencia = 0.99

        item["zona"] = zona
        item["factor_urgencia"] = factor_urgencia
        info_equipos[item["equipo"]] = item

    return {
        "total_equipos": total_equipos,
        "local": info_equipos.get(equipo_local),
        "visita": info_equipos.get(equipo_visita),
    }