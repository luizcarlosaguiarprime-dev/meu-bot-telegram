import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

import requests
import telebot
from flask import Flask

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não configurada.")

if not APIFOOTBALL_KEY:
    raise RuntimeError("APIFOOTBALL_KEY não configurada.")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

TZ = ZoneInfo("America/Sao_Paulo")

API_FOOTBALL = "https://v3.football.api-sports.io"
ODDS_API = "https://api.the-odds-api.com/v4"

LIVE_INTERVAL = int(os.getenv("LIVE_INTERVAL", "60"))
HISTORY_MATCHES = int(os.getenv("HISTORY_MATCHES", "10"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "10"))

LIVE_INTERVAL = max(30, LIVE_INTERVAL)
HISTORY_MATCHES = min(15, max(5, HISTORY_MATCHES))
ALERT_COOLDOWN = max(5, ALERT_COOLDOWN)

# ============================================================
# LIGAS
# ============================================================

LEAGUES = {
    71: "🇧🇷 Brasileirão Série A",
    39: "🏴 Premier League",
    140: "🇪🇸 La Liga",
    78: "🇩🇪 Bundesliga",
    135: "🇮🇹 Serie A",
    61: "🇫🇷 Ligue 1",
    94: "🇵🇹 Primeira Liga",
    72: "🇧🇷 Copa do Brasil",
    45: "🏴 FA Cup",
    143: "🇪🇸 Copa del Rey",
    81: "🇩🇪 DFB Pokal",
    137: "🇮🇹 Coppa Italia",
    66: "🇫🇷 Coupe de France",
    96: "🇵🇹 Taça de Portugal",
    2: "🏆 Champions League",
    3: "🏆 Europa League",
    848: "🏆 Conference League",
    13: "🏆 Libertadores",
    11: "🏆 Sudamericana",
}

# Competições equivalentes na The Odds API
ODDS_SPORTS = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    "soccer_brazil_campeonato": "Brasileirão",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "soccer_conmebol_libertadores": "Libertadores",
    "soccer_conmebol_sudamericana": "Sudamericana",
}

LIVE_STATUSES = {
    "1H",
    "HT",
    "2H",
    "ET",
    "BT",
    "P",
    "LIVE",
    "INT",
}

# ============================================================
# MEMÓRIA
# ============================================================

CHAT_ID = None

cache = {}
cache_lock = threading.Lock()

last_alert = {}
last_state = {}

running = True


# ============================================================
# CACHE
# ============================================================

def cache_get(key):
    with cache_lock:
        item = cache.get(key)

    if not item:
        return None

    expires, value = item

    if time.time() > expires:
        with cache_lock:
            cache.pop(key, None)
        return None

    return value


def cache_set(key, value, ttl=45):
    with cache_lock:
        cache[key] = (time.time() + ttl, value)


# ============================================================
# API-FOOTBALL
# ============================================================

def api_football(endpoint, params=None, ttl=45):
    params = params or {}

    key = "football:" + endpoint + ":" + "&".join(
        f"{k}={params[k]}" for k in sorted(params)
    )

    cached = cache_get(key)

    if cached is not None:
        return cached

    headers = {
        "x-apisports-key": APIFOOTBALL_KEY
    }

    try:
        r = requests.get(
            API_FOOTBALL + endpoint,
            headers=headers,
            params=params,
            timeout=20
        )

        if r.status_code != 200:
            print("API Football:", r.status_code, r.text[:300])
            return None

        data = r.json()

        cache_set(key, data, ttl)

        return data

    except Exception as e:
        print("Erro API Football:", e)
        return None


# ============================================================
# THE ODDS API
# ============================================================

def odds_api(endpoint, params=None, ttl=60):
    if not ODDS_API_KEY:
        return None

    params = params or {}
    params = dict(params)
    params["apiKey"] = ODDS_API_KEY

    key = "odds:" + endpoint + ":" + "&".join(
        f"{k}={params[k]}" for k in sorted(params)
    )

    cached = cache_get(key)

    if cached is not None:
        return cached

    try:
        r = requests.get(
            ODDS_API + endpoint,
            params=params,
            timeout=20
        )

        if r.status_code != 200:
            print("Odds API:", r.status_code, r.text[:300])
            return None

        data = r.json()

        cache_set(key, data, ttl)

        return data

    except Exception as e:
        print("Erro Odds API:", e)
        return None


# ============================================================
# UTILIDADES
# ============================================================

def agora():
    return datetime.now(TZ)


def formatar_hora(iso):
    try:
        dt = datetime.fromisoformat(
            iso.replace("Z", "+00:00")
        ).astimezone(TZ)

        return dt.strftime("%d/%m %H:%M")

    except Exception:
        return "horário desconhecido"


def numero(valor):
    try:
        if valor is None:
            return None

        if isinstance(valor, str):
            valor = valor.replace("%", "").strip()

        return float(valor)

    except Exception:
        return None


def nome_time(fixture, lado):
    return fixture["teams"][lado]["name"]


def fixture_id(fixture):
    return fixture["fixture"]["id"]


def status_fixture(fixture):
    return fixture["fixture"]["status"]["short"]


def eh_live(fixture):
    return status_fixture(fixture) in LIVE_STATUSES


def nome_liga(fixture):
    return LEAGUES.get(
        fixture["league"]["id"],
        fixture["league"].get("name", "Liga")
    )


# ============================================================
# TEMPORADA
# ============================================================

def temporada_da_liga(league_id):
    """
    Para futebol, normalmente o campo season corresponde
    ao ano em que a temporada começou.
    """

    return agora().year


# ============================================================
# JOGOS AO VIVO
# ============================================================

def buscar_live():
    encontrados = {}

    # --------------------------------------------------------
    # PRIMEIRA TENTATIVA:
    # todos os jogos ao vivo
    # --------------------------------------------------------

    data = api_football(
        "/fixtures",
        {"live": "all"},
        ttl=30
    )

    if data and data.get("response"):
        for f in data["response"]:
            lid = f["league"]["id"]

            if lid in LEAGUES and eh_live(f):
                encontrados[fixture_id(f)] = f

    # --------------------------------------------------------
    # SEGUNDA TENTATIVA:
    # busca individual por liga
    #
    # Isso evita perder uma partida caso live=all
    # não retorne corretamente determinada competição.
    # --------------------------------------------------------

    hoje = agora().strftime("%Y-%m-%d")

    for lid in LEAGUES:

        data = api_football(
            "/fixtures",
            {
                "league": lid,
                "season": temporada_da_liga(lid),
                "date": hoje
            },
            ttl=30
        )

        if not data or not data.get("response"):
            continue

        for f in data["response"]:

            if eh_live(f):
                encontrados[fixture_id(f)] = f

    return list(encontrados.values())


# ============================================================
# PRÓXIMOS JOGOS
# ============================================================

def buscar_proximos(max_por_liga=5):
    jogos = []

    hoje = agora().date()

    for lid in LEAGUES:

        data = api_football(
            "/fixtures",
            {
                "league": lid,
                "season": temporada_da_liga(lid),
                "from": hoje.strftime("%Y-%m-%d"),
                "to": (hoje + timedelta(days=3)).strftime("%Y-%m-%d")
            },
            ttl=120
        )

        if not data or not data.get("response"):
            continue

        contador = 0

        for f in data["response"]:

            status = status_fixture(f)

            if status in {"NS", "TBD"}:

                jogos.append(f)

                contador += 1

                if contador >= max_por_liga:
                    break

    jogos.sort(
        key=lambda x: x["fixture"]["date"]
    )

    return jogos


# ============================================================
# ESTATÍSTICAS DE UMA PARTIDA
# ============================================================

def estatisticas_fixture(fid):
    data = api_football(
        "/fixtures/statistics",
        {"fixture": fid},
        ttl=60
    )

    if not data or not data.get("response"):
        return {}

    resultado = {}

    for equipe in data["response"]:

        nome = equipe["team"]["name"]
        stats = {}

        for item in equipe.get("statistics", []):

            tipo = item.get("type")
            valor = item.get("value")

            stats[tipo] = numero(valor)

        resultado[nome] = stats

    return resultado


# ============================================================
# EVENTOS
# ============================================================

def eventos_fixture(fid):
    data = api_football(
        "/fixtures/events",
        {"fixture": fid},
        ttl=30
    )

    if not data:
        return []

    return data.get("response", [])


# ============================================================
# HISTÓRICO DO TIME
# ============================================================

def historico_time(team_id):
    data = api_football(
        "/fixtures",
        {
            "team": team_id,
            "last": HISTORY_MATCHES
        },
        ttl=600
    )

    if not data:
        return []

    return data.get("response", [])


# ============================================================
# ESTATÍSTICAS HISTÓRICAS
# ============================================================

def estatisticas_historicas(team_id):

    jogos = historico_time(team_id)

    resultado = []

    # Limitamos para não consumir a API inteira
    # de uma vez.
    for jogo in jogos[:8]:

        fid = fixture_id(jogo)

        stats = estatisticas_fixture(fid)

        if stats:
            resultado.append({
                "fixture": jogo,
                "stats": stats
            })

    return resultado


# ============================================================
# EXTRAI ESTATÍSTICAS
# ============================================================

def encontrar_stats(stats, team_name):

    if not stats:
        return {}

    if team_name in stats:
        return stats[team_name]

    # Busca aproximada
    for nome, valores in stats.items():

        if nome.lower() == team_name.lower():
            return valores

    return {}


def valor_stat(stats, nomes):

    for nome in nomes:

        if nome in stats:
            valor = numero(stats[nome])

            if valor is not None:
                return valor

    return None


# ============================================================
# MÉDIAS HISTÓRICAS
# ============================================================

def calcular_media_historica(team_id, team_name):

    jogos = estatisticas_historicas(team_id)

    if not jogos:
        return {
            "jogos": 0,
            "gols_marcados": 0,
            "gols_sofridos": 0,
            "escanteios": 0,
            "finalizacoes": 0,
            "no_alvo": 0,
            "posse": 0,
            "ataques_perigosos": 0,
            "chutes_fora": 0,
        }

    valores = defaultdict(list)

    gols_marcados = []
    gols_sofridos = []

    for item in jogos:

        f = item["fixture"]
        stats = encontrar_stats(
            item["stats"],
            team_name
        )

        home_id = f["teams"]["home"]["id"]

        team_id_f = team_id

        gh = f["goals"]["home"]
        ga = f["goals"]["away"]

        if f["teams"]["home"]["id"] == team_id_f:
            gols_marcados.append(gh or 0)
            gols_sofridos.append(ga or 0)
        else:
            gols_marcados.append(ga or 0)
            gols_sofridos.append(gh or 0)

        mapeamento = {
            "escanteios": [
                "Corner Kicks"
            ],
            "finalizacoes": [
                "Total Shots"
            ],
            "no_alvo": [
                "Shots on Goal"
            ],
            "posse": [
                "Ball Possession"
            ],
            "ataques_perigosos": [
                "Dangerous Attacks"
            ],
            "chutes_fora": [
                "Shots off Goal"
            ],
        }

        for chave, nomes in mapeamento.items():

            v = valor_stat(stats, nomes)

            if v is not None:
                valores[chave].append(v)

    def media(nome):
        lista = valores.get(nome, [])

        if not lista:
            return 0

        return sum(lista) / len(lista)

    return {
        "jogos": len(jogos),
        "gols_marcados": (
            sum(gols_marcados) / len(gols_marcados)
            if gols_marcados else 0
        ),
        "gols_sofridos": (
            sum(gols_sofridos) / len(gols_sofridos)
            if gols_sofridos else 0
        ),
        "escanteios": media("escanteios"),
        "finalizacoes": media("finalizacoes"),
        "no_alvo": media("no_alvo"),
        "posse": media("posse"),
        "ataques_perigosos": media("ataques_perigosos"),
        "chutes_fora": media("chutes_fora"),
    }


# ============================================================
# FORMA RECENTE
# ============================================================

def forma_recente(team_id):

    jogos = historico_time(team_id)

    pontos = 0
    vitorias = 0
    empates = 0
    derrotas = 0

    ultimos = []

    for jogo in jogos:

        home = jogo["teams"]["home"]["id"] == team_id

        gh = jogo["goals"]["home"]
        ga = jogo["goals"]["away"]

        if gh is None or ga is None:
            continue

        if home:

            if gh > ga:
                resultado = "V"
                pontos += 3
                vitorias += 1

            elif gh == ga:
                resultado = "E"
                pontos += 1
                empates += 1

            else:
                resultado = "D"
                derrotas += 1

        else:

            if ga > gh:
                resultado = "V"
                pontos += 3
                vitorias += 1

            elif ga == gh:
                resultado = "E"
                pontos += 1
                empates += 1

            else:
                resultado = "D"
                derrotas += 1

        ultimos.append(resultado)

    return {
        "jogos": len(ultimos),
        "pontos": pontos,
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "forma": "".join(ultimos[:5])
    }


# ============================================================
# ÍNDICE PRÉ-JOGO
# ============================================================

def indice_pre_jogo(media, forma):

    score = 50.0

    score += media["gols_marcados"] * 5
    score -= media["gols_sofridos"] * 3

    score += media["finalizacoes"] * 0.8
    score += media["no_alvo"] * 1.5

    score += media["escanteios"] * 1.0

    score += max(0, media["posse"] - 50) * 0.20

    score += forma["vitorias"] * 1.5
    score -= forma["derrotas"] * 1.2

    return max(0, min(100, score))


# ============================================================
# ÍNDICE LIVE
# ============================================================

def indice_live(stats, team_name):

    s = encontrar_stats(stats, team_name)

    finalizacoes = valor_stat(
        s,
        ["Total Shots"]
    ) or 0

    no_alvo = valor_stat(
        s,
        ["Shots on Goal"]
    ) or 0

    escanteios = valor_stat(
        s,
        ["Corner Kicks"]
    ) or 0

    posse = valor_stat(
        s,
        ["Ball Possession"]
    ) or 0

    ataques = valor_stat(
        s,
        ["Dangerous Attacks"]
    ) or 0

    ataques_norm = min(ataques / 30 * 100, 100)
    chutes_norm = min(finalizacoes / 10 * 100, 100)
    alvo_norm = min(no_alvo / 5 * 100, 100)
    corners_norm = min(escanteios / 6 * 100, 100)

    posse_norm = min(
        max((posse - 40) * 1.7, 0),
        100
    )

    score = (
        ataques_norm * 0.35 +
        chutes_norm * 0.25 +
        alvo_norm * 0.20 +
        corners_norm * 0.10 +
        posse_norm * 0.10
    )

    return score


# ============================================================
# ANÁLISE LIVE
# ============================================================

def analisar_live(fixture):

    fid = fixture_id(fixture)

    home = nome_time(fixture, "home")
    away = nome_time(fixture, "away")

    stats = estatisticas_fixture(fid)

    home_score = indice_live(stats, home)
    away_score = indice_live(stats, away)

    home_stats = encontrar_stats(stats, home)
    away_stats = encontrar_stats(stats, away)

    home_corners = valor_stat(
        home_stats,
        ["Corner Kicks"]
    ) or 0

    away_corners = valor_stat(
        away_stats,
        ["Corner Kicks"]
    ) or 0

    home_shots = valor_stat(
        home_stats,
        ["Total Shots"]
    ) or 0

    away_shots = valor_stat(
        away_stats,
        ["Total Shots"]
    ) or 0

    home_target = valor_stat(
        home_stats,
        ["Shots on Goal"]
    ) or 0

    away_target = valor_stat(
        away_stats,
        ["Shots on Goal"]
    ) or 0

    home_possession = valor_stat(
        home_stats,
        ["Ball Possession"]
    )

    away_possession = valor_stat(
        away_stats,
        ["Ball Possession"]
    )

    home_attacks = valor_stat(
        home_stats,
        ["Dangerous Attacks"]
    ) or 0

    away_attacks = valor_stat(
        away_stats,
        ["Dangerous Attacks"]
    ) or 0

    total = home_score + away_score

    if total <= 0:
        dominio = "Sem dados suficientes"
    elif home_score > away_score * 1.15:
        dominio = f"🔥 Pressão do {home}"
    elif away_score > home_score * 1.15:
        dominio = f"🔥 Pressão do {away}"
    else:
        dominio = "⚖️ Jogo equilibrado"

    diferenca_corners = abs(
        home_corners - away_corners
    )

    diferenca_shots = abs(
        home_shots - away_shots
    )

    sinais = []

    if diferenca_corners >= 3:
        sinais.append("ESCANTEIOS")

    if diferenca_shots >= 4:
        sinais.append("FINALIZAÇÕES")

    if abs(home_attacks - away_attacks) >= 10:
        sinais.append("PRESSÃO")

    if max(home_target, away_target) >= 3:
        sinais.append("FINALIZAÇÕES NO ALVO")

    if home_corners + away_corners >= 6:
        sinais.append("MUITOS ESCANTEIOS")

    return {
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "dominio": dominio,
        "sinais": sinais,
        "home_corners": home_corners,
        "away_corners": away_corners,
        "home_shots": home_shots,
        "away_shots": away_shots,
        "home_target": home_target,
        "away_target": away_target,
        "home_possession": home_possession,
        "away_possession": away_possession,
        "home_attacks": home_attacks,
        "away_attacks": away_attacks,
    }


# ============================================================
# ODDS
# ============================================================

def buscar_odds_jogo(home, away):

    if not ODDS_API_KEY:
        return []

    resultados = []

    for sport_key in ODDS_SPORTS:

        data = odds_api(
            f"/sports/{sport_key}/odds",
            {
                "regions": "eu",
                "markets": "h2h,totals,btts",
                "oddsFormat": "decimal"
            },
            ttl=60
        )

        if not data:
            continue

        for jogo in data:

            h = jogo.get("home_team", "")
            a = jogo.get("away_team", "")

            if (
                h.lower() in home.lower()
                or home.lower() in h.lower()
            ) and (
                a.lower() in away.lower()
                or away.lower() in a.lower()
            ):

                resultados.append(jogo)

    return resultados


def formatar_odds(odds):

    if not odds:
        return "📊 Odds: não disponíveis."

    linhas = []

    casas = set()

    for jogo in odds[:1]:

        for bookmaker in jogo.get("bookmakers", []):

            nome = bookmaker.get("title", "Casa")

            casas.add(nome)

            for mercado in bookmaker.get("markets", []):

                if mercado.get("key") != "h2h":
                    continue

                for outcome in mercado.get("outcomes", []):

                    nome_outcome = outcome.get("name")
                    preco = outcome.get("price")

                    linhas.append(
                        f"• {nome}: {nome_outcome} @ {preco}"
                    )

    if not linhas:
        return "📊 Odds disponíveis, mas sem mercado compatível."

    return (
        f"📊 **ODDS — {len(casas)} casas encontradas**\n"
        + "\n".join(linhas[:12])
    )


# ============================================================
# PRÉ-JOGO COMPLETO
# ============================================================

def analisar_pre_jogo(fixture):

    home_id = fixture["teams"]["home"]["id"]
    away_id = fixture["teams"]["away"]["id"]

    home = nome_time(fixture, "home")
    away = nome_time(fixture, "away")

    media_home = calcular_media_historica(
        home_id,
        home
    )

    media_away = calcular_media_historica(
        away_id,
        away
    )

    forma_home = forma_recente(home_id)
    forma_away = forma_recente(away_id)

    indice_home = indice_pre_jogo(
        media_home,
        forma_home
    )

    indice_away = indice_pre_jogo(
        media_away,
        forma_away
    )

    if indice_home > indice_away + 8:
        expectativa = (
            f"📈 Os dados recentes mostram maior "
            f"volume ofensivo do {home}."
        )

    elif indice_away > indice_home + 8:
        expectativa = (
            f"📈 Os dados recentes mostram maior "
            f"volume ofensivo do {away}."
        )

    else:
        expectativa = (
            "⚖️ Os indicadores pré-jogo estão "
            "relativamente equilibrados."
        )

    media_corners = (
        media_home["escanteios"] +
        media_away["escanteios"]
    )

    media_shots = (
        media_home["finalizacoes"] +
        media_away["finalizacoes"]
    )

    tendencias = []

    if media_corners >= 8:
        tendencias.append("🚩 tendência de volume de escanteios")

    if media_shots >= 20:
        tendencias.append("🎯 tendência de muitas finalizações")

    if (
        media_home["gols_marcados"] +
        media_away["gols_marcados"]
    ) >= 2.5:
        tendencias.append("⚽ histórico recente com bom volume de gols")

    if not tendencias:
        tendencias.append(
            "📊 sem uma tendência estatística forte isolada"
        )

    return {
        "home": home,
        "away": away,
        "home_id": home_id,
        "away_id": away_id,
        "media_home": media_home,
        "media_away": media_away,
        "forma_home": forma_home,
        "forma_away": forma_away,
        "indice_home": indice_home,
        "indice_away": indice_away,
        "expectativa": expectativa,
        "tendencias": tendencias,
    }


# ============================================================
# TEXTO PRÉ-JOGO
# ============================================================

def texto_pre_jogo(fixture):

    analise = analisar_pre_jogo(fixture)

    h = analise["home"]
    a = analise["away"]

    mh = analise["media_home"]
    ma = analise["media_away"]

    fh = analise["forma_home"]
    fa = analise["forma_away"]

    texto = f"""
🟢 **ANÁLISE PRÉ-JOGO**

⚽ **{h} x {a}**
🏆 {nome_liga(fixture)}
🕐 {formatar_hora(fixture["fixture"]["date"])}

━━━━━━━━━━━━━━━━━━

📚 **RETROSPECTO RECENTE**

🏠 **{h}**
Últimos jogos: {fh["jogos"]}
Forma: `{fh["forma"]}`
Vitórias: {fh["vitorias"]}
Empates: {fh["empates"]}
Derrotas: {fh["derrotas"]}

⚽ Gols marcados: {mh["gols_marcados"]:.2f}
⚽ Gols sofridos: {mh["gols_sofridos"]:.2f}
🚩 Escanteios: {mh["escanteios"]:.2f}
🎯 Finalizações: {mh["finalizacoes"]:.2f}
🎯 No alvo: {mh["no_alvo"]:.2f}
📊 Posse: {mh["posse"]:.1f}%

━━━━━━━━━━━━━━━━━━

✈️ **{a}**

Últimos jogos: {fa["jogos"]}
Forma: `{fa["forma"]}`
Vitórias: {fa["vitorias"]}
Empates: {fa["empates"]}
Derrotas: {fa["derrotas"]}

⚽ Gols marcados: {ma["gols_marcados"]:.2f}
⚽ Gols sofridos: {ma["gols_sofridos"]:.2f}
🚩 Escanteios: {ma["escanteios"]:.2f}
🎯 Finalizações: {ma["finalizacoes"]:.2f}
🎯 No alvo: {ma["no_alvo"]:.2f}
📊 Posse: {ma["posse"]:.1f}%

━━━━━━━━━━━━━━━━━━

🔎 **O QUE ESPERAR**

{analise["expectativa"]}

"""

    for tendencia in analise["tendencias"]:
        texto += f"\n{tendencia}"

    texto += """

━━━━━━━━━━━━━━━━━━

📌 **INDICADORES**

"""
    texto += (
        f"{h}: {analise['indice_home']:.0f}/100\n"
        f"{a}: {analise['indice_away']:.0f}/100\n"
    )

    texto += """

⚠️ Estes indicadores são estatísticos e
não representam garantia de resultado.

A confirmação deve ser feita durante o jogo,
observando o comportamento LIVE.
"""

    # Odds
    odds = buscar_odds_jogo(h, a)

    texto += "\n\n" + formatar_odds(odds)

    return texto


# ============================================================
# TEXTO LIVE
# ============================================================

def texto_live(fixture):

    analise = analisar_live(fixture)

    h = analise["home"]
    a = analise["away"]

    minuto = fixture["fixture"]["status"].get(
        "elapsed",
        "?"
    )

    gols_h = fixture["goals"]["home"] or 0
    gols_a = fixture["goals"]["away"] or 0

    hp = analise["home_possession"]
    ap = analise["away_possession"]

    hp_txt = f"{hp:.0f}%" if hp is not None else "N/D"
    ap_txt = f"{ap:.0f}%" if ap is not None else "N/D"

    texto = f"""
🔴 **ANÁLISE LIVE**

⚽ **{h} {gols_h} x {gols_a} {a}**
⏱️ {minuto}'

━━━━━━━━━━━━━━━━━━

🔥 **DOMÍNIO / PRESSÃO**

{analise["dominio"]}

{h}: {analise["home_score"]:.0f}/100
{a}: {analise["away_score"]:.0f}/100

━━━━━━━━━━━━━━━━━━

📊 **ESTATÍSTICAS LIVE**

🎯 Finalizações
{h}: {analise["home_shots"]} | {a}: {analise["away_shots"]}

🎯 No alvo
{h}: {analise["home_target"]} | {a}: {analise["away_target"]}

🚩 Escanteios
{h}: {analise["home_corners"]} | {a}: {analise["away_corners"]}

📊 Posse
{h}: {hp_txt} | {a}: {ap_txt}

🔥 Ataques perigosos
{h}: {analise["home_attacks"]} | {a}: {analise["away_attacks"]}

━━━━━━━━━━━━━━━━━━
"""

    if analise["sinais"]:

        texto += (
            "🚨 **INDICADORES ATIVOS**\n"
            + "\n".join(
                f"• {s}" for s in analise["sinais"]
            )
            + "\n\n"
        )

    else:

        texto += (
            "🟡 **SEM SINAL FORTE NO MOMENTO**\n\n"
        )

    texto += (
        "📌 A leitura considera o volume estatístico "
        "da partida, não apenas o placar.\n\n"
        "⚠️ Dados estatísticos não garantem resultado."
    )

    return texto


# ============================================================
# ENVIO SEGURO
# ============================================================

def enviar(texto, chat_id=None):

    destino = chat_id or CHAT_ID

    if not destino:
        return

    try:
        # Telegram possui limite de mensagem
        partes = [
            texto[i:i + 3900]
            for i in range(0, len(texto), 3900)
        ]

        for parte in partes:
            bot.send_message(
                destino,
                parte,
                parse_mode="Markdown"
            )

    except Exception as e:
        print("Erro Telegram:", e)


# ============================================================
# ALERTAS LIVE
# ============================================================

def deve_alertar(fid, analise):

    agora_ts = time.time()

    ultimo = last_alert.get(fid, 0)

    if agora_ts - ultimo < ALERT_COOLDOWN * 60:
        return False

    estado = (
        round(analise["home_score"] / 10) * 10,
        round(analise["away_score"] / 10) * 10,
        tuple(analise["sinais"])
    )

    anterior = last_state.get(fid)

    last_state[fid] = estado

    if anterior is None:
        return False

    if estado != anterior:
        if analise["sinais"]:
            last_alert[fid] = agora_ts
            return True

    return False


# ============================================================
# MONITOR LIVE
# ============================================================

def monitor_live():

    global running

    print("Monitor LIVE iniciado.")

    while running:

        try:

            if not CHAT_ID:
                time.sleep(LIVE_INTERVAL)
                continue

            jogos = buscar_live()

            print(
                f"Live: {len(jogos)} jogo(s) encontrado(s)."
            )

            for jogo in jogos:

                try:

                    analise = analisar_live(jogo)

                    if deve_alertar(
                        fixture_id(jogo),
                        analise
                    ):

                        enviar(
                            texto_live(jogo)
                        )

                except Exception as e:
                    print(
                        "Erro analisando live:",
                        e
                    )

        except Exception as e:
            print(
                "Erro monitor:",
                e
            )

        time.sleep(LIVE_INTERVAL)


# ============================================================
# COMANDOS TELEGRAM
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    texto = """
🤖 **BOT DE ANÁLISE DE FUTEBOL**

Bot conectado com sucesso.

🟢 Pré-jogo
🔴 Análise LIVE
📚 Histórico das equipes
🚩 Escanteios
🎯 Finalizações
📊 Posse
🔥 Pressão
⚽ Eventos
📈 Tendências
💰 Odds disponíveis

**Comandos:**

/jogos — jogos ao vivo
/prejogo — próximos jogos
/analisar TIME — análise de uma partida
/status — status do bot
/ligas — ligas monitoradas
/parar — parar monitoramento
/continuar — continuar monitoramento

O bot é informativo e não realiza apostas.
"""

    enviar(texto, message.chat.id)


@bot.message_handler(commands=["status"])
def status(message):

    enviar(
        "🟢 **BOT ONLINE**\n\n"
        f"Monitoramento: {'ATIVO' if running else 'PAUSADO'}\n"
        f"Intervalo LIVE: {LIVE_INTERVAL}s\n"
        f"Histórico: {HISTORY_MATCHES} jogos\n"
        f"API-Football: {'OK' if APIFOOTBALL_KEY else 'NÃO CONFIGURADA'}\n"
        f"The Odds API: {'OK' if ODDS_API_KEY else 'NÃO CONFIGURADA'}",
        message.chat.id
    )


@bot.message_handler(commands=["jogos"])
def jogos_live(message):

    enviar(
        "🔎 Procurando jogos ao vivo...",
        message.chat.id
    )

    jogos = buscar_live()

    if not jogos:

        enviar(
            "🟡 Nenhum jogo ao vivo das ligas "
            "configuradas foi encontrado agora.",
            message.chat.id
        )

        return

    texto = "🔴 **JOGOS AO VIVO**\n\n"

    for jogo in jogos:

        h = nome_time(jogo, "home")
        a = nome_time(jogo, "away")

        gh = jogo["goals"]["home"] or 0
        ga = jogo["goals"]["away"] or 0

        minuto = jogo["fixture"]["status"].get(
            "elapsed",
            "?"
        )

        texto += (
            f"⚽ **{h} {gh} x {ga} {a}**\n"
            f"⏱️ {minuto}'\n"
            f"🏆 {nome_liga(jogo)}\n\n"
        )

    enviar(texto, message.chat.id)


@bot.message_handler(commands=["prejogo"])
def pre_jogo(message):

    enviar(
        "🔎 Procurando próximos jogos...",
        message.chat.id
    )

    jogos = buscar_proximos()

    if not jogos:

        enviar(
            "🟡 Nenhum próximo jogo encontrado.",
            message.chat.id
        )

        return

    texto = (
        "🟢 **PRÓXIMOS JOGOS**\n\n"
    )

    for i, jogo in enumerate(jogos[:15], 1):

        texto += (
            f"{i}. ⚽ "
            f"**{nome_time(jogo, 'home')} x "
            f"{nome_time(jogo, 'away')}**\n"
            f"🏆 {nome_liga(jogo)}\n"
            f"🕐 {formatar_hora(jogo['fixture']['date'])}\n\n"
        )

    enviar(texto, message.chat.id)


@bot.message_handler(commands=["analisar"])
def analisar_comando(message):

    partes = message.text.split(maxsplit=1)

    if len(partes) < 2:

        enviar(
            "Use assim:\n\n"
            "`/analisar Botafogo`\n\n"
            "ou\n\n"
            "`/analisar Grêmio`",
            message.chat.id
        )

        return

    termo = partes[1].lower()

    enviar(
        "🔎 Procurando a partida...",
        message.chat.id
    )

    jogos_live = buscar_live()

    encontrados = []

    for jogo in jogos_live:

        h = nome_time(jogo, "home").lower()
        a = nome_time(jogo, "away").lower()

        if termo in h or termo in a:
            encontrados.append(jogo)

    if encontrados:

        for jogo in encontrados:
            enviar(
                texto_live(jogo),
                message.chat.id
            )

        return

    proximos = buscar_proximos(
        max_por_liga=10
    )

    for jogo in proximos:

        h = nome_time(jogo, "home").lower()
        a = nome_time(jogo, "away").lower()

        if termo in h or termo in a:

            enviar(
                texto_pre_jogo(jogo),
                message.chat.id
            )

            return

    enviar(
        "🟡 Não encontrei uma partida desse time "
        "nas ligas configuradas.",
        message.chat.id
    )


@bot.message_handler(commands=["ligas"])
def ligas(message):

    texto = "🏆 **LIGAS MONITORADAS**\n\n"

    for lid, nome in LEAGUES.items():

        texto += f"`{lid}` — {nome}\n"

    enviar(texto, message.chat.id)


@bot.message_handler(commands=["parar"])
def parar(message):

    global running

    running = False

    enviar(
        "⏸️ Monitoramento LIVE pausado.\n\n"
        "Use /continuar para ativar novamente.",
        message.chat.id
    )


@bot.message_handler(commands=["continuar"])
def continuar(message):

    global running

    running = True

    enviar(
        "▶️ Monitoramento LIVE ativado.",
        message.chat.id
    )


# ============================================================
# MENSAGEM NORMAL
# ============================================================

@bot.message_handler(func=lambda message: True)
def qualquer_mensagem(message):

    enviar(
        "🤖 Use um dos comandos:\n\n"
        "/jogos\n"
        "/prejogo\n"
        "/analisar TIME\n"
        "/status\n"
        "/ligas\n"
        "/parar\n"
        "/continuar",
        message.chat.id
    )


# ============================================================
# FLASK
# ============================================================

@app.route("/")
def home():

    return {
        "status": "online",
        "bot": "football-live-analyzer"
    }


@app.route("/health")
def health():

    return {
        "status": "healthy",
        "time": agora().isoformat()
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

def iniciar_telegram():

    print("Telegram polling iniciado.")

    while True:

        try:

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True
            )

        except Exception as e:

            print(
                "Erro Telegram:",
                e
            )

            time.sleep(10)


def iniciar_monitor():

    thread = threading.Thread(
        target=monitor_live,
        daemon=True
    )

    thread.start()


if __name__ == "__main__":

    iniciar_monitor()

    telegram_thread = threading.Thread(
        target=iniciar_telegram,
        daemon=True
    )

    telegram_thread.start()

    port = int(
        os.getenv("PORT", "10000")
    )

    print(
        f"Servidor Flask na porta {port}"
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
