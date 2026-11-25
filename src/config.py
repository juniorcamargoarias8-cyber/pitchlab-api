import os

CACHE_DIR = os.path.join("data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

PESOS_TEMPORADA_EUROPA = {
    "2627": 0.70,
    "2526": 0.20,
    "2425": 0.06,
    "2324": 0.04,
}

PESOS_TEMPORADA_AMERICA = {
    2026: 0.70,
    2025: 0.20,
    2024: 0.06,
    2023: 0.04,
}

CODIGOS_EUROPA = {
    "Premier League (Inglaterra)": "E0",
    "LaLiga (España)": "SP1",
    "Serie A (Italia)": "I1",
    "Bundesliga (Alemania)": "D1",
    "Ligue 1 (Francia)": "F1",
    "Eredivisie (Países Bajos)": "N1",
    "Primeira Liga (Portugal)": "P1",
    "Jupiler Pro League (Bélgica)": "B1",
    "Premiership (Escocia)": "SC0",
    "Süper Lig (Turquía)": "T1",
    "Super League (Grecia)": "G1",
    "Championship (Inglaterra 2ª)": "E1",
    "LaLiga Hypermotion (España 2ª)": "SP2",
    "Serie B (Italia 2ª)": "I2",
    "2. Bundesliga (Alemania 2ª)": "D2",
    "Ligue 2 (Francia 2ª)": "F2",
}

URLS_AMERICA = {
    "Liga Profesional (Argentina)": "https://www.football-data.co.uk/new/ARG.csv",
    "Brasileirão Serie A (Brasil)": "https://www.football-data.co.uk/new/BRA.csv",
    "Liga MX (México)": "https://www.football-data.co.uk/new/MEX.csv",
    "MLS (Estados Unidos)": "https://www.football-data.co.uk/new/USA.csv",
}

ESPN_TORNEOS_INTERNACIONALES = {
    "UEFA Champions League": "uefa.champions",
    "UEFA Europa League": "uefa.europa",
    "CONMEBOL Libertadores": "conmebol.libertadores",
}

LIGAS_DISPONIBLES = {
    **ESPN_TORNEOS_INTERNACIONALES,
    **{k: f"https://www.football-data.co.uk/mmz4281/2627/{v}.csv" for k, v in CODIGOS_EUROPA.items()},
    **URLS_AMERICA,
}

REGION_LIGA = {
    **{k: "America" for k in URLS_AMERICA.keys()},
    "CONMEBOL Libertadores": "America",
}

ESPN_TODAS_LIGAS = {
    **ESPN_TORNEOS_INTERNACIONALES,
    "Premier League (Inglaterra)": "eng.1",
    "LaLiga (España)": "esp.1",
    "Serie A (Italia)": "ita.1",
    "Bundesliga (Alemania)": "ger.1",
    "Ligue 1 (Francia)": "fra.1",
    "Eredivisie (Países Bajos)": "ned.1",
    "Primeira Liga (Portugal)": "por.1",
    "Jupiler Pro League (Bélgica)": "bel.1",
    "Premiership (Escocia)": "sco.1",
    "Süper Lig (Turquía)": "tur.1",
    "Super League (Grecia)": "gre.1",
    "Championship (Inglaterra 2ª)": "eng.2",
    "LaLiga Hypermotion (España 2ª)": "esp.2",
    "Serie B (Italia 2ª)": "ita.2",
    "2. Bundesliga (Alemania 2ª)": "ger.2",
    "Ligue 2 (Francia 2ª)": "fra.2",
    "Liga Profesional (Argentina)": "arg.1",
    "Brasileirão Serie A (Brasil)": "bra.1",
    "Liga MX (México)": "mex.1",
    "MLS (Estados Unidos)": "usa.1",
}

ESPN_SLUGS_AMERICA = {
    "Liga Profesional (Argentina)": "arg.1",
    "Brasileirão Serie A (Brasil)": "bra.1",
    "Liga MX (México)": "mex.1",
    "MLS (Estados Unidos)": "usa.1",
}

ALIAS_EQUIPOS = {
    "atletico-mg": "atletico mineiro",
    "atletico-pr": "athletico paranaense",
    "athletico-pr": "athletico paranaense",
    "atletico-go": "atletico goianiense",
    "bragantino": "red bull bragantino",
    "vasco": "vasco da gama",
    "sport recife": "sport",
    "atl tucuman": "atletico tucuman",
    "ind rivadavia": "independiente rivadavia",
    "newells old boys": "newells",
    "newell s old boys": "newells",
    "velez sarsfield": "velez",
    "est la plata": "estudiantes",
    "gimn la plata": "gimnasia",
    "gimnasia l p": "gimnasia",
    "gimnasia la plata": "gimnasia",
    "unam pumas": "pumas",
    "club america": "america",
    "uanl tigres": "tigres",
    "guadalajara chivas": "guadalajara",
    "man city": "manchester city",
    "man united": "manchester united",
    "nottm forest": "nottingham forest",
    "psg": "paris saint-germain",
    "paris saint germain": "paris saint-germain",
    "inter": "internazionale",
    "inter milan": "internazionale",
    "bayern": "bayern munich",
    "atletico": "atletico madrid",
    "sporting cp": "sporting lisbon",
}