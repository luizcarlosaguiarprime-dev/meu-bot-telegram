import os
import time
import threading
import statistics
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask
import telebot


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não configurado no Render.")

if not APIFOOTBALL_KEY:
    raise RuntimeError("APIFOOTBALL_KEY não configurada no Render.")

bot = telebot.TeleBot(BOT_TOKEN)

TZ = ZoneInfo("America/Sao_Paulo")

LIVE_INTERVAL = int(os.getenv("LIVE_INTERVAL", "60"))
HISTORY_MATCHES = int(os.getenv("HISTORY_MATCHES", "15"))

# Tempo mínimo entre alertas do mesmo jogo
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "10")) * 60


# ============================================================
# LIGAS
# ============================================================

LEAGUES = {
    71: "Brasileirão Série A",
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    94: "Primeira Liga",

    72: "Copa do Brasil",
    45: "FA Cup",
    143: "Copa del Rey",
    81: "DFB Pokal",
    137: "Coppa Italia",
    66: "Coupe de France",
    96: "Taça de Portugal",

    2: "Champions League",
    3: "Europa League",
    848: "Conference League",

    13: "Libertadores",
    11: "Sul-Americana",
}


# ============================================================
# STATUS
# ============================================================

LIVE_STATUS = {
    "1H",
    "HT",
    "2H",
    "ET",
    "BT",
    "P",
    "LIVE",
}

FINAL_STATUS = {
    "FT",
    "AET",
    "PEN",
    "CANC",
    "ABD",
    "AWD",
    "WO",
}


# ============================================================
# MEMÓRIA
# ============================================================

CHAT_ID = None

BOT_ATIVO = True

# Jogos que já receberam análise pré-jogo
PREJOGO_ENVIADO = set()

# Último alerta enviado por jogo
ULTIMO_ALERTA = {}

# Histórico de informações do jogo
ESTADO_JOGOS = {}

# Cache simples
CACHE = {}


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando! 🤖"


@app.route("/health")
def health():
    return "OK"


# ============================================================
# DATA / HORA
# ============================================================

def agora_brasilia():
    return datetime.now(TZ)


def formatar_data(dt):
    return dt.strftime("%d/%m/%Y %H:%M")


def converter_data_api(data_str):
    try:
        dt = datetime.fromisoformat(
            data_str.replace("Z", "+00:00")
        )
        return dt.astimezone(TZ)
    except Exception:
        return None


# ============================================================
# API-FOOTBALL
# ============================================================

API_URL = "https://v3.football.api-sports.io"


def api_get(endpoint, params=None, cache_seconds=30):
    """
    Consulta a API-Football com cache simples.
    """

    if params is None:
        params = {}

    chave = endpoint + "|" + str(sorted(params.items()))

    agora = time.time()

    if chave in CACHE:
        momento, dados = CACHE[chave]

        if agora - momento < cache_seconds:
            return dados

    headers = {
        "x-apisports-key": APIFOOTBALL_KEY
    }

    try:
        resposta = requests.get(
            API_URL + endpoint,
            headers=headers,
            params=params,
            timeout=20
        )

        if resposta.status_code != 200:
            print(
                f"API-Football erro {resposta.status_code}: "
                f"{resposta.text[:500]}"
            )
            return None

        dados = resposta.json()

        CACHE[chave] = (agora, dados)

        return dados

    except Exception as e:
        print("Erro API-Football:", e)
        return None


# ============================================================
# FILTRO DE LIGAS
# ============================================================

def liga_configurada(league_id):
    try:
        return int(league_id) in LEAGUES
    except Exception:
        return False


# ============================================================
# BUSCAR JOGOS DAS PRÓXIMAS 24 HORAS
# ============================================================

def buscar_proximos_24h():

    agora = agora_brasilia()

    inicio = agora.date()
    fim = (agora + timedelta(days=1)).date()

    params = {
        "from": inicio.strftime("%Y-%m-%d"),
        "to": fim.strftime("%Y-%m-%d"),
        "timezone": "America/Sao_Paulo"
    }

    dados = api_get(
        "/fixtures",
        params,
        cache_seconds=30
    )

    if not dados:
        return []

    jogos = []

    for item in dados.get("response", []):

        fixture = item.get("fixture", {})
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        status = fixture.get("status", {})

        league_id = league.get("id")

        if not liga_configurada(league_id):
            continue

        data_jogo = converter_data_api(
            fixture.get("date", "")
        )

        if not data_jogo:
            continue

        # Janela exata de 24 horas a partir do momento do comando
        if data_jogo < agora:
            continue

        if data_jogo > agora + timedelta(hours=24):
            continue

        jogos.append({
            "id": fixture.get("id"),
            "liga_id": league_id,
            "liga": LEAGUES.get(
                league_id,
                league.get("name", "Liga")
            ),
            "casa": teams.get("home", {}).get("name", "?"),
            "fora": teams.get("away", {}).get("name", "?"),
            "data": data_jogo,
            "status": status.get("short", ""),
            "elapsed": status.get("elapsed"),
            "gols_casa": goals.get("home"),
            "gols_fora": goals.get("away"),
        })

    jogos.sort(key=lambda x: x["data"])

    return jogos


# ============================================================
# BUSCAR JOGOS LIVE
# ============================================================

def buscar_jogos_live():

    dados = api_get(
        "/fixtures",
        {
            "live": "all"
        },
        cache_seconds=20
    )

    if not dados:
        return []

    jogos = []

    for item in dados.get("response", []):

        fixture = item.get("fixture", {})
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        status = fixture.get("status", {})

        league_id = league.get("id")

        if not liga_configurada(league_id):
            continue

        estado = status.get("short", "")

        if estado not in LIVE_STATUS:
            continue

        jogos.append({
            "id": fixture.get("id"),
            "liga_id": league_id,
            "liga": LEAGUES.get(
                league_id,
                league.get("name", "Liga")
            ),
            "casa": teams.get("home", {}).get("name", "?"),
            "fora": teams.get("away", {}).get("name", "?"),
            "data": converter_data_api(
                fixture.get("date", "")
            ),
            "status": estado,
            "elapsed": status.get("elapsed"),
            "gols_casa": goals.get("home", 0),
            "gols_fora": goals.get("away", 0),
        })

    return jogos


# ============================================================
# COMBINAR PRÓXIMOS + LIVE
# ============================================================

def buscar_jogos():

    proximos = buscar_proximos_24h()
    lives = buscar_jogos_live()

    todos = {}

    for jogo in proximos:
        todos[jogo["id"]] = jogo

    for jogo in lives:
        todos[jogo["id"]] = jogo

    lista = list(todos.values())

    lista.sort(
        key=lambda x: (
            0 if x["status"] in LIVE_STATUS else 1,
            x["data"] if x["data"] else agora_brasilia()
        )
    )

    return lista


# ============================================================
# ESTATÍSTICAS DO JOGO LIVE
# ============================================================

def buscar_estatisticas_live(fixture_id):

    dados = api_get(
        "/fixtures/statistics",
        {
            "fixture": fixture_id
        },
        cache_seconds=25
    )

    if not dados:
        return {}

    resultado = {}

    for equipe in dados.get("response", []):

        team = equipe.get("team", {})
        team_id = team.get("id")
        nome = team.get("name")

        estatisticas = {}

        for stat in equipe.get("statistics", []):

            tipo = stat.get("type")
            valor = stat.get("value")

            if tipo:
                estatisticas[tipo] = valor

        resultado[team_id] = {
            "nome": nome,
            "stats": estatisticas
        }

    return resultado


# ============================================================
# CONVERSÃO DE ESTATÍSTICAS
# ============================================================

def numero(valor):

    if valor is None:
        return None

    if isinstance(valor, int):
        return valor

    if isinstance(valor, float):
        return valor

    texto = str(valor).strip()

    if texto.endswith("%"):
        try:
            return float(texto[:-1])
        except:
            return None

    try:
        return float(texto)
    except:
        return None


def obter_stat(stats, nomes):

    for nome in nomes:

        if nome in stats:
            valor = numero(stats[nome])

            if valor is not None:
                return valor

    return None


# ============================================================
# HISTÓRICO DE UM TIME
# ============================================================

def buscar_historico_time(team_id):

    dados = api_get(
        "/fixtures",
        {
            "team": team_id,
            "last": HISTORY_MATCHES,
            "timezone": "America/Sao_Paulo"
        },
        cache_seconds=600
    )

    if not dados:
        return []

    jogos = []

    for item in dados.get("response", []):

        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        league = item.get("league", {})

        if fixture.get("status", {}).get("short") not in FINAL_STATUS:
            continue

        casa_id = teams.get("home", {}).get("id")
        fora_id = teams.get("away", {}).get("id")

        if team_id == casa_id:
            marcou = goals.get("home")
            sofreu = goals.get("away")
            casa = True
        elif team_id == fora_id:
            marcou = goals.get("away")
            sofreu = goals.get("home")
            casa = False
        else:
            continue

        jogos.append({
            "fixture_id": fixture.get("id"),
            "data": converter_data_api(
                fixture.get("date", "")
            ),
            "marcou": marcou or 0,
            "sofreu": sofreu or 0,
            "casa": casa,
            "liga": league.get("name", "")
        })

    jogos.sort(
        key=lambda x: x["data"] or agora_brasilia(),
        reverse=True
    )

    return jogos[:HISTORY_MATCHES]


# ============================================================
# ESTATÍSTICAS HISTÓRICAS
# ============================================================

def buscar_estatisticas_historicas(team_id):

    historico = buscar_historico_time(team_id)

    if not historico:
        return {}

    dados_jogos = []

    # Pegamos os jogos mais recentes para não consumir API demais
    for jogo in historico[:8]:

        fixture_id = jogo["fixture_id"]

        dados = api_get(
            "/fixtures/statistics",
            {
                "fixture": fixture_id
            },
            cache_seconds=3600
        )

        if not dados:
            continue

        equipe_stats = None

        for equipe in dados.get("response", []):

            if equipe.get("team", {}).get("id") == team_id:
                equipe_stats = equipe
                break

        if not equipe_stats:
            continue

        stats = {}

        for stat in equipe_stats.get("statistics", []):

            tipo = stat.get("type")
            valor = stat.get("value")

            if tipo:
                stats[tipo] = numero(valor)

        dados_jogos.append({
            "fixture": fixture_id,
            "stats": stats
        })

    return {
        "historico": historico,
        "estatisticas": dados_jogos
    }


# ============================================================
# MÉDIA PONDERADA
# ============================================================

def media_ponderada(valores):

    valores_validos = [
        v for v in valores
        if v is not None
    ]

    if not valores_validos:
        return None

    pesos = list(
        range(len(valores_validos), 0, -1)
    )

    total = sum(pesos)

    return sum(
        valor * peso
        for valor, peso
        in zip(valores_validos, pesos)
    ) / total


# ============================================================
# ANÁLISE HISTÓRICA
# ============================================================

def analisar_historico(team_id):

    dados = buscar_estatisticas_historicas(team_id)

    if not dados:
        return {
            "jogos": 0,
            "gols_marcados": None,
            "gols_sofridos": None,
            "escanteios": None,
            "finalizacoes": None,
            "finalizacoes_gol": None,
            "posse": None,
            "cartoes": None,
        }

    historico = dados.get("historico", [])
    estatisticas = dados.get("estatisticas", [])

    gols_marcados = []
    gols_sofridos = []

    for jogo in historico:

        gols_marcados.append(
            jogo.get("marcou", 0)
        )

        gols_sofridos.append(
            jogo.get("sofreu", 0)
        )

    escanteios = []
    finalizacoes = []
    finalizacoes_gol = []
    posse = []
    cartoes = []

    for jogo in estatisticas:

        stats = jogo.get("stats", {})

        esc = obter_stat(
            stats,
            [
                "Corner Kicks"
            ]
        )

        fin = obter_stat(
            stats,
            [
                "Total Shots"
            ]
        )

        fin_gol = obter_stat(
            stats,
            [
                "Shots on Goal"
            ]
        )

        pos = obter_stat(
            stats,
            [
                "Ball Possession"
            ]
        )

        car = obter_stat(
            stats,
            [
                "Yellow Cards"
            ]
        )

        if esc is not None:
            escanteios.append(esc)

        if fin is not None:
            finalizacoes.append(fin)

        if fin_gol is not None:
            finalizacoes_gol.append(fin_gol)

        if pos is not None:
            posse.append(pos)

        if car is not None:
            cartoes.append(car)

    return {
        "jogos": len(historico),

        "gols_marcados": media_ponderada(
            gols_marcados
        ),

        "gols_sofridos": media_ponderada(
            gols_sofridos
        ),

        "escanteios": media_ponderada(
            escanteios
        ),

        "finalizacoes": media_ponderada(
            finalizacoes
        ),

        "finalizacoes_gol": media_ponderada(
            finalizacoes_gol
        ),

        "posse": media_ponderada(
            posse
        ),

        "cartoes": media_ponderada(
            cartoes
        ),
    }


# ============================================================
# DESEMPENHO RECENTE
# ============================================================

def resumo_forma(team_id):

    historico = buscar_historico_time(team_id)

    if not historico:
        return {
            "jogos": 0,
            "vitorias": 0,
            "empates": 0,
            "derrotas": 0,
            "gols_marcados": 0,
            "gols_sofridos": 0
        }

    vitorias = 0
    empates = 0
    derrotas = 0

    gols_marcados = []
    gols_sofridos = []

    for jogo in historico:

        marcou = jogo["marcou"]
        sofreu = jogo["sofreu"]

        gols_marcados.append(marcou)
        gols_sofridos.append(sofreu)

        if marcou > sofreu:
            vitorias += 1
        elif marcou == sofreu:
            empates += 1
        else:
            derrotas += 1

    return {
        "jogos": len(historico),
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "gols_marcados": sum(gols_marcados),
        "gols_sofridos": sum(gols_sofridos)
    }


# ============================================================
# ANÁLISE PRÉ-JOGO
# ============================================================

def analisar_prejogo(jogo):

    fixture_id = jogo["id"]

    # Para obter os IDs dos times
    dados = api_get(
        "/fixtures",
        {
            "id": fixture_id
        },
        cache_seconds=600
    )

    if not dados or not dados.get("response"):
        return None

    item = dados["response"][0]

    teams = item.get("teams", {})

    casa = teams.get("home", {})
    fora = teams.get("away", {})

    casa_id = casa.get("id")
    fora_id = fora.get("id")

    if not casa_id or not fora_id:
        return None

    forma_casa = resumo_forma(casa_id)
    forma_fora = resumo_forma(fora_id)

    stats_casa = analisar_historico(casa_id)
    stats_fora = analisar_historico(fora_id)

    return {
        "casa_id": casa_id,
        "fora_id": fora_id,
        "casa": casa.get("name"),
        "fora": fora.get("name"),
        "forma_casa": forma_casa,
        "forma_fora": forma_fora,
        "stats_casa": stats_casa,
        "stats_fora": stats_fora
    }


# ============================================================
# TEXTO PRÉ-JOGO
# ============================================================

def texto_prejogo(jogo, analise):

    if not analise:
        return (
            f"⚽ {jogo['casa']} x {jogo['fora']}\n"
            f"🏆 {jogo['liga']}\n"
            f"🕐 {formatar_data(jogo['data'])}\n\n"
            "⚠️ Não foi possível carregar o histórico completo."
        )

    fc = analise["forma_casa"]
    ff = analise["forma_fora"]

    sc = analise["stats_casa"]
    sf = analise["stats_fora"]

    texto = []

    texto.append("📋 PRÉ-JOGO")
    texto.append("")
    texto.append(
        f"🏆 {jogo['liga']}"
    )
    texto.append(
        f"⚽ {jogo['casa']} x {jogo['fora']}"
    )
    texto.append(
        f"🕐 {formatar_data(jogo['data'])}"
    )

    texto.append("")
    texto.append("📊 RETROSPECTO RECENTE")

    texto.append(
        f"🏠 {jogo['casa']}: "
        f"{fc['vitorias']}V "
        f"{fc['empates']}E "
        f"{fc['derrotas']}D"
    )

    texto.append(
        f"✈️ {jogo['fora']}: "
        f"{ff['vitorias']}V "
        f"{ff['empates']}E "
        f"{ff['derrotas']}D"
    )

    texto.append("")
    texto.append("⚽ GOLS")

    if sc["gols_marcados"] is not None:
        texto.append(
            f"🏠 {jogo['casa']}: "
            f"{sc['gols_marcados']:.1f} marcados / "
            f"{sc['gols_sofridos']:.1f} sofridos"
        )

    if sf["gols_marcados"] is not None:
        texto.append(
            f"✈️ {jogo['fora']}: "
            f"{sf['gols_marcados']:.1f} marcados / "
            f"{sf['gols_sofridos']:.1f} sofridos"
        )

    texto.append("")
    texto.append("🚩 ESCANTEIOS")

    if sc["escanteios"] is not None:
        texto.append(
            f"🏠 {jogo['casa']}: "
            f"{sc['escanteios']:.1f}"
        )

    if sf["escanteios"] is not None:
        texto.append(
            f"✈️ {jogo['fora']}: "
            f"{sf['escanteios']:.1f}"
        )

    texto.append("")
    texto.append("🎯 FINALIZAÇÕES")

    if sc["finalizacoes"] is not None:
        texto.append(
            f"🏠 {jogo['casa']}: "
            f"{sc['finalizacoes']:.1f}"
        )

    if sf["finalizacoes"] is not None:
        texto.append(
            f"✈️ {jogo['fora']}: "
            f"{sf['finalizacoes']:.1f}"
        )

    texto.append("")
    texto.append("📈 LEITURA ESTATÍSTICA")

    motivos = []

    if (
        sc["escanteios"] is not None
        and sf["escanteios"] is not None
    ):
        media_cantos = (
            sc["escanteios"] +
            sf["escanteios"]
        )

        if media_cantos >= 10:
            motivos.append(
                "tendência de jogo com muitos escanteios"
            )

    if (
        sc["finalizacoes"] is not None
        and sf["finalizacoes"] is not None
    ):
        media_finalizacoes = (
            sc["finalizacoes"] +
            sf["finalizacoes"]
        )

        if media_finalizacoes >= 22:
            motivos.append(
                "volume histórico elevado de finalizações"
            )

    if (
        fc["vitorias"] > ff["vitorias"]
    ):
        motivos.append(
            f"{jogo['casa']} apresenta retrospecto recente superior"
        )

    elif (
        ff["vitorias"] > fc["vitorias"]
    ):
        motivos.append(
            f"{jogo['fora']} apresenta retrospecto recente superior"
        )

    if motivos:

        for motivo in motivos:
            texto.append(
                f"• {motivo.capitalize()}."
            )

    else:
        texto.append(
            "• Sem tendência estatística forte identificada antes do jogo."
        )

    texto.append("")
    texto.append(
        "⚠️ Análise estatística. Não é garantia de resultado."
    )

    return "\n".join(texto)


# ============================================================
# ANÁLISE LIVE
# ============================================================

def analisar_live(jogo):

    fixture_id = jogo["id"]

    estatisticas = buscar_estatisticas_live(
        fixture_id
    )

    if not estatisticas:
        return None

    casa_id = None
    fora_id = None

    # Descobrir IDs através do jogo
    dados = api_get(
        "/fixtures",
        {
            "id": fixture_id
        },
        cache_seconds=600
    )

    if dados and dados.get("response"):

        teams = dados["response"][0].get(
            "teams", {}
        )

        casa_id = teams.get(
            "home", {}
        ).get("id")

        fora_id = teams.get(
            "away", {}
        ).get("id")

    if not casa_id or not fora_id:
        return None

    casa = estatisticas.get(casa_id, {})
    fora = estatisticas.get(fora_id, {})

    sc = casa.get("stats", {})
    sf = fora.get("stats", {})

    posse_casa = obter_stat(
        sc,
        ["Ball Possession"]
    )

    posse_fora = obter_stat(
        sf,
        ["Ball Possession"]
    )

    final_casa = obter_stat(
        sc,
        ["Total Shots"]
    )

    final_fora = obter_stat(
        sf,
        ["Total Shots"]
    )

    gol_casa = obter_stat(
        sc,
        ["Shots on Goal"]
    )

    gol_fora = obter_stat(
        sf,
        ["Shots on Goal"]
    )

    cantos_casa = obter_stat(
        sc,
        ["Corner Kicks"]
    )

    cantos_fora = obter_stat(
        sf,
        ["Corner Kicks"]
    )

    ataques_casa = obter_stat(
        sc,
        ["Dangerous Attacks"]
    )

    ataques_fora = obter_stat(
        sf,
        ["Dangerous Attacks"]
    )

    recuperacoes_casa = obter_stat(
        sc,
        [
            "Ball Recoveries",
            "Recoveries"
        ]
    )

    recuperacoes_fora = obter_stat(
        sf,
        [
            "Ball Recoveries",
            "Recoveries"
        ]
    )

    return {
        "posse_casa": posse_casa,
        "posse_fora": posse_fora,

        "final_casa": final_casa,
        "final_fora": final_fora,

        "gol_casa": gol_casa,
        "gol_fora": gol_fora,

        "cantos_casa": cantos_casa,
        "cantos_fora": cantos_fora,

        "ataques_casa": ataques_casa,
        "ataques_fora": ataques_fora,

        "recuperacoes_casa": recuperacoes_casa,
        "recuperacoes_fora": recuperacoes_fora
    }


# ============================================================
# SCORE DE PRESSÃO
# ============================================================

def calcular_pressao(dados):

    pontos_casa = 0
    pontos_fora = 0

    # Posse
    if (
        dados["posse_casa"] is not None
        and dados["posse_fora"] is not None
    ):
        if dados["posse_casa"] > dados["posse_fora"] + 8:
            pontos_casa += 15

        elif dados["posse_fora"] > dados["posse_casa"] + 8:
            pontos_fora += 15

    # Finalizações
    if (
        dados["final_casa"] is not None
        and dados["final_fora"] is not None
    ):
        diferenca = (
            dados["final_casa"] -
            dados["final_fora"]
        )

        if diferenca >= 4:
            pontos_casa += 25

        elif diferenca <= -4:
            pontos_fora += 25

    # Finalizações no gol
    if (
        dados["gol_casa"] is not None
        and dados["gol_fora"] is not None
    ):
        diferenca = (
            dados["gol_casa"] -
            dados["gol_fora"]
        )

        if diferenca >= 2:
            pontos_casa += 30

        elif diferenca <= -2:
            pontos_fora += 30

    # Ataques perigosos
    if (
        dados["ataques_casa"] is not None
        and dados["ataques_fora"] is not None
    ):
        diferenca = (
            dados["ataques_casa"] -
            dados["ataques_fora"]
        )

        if diferenca >= 10:
            pontos_casa += 20

        elif diferenca <= -10:
            pontos_fora += 20

    # Escanteios
    if (
        dados["cantos_casa"] is not None
        and dados["cantos_fora"] is not None
    ):
        diferenca = (
            dados["cantos_casa"] -
            dados["cantos_fora"]
        )

        if diferenca >= 2:
            pontos_casa += 10

        elif diferenca <= -2:
            pontos_fora += 10

    return {
        "casa": min(pontos_casa, 100),
        "fora": min(pontos_fora, 100)
    }


# ============================================================
# GERAR SINAIS LIVE
# ============================================================

def gerar_sinais_live(jogo, dados):

    if not dados:
        return []

    pressao = calcular_pressao(dados)

    sinais = []

    # --------------------------------------------------------
    # PRESSÃO
    # --------------------------------------------------------

    if pressao["casa"] >= 60:

        sinais.append({
            "tipo": "PRESSÃO",
            "lado": jogo["casa"],
            "score": pressao["casa"],
            "motivo": (
                "domínio estatístico do mandante "
                "em volume ofensivo"
            )
        })

    if pressao["fora"] >= 60:

        sinais.append({
            "tipo": "PRESSÃO",
            "lado": jogo["fora"],
            "score": pressao["fora"],
            "motivo": (
                "domínio estatístico do visitante "
                "em volume ofensivo"
            )
        })

    # --------------------------------------------------------
    # ESCANTEIOS
    # --------------------------------------------------------

    cc = dados["cantos_casa"]
    cf = dados["cantos_fora"]

    if cc is not None and cf is not None:

        total = cc + cf

        if total >= 5:

            lado = (
                jogo["casa"]
                if cc > cf
                else jogo["fora"]
            )

            vantagem = abs(cc - cf)

            score = min(
                100,
                55 + (total * 4) + (vantagem * 5)
            )

            sinais.append({
                "tipo": "ESCANTEIOS",
                "lado": lado,
                "score": score,
                "motivo": (
                    f"{total:.0f} escanteios registrados "
                    f"até o momento"
                )
            })

    # --------------------------------------------------------
    # FINALIZAÇÕES
    # --------------------------------------------------------

    fc = dados["final_casa"]
    ff = dados["final_fora"]

    if fc is not None and ff is not None:

        total_final = fc + ff

        if total_final >= 10:

            lado = (
                jogo["casa"]
                if fc > ff
                else jogo["fora"]
            )

            score = min(
                100,
                55 + total_final * 2
            )

            sinais.append({
                "tipo": "VOLUME OFENSIVO",
                "lado": lado,
                "score": score,
                "motivo": (
                    f"{total_final:.0f} finalizações "
                    "registradas"
                )
            })

    # --------------------------------------------------------
    # FINALIZAÇÕES NO GOL
    # --------------------------------------------------------

    gc = dados["gol_casa"]
    gf = dados["gol_fora"]

    if gc is not None and gf is not None:

        total_gol = gc + gf

        if total_gol >= 4:

            lado = (
                jogo["casa"]
                if gc > gf
                else jogo["fora"]
            )

            score = min(
                100,
                60 + total_gol * 5
            )

            sinais.append({
                "tipo": "PERIGO",
                "lado": lado,
                "score": score,
                "motivo": (
                    f"{total_gol:.0f} finalizações "
                    "no alvo"
                )
            })

    # --------------------------------------------------------
    # COMBINAÇÃO PRESSÃO + ESCANTEIOS
    # --------------------------------------------------------

    maior_pressao = max(
        pressao["casa"],
        pressao["fora"]
    )

    if (
        maior_pressao >= 60
        and cc is not None
        and cf is not None
        and (cc + cf) >= 5
    ):

        lado = (
            jogo["casa"]
            if pressao["casa"] > pressao["fora"]
            else jogo["fora"]
        )

        sinais.append({
            "tipo": "PRESSÃO + ESCANTEIOS",
            "lado": lado,
            "score": min(
                100,
                maior_pressao + 10
            ),
            "motivo": (
                "pressão ofensiva combinada "
                "com volume de escanteios"
            )
        })

    sinais.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return sinais


# ============================================================
# TEXTO LIVE
# ============================================================

def texto_live(jogo, dados, sinal):

    minuto = jogo.get("elapsed")

    if minuto:
        tempo = f"{minuto}'"
    else:
        tempo = jogo.get("status", "LIVE")

    texto = []

    texto.append("🔴 LIVE")
    texto.append("")
    texto.append(
        f"🏆 {jogo['liga']}"
    )
    texto.append(
        f"⚽ {jogo['casa']} "
        f"{jogo.get('gols_casa', 0)} x "
        f"{jogo.get('gols_fora', 0)} "
        f"{jogo['fora']}"
    )
    texto.append(
        f"⏱ {tempo}"
    )

    texto.append("")
    texto.append(
        f"🔥 {sinal['tipo']}"
    )
    texto.append(
        f"🎯 Equipe em destaque: {sinal['lado']}"
    )
    texto.append(
        f"📊 Força do sinal: {sinal['score']:.0f}/100"
    )
    texto.append(
        f"📝 {sinal['motivo']}"
    )

    texto.append("")

    if dados["posse_casa"] is not None:
        texto.append(
            f"Possse: "
            f"{jogo['casa']} "
            f"{dados['posse_casa']:.0f}% x "
            f"{dados['posse_fora']:.0f}% "
            f"{jogo['fora']}"
        )

    if (
        dados["final_casa"] is not None
        and dados["final_fora"] is not None
    ):
        texto.append(
            f"🎯 Finalizações: "
            f"{dados['final_casa']:.0f} x "
            f"{dados['final_fora']:.0f}"
        )

    if (
        dados["gol_casa"] is not None
        and dados["gol_fora"] is not None
    ):
        texto.append(
            f"🥅 No alvo: "
            f"{dados['gol_casa']:.0f} x "
            f"{dados['gol_fora']:.0f}"
        )

    if (
        dados["cantos_casa"] is not None
        and dados["cantos_fora"] is not None
    ):
        texto.append(
            f"🚩 Escanteios: "
            f"{dados['cantos_casa']:.0f} x "
            f"{dados['cantos_fora']:.0f}"
        )

    if (
        dados["ataques_casa"] is not None
        and dados["ataques_fora"] is not None
    ):
        texto.append(
            f"⚡ Ataques perigosos: "
            f"{dados['ataques_casa']:.0f} x "
            f"{dados['ataques_fora']:.0f}"
        )

    texto.append("")
    texto.append(
        "⚠️ Sinal estatístico para acompanhamento. "
        "Não é garantia de resultado."
    )

    return "\n".join(texto)


# ============================================================
# EVITAR SPAM
# ============================================================

def alerta_permitido(fixture_id, sinal):

    chave = (
        f"{fixture_id}:"
        f"{sinal['tipo']}:"
        f"{sinal['lado']}"
    )

    agora = time.time()

    ultimo = ULTIMO_ALERTA.get(chave, 0)

    if agora - ultimo < ALERT_COOLDOWN:
        return False

    ULTIMO_ALERTA[chave] = agora

    return True


# ============================================================
# /START
# ============================================================

@bot.message_handler(commands=["start"])
def comando_start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "🤖 BOT ATIVADO\n\n"
        "Agora eu acompanho:\n\n"
        "🟢 Jogos nas próximas 24 horas\n"
        "🔴 Jogos LIVE\n"
        "📊 Retrospecto dos times\n"
        "⚽ Gols\n"
        "🎯 Finalizações\n"
        "🥅 Finalizações no alvo\n"
        "🚩 Escanteios\n"
        "⚡ Ataques perigosos\n"
        "🔥 Pressão durante o jogo\n\n"
        "Quando uma situação estatística "
        "forte aparecer durante o LIVE, "
        "eu envio um alerta.\n\n"
        "Use /jogos para testar agora."
    )


# ============================================================
# /JOGOS
# ============================================================

@bot.message_handler(commands=["jogos"])
def comando_jogos(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "🔎 Procurando jogos LIVE e "
        "das próximas 24 horas..."
    )

    jogos = buscar_jogos()

    if not jogos:

        bot.send_message(
            CHAT_ID,
            "🟡 Nenhum jogo encontrado.\n\n"
            "A busca considera:\n"
            "• próximas 24 horas a partir de agora\n"
            "• jogos LIVE neste momento\n"
            "• todas as ligas configuradas"
        )

        return

    texto = []

    texto.append(
        f"⚽ JOGOS ENCONTRADOS: {len(jogos)}"
    )

    texto.append("")

    for jogo in jogos:

        if jogo["status"] in LIVE_STATUS:

            minuto = jogo.get("elapsed")

            if minuto:
                situacao = (
                    f"🔴 LIVE {minuto}'"
                )
            else:
                situacao = "🔴 LIVE"

            horario = situacao

        else:

            horario = (
                f"🕐 {formatar_data(jogo['data'])}"
            )

        texto.append(
            f"🏆 {jogo['liga']}"
        )

        texto.append(
            f"⚽ {jogo['casa']} x "
            f"{jogo['fora']}"
        )

        texto.append(horario)

        texto.append("")

    bot.send_message(
        CHAT_ID,
        "\n".join(texto)
    )


# ============================================================
# /PREJOGO
# ============================================================

@bot.message_handler(commands=["prejogo"])
def comando_prejogo(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "📋 Buscando jogos das próximas "
        "24 horas e analisando o retrospecto..."
    )

    jogos = buscar_proximos_24h()

    if not jogos:

        bot.send_message(
            CHAT_ID,
            "🟡 Nenhum jogo encontrado "
            "nas próximas 24 horas."
        )

        return

    contador = 0

    for jogo in jogos:

        if jogo["id"] in PREJOGO_ENVIADO:
            continue

        analise = analisar_prejogo(jogo)

        texto = texto_prejogo(
            jogo,
            analise
        )

        bot.send_message(
            CHAT_ID,
            texto
        )

        PREJOGO_ENVIADO.add(
            jogo["id"]
        )

        contador += 1

        # Pequeno intervalo para evitar excesso de requisições
        time.sleep(1)

    if contador == 0:

        bot.send_message(
            CHAT_ID,
            "ℹ️ Os jogos das próximas 24 horas "
            "já foram analisados anteriormente."
        )


# ============================================================
# /ANALISAR
# ============================================================

@bot.message_handler(commands=["analisar"])
def comando_analisar(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    partes = message.text.split(
        maxsplit=1
    )

    if len(partes) < 2:

        bot.send_message(
            CHAT_ID,
            "Use assim:\n\n"
            "/analisar Flamengo"
        )

        return

    nome = partes[1].strip().lower()

    jogos = buscar_jogos()

    encontrados = []

    for jogo in jogos:

        if (
            nome in jogo["casa"].lower()
            or nome in jogo["fora"].lower()
        ):
            encontrados.append(jogo)

    if not encontrados:

        bot.send_message(
            CHAT_ID,
            f"🟡 Não encontrei jogo de "
            f"'{partes[1]}' nas próximas "
            f"24 horas ou LIVE."
        )

        return

    for jogo in encontrados:

        if jogo["status"] in LIVE_STATUS:

            dados = analisar_live(jogo)

            if not dados:

                bot.send_message(
                    CHAT_ID,
                    "🔴 Jogo LIVE encontrado, "
                    "mas as estatísticas ainda "
                    "não estão disponíveis."
                )

                continue

            sinais = gerar_sinais_live(
                jogo,
                dados
            )

            if sinais:

                bot.send_message(
                    CHAT_ID,
                    texto_live(
                        jogo,
                        dados,
                        sinais[0]
                    )
                )

            else:

                bot.send_message(
                    CHAT_ID,
                    f"🔴 LIVE\n\n"
                    f"⚽ {jogo['casa']} x "
                    f"{jogo['fora']}\n\n"
                    "Ainda não encontrei "
                    "um sinal estatístico forte."
                )

        else:

            analise = analisar_prejogo(
                jogo
            )

            bot.send_message(
                CHAT_ID,
                texto_prejogo(
                    jogo,
                    analise
                )
            )


# ============================================================
# /STATUS
# ============================================================

@bot.message_handler(commands=["status"])
def comando_status(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    jogos_live = buscar_jogos_live()

    bot.send_message(
        CHAT_ID,
        "🤖 STATUS DO BOT\n\n"
        f"Monitoramento: "
        f"{'ATIVO 🟢' if BOT_ATIVO else 'PAUSADO 🔴'}\n"
        f"Jogos LIVE: {len(jogos_live)}\n"
        f"Intervalo: {LIVE_INTERVAL}s\n"
        f"Histórico por equipe: "
        f"{HISTORY_MATCHES} jogos\n\n"
        "API-Football: conectada\n"
        f"The Odds API: "
        f"{'configurada' if ODDS_API_KEY else 'não configurada'}"
    )


# ============================================================
# /LIGAS
# ============================================================

@bot.message_handler(commands=["ligas"])
def comando_ligas(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    texto = [
        "🏆 LIGAS MONITORADAS",
        ""
    ]

    for league_id, nome in LEAGUES.items():

        texto.append(
            f"• {nome}"
        )

    bot.send_message(
        CHAT_ID,
        "\n".join(texto)
    )


# ============================================================
# /PARAR
# ============================================================

@bot.message_handler(commands=["parar"])
def comando_parar(message):

    global BOT_ATIVO

    BOT_ATIVO = False

    bot.send_message(
        message.chat.id,
        "⏸ Monitoramento LIVE pausado."
    )


# ============================================================
# /CONTINUAR
# ============================================================

@bot.message_handler(commands=["continuar"])
def comando_continuar(message):

    global BOT_ATIVO
    global CHAT_ID

    CHAT_ID = message.chat.id

    BOT_ATIVO = True

    bot.send_message(
        CHAT_ID,
        "▶️ Monitoramento LIVE ativado novamente."
    )


# ============================================================
# MONITOR AUTOMÁTICO
# ============================================================

def monitorar():

    global BOT_ATIVO

    print("Monitor LIVE iniciado.")

    while True:

        try:

            if not BOT_ATIVO:

                time.sleep(
                    LIVE_INTERVAL
                )

                continue

            if CHAT_ID is None:

                time.sleep(
                    LIVE_INTERVAL
                )

                continue

            jogos = buscar_jogos()

            print(
                f"Monitor: {len(jogos)} jogos "
                f"encontrados."
            )

            # ------------------------------------------------
            # PRÉ-JOGOS
            # ------------------------------------------------

            for jogo in jogos:

                if jogo["status"] in LIVE_STATUS:
                    continue

                fixture_id = jogo["id"]

                # Só envia automaticamente uma vez
                if fixture_id in PREJOGO_ENVIADO:
                    continue

                try:

                    analise = analisar_prejogo(
                        jogo
                    )

                    bot.send_message(
                        CHAT_ID,
                        texto_prejogo(
                            jogo,
                            analise
                        )
                    )

                    PREJOGO_ENVIADO.add(
                        fixture_id
                    )

                except Exception as e:

                    print(
                        "Erro no pré-jogo:",
                        e
                    )

            # ------------------------------------------------
            # LIVE
            # ------------------------------------------------

            lives = [
                jogo
                for jogo in jogos
                if jogo["status"] in LIVE_STATUS
            ]

            for jogo in lives:

                try:

                    dados = analisar_live(
                        jogo
                    )

                    if not dados:
                        continue

                    sinais = gerar_sinais_live(
                        jogo,
                        dados
                    )

                    # Só alerta sinais fortes
                    sinais_fortes = [
                        s
                        for s in sinais
                        if s["score"] >= 68
                    ]

                    for sinal in sinais_fortes:

                        if not alerta_permitido(
                            jogo["id"],
                            sinal
                        ):
                            continue

                        bot.send_message(
                            CHAT_ID,
                            texto_live(
                                jogo,
                                dados,
                                sinal
                            )
                        )

                except Exception as e:

                    print(
                        "Erro análise LIVE:",
                        e
                    )

            time.sleep(
                LIVE_INTERVAL
            )

        except Exception as e:

            print(
                "Erro no monitor:",
                e
            )

            time.sleep(30)


# ============================================================
# POLLING TELEGRAM
# ============================================================

def iniciar_telegram():

    print("Telegram polling iniciado.")

    while True:

        try:

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                "Erro Telegram:",
                e
            )

            time.sleep(10)


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    thread_monitor = threading.Thread(
        target=monitorar,
        daemon=True
    )

    thread_monitor.start()

    thread_telegram = threading.Thread(
        target=iniciar_telegram,
        daemon=True
    )

    thread_telegram.start()

    print("Servidor Flask iniciando.")

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )
