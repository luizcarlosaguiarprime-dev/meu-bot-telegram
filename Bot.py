import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import telebot
from flask import Flask


# =========================================================
# CONFIGURAÇÕES
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

API_FOOTBALL_KEY = (
    os.environ.get("API_FOOTBALL_KEY")
    or os.environ.get("API_SPORTS_KEY")
    or os.environ.get("APISPORTS_KEY")
)

API_BASE = "https://v3.football.api-sports.io"

# Temporada atual
SEASON = 2026

FUSO = ZoneInfo("America/Sao_Paulo")


# =========================================================
# PRINCIPAIS COMPETIÇÕES
# =========================================================

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

LIGA_IDS = "-".join(str(x) for x in LIGAS.keys())


# =========================================================
# TELEGRAM
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não encontrado no Render.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot de futebol funcionando!"


@app.route("/health")
def health():
    return "OK"


# =========================================================
# CONTROLE DE REQUISIÇÕES
# =========================================================

ultima_requisicao = 0
LOCK_API = threading.Lock()


def controlar_intervalo():
    global ultima_requisicao

    with LOCK_API:
        agora = time.time()

        intervalo = agora - ultima_requisicao

        if intervalo < 7:
            time.sleep(7 - intervalo)

        ultima_requisicao = time.time()


# =========================================================
# CHAMADA API-FOOTBALL
# =========================================================

def api_get(endpoint, params=None):

    controlar_intervalo()

    if not API_FOOTBALL_KEY:
        print("ERRO: API_FOOTBALL_KEY não encontrada.")
        return None

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY
    }

    url = API_BASE + endpoint

    try:

        resposta = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        print(
            f"API {endpoint} | HTTP {resposta.status_code}"
        )

        try:
            dados = resposta.json()
        except Exception:
            print("Erro ao converter resposta da API para JSON.")
            return None

        if resposta.status_code != 200:

            print(
                f"ERRO API HTTP {resposta.status_code}: {dados}"
            )

            return None

        erros = dados.get("errors")

        if erros:

            print(f"API errors: {erros}")

            return None

        return dados

    except Exception as e:

        print(f"Erro de conexão com API: {e}")

        return None


# =========================================================
# DATA ATUAL
# =========================================================

def data_brasilia():

    agora = datetime.now(FUSO)

    return agora.strftime("%Y-%m-%d")


# =========================================================
# JOGOS DO DIA
# =========================================================

def jogos_do_dia():

    data = data_brasilia()

    print(
        f"Buscando jogos de {data} | temporada {SEASON}"
    )

    dados = api_get(
        "/fixtures",
        {
            "date": data,
            "season": SEASON,
            "timezone": "America/Sao_Paulo"
        }
    )

    if not dados:
        return []

    jogos = []

    for jogo in dados.get("response", []):

        liga_id = jogo.get("league", {}).get("id")

        # Proteção extra:
        # somente competições permitidas
        if liga_id not in LIGAS:
            continue

        jogos.append(jogo)

    return jogos


# =========================================================
# ÚLTIMOS 10 JOGOS
# =========================================================

def ultimos_10(team_id):

    dados = api_get(
        "/fixtures",
        {
            "team": team_id,
            "last": 10,
            "season": SEASON
        }
    )

    if not dados:
        return []

    jogos = []

    for jogo in dados.get("response", []):

        status = (
            jogo.get("fixture", {})
            .get("status", {})
            .get("short")
        )

        if status not in ["FT", "AET", "PEN"]:
            continue

        jogos.append(jogo)

    return jogos[:10]


# =========================================================
# RESUMO ESTATÍSTICO
# =========================================================

def resumo(jogos, team_id):

    if not jogos:

        return {
            "jogos": 0,
            "vitorias": 0,
            "empates": 0,
            "derrotas": 0,
            "gols_feitos": 0,
            "gols_sofridos": 0,
            "over15": 0,
            "over25": 0,
            "btts": 0,
        }

    vitorias = 0
    empates = 0
    derrotas = 0

    gols_feitos = 0
    gols_sofridos = 0

    over15 = 0
    over25 = 0
    btts = 0

    total = 0

    for jogo in jogos:

        gols_home = (
            jogo.get("goals", {})
            .get("home")
        )

        gols_away = (
            jogo.get("goals", {})
            .get("away")
        )

        if gols_home is None or gols_away is None:
            continue

        total += 1

        home_id = (
            jogo.get("teams", {})
            .get("home", {})
            .get("id")
        )

        away_id = (
            jogo.get("teams", {})
            .get("away", {})
            .get("id")
        )

        if team_id == home_id:

            feitos = gols_home
            sofridos = gols_away

        elif team_id == away_id:

            feitos = gols_away
            sofridos = gols_home

        else:
            continue

        gols_feitos += feitos
        gols_sofridos += sofridos

        if feitos > sofridos:
            vitorias += 1

        elif feitos == sofridos:
            empates += 1

        else:
            derrotas += 1

        total_gols = gols_home + gols_away

        if total_gols >= 2:
            over15 += 1

        if total_gols >= 3:
            over25 += 1

        if gols_home > 0 and gols_away > 0:
            btts += 1

    if total == 0:
        return {
            "jogos": 0,
            "vitorias": 0,
            "empates": 0,
            "derrotas": 0,
            "gols_feitos": 0,
            "gols_sofridos": 0,
            "over15": 0,
            "over25": 0,
            "btts": 0,
        }

    return {
        "jogos": total,
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "gols_feitos": gols_feitos,
        "gols_sofridos": gols_sofridos,
        "over15": round((over15 / total) * 100, 1),
        "over25": round((over25 / total) * 100, 1),
        "btts": round((btts / total) * 100, 1),
    }


# =========================================================
# ANÁLISE PRÉ-JOGO
# =========================================================

def analisar_jogo(jogo):

    home = jogo["teams"]["home"]
    away = jogo["teams"]["away"]

    home_id = home["id"]
    away_id = away["id"]

    print(
        f"Analisando últimos 10: "
        f"{home['name']} x {away['name']}"
    )

    ult_home = ultimos_10(home_id)

    ult_away = ultimos_10(away_id)

    resumo_home = resumo(
        ult_home,
        home_id
    )

    resumo_away = resumo(
        ult_away,
        away_id
    )

    oportunidades = []

    # -----------------------------------------------------
    # OVER 1.5
    # -----------------------------------------------------

    over15 = round(
        (
            resumo_home["over15"]
            + resumo_away["over15"]
        ) / 2,
        1
    )

    oportunidades.append(
        (
            over15,
            "Mais de 1.5 gols",
            f"{over15}%"
        )
    )

    # -----------------------------------------------------
    # OVER 2.5
    # -----------------------------------------------------

    over25 = round(
        (
            resumo_home["over25"]
            + resumo_away["over25"]
        ) / 2,
        1
    )

    oportunidades.append(
        (
            over25,
            "Mais de 2.5 gols",
            f"{over25}%"
        )
    )

    # -----------------------------------------------------
    # AMBAS MARCAM
    # -----------------------------------------------------

    btts = round(
        (
            resumo_home["btts"]
            + resumo_away["btts"]
        ) / 2,
        1
    )

    oportunidades.append(
        (
            btts,
            "Ambas marcam",
            f"{btts}%"
        )
    )

    # -----------------------------------------------------
    # DUPLA CHANCE
    # -----------------------------------------------------

    total_home = resumo_home["jogos"]

    total_away = resumo_away["jogos"]

    if total_home > 0 and total_away > 0:

        taxa_home = (
            (
                resumo_home["vitorias"]
                + resumo_home["empates"]
            )
            / total_home
        ) * 100

        taxa_away = (
            (
                resumo_away["vitorias"]
                + resumo_away["empates"]
            )
            / total_away
        ) * 100

        dupla_chance = round(
            (taxa_home + taxa_away) / 2,
            1
        )

    else:

        dupla_chance = 0

    oportunidades.append(
        (
            dupla_chance,
            "Dupla chance",
            f"{dupla_chance}%"
        )
    )

    # Ordena pelas maiores taxas
    oportunidades.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return {
        "home": home,
        "away": away,
        "resumo_home": resumo_home,
        "resumo_away": resumo_away,
        "oportunidades": oportunidades[:2],
    }


# =========================================================
# FORMATAÇÃO DO JOGO
# =========================================================

def formatar_jogo(jogo):

    fixture = jogo.get("fixture", {})

    data = fixture.get("date")

    try:

        dt = datetime.fromisoformat(
            data.replace("Z", "+00:00")
        )

        dt = dt.astimezone(FUSO)

        horario = dt.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:

        horario = "Horário indisponível"

    liga = jogo.get("league", {})

    home = jogo["teams"]["home"]["name"]

    away = jogo["teams"]["away"]["name"]

    texto = (
        f"🏆 <b>{liga.get('name', 'Competição')}</b>\n"
        f"⚽ <b>{home} x {away}</b>\n"
        f"🕒 {horario} Brasília\n"
    )

    analise = analisar_jogo(jogo)

    rh = analise["resumo_home"]

    ra = analise["resumo_away"]

    texto += "\n"
    texto += "📊 <b>Últimos 10 jogos</b>\n\n"

    texto += (
        f"🔵 <b>{home}</b>\n"
        f"V: {rh['vitorias']} | "
        f"E: {rh['empates']} | "
        f"D: {rh['derrotas']}\n"
        f"Gols: {rh['gols_feitos']} "
        f"marcados / {rh['gols_sofridos']} sofridos\n"
        f"Over 1.5: {rh['over15']}%\n"
        f"Over 2.5: {rh['over25']}%\n"
        f"BTTS: {rh['btts']}%\n\n"
    )

    texto += (
        f"🔴 <b>{away}</b>\n"
        f"V: {ra['vitorias']} | "
        f"E: {ra['empates']} | "
        f"D: {ra['derrotas']}\n"
        f"Gols: {ra['gols_feitos']} "
        f"marcados / {ra['gols_sofridos']} sofridos\n"
        f"Over 1.5: {ra['over15']}%\n"
        f"Over 2.5: {ra['over25']}%\n"
        f"BTTS: {ra['btts']}%\n\n"
    )

    texto += "🔎 <b>Principais indicadores</b>\n"

    for valor, mercado, taxa in analise["oportunidades"]:

        texto += (
            f"• {mercado}: "
            f"<b>{taxa}</b>\n"
        )

    texto += (
        "\n⚠️ Indicadores baseados no histórico "
        "recente. Não representam garantia de resultado."
    )

    return texto


# =========================================================
# JOGOS AO VIVO
# =========================================================

def jogos_live():

    dados = api_get(
        "/fixtures",
        {
            "live": LIGA_IDS,
            "season": SEASON,
            "timezone": "America/Sao_Paulo"
        }
    )

    if not dados:
        return []

    jogos = []

    for jogo in dados.get("response", []):

        liga_id = (
            jogo.get("league", {})
            .get("id")
        )

        # PROTEÇÃO ABSOLUTA:
        # não permitir campeonatos fora da lista
        if liga_id not in LIGAS:
            continue

        jogos.append(jogo)

    return jogos


# =========================================================
# ESTATÍSTICAS AO VIVO
# =========================================================

def estatisticas_live(fixture_id):

    dados = api_get(
        "/fixtures/statistics",
        {
            "fixture": fixture_id
        }
    )

    if not dados:
        return None

    return dados.get("response", [])


# =========================================================
# PEGAR ESTATÍSTICA
# =========================================================

def pegar_stat(stats, nome):

    if not stats:
        return None

    for item in stats:

        estatisticas = item.get(
            "statistics",
            []
        )

        for stat in estatisticas:

            tipo = stat.get("type")

            valor = stat.get("value")

            if tipo == nome:

                if valor is None:
                    return None

                return valor

    return None


# =========================================================
# FORMATAR ESTATÍSTICA
# =========================================================

def formatar_stat(valor):

    if valor is None:
        return "—"

    return str(valor)


# =========================================================
# ANÁLISE AO VIVO
# =========================================================

def analisar_live(jogo):

    fixture_id = (
        jogo.get("fixture", {})
        .get("id")
    )

    home = jogo["teams"]["home"]["name"]

    away = jogo["teams"]["away"]["name"]

    stats = estatisticas_live(
        fixture_id
    )

    if not stats:

        return (
            f"⚽ <b>{home} x {away}</b>\n\n"
            "📊 Estatísticas ao vivo\n"
            "Ainda não disponíveis na API.\n\n"
            "⚠️ Ausência de dados não será "
            "interpretada como zero."
        )

    home_stats = None
    away_stats = None

    if len(stats) >= 1:
        home_stats = stats[0]

    if len(stats) >= 2:
        away_stats = stats[1]

    def buscar(stats_obj, nome):

        if not stats_obj:
            return None

        for item in stats_obj.get(
            "statistics",
            []
        ):

            if item.get("type") == nome:

                return item.get("value")

        return None

    posse_home = buscar(
        home_stats,
        "Ball Possession"
    )

    posse_away = buscar(
        away_stats,
        "Ball Possession"
    )

    chutes_home = buscar(
        home_stats,
        "Total Shots"
    )

    chutes_away = buscar(
        away_stats,
        "Total Shots"
    )

    gol_home = buscar(
        home_stats,
        "Shots on Goal"
    )

    gol_away = buscar(
        away_stats,
        "Shots on Goal"
    )

    corners_home = buscar(
        home_stats,
        "Corner Kicks"
    )

    corners_away = buscar(
        away_stats,
        "Corner Kicks"
    )

    perigosos_home = buscar(
        home_stats,
        "Dangerous Attacks"
    )

    perigosos_away = buscar(
        away_stats,
        "Dangerous Attacks"
    )

    texto = (
        f"🔴 <b>AO VIVO</b>\n\n"
        f"⚽ <b>{home} x {away}</b>\n\n"
        "📊 <b>Estatísticas</b>\n\n"
        f"Posses: "
        f"{formatar_stat(posse_home)} "
        f"x "
        f"{formatar_stat(posse_away)}\n"
        f"Chutes: "
        f"{formatar_stat(chutes_home)} "
        f"x "
        f"{formatar_stat(chutes_away)}\n"
        f"Chutes no gol: "
        f"{formatar_stat(gol_home)} "
        f"x "
        f"{formatar_stat(gol_away)}\n"
        f"Escanteios: "
        f"{formatar_stat(corners_home)} "
        f"x "
        f"{formatar_stat(corners_away)}\n"
        f"Ataques perigosos: "
        f"{formatar_stat(perigosos_home)} "
        f"x "
        f"{formatar_stat(perigosos_away)}\n\n"
        "⚠️ Quando a API não fornece uma "
        "estatística, ela aparece como "
        "— e não como zero."
    )

    return texto


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def start(message):

    texto = (
        "🤖 <b>BOT DE FUTEBOL ATIVADO</b>\n\n"
        "📊 Análise estatística de futebol\n\n"
        "Comandos disponíveis:\n\n"
        "📅 /jogos — jogos de hoje\n"
        "🔴 /live — jogos ao vivo\n"
        "🔎 /analisar ID — analisar uma partida\n"
        "📡 /status — verificar API\n\n"
        "🏆 Competições principais\n"
        "🇬🇧 Premier League\n"
        "🇪🇸 La Liga\n"
        "🇩🇪 Bundesliga\n"
        "🇮🇹 Serie A\n"
        "🇫🇷 Ligue 1\n"
        "🏆 Champions League\n"
        "🏆 Europa League\n"
        "🏆 Conference League\n"
        "🇧🇷 Brasileirão\n"
        "🌎 Libertadores\n"
        "🌎 Sul-Americana"
    )

    bot.reply_to(
        message,
        texto
    )


# =========================================================
# /STATUS
# =========================================================

@bot.message_handler(commands=["status"])
def status(message):

    if not API_FOOTBALL_KEY:

        bot.reply_to(
            message,
            "❌ API-Football: chave não encontrada no Render."
        )

        return

    dados = api_get(
        "/status"
    )

    if not dados:

        bot.reply_to(
            message,
            "❌ API-Football não respondeu corretamente."
        )

        return

    resposta = dados.get(
        "response",
        {}
    )

    account = resposta.get(
        "account",
        {}
    )

    subscription = resposta.get(
        "subscription",
        {}
    )

    texto = (
        "📡 <b>STATUS DA API-FOOTBALL</b>\n\n"
        "🟢 API respondeu corretamente.\n\n"
        f"👤 Conta: "
        f"{account.get('firstname', '')} "
        f"{account.get('lastname', '')}\n"
        f"📦 Plano: "
        f"{subscription.get('plan', 'indisponível')}\n"
        f"📅 Temporada configurada: {SEASON}"
    )

    bot.reply_to(
        message,
        texto
    )


# =========================================================
# /JOGOS
# =========================================================

@bot.message_handler(commands=["jogos"])
def jogos(message):

    bot.send_message(
        message.chat.id,
        "🔎 Buscando os jogos de hoje..."
    )

    lista = jogos_do_dia()

    if not lista:

        bot.send_message(
            message.chat.id,
            "❌ Nenhum jogo encontrado nas "
            "competições principais."
        )

        return

    # Limite de segurança
    lista = lista[:20]

    for jogo in lista:

        try:

            texto = formatar_jogo(
                jogo
            )

            bot.send_message(
                message.chat.id,
                texto
            )

        except Exception as e:

            print(
                f"Erro ao formatar jogo: {e}"
            )


# =========================================================
# /LIVE
# =========================================================

@bot.message_handler(commands=["live"])
def live(message):

    bot.send_message(
        message.chat.id,
        "🔴 Buscando partidas ao vivo..."
    )

    lista = jogos_live()

    if not lista:

        bot.send_message(
            message.chat.id,
            "🔴 Nenhuma partida ao vivo "
            "nas competições principais."
        )

        return

    for jogo in lista:

        try:

            texto = analisar_live(
                jogo
            )

            bot.send_message(
                message.chat.id,
                texto
            )

        except Exception as e:

            print(
                f"Erro na análise live: {e}"
            )


# =========================================================
# /ANALISAR ID
# =========================================================

@bot.message_handler(commands=["analisar"])
def analisar_comando(message):

    partes = message.text.split()

    if len(partes) != 2:

        bot.reply_to(
            message,
            "Use assim:\n"
            "<code>/analisar 123456</code>"
        )

        return

    try:

        fixture_id = int(
            partes[1]
        )

    except ValueError:

        bot.reply_to(
            message,
            "❌ ID da partida inválido."
        )

        return

    dados = api_get(
        "/fixtures",
        {
            "id": fixture_id,
            "season": SEASON,
            "timezone": "America/Sao_Paulo"
        }
    )

    if not dados:

        bot.reply_to(
            message,
            "❌ Não consegui encontrar essa partida."
        )

        return

    lista = dados.get(
        "response",
        []
    )

    if not lista:

        bot.reply_to(
            message,
            "❌ Partida não encontrada."
        )

        return

    jogo = lista[0]

    liga_id = (
        jogo.get("league", {})
        .get("id")
    )

    if liga_id not in LIGAS:

        bot.reply_to(
            message,
            "❌ Essa partida não pertence "
            "às competições principais configuradas."
        )

        return

    try:

        texto = formatar_jogo(
            jogo
        )

        bot.send_message(
            message.chat.id,
            texto
        )

    except Exception as e:

        print(
            f"Erro na análise: {e}"
        )

        bot.reply_to(
            message,
            "❌ Erro ao analisar a partida."
        )


# =========================================================
# COMANDO PAUSAR
# =========================================================

bot_ativo = True


@bot.message_handler(commands=["pausar"])
def pausar(message):

    global bot_ativo

    bot_ativo = False

    bot.reply_to(
        message,
        "⏸️ Bot pausado."
    )


# =========================================================
# COMANDO ATIVAR
# =========================================================

@bot.message_handler(commands=["ativar"])
def ativar(message):

    global bot_ativo

    bot_ativo = True

    bot.reply_to(
        message,
        "▶️ Bot ativado novamente."
    )


# =========================================================
# POLLING
# =========================================================

def iniciar_bot():

    print(
        "BOT DE FUTEBOL | API-Sports / API-Football"
    )

    if API_FOOTBALL_KEY:

        print(
            "Variável API_FOOTBALL_KEY encontrada."
        )

    else:

        print(
            "ERRO: API_FOOTBALL_KEY não encontrada."
        )

    while True:

        try:

            print(
                "Removendo webhook do Telegram..."
            )

            bot.remove_webhook()

            time.sleep(3)

            print(
                "Iniciando polling do Telegram..."
            )

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True
            )

        except Exception as e:

            print(
                f"Erro no polling do Telegram: {e}"
            )

            print(
                "Aguardando 10 segundos "
                "antes de tentar novamente..."
            )

            time.sleep(10)


# =========================================================
# EXECUÇÃO
# =========================================================

def iniciar_flask():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


if __name__ == "__main__":

    thread_flask = threading.Thread(
        target=iniciar_flask
    )

    thread_flask.daemon = True

    thread_flask.start()

    iniciar_bot()
