import os
import time
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from src.config import CACHE_DIR

MODELS_DIR = os.path.join(CACHE_DIR, "ml_models")
os.makedirs(MODELS_DIR, exist_ok=True)


def _construir_dataset_liga(df: pd.DataFrame) -> pd.DataFrame:
    """Genera características rolling (últimos 5 partidos previos sin fuga de datos) para entrenar ML."""
    if df is None or len(df) < 60:
        return pd.DataFrame()

    df_ord = df.sort_values("Date").reset_index(drop=True)
    tiene_corners = "HC" in df_ord.columns and "AC" in df_ord.columns

    historial_gf = {}
    historial_gc = {}
    historial_cf = {}
    historial_cc = {}
    filas = []

    for _, row in df_ord.iterrows():
        h = row["HomeTeam"]
        a = row["AwayTeam"]
        fthg = float(row.get("FTHG", 0) or 0)
        ftag = float(row.get("FTAG", 0) or 0)

        gf_h = historial_gf.get(h, [])
        gc_h = historial_gc.get(h, [])
        gf_a = historial_gf.get(a, [])
        gc_a = historial_gc.get(a, [])

        # Necesitamos al menos 4 partidos previos por equipo para evitar ruido inicial
        if len(gf_h) >= 4 and len(gf_a) >= 4:
            prom_gf_h = np.mean(gf_h[-6:])
            prom_gc_h = np.mean(gc_h[-6:])
            prom_gf_a = np.mean(gf_a[-6:])
            prom_gc_a = np.mean(gc_a[-6:])

            cf_h = historial_cf.get(h, [])
            cc_h = historial_cc.get(h, [])
            cf_a = historial_cf.get(a, [])
            cc_a = historial_cc.get(a, [])

            prom_cf_h = np.mean(cf_h[-6:]) if cf_h else 4.8
            prom_cc_h = np.mean(cc_h[-6:]) if cc_h else 4.5
            prom_cf_a = np.mean(cf_a[-6:]) if cf_a else 4.2
            prom_cc_a = np.mean(cc_a[-6:]) if cc_a else 4.8

            fila = {
                "prom_gf_h": prom_gf_h,
                "prom_gc_h": prom_gc_h,
                "prom_gf_a": prom_gf_a,
                "prom_gc_a": prom_gc_a,
                "diff_ataque": (prom_gf_h - prom_gc_a) - (prom_gf_a - prom_gc_h),
                "suma_goles_esp": (prom_gf_h + prom_gc_a + prom_gf_a + prom_gc_h) / 2.0,
                "prom_cf_h": prom_cf_h,
                "prom_cc_h": prom_cc_h,
                "prom_cf_a": prom_cf_a,
                "prom_cc_a": prom_cc_a,
                "y_1": int(fthg > ftag),
                "y_1x": int(fthg >= ftag),
                "y_x2": int(ftag >= fthg),
                "y_over15": int((fthg + ftag) > 1.5),
                "y_over25": int((fthg + ftag) > 2.5),
                "y_under35": int((fthg + ftag) < 3.5),
                "y_btts": int(fthg > 0 and ftag > 0),
            }

            if tiene_corners and pd.notna(row.get("HC")) and pd.notna(row.get("AC")):
                tot_c = float(row["HC"]) + float(row["AC"])
                fila["y_corners75"] = int(tot_c > 7.5)
                fila["y_corners85"] = int(tot_c > 8.5)
            else:
                fila["y_corners75"] = np.nan
                fila["y_corners85"] = np.nan

            filas.append(fila)

        # Actualizar historial post-partido
        historial_gf.setdefault(h, []).append(fthg)
        historial_gc.setdefault(h, []).append(ftag)
        historial_gf.setdefault(a, []).append(ftag)
        historial_gc.setdefault(a, []).append(fthg)

        if tiene_corners and pd.notna(row.get("HC")) and pd.notna(row.get("AC")):
            historial_cf.setdefault(h, []).append(float(row["HC"]))
            historial_cc.setdefault(h, []).append(float(row["AC"]))
            historial_cf.setdefault(a, []).append(float(row["AC"]))
            historial_cc.setdefault(a, []).append(float(row["HC"]))

    return pd.DataFrame(filas)


def _obtener_modelos_liga(liga: str, df: pd.DataFrame) -> dict:
    """Entrena o carga desde disco los modelos GradientBoosting de la liga (caché de 24h)."""
    slug_safe = "".join(c if c.isalnum() else "_" for c in liga.lower())
    ruta_modelo = os.path.join(MODELS_DIR, f"gb_model_{slug_safe}.joblib")

    if os.path.exists(ruta_modelo) and (time.time() - os.path.getmtime(ruta_modelo)) < 86400:
        try:
            return joblib.load(ruta_modelo)
        except Exception:
            pass

    ds = _construir_dataset_liga(df)
    if ds.empty or len(ds) < 50:
        return {}

    features = [
        "prom_gf_h", "prom_gc_h", "prom_gf_a", "prom_gc_a",
        "diff_ataque", "suma_goles_esp",
        "prom_cf_h", "prom_cc_h", "prom_cf_a", "prom_cc_a",
    ]
    targets = {
        "1 (Gana Local)": "y_1",
        "1X (Local o Empate)": "y_1x",
        "X2 (Visita o Empate)": "y_x2",
        "Más de 1.5 Goles": "y_over15",
        "Más de 2.5 Goles": "y_over25",
        "Menos de 3.5 Goles": "y_under35",
        "BTTS (Ambos Anotan - Sí)": "y_btts",
        "Más de 7.5 Córners": "y_corners75",
        "Más de 8.5 Córners": "y_corners85",
    }

    modelos = {}
    for nombre_mercado, col_y in targets.items():
        sub = ds.dropna(subset=[col_y])
        if len(sub) < 50 or sub[col_y].nunique() < 2:
            continue

        X = sub[features].values
        y = sub[col_y].astype(int).values

        clf = GradientBoostingClassifier(
            n_estimators=90,
            learning_rate=0.06,
            max_depth=3,
            subsample=0.85,
            random_state=42,
        )
        clf.fit(X, y)
        modelos[nombre_mercado] = clf

    if modelos:
        try:
            joblib.dump(modelos, ruta_modelo)
        except Exception:
            pass

    return modelos


def predecir_probabilidades_ml(liga: str, df: pd.DataFrame, stats_l: dict, stats_v: dict) -> dict:
    """Devuelve diccionario {mercado: probabilidad_pct} generado por Machine Learning."""
    modelos = _obtener_modelos_liga(liga, df)
    if not modelos:
        return {}

    prom_gf_h = float(stats_l.get("prom_goles_favor", 1.3))
    prom_gc_h = float(stats_l.get("prom_goles_contra", 1.1))
    prom_gf_a = float(stats_v.get("prom_goles_favor", 1.1))
    prom_gc_a = float(stats_v.get("prom_goles_contra", 1.3))

    prom_cf_h = float(stats_l.get("prom_corners_favor", 4.8))
    prom_cc_h = float(stats_l.get("prom_corners_contra", 4.5))
    prom_cf_a = float(stats_v.get("prom_corners_favor", 4.2))
    prom_cc_a = float(stats_v.get("prom_corners_contra", 4.8))

    x_vec = np.array([[
        prom_gf_h,
        prom_gc_h,
        prom_gf_a,
        prom_gc_a,
        (prom_gf_h - prom_gc_a) - (prom_gf_a - prom_gc_h),
        (prom_gf_h + prom_gc_a + prom_gf_a + prom_gc_h) / 2.0,
        prom_cf_h,
        prom_cc_h,
        prom_cf_a,
        prom_cc_a,
    ]])

    salida = {}
    for mercado, clf in modelos.items():
        prob_1 = float(clf.predict_proba(x_vec)[0][1])
        salida[mercado] = round(prob_1 * 100, 1)

    return salida