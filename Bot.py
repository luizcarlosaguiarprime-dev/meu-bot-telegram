import os
import time
import threading
from datetime import datetime
import requests
import telebot
from flask import Flask

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

API_FOOTBALL_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_SPORTS_KEY")
    or os.getenv("APISPORTS_KEY")
)

API_BASE = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_FOOTBALL_KEY
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não configurado.")

if not API_FOOTBALL_KEY:
    raise RuntimeError("API_FOOTBALL_KEY não configurada.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ============================================================
# LIGAS
# ============================================================

LIGAS = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    71: "Brasileirão Série A",
    13: "Copa Libertadores",
    11: "Copa Sul-Americana",
}

BOT_ATIVO = True

CACHE = {}
CHAMADAS = []

LIMITE_CHAMADAS = 9
JANELA = 60


# ============================================================
# API
# ============================================================

def pode_consultar():

    agora = time.time()

    while CHAMADAS and agora - CHAMADAS[0] > JANELA:
        CHAMADAS.pop(0)

    if len(CHAMADAS) >= LIMITE_CHAMADAS:
        return False

    CHAMADAS.append(agora)

    return True


def api_get(endpoint, params=None, cache_key=None, ttl=300):

    agora = time.time()

    if cache_key in CACHE:

        momento, dados = CACHE[cache_key]

        if agora - momento < ttl:
            return dados, None

    if not pode_consultar():
        return None, "Limite temporário de consultas atingido."

    try:

        resposta = requests.get(
            API_BASE + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=20
        )

        if resposta.status_code != 200:
            return None, f"Erro HTTP {resposta.status_code}: {resposta.text[:300]}"

        dados = resposta.json()

        if dados.get("errors"):
            return None, f"Erro API: {dados['errors']}"

        if cache_key:
            CACHE[cache_key] = (agora, dados)

        return dados, None

    except Exception as e:

        return None, f"Erro de conexão: {e}"


# ============================================================
# AUXILIARES
# ============================================================

def numero(valor):

    try:

        if isinstance(valor, str):
            valor = valor.replace("%", "").strip()

        return float(valor)

    except:

        return 0


def inteiro(valor):

    try:
        return int(float(valor))
    except:
        return 0


def media(lista):

    if not lista:
        return 0

    return sum(lista) / len(lista)


def nome_liga(fixture):

    liga = fixture.get("league", {})

    return LIGAS.get(
        liga.get("id"),
        liga.get("name", "Competição")
    )


def horario(fixture):

    data = fixture.get("fixture", {}).get("date")

    if not data:
        return "Horário não informado"

    try:

        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(
            data.replace("Z", "+00:00")
        )

        dt = dt.astimezone(
            ZoneInfo("America/Sao_Paulo")
        )

        return dt.strftime("%d/%m/%Y %H:%M")

    except:

        return data[:16]


# ============================================================
# JOGOS
# ============================================================

def jogos_do_dia():

    hoje = datetime.now().strftime("%Y-%m-%d")

    dados, erro = api_get(
        "/fixtures",
        {
            "date": hoje,
            "timezone": "America/Sao_Paulo"
        },
        cache_key=f"jogos_{hoje}",
        ttl=300
    )

    if erro:
        return [], erro

    lista = []

    for jogo in dados.get("response", []):

        liga_id = jogo.get("league", {}).get("id")

        if liga_id in LIGAS:
            lista.append(jogo)

    return lista, None


def jogos_live():

    dados, erro = api_get(
        "/fixtures",
        {
            "live": "all",
            "timezone": "America/Sao_Paulo"
        },
        cache_key="live",
        ttl=20
    )

    if erro:
        return [], erro

    lista = []

    for jogo in dados.get("response", []):

        liga_id = jogo.get("league", {}).get("id")

        if liga_id in LIGAS:
            lista.append(jogo)

    return lista, None


# ============================================================
# ÚLTIMOS 10 JOGOS
# ============================================================

def ultimos_10(team_id, league_id, season):

    chave = f"ultimos_{team_id}_{league_id}_{season}"

    dados, erro = api_get(
        "/fixtures",
        {
            "team": team_id,
            "last": 10,
            "league": league_id,
            "season": season,
            "timezone": "America/Sao_Paulo"
        },
        cache_key=chave,
        ttl=3600
    )

    if erro:
        return [], erro

    return dados.get("response", []), None


def resumo(jogos, team_id):

    resultado = {

        "jogos": 0,
        "vitorias": 0,
        "empates": 0,
        "derrotas": 0,

        "gols_marcados": 0,
        "gols_sofridos": 0,

        "over15": 0,
        "over25": 0,

        "ambas": 0
    }

    for jogo in jogos:

        home_id = jogo.get("teams", {}).get("home", {}).get("id")
        away_id = jogo.get("teams", {}).get("away", {}).get("id")

        gh = jogo.get("goals", {}).get("home")
        ga = jogo.get("goals", {}).get("away")

        if gh is None or ga is None:
            continue

        gh = inteiro(gh)
        ga = inteiro(ga)

        resultado["jogos"] += 1

        if home_id == team_id:

            marcados = gh
            sofridos = ga

        elif away_id == team_id:

            marcados = ga
            sofridos = gh

        else:
            continue

        resultado["gols_marcados"] += marcados
        resultado["gols_sofridos"] += sofridos

        if marcados > sofridos:
            resultado["vitorias"] += 1

        elif marcados == sofridos:
            resultado["empates"] += 1

        else:
            resultado["derrotas"] += 1

        total = gh + ga

        if total >= 2:
            resultado["over15"] += 1

        if total >= 3:
            resultado["over25"] += 1

        if marcados > 0 and sofridos > 0:
            resultado["ambas"] += 1

    n = resultado["jogos"]

    if n:

        resultado["pct_vitorias"] = (
            resultado["vitorias"] / n * 100
        )

        resultado["pct_over15"] = (
            resultado["over15"] / n * 100
        )

        resultado["pct_over25"] = (
            resultado["over25"] / n * 100
        )

        resultado["pct_ambas"] = (
            resultado["ambas"] / n * 100
        )

        resultado["media_marcados"] = (
            resultado["gols_marcados"] / n
        )

        resultado["media_sofridos"] = (
            resultado["gols_sofridos"] / n
        )

    else:

        resultado["pct_vitorias"] = 0
        resultado["pct_over15"] = 0
        resultado["pct_over25"] = 0
        resultado["pct_ambas"] = 0
        resultado["media_marcados"] = 0
        resultado["media_sofridos"] = 0

    return resultado


# ============================================================
# ESTATÍSTICAS DA EQUIPE
# ============================================================

def estatisticas_equipe(team_id, league_id, season):

    chave = f"stats_{team_id}_{league_id}_{season}"

    dados, erro = api_get(
        "/teams/statistics",
        {
            "team": team_id,
            "league": league_id,
            "season": season
        },
        cache_key=chave,
        ttl=3600
    )

    if erro:
        return {}, erro

    return dados.get("response") or {}, None


# ============================================================
# ANÁLISE PRÉ-JOGO
# ============================================================

def analisar_jogo(fixture):

    league_id = fixture.get("league", {}).get("id")

    season = fixture.get("league", {}).get("season")

    home = fixture.get("teams", {}).get("home", {})
    away = fixture.get("teams", {}).get("away", {})

    home_id = home.get("id")
    away_id = away.get("id")

    home_name = home.get("name", "Casa")
    away_name = away.get("name", "Fora")

    jogos_home, erro1 = ultimos_10(
        home_id,
        league_id,
        season
    )

    jogos_away, erro2 = ultimos_10(
        away_id,
        league_id,
        season
    )

    rh = resumo(
        jogos_home,
        home_id
    )

    ra = resumo(
        jogos_away,
        away_id
    )

    oportunidades = []

    # ========================================================
    # RESULTADO
    # ========================================================

    diferenca_forma = (
        rh["pct_vitorias"]
        -
        ra["pct_vitorias"]
    )

    if diferenca_forma >= 15:

        confianca = min(
            88,
            58 + diferenca_forma * 0.5
        )

        oportunidades.append({

            "tipo": "Resultado",

            "selecao":
                f"{home_name} para vencer",

            "confianca": confianca,

            "motivos": [

                f"{home_name} venceu "
                f"{rh['vitorias']} das "
                f"{rh['jogos']} últimas partidas",

                f"{away_name} venceu "
                f"{ra['vitorias']} das "
                f"{ra['jogos']} últimas",

                "vantagem de forma recente"
            ]
        })

    elif diferenca_forma <= -15:

        confianca = min(
            88,
            58 + abs(diferenca_forma) * 0.5
        )

        oportunidades.append({

            "tipo": "Resultado",

            "selecao":
                f"{away_name} para vencer",

            "confianca": confianca,

            "motivos": [

                f"{away_name} venceu "
                f"{ra['vitorias']} das "
                f"{ra['jogos']} últimas partidas",

                f"{home_name} venceu "
                f"{rh['vitorias']} das "
                f"{rh['jogos']} últimas",

                "vantagem de forma recente"
            ]
        })

    else:

        if rh["pct_vitorias"] >= ra["pct_vitorias"]:

            selecao = f"{home_name} ou empate (1X)"

        else:

            selecao = f"{away_name} ou empate (X2)"

        oportunidades.append({

            "tipo": "Dupla possibilidade",

            "selecao": selecao,

            "confianca": 65,

            "motivos": [

                "diferença de desempenho recente pequena",

                "dupla possibilidade reduz a exposição ao empate"
            ]
        })


    # ========================================================
    # GOLS
    # ========================================================

    over15 = media([
        rh["pct_over15"],
        ra["pct_over15"]
    ])

    over25 = media([
        rh["pct_over25"],
        ra["pct_over25"]
    ])

    ambas = media([
        rh["pct_ambas"],
        ra["pct_ambas"]
    ])

    media_gols = (
        rh["media_marcados"]
        +
        rh["media_sofridos"]
        +
        ra["media_marcados"]
        +
        ra["media_sofridos"]
    ) / 2


    if over25 >= 60 and media_gols >= 2.5:

        oportunidades.append({

            "tipo": "Gols",

            "selecao":
                "Mais de 2,5 gols",

            "confianca":
                min(
                    88,
                    max(
                        60,
                        over25 * 0.75
                    )
                ),

            "motivos": [

                f"Over 2,5 em {over25:.0f}% "
                "das últimas partidas",

                f"Média combinada de gols: "
                f"{media_gols:.1f}"
            ]
        })

    elif over15 >= 70 and media_gols >= 1.8:

        oportunidades.append({

            "tipo": "Gols",

            "selecao":
                "Mais de 1,5 gols",

            "confianca":
                min(
                    90,
                    max(
                        62,
                        over15 * 0.8
                    )
                ),

            "motivos": [

                f"Over 1,5 em {over15:.0f}% "
                "das últimas partidas",

                f"Média combinada de gols: "
                f"{media_gols:.1f}"
            ]
        })


    # ========================================================
    # AMBAS MARCAM
    # ========================================================

    if ambas >= 60:

        oportunidades.append({

            "tipo": "Gols",

            "selecao":
                "Ambas marcam — SIM",

            "confianca":
                min(
                    85,
                    ambas * 0.82
                ),

            "motivos": [

                f"Ambas marcam em "
                f"{ambas:.0f}% das partidas recentes"
            ]
        })


    # ========================================================
    # ORDENAÇÃO
    # ========================================================

    oportunidades.sort(
        key=lambda x: x["confianca"],
        reverse=True
    )

    # duas melhores
    return rh, ra, oportunidades[:2]


# ============================================================
# FORMATAR JOGO
# ============================================================

def formatar_jogo(fixture):

    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]

    rh, ra, oportunidades = analisar_jogo(
        fixture
    )

    texto = [

        f"⚽ <b>{home} x {away}</b>",

        f"🏆 {nome_liga(fixture)}",

        f"🕐 {horario(fixture)}",

        "",

        "📊 <b>ÚLTIMAS 10 PARTIDAS</b>",

        f"🏠 {home}: "
        f"{rh['vitorias']}V "
        f"{rh['empates']}E "
        f"{rh['derrotas']}D",

        f"⚽ Gols: "
        f"{rh['gols_marcados']} "
        f"marcados / "
        f"{rh['gols_sofridos']} sofridos",

        f"📈 Over 1,5: "
        f"{rh['pct_over15']:.0f}%",

        f"📈 Over 2,5: "
        f"{rh['pct_over25']:.0f}%",

        "",

        f"✈️ {away}: "
        f"{ra['vitorias']}V "
        f"{ra['empates']}E "
        f"{ra['derrotas']}D",

        f"⚽ Gols: "
        f"{ra['gols_marcados']} "
        f"marcados / "
        f"{ra['gols_sofridos']} sofridos",

        f"📈 Over 1,5: "
        f"{ra['pct_over15']:.0f}%",

        f"📈 Over 2,5: "
        f"{ra['pct_over25']:.0f}%",

        "",

        "🎯 <b>2 MELHORES OPORTUNIDADES</b>"
    ]


    if not oportunidades:

        texto.append(
            "⚠️ Dados insuficientes."
        )

    else:

        for i, op in enumerate(
            oportunidades,
            1
        ):

            texto.extend([

                "",

                f"<b>{i}. {op['selecao']}</b>",

                f"📌 Mercado: {op['tipo']}",

                f"📈 Probabilidade estimada: "
                f"<b>{op['confianca']:.0f}%</b>"
            ])

            for motivo in op["motivos"]:

                texto.append(
                    f"• {motivo}"
                )


    texto.extend([

        "",

        "⚠️ Análise estatística informativa. "
        "Não garante resultado."
    ])

    return "\n".join(texto)


# ============================================================
# LIVE
# ============================================================

def estatisticas_live(fixture_id):

    dados, erro = api_get(

        "/fixtures/statistics",

        {
            "fixture": fixture_id
        },

        cache_key=f"live_stats_{fixture_id}",

        ttl=20
    )

    if erro:
        return [], erro

    return dados.get("response", []), None


def stat(lista, nome):

    for item in lista or []:

        if item.get("type") == nome:

            valor = item.get("value")

            return numero(valor)

    return 0


def analisar_live(fixture, stats):

    if len(stats) < 2:
        return None

    casa = stats[0]
    fora = stats[1]

    c = casa.get("statistics", [])
    f = fora.get("statistics", [])


    posse_c = stat(
        c,
        "Ball Possession"
    )

    posse_f = stat(
        f,
        "Ball Possession"
    )

    chutes_c = stat(
        c,
        "Total Shots"
    )

    chutes_f = stat(
        f,
        "Total Shots"
    )

    alvo_c = stat(
        c,
        "Shots on Goal"
    )

    alvo_f = stat(
        f,
        "Shots on Goal"
    )

    corners_c = stat(
        c,
        "Corner Kicks"
    )

    corners_f = stat(
        f,
        "Corner Kicks"
    )

    perigosos_c = stat(
        c,
        "Dangerous Attacks"
    )

    perigosos_f = stat(
        f,
        "Dangerous Attacks"
    )

    cartoes_c = stat(
        c,
        "Yellow Cards"
    )

    cartoes_f = stat(
        f,
        "Yellow Cards"
    )


    # ========================================================
    # PRESSÃO
    # ========================================================

    score_c = (

        posse_c * 0.10
        +
        chutes_c * 0.20
        +
        alvo_c * 0.30
        +
        corners_c * 0.15
        +
        perigosos_c * 0.25
    )

    score_f = (

        posse_f * 0.10
        +
        chutes_f * 0.20
        +
        alvo_f * 0.30
        +
        corners_f * 0.15
        +
        perigosos_f * 0.25
    )


    total = score_c + score_f

    if total:

        pressao_c = score_c / total * 100
        pressao_f = score_f / total * 100

    else:

        pressao_c = 50
        pressao_f = 50


    oportunidades = []

    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]


    # ========================================================
    # OPORTUNIDADE DE RESULTADO
    # ========================================================

    diferenca = pressao_c - pressao_f


    if diferenca >= 18:

        oportunidades.append({

            "selecao":
                f"{home} — tendência favorável",

            "mercado":
                "Resultado ao vivo",

            "confianca":
                min(
                    88,
                    60 + diferenca * 0.5
                ),

            "motivos": [

                f"Pressão: "
                f"{pressao_c:.0f}% x "
                f"{pressao_f:.0f}%",

                f"No alvo: "
                f"{inteiro(alvo_c)} x "
                f"{inteiro(alvo_f)}",

                f"Escanteios: "
                f"{inteiro(corners_c)} x "
                f"{inteiro(corners_f)}"
            ]
        })


    elif diferenca <= -18:

        oportunidades.append({

            "selecao":
                f"{away} — tendência favorável",

            "mercado":
                "Resultado ao vivo",

            "confianca":
                min(
                    88,
                    60 + abs(diferenca) * 0.5
                ),

            "motivos": [

                f"Pressão: "
                f"{pressao_f:.0f}% x "
                f"{pressao_c:.0f}%",

                f"No alvo: "
                f"{inteiro(alvo_f)} x "
                f"{inteiro(alvo_c)}",

                f"Escanteios: "
                f"{inteiro(corners_f)} x "
                f"{inteiro(corners_c)}"
            ]
        })


    # ========================================================
    # GOLS
    # ========================================================

    total_chutes = chutes_c + chutes_f
    total_alvo = alvo_c + alvo_f
    total_corners = corners_c + corners_f
    total_perigosos = perigosos_c + perigosos_f


    if (

        total_chutes >= 15
        or total_alvo >= 7
        or total_corners >= 8
        or total_perigosos >= 70

    ):

        oportunidades.append({

            "selecao":
                "Tendência de mais gols",

            "mercado":
                "Gols ao vivo",

            "confianca":
                min(
                    87,
                    58
                    + total_alvo * 2
                    + total_chutes * 0.25
                ),

            "motivos": [

                f"Finalizações: "
                f"{inteiro(total_chutes)}",

                f"No alvo: "
                f"{inteiro(total_alvo)}",

                f"Escanteios: "
                f"{inteiro(total_corners)}",

                f"Ataques perigosos: "
                f"{inteiro(total_perigosos)}"
            ]
        })


    # ========================================================
    # ESCANTEIOS
    # ========================================================

    if total_corners >= 7:

        oportunidades.append({

            "selecao":
                "Tendência de mais escanteios",

            "mercado":
                "Escanteios ao vivo",

            "confianca":
                min(
                    84,
                    58 + total_corners * 2
                ),

            "motivos": [

                f"{inteiro(total_corners)} "
                "escanteios já registrados",

                "Volume ofensivo elevado"
            ]
        })


    # ========================================================
    # CARTÕES
    # ========================================================

    total_cartoes = cartoes_c + cartoes_f

    if total_cartoes >= 3:

        oportunidades.append({

            "selecao":
                "Tendência de mais cartões",

            "mercado":
                "Cartões ao vivo",

            "confianca":
                min(
                    82,
                    58 + total_cartoes * 3
                ),

            "motivos": [

                f"{inteiro(total_cartoes)} "
                "cartões amarelos registrados"
            ]
        })


    oportunidades.sort(
        key=lambda x: x["confianca"],
        reverse=True
    )


    return {

        "posse_c": posse_c,
        "posse_f": posse_f,

        "chutes_c": chutes_c,
        "chutes_f": chutes_f,

        "alvo_c": alvo_c,
        "alvo_f": alvo_f,

        "corners_c": corners_c,
        "corners_f": corners_f,

        "perigosos_c": perigosos_c,
        "perigosos_f": perigosos_f,

        "cartoes_c": cartoes_c,
        "cartoes_f": cartoes_f,

        "pressao_c": pressao_c,
        "pressao_f": pressao_f,

        "oportunidades":
            oportunidades[:2]
    }


# ============================================================
# FORMATAR LIVE
# ============================================================

def formatar_live(
    fixture,
    analise
):

    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]

    gh = inteiro(
        fixture.get("goals", {}).get("home")
    )

    ga = inteiro(
        fixture.get("goals", {}).get("away")
    )

    minuto = (
        fixture.get("fixture", {})
        .get("status", {})
        .get("elapsed")
        or 0
    )

    texto = [

        f"🔴 <b>{home} x {away}</b>",

        f"🏆 {nome_liga(fixture)}",

        f"⏱️ {minuto}' — "
        f"<b>{gh} x {ga}</b>",

        "",

        "📊 <b>ESTATÍSTICAS AO VIVO</b>",

        f"Posse: "
        f"{analise['posse_c']:.0f}% x "
        f"{analise['posse_f']:.0f}%",

        f"Finalizações: "
        f"{inteiro(analise['chutes_c'])} x "
        f"{inteiro(analise['chutes_f'])}",

        f"No alvo: "
        f"{inteiro(analise['alvo_c'])} x "
        f"{inteiro(analise['alvo_f'])}",

        f"Escanteios: "
        f"{inteiro(analise['corners_c'])} x "
        f"{inteiro(analise['corners_f'])}",

        f"Ataques perigosos: "
        f"{inteiro(analise['perigosos_c'])} x "
        f"{inteiro(analise['perigosos_f'])}",

        f"Cartões: "
        f"{inteiro(analise['cartoes_c'])} x "
        f"{inteiro(analise['cartoes_f'])}",

        "",

        f"🔥 Pressão: "
        f"{analise['pressao_c']:.0f}% x "
        f"{analise['pressao_f']:.0f}%",

        "",

        "🎯 <b>OPORTUNIDADES AO VIVO</b>"
    ]


    if not analise["oportunidades"]:

        texto.append(
            "⚠️ Ainda não há dados suficientes."
        )

    else:

        for i, op in enumerate(
            analise["oportunidades"],
            1
        ):

            texto.extend([

                "",

                f"<b>{i}. {op['selecao']}</b>",

                f"📌 Mercado: "
                f"{op['mercado']}",

                f"📈 Força estatística: "
                f"<b>{op['confianca']:.0f}%</b>"
            ])

            for motivo in op["motivos"]:

                texto.append(
                    f"• {motivo}"
                )


    texto.extend([

        "",

        "⚠️ Análise informativa. "
        "Não garante resultado."
    ])

    return "\n".join(texto)


# ============================================================
# /START
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):

    bot.reply_to(

        message,

        "🤖 <b>BOT DE ANÁLISE DE FUTEBOL</b>\n\n"

        "🟢 Bot conectado.\n\n"

        "📡 Fonte: API-Sports / API-Football\n\n"

        "<b>Comandos:</b>\n\n"

        "📅 /jogos — Jogos do dia + 2 oportunidades\n"

        "🔴 /live — Jogos ao vivo + análise em tempo real\n"

        "🔎 /analisar ID — Analisar jogo específico\n"

        "📡 /status — Ver conexão\n\n"

        "⏸️ /pausar — Pausar bot\n"

        "▶️ /ativar — Ativar bot\n\n"

        "ℹ️ A análise considera forma recente, "
        "gols, pressão, escanteios e cartões "
        "quando os dados estão disponíveis."
    )


# ============================================================
# /STATUS
# ============================================================

@bot.message_handler(commands=["status"])
def status(message):

    dados, erro = api_get(
        "/status",
        cache_key="status",
        ttl=30
    )

    if erro:

        bot.reply_to(
            message,
            f"🔴 <b>API OFFLINE</b>\n\n{erro}"
        )

        return

    bot.reply_to(

        message,

        "🟢 <b>SISTEMA ONLINE</b>\n\n"

        "Telegram: conectado ✅\n"

        "API-Sports: conectada ✅\n"

        "Bot ativo: "
        f"{'SIM' if BOT_ATIVO else 'NÃO'}"
    )


# ============================================================
# /JOGOS
# ============================================================

@bot.message_handler(commands=["jogos"])
def jogos(message):

    if not BOT_ATIVO:

        bot.reply_to(
            message,
            "⏸️ Bot pausado. Use /ativar."
        )

        return

    bot.send_message(

        message.chat.id,

        "🔎 <b>Buscando os jogos de hoje...</b>\n"
        "📊 Analisando as estatísticas..."
    )

    lista, erro = jogos_do_dia()

    if erro:

        bot.send_message(
            message.chat.id,
            f"🔴 Erro na API:\n{erro}"
        )

        return

    if not lista:

        bot.send_message(

            message.chat.id,

            "📅 Nenhum jogo encontrado hoje "
            "nas competições configuradas."
        )

        return


    bot.send_message(

        message.chat.id,

        f"📅 <b>JOGOS DE HOJE</b>\n\n"
        f"⚽ {len(lista)} partidas encontradas."
    )


    for fixture in lista:

        try:

            texto = formatar_jogo(
                fixture
            )

            bot.send_message(
                message.chat.id,
                texto
            )

            time.sleep(1)

        except Exception as e:

            bot.send_message(

                message.chat.id,

                f"⚠️ Erro ao analisar partida:\n{e}"
            )


# ============================================================
# /LIVE
# ============================================================

@bot.message_handler(commands=["live"])
def live(message):

    if not BOT_ATIVO:

        bot.reply_to(
            message,
            "⏸️ Bot pausado. Use /ativar."
        )

        return


    bot.send_message(

        message.chat.id,

        "🔴 <b>Procurando jogos ao vivo...</b>\n"
        "🔥 Analisando pressão e estatísticas."
    )


    lista, erro = jogos_live()

    if erro:

        bot.send_message(
            message.chat.id,
            f"🔴 Erro na API:\n{erro}"
        )

        return


    if not lista:

        bot.send_message(

            message.chat.id,

            "⚽ Nenhum jogo ao vivo encontrado "
            "nas competições configuradas."
        )

        return


    for fixture in lista:

        fixture_id = (
            fixture.get("fixture", {})
            .get("id")
        )

        stats, erro_stats = estatisticas_live(
            fixture_id
        )

        if erro_stats:

            continue


        analise = analisar_live(
            fixture,
            stats
        )

        if not analise:

            bot.send_message(

                message.chat.id,

                f"⚽ <b>{fixture['teams']['home']['name']} "
                f"x {fixture['teams']['away']['name']}</b>\n\n"
                "📊 Estatísticas ainda não disponíveis."
            )

            continue


        bot.send_message(

            message.chat.id,

            formatar_live(
                fixture,
                analise
            )
        )


# ============================================================
# /ANALISAR
# ============================================================

@bot.message_handler(commands=["analisar"])
def analisar(message):

    partes = message.text.split()

    if len(partes) < 2:

        bot.reply_to(
            message,
            "Use: <code>/analisar ID</code>"
        )

        return


    try:

        fixture_id = int(partes[1])

    except:

        bot.reply_to(
            message,
            "❌ O ID precisa ser numérico."
        )

        return


    dados, erro = api_get(

        "/fixtures",

        {
            "id": fixture_id,
            "timezone": "America/Sao_Paulo"
        },

        cache_key=f"fixture_{fixture_id}",

        ttl=120
    )


    if erro:

        bot.reply_to(
            message,
            f"🔴 {erro}"
        )

        return


    resposta = dados.get("response", [])

    if not resposta:

        bot.reply_to(
            message,
            "❌ Jogo não encontrado."
        )

        return


    fixture = resposta[0]

    status_jogo = (
        fixture.get("fixture", {})
        .get("status", {})
        .get("short")
    )


    if status_jogo in {

        "1H",
        "HT",
        "2H",
        "ET",
        "P",
        "LIVE"

    }:

        stats, erro = estatisticas_live(
            fixture_id
        )

        if erro:

            bot.reply_to(
                message,
                f"🔴 {erro}"
            )

            return


        analise = analisar_live(
            fixture,
            stats
        )

        if analise:

            bot.send_message(

                message.chat.id,

                formatar_live(
                    fixture,
                    analise
                )
            )

        else:

            bot.send_message(

                message.chat.id,

                "📊 Estatísticas ao vivo "
                "ainda não disponíveis."
            )

    else:

        bot.send_message(

            message.chat.id,

            "🔎 <b>Analisando partida...</b>"
        )

        try:

            bot.send_message(

                message.chat.id,

                formatar_jogo(
                    fixture
                )
            )

        except Exception as e:

            bot.send_message(

                message.chat.id,

                f"🔴 Erro na análise: {e}"
            )


# ============================================================
# PAUSAR
# ============================================================

@bot.message_handler(commands=["pausar"])
def pausar(message):

    global BOT_ATIVO

    BOT_ATIVO = False

    bot.reply_to(
        message,
        "⏸️ <b>Bot pausado.</b>"
    )


# ============================================================
# ATIVAR
# ============================================================

@bot.message_handler(commands=["ativar"])
def ativar(message):

    global BOT_ATIVO

    BOT_ATIVO = True

    bot.reply_to(
        message,
        "▶️ <b>Bot ativado novamente.</b>"
    )


# ============================================================
# FLASK / RENDER
# ============================================================

@app.route("/")
def home():

    return "Bot funcionando! API-Sports/API-Football conectado."


@app.route("/health")
def health():

    return {

        "status": "online",

        "bot_ativo": BOT_ATIVO,

        "api": "API-Sports/API-Football"
    }


def servidor():

    porta = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=porta
    )


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_bot():

    while True:

        try:

            print(
                "🤖 Iniciando Telegram..."
            )

            bot.remove_webhook()

            time.sleep(2)

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                f"❌ Erro Telegram: {e}"
            )

            time.sleep(10)


# ============================================================
# INÍCIO
# ============================================================

if __name__ == "__main__":

    print(
        "======================================"
    )

    print(
        "🤖 BOT DE FUTEBOL"
    )

    print(
        "📡 API-Sports/API-Football"
    )

    print(
        "🚀 Iniciando..."
    )

    print(
        "======================================"
    )


    thread = threading.Thread(

        target=servidor,

        daemon=True
    )

    thread.start()


    iniciar_bot()
