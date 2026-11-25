import os
import json
import time
import unicodedata
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.config import (
    CACHE_DIR,
    REGION_LIGA,
    ESPN_TODAS_LIGAS,
    ESPN_TORNEOS_INTERNACIONALES,
    ALIAS_EQUIPOS,
)

_session = requests.Session()
_session.headers.update({"User-Agent": "PitchLab/1.0"})
_TZ_LIMA = timezone(timedelta(hours=-5))


def _ahora_lima() -> datetime:
    return datetime.now(_TZ_LIMA)


def quitar_tildes(texto: str) -> str:
    texto_norm = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in texto_norm if unicodedata.category(c) != "Mn").lower().strip()


def normalizar_nombre(nombre: str) -> str:
    n = quitar_tildes(nombre).replace("'", "").replace(".", " ").replace("/", " ")
    n = " ".join(n.split())
    return ALIAS_EQUIPOS.get(n, n)


def obtener_info_espn_equipo(nombre_liga: str, equipo_csv: str):
    slug = ESPN_TODAS_LIGAS.get(nombre_liga)
    if not slug:
        return None, ""

    if slug == "arg.1":
        slugs_a_probar = ["arg.1", "arg.copa_lpf"]
    elif slug in ("uefa.champions", "uefa.europa"):
        slugs_a_probar = ["uefa.champions", "uefa.europa", "all"]
    else:
        slugs_a_probar = [slug]

    eq_norm = normalizar_nombre(equipo_csv)
    palabras_clave = [
        p for p in eq_norm.split()
        if len(p) >= 4 and p not in ("club", "deportivo", "atletico", "plata", "boys", "linz", "city")
    ]

    for s_actual in slugs_a_probar:
        ruta_teams = os.path.join(CACHE_DIR, f"espn_teams_v2_{s_actual}.json")
        if os.path.exists(ruta_teams) and (time.time() - os.path.getmtime(ruta_teams)) < 86400:
            with open(ruta_teams, "r", encoding="utf-8") as f:
                teams_data = json.load(f)
        else:
            try:
                url_teams = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{s_actual}/teams?limit=500"
                resp = _session.get(url_teams, timeout=8).json()
                teams_data = resp.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
                if teams_data:
                    with open(ruta_teams, "w", encoding="utf-8") as f:
                        json.dump(teams_data, f)
            except Exception:
                continue

        for item in teams_data:
            t = item.get("team", {})
            nombres_posibles = [
                normalizar_nombre(t.get("displayName", "")),
                normalizar_nombre(t.get("shortDisplayName", "")),
                normalizar_nombre(t.get("name", "")),
            ]
            if any(eq_norm == nom or (nom and (eq_norm in nom or nom in eq_norm)) for nom in nombres_posibles):
                logos = t.get("logos", [])
                return str(t.get("id")), (logos[0].get("href", "") if logos else "")
            if palabras_clave and any(any(p in nom for p in palabras_clave) for nom in nombres_posibles if nom):
                logos = t.get("logos", [])
                return str(t.get("id")), (logos[0].get("href", "") if logos else "")

    return None, ""


def obtener_partidos_equipo_espn_global(espn_team_id: str, nombre_equipo: str) -> list:
    """Obtiene partidos completados priorizando torneos continentales (PesoBase 0.70) sobre liga local (0.30)."""
    if not espn_team_id:
        return []

    fecha_lima = _ahora_lima().strftime("%Y-%m-%d")
    ruta_cache = os.path.join(CACHE_DIR, f"espn_global_sched_v3_{espn_team_id}_{fecha_lima}.json")
    if os.path.exists(ruta_cache) and (time.time() - os.path.getmtime(ruta_cache)) < 3600:
        with open(ruta_cache, "r", encoding="utf-8") as f:
            return json.load(f)

    vistos = set()
    filas = []

    config_slugs = [
        ("uefa.champions", 0.70),
        ("uefa.europa", 0.70),
        ("conmebol.libertadores", 0.70),
        ("all", 0.30),
    ]

    for slug_busq, peso in config_slugs:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug_busq}/teams/{espn_team_id}/schedule"
            resp = _session.get(url, timeout=8).json()
            eventos = resp.get("events", [])
            for ev in eventos:
                ev_id = str(ev.get("id", ""))
                if not ev_id or ev_id in vistos:
                    continue
                comp = ev.get("competitions", [{}])[0]
                if not comp.get("status", {}).get("type", {}).get("completed", False):
                    continue
                comps = comp.get("competitors", [])
                if len(comps) < 2:
                    continue

                vistos.add(ev_id)
                h_o = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
                a_o = next((c for c in comps if c.get("homeAway") == "away"), comps[1])

                h_id = str(h_o.get("team", {}).get("id", ""))
                a_id = str(a_o.get("team", {}).get("id", ""))
                h_nom = nombre_equipo if h_id == str(espn_team_id) else h_o.get("team", {}).get("displayName", "")
                a_nom = nombre_equipo if a_id == str(espn_team_id) else a_o.get("team", {}).get("displayName", "")

                filas.append({
                    "id": ev_id,
                    "Date": ev.get("date", "")[:10],
                    "HomeTeam": h_nom,
                    "AwayTeam": a_nom,
                    "FTHG": int(float(h_o.get("score", 0) or 0)),
                    "FTAG": int(float(a_o.get("score", 0) or 0)),
                    "TempKey": "copa" if peso == 0.70 else "liga",
                    "PesoBase": peso,
                })
        except Exception:
            continue

    filas.sort(key=lambda x: x["Date"])
    if filas:
        with open(ruta_cache, "w", encoding="utf-8") as f:
            json.dump(filas, f, ensure_ascii=False)
    return filas


def _descargar_boxscore_individual(slug_usado: str, event_id: str):
    ruta_ev = os.path.join(CACHE_DIR, f"espn_match_{event_id}.json")
    if os.path.exists(ruta_ev):
        with open(ruta_ev, "r", encoding="utf-8") as f:
            return json.load(f)
    try:
        url_sum = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug_usado}/summary?event={event_id}"
        summary = _session.get(url_sum, timeout=8).json()
        with open(ruta_ev, "w", encoding="utf-8") as f:
            json.dump(summary, f)
        return summary
    except Exception:
        return None


def _extraer_eventos_schedule(slug_s: str, espn_team_id: str) -> list:
    fecha_lima = _ahora_lima().strftime("%Y-%m-%d")
    ruta_sched = os.path.join(CACHE_DIR, f"espn_sched_v3_{slug_s}_{espn_team_id}_{fecha_lima}.json")
    if os.path.exists(ruta_sched) and (time.time() - os.path.getmtime(ruta_sched)) < 3600:
        with open(ruta_sched, "r", encoding="utf-8") as f:
            ev_temp = json.load(f)
    else:
        try:
            url_sched = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug_s}/teams/{espn_team_id}/schedule"
            resp_sched = _session.get(url_sched, timeout=10).json()
            ev_temp = resp_sched.get("events", [])
            if ev_temp:
                with open(ruta_sched, "w", encoding="utf-8") as f:
                    json.dump(ev_temp, f)
        except Exception:
            ev_temp = []

    return [
        str(ev["id"]) for ev in ev_temp
        if ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False)
    ]


def obtener_stats_espn_america(slug_liga: str, espn_team_id: str, ultimos_n: int = 8):
    """Extrae tiros al arco, córners y tarjetas ponderando 70% copa continental / 30% liga local."""
    if not espn_team_id:
        return None

    es_torneo_continental = slug_liga in ("uefa.champions", "uefa.europa", "conmebol.libertadores")
    ids_ponderados = []

    if es_torneo_continental:
        ids_copa = _extraer_eventos_schedule(slug_liga, espn_team_id)[-ultimos_n:]
        if not ids_copa and slug_liga == "uefa.champions":
            ids_copa = _extraer_eventos_schedule("uefa.europa", espn_team_id)[-ultimos_n:]
        ids_liga = [eid for eid in _extraer_eventos_schedule("all", espn_team_id)[-ultimos_n:] if eid not in ids_copa]

        peso_cada_copa = (0.70 / len(ids_copa)) if ids_copa else 0.0
        peso_cada_liga = (0.30 / len(ids_liga)) if ids_liga else 0.0
        if not ids_copa:
            peso_cada_liga = (1.0 / len(ids_liga)) if ids_liga else 0.0
        elif not ids_liga:
            peso_cada_copa = 1.0 / len(ids_copa)

        for eid in ids_copa:
            ids_ponderados.append((eid, slug_liga, peso_cada_copa))
        for eid in ids_liga:
            ids_ponderados.append((eid, "all", peso_cada_liga))
    else:
        slugs_a_probar = ["arg.1", "arg.copa_lpf"] if slug_liga == "arg.1" else [slug_liga]
        for s_actual in slugs_a_probar:
            ids_loc = _extraer_eventos_schedule(s_actual, espn_team_id)[-ultimos_n:]
            if ids_loc:
                w = 1.0 / len(ids_loc)
                ids_ponderados = [(eid, s_actual, w) for eid in ids_loc]
                break

    if not ids_ponderados:
        return None

    summaries_map = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {
            executor.submit(_descargar_boxscore_individual, s_u, eid): (eid, peso)
            for eid, s_u, peso in ids_ponderados
        }
        for fut in as_completed(futuros):
            eid, peso = futuros[fut]
            res = fut.result()
            if res:
                summaries_map[eid] = (res, peso)

    totales = {"tf": 0.0, "tc": 0.0, "cf": 0.0, "cc": 0.0, "am": 0.0, "ro": 0.0, "peso_valido": 0.0}
    for eid, (summary, peso) in summaries_map.items():
        equipos_box = summary.get("boxscore", {}).get("teams", [])
        if len(equipos_box) < 2:
            continue

        totales["peso_valido"] += peso
        for eq_box in equipos_box:
            es_nuestro = (str(eq_box.get("team", {}).get("id")) == str(espn_team_id))
            stats_dict = {}
            for s in eq_box.get("statistics", []):
                try:
                    stats_dict[s["name"]] = float(s.get("displayValue", 0) or 0)
                except ValueError:
                    pass

            tiros_arco = stats_dict.get("shotsOnTarget", stats_dict.get("onGoalShot", 0))
            corners = stats_dict.get("wonCorners", stats_dict.get("cornerKicks", 0))
            amarillas = stats_dict.get("yellowCards", 0)
            rojas = stats_dict.get("redCards", 0)

            if es_nuestro:
                totales["tf"] += tiros_arco * peso
                totales["cf"] += corners * peso
                totales["am"] += amarillas * peso
                totales["ro"] += rojas * peso
            else:
                totales["tc"] += tiros_arco * peso
                totales["cc"] += corners * peso

    pv = totales["peso_valido"]
    if pv <= 0:
        return None

    return {
        "prom_tiros_arco_favor": round(totales["tf"] / pv, 2),
        "prom_tiros_arco_contra": round(totales["tc"] / pv, 2),
        "prom_corners_favor": round(totales["cf"] / pv, 2),
        "prom_corners_contra": round(totales["cc"] / pv, 2),
        "prom_amarillas": round(totales["am"] / pv, 2),
        "prom_rojas": round(totales["ro"] / pv, 2),
    }


def descargar_partidos_torneo_espn(slug: str) -> list:
    """Obtiene los equipos y resultados de Champions/Europa/Libertadores desde ESPN Teams + Schedules."""
    ruta_cache = os.path.join(CACHE_DIR, f"espn_torneo_v3_{slug}.json")
    if os.path.exists(ruta_cache) and (time.time() - os.path.getmtime(ruta_cache)) < 7200:
        with open(ruta_cache, "r", encoding="utf-8") as f:
            return json.load(f)

    try:
        url_teams = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams?limit=100"
        resp = _session.get(url_teams, timeout=8).json()
        teams_list = resp.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
    except Exception:
        teams_list = []

    team_ids = [(str(t["team"]["id"]), t["team"]["displayName"]) for t in teams_list if "team" in t]

    def _fetch_team_games(tid_nom):
        tid, _ = tid_nom
        juegos = []
        try:
            url_s = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/{tid}/schedule"
            ev_list = _session.get(url_s, timeout=8).json().get("events", [])
            for ev in ev_list:
                comp = ev.get("competitions", [{}])[0]
                if not comp.get("status", {}).get("type", {}).get("completed", False):
                    continue
                comps = comp.get("competitors", [])
                if len(comps) < 2:
                    continue
                h_o = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
                a_o = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
                juegos.append({
                    "id": str(ev.get("id", "")),
                    "Date": ev.get("date", "")[:10],
                    "HomeTeam": h_o.get("team", {}).get("displayName", ""),
                    "AwayTeam": a_o.get("team", {}).get("displayName", ""),
                    "FTHG": int(float(h_o.get("score", 0) or 0)),
                    "FTAG": int(float(a_o.get("score", 0) or 0)),
                })
        except Exception:
            pass
        return juegos

    vistos = set()
    partidos = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for res_team in ex.map(_fetch_team_games, team_ids):
            for p in res_team:
                if p["id"] not in vistos:
                    vistos.add(p["id"])
                    partidos.append(p)

    partidos.sort(key=lambda x: x["Date"])
    if partidos:
        with open(ruta_cache, "w", encoding="utf-8") as f:
            json.dump(partidos, f, ensure_ascii=False)

    return partidos


def _emparejar_equipo_csv(nombre_espn: str, lista_equipos_csv: list):
    espn_norm = normalizar_nombre(nombre_espn)
    for eq in lista_equipos_csv:
        if normalizar_nombre(eq) == espn_norm:
            return eq
    for eq in lista_equipos_csv:
        eq_csv_norm = normalizar_nombre(eq)
        if eq_csv_norm in espn_norm or espn_norm in eq_csv_norm:
            return eq
    palabras_espn = [
        p for p in espn_norm.split()
        if len(p) >= 5 and p not in ("club", "deportivo", "atletico", "united", "sporting")
    ]
    for eq in lista_equipos_csv:
        eq_csv_norm = normalizar_nombre(eq)
        if any(p in eq_csv_norm for p in palabras_espn):
            return eq
    return None


def _consultar_liga_cartelera(nombre_liga: str, slug: str, fecha_str: str, cargar_liga_fn):
    encontrados = []
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={fecha_str}"
        resp = _session.get(url, timeout=5).json()
        eventos = resp.get("events", [])
        if not eventos:
            return []

        league_info = resp.get("leagues", [{}])[0]
        league_logos = league_info.get("logos", [])
        league_logo = league_logos[0].get("href", "") if league_logos else ""
        season_slug = league_info.get("season", {}).get("displayName", "2026/27")

        es_torneo_intl = nombre_liga in ESPN_TORNEOS_INTERNACIONALES
        equipos_csv = []
        if not es_torneo_intl:
            df_liga = cargar_liga_fn(nombre_liga)
            df_actual = df_liga[df_liga["TempKey"].isin(["2627", 2026])]
            if df_actual.empty:
                df_actual = df_liga
            equipos_csv = sorted(df_actual["HomeTeam"].unique().tolist())

        for ev in eventos:
            comp = ev.get("competitions", [{}])[0]
            competidores = comp.get("competitors", [])
            if len(competidores) < 2:
                continue

            home_obj = next((c for c in competidores if c.get("homeAway") == "home"), competidores[0])
            away_obj = next((c for c in competidores if c.get("homeAway") == "away"), competidores[1])

            home_espn = home_obj.get("team", {}).get("displayName", "")
            away_espn = away_obj.get("team", {}).get("displayName", "")
            home_logo = home_obj.get("team", {}).get("logo", "")
            away_logo = away_obj.get("team", {}).get("logo", "")

            if es_torneo_intl:
                home_csv, away_csv = home_espn, away_espn
            else:
                home_csv = _emparejar_equipo_csv(home_espn, equipos_csv)
                away_csv = _emparejar_equipo_csv(away_espn, equipos_csv)

            if home_csv and away_csv:
                estado_obj = ev.get("status", {}).get("type", {})
                venue = comp.get("venue", {}).get("fullName", "")
                encontrados.append({
                    "liga": nombre_liga,
                    "region": REGION_LIGA.get(nombre_liga, "Europa"),
                    "liga_logo": league_logo,
                    "temporada": season_slug,
                    "local_csv": home_csv,
                    "visita_csv": away_csv,
                    "local_display": home_espn,
                    "visita_display": away_espn,
                    "local_logo": home_logo,
                    "visita_logo": away_logo,
                    "estadio": venue,
                    "hora_utc": ev.get("date", ""),
                    "estado": estado_obj.get("shortDetail", "Programado"),
                    "completado": estado_obj.get("completed", False),
                })
    except Exception:
        pass
    return encontrados


def obtener_cartelera_hoy(cargar_liga_fn, fecha_str: str = None):
    hoy_lima = _ahora_lima().strftime("%Y%m%d")
    if not fecha_str:
        fecha_str = hoy_lima

    # TTL dinámico: 60 segundos para la jornada de hoy (minutos en vivo / FT), 30 min para otros días
    ttl_segundos = 60 if fecha_str == hoy_lima else 1800

    ruta_cache = os.path.join(CACHE_DIR, f"cartelera_v5_{fecha_str}.json")
    if os.path.exists(ruta_cache) and (time.time() - os.path.getmtime(ruta_cache)) < ttl_segundos:
        with open(ruta_cache, "r", encoding="utf-8") as f:
            return json.load(f)

    partidos_detectados = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futuros = [
            executor.submit(_consultar_liga_cartelera, nombre_liga, slug, fecha_str, cargar_liga_fn)
            for nombre_liga, slug in ESPN_TODAS_LIGAS.items()
        ]
        for fut in as_completed(futuros):
            partidos_detectados.extend(fut.result())

    orden_ligas = list(ESPN_TODAS_LIGAS.keys())
    partidos_detectados.sort(
        key=lambda x: (orden_ligas.index(x["liga"]) if x["liga"] in orden_ligas else 99, x["hora_utc"])
    )

    with open(ruta_cache, "w", encoding="utf-8") as f:
        json.dump(partidos_detectados, f, ensure_ascii=False, indent=2)

    return partidos_detectados