import os
import time
import threading
import requests
import telebot

from flask import Flask, jsonify


# =========================================================
# CONFIGURAÇÃO
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

HOST = "sportapi7.p.rapidapi.com"

INTERVALO_LIVE = 60
HISTORICO_JOGOS = 15

# Próximas 24 horas
HORAS_A_FRENTE = 24


# =========================================================
# BOT
# =========================================================

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN não configurado no Render.")

if not RAPIDAPI_KEY:
    raise Exception("RAPIDAPI_KEY não configurada no Render.")

bot = telebot.TeleBot(BOT_TOKEN)

app = Flask(__name__)

CHAT_ID = None
MONITORANDO = True


# =========================================================
# SESSÃO RAPIDAPI
# =========================================================

session = requests.Session()

HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": HOST
}


# =========================================================
# REQUISIÇÃO À API
# =========================================================

def api_get(endpoint, params=None):

    url = f"https://{HOST}{endpoint}"

    try:

        resposta = session.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=20
        )

        if resposta.status_code == 200:
            return resposta.json()

        print(
            f"API ERROR {resposta.status_code}: "
            f"{endpoint}"
        )

        return None

    except Exception as e:

        print(f"ERRO API: {e}")

        return None


# =========================================================
# TESTE DA API
# =========================================================

def testar_api():

    dados = api_get("/api/v1/sport/football/live")

    if dados is not None:
        return True

    return False


# =========================================================
# JOGOS AO VIVO
# =========================================================

def obter_jogos_live():

    endpoints = [

        "/api/v1/sport/football/live",

        "/api/v1/sport/football/events/live",

    ]

    for endpoint in endpoints:

        dados = api_get(endpoint)

        if dados:

            if isinstance(dados, dict):

                if "events" in dados:
                    return dados["events"]

                if "data" in dados:
                    return dados["data"]

            if isinstance(dados, list):
                return dados

    return []


# =========================================================
# JOGOS DO DIA
# =========================================================

def obter_jogos_data(data):

    endpoints = [

        f"/api/v1/sport/football/scheduled-events/{data}",

        f"/api/v1/sport/football/events/{data}",

    ]

    for endpoint in endpoints:

        dados = api_get(endpoint)

        if dados:

            if isinstance(dados, dict):

                if "events" in dados:
                    return dados["events"]

                if "data" in dados:
                    return dados["data"]

            if isinstance(dados, list):
                return dados

    return []


# =========================================================
# DADOS DE UMA PARTIDA
# =========================================================

def obter_evento(event_id):

    endpoints = [

        f"/api/v1/event/{event_id}",

    ]

    for endpoint in endpoints:

        dados = api_get(endpoint)

        if dados:
            return dados

    return None


# =========================================================
# ESTATÍSTICAS DA PARTIDA
# =========================================================

def obter_estatisticas(event_id):

    endpoints = [

        f"/api/v1/event/{event_id}/statistics",

    ]

    for endpoint in endpoints:

        dados = api_get(endpoint)

        if dados:
            return dados

    return None


# =========================================================
# SHOTMAP
# =========================================================

def obter_shotmap(event_id, team_id):

    endpoint = (
        f"/api/v1/event/{event_id}/"
        f"shotmap/{team_id}"
    )

    return api_get(endpoint)


# =========================================================
# HISTÓRICO DO TIME
# =========================================================

def obter_historico_time(team_id):

    endpoints = [

        f"/api/v1/team/{team_id}/events/last/0",

        f"/api/v1/team/{team_id}/matches/previous/0",

    ]

    for endpoint in endpoints:

        dados = api_get(endpoint)

        if dados:

            if isinstance(dados, dict):

                if "events" in dados:
                    return dados["events"]

                if "data" in dados:
                    return dados["data"]

            if isinstance(dados, list):
                return dados

    return []


# =========================================================
# NORMALIZAR NOME DO TIME
# =========================================================

def nome_time(time):

    if not time:
        return "Desconhecido"

    if isinstance(time, dict):

        return (
            time.get("name")
            or time.get("shortName")
            or "Desconhecido"
        )

    return str(time)


# =========================================================
# EXTRAIR TIMES
# =========================================================

def extrair_times(evento):

    home = (
        evento.get("homeTeam")
        or evento.get("home")
        or {}
    )

    away = (
        evento.get("awayTeam")
        or evento.get("away")
        or {}
    )

    return home, away


# =========================================================
# STATUS
# =========================================================

def status_evento(evento):

    status = evento.get("status", {})

    if isinstance(status, dict):

        return (
            status.get("type")
            or status.get("description")
            or status.get("code")
            or ""
        )

    return str(status)


# =========================================================
# JOGO AO VIVO?
# =========================================================

def esta_live(evento):

    status = status_evento(evento).lower()

    palavras = [
        "inprogress",
        "in_progress",
        "live",
        "halftime",
        "1h",
        "2h",
        "firsthalf",
        "secondhalf"
    ]

    return any(p in status for p in palavras)


# =========================================================
# SCORE
# =========================================================

def obter_placar(evento):

    home_score = 0
    away_score = 0

    home = evento.get("homeScore", {})
    away = evento.get("awayScore", {})

    if isinstance(home, dict):
        home_score = (
            home.get("current")
            or home.get("display")
            or 0
        )

    if isinstance(away, dict):
        away_score = (
            away.get("current")
            or away.get("display")
            or 0
        )

    return home_score, away_score


# =========================================================
# MINUTO
# =========================================================

def obter_minuto(evento):

    status = evento.get("status", {})

    if isinstance(status, dict):

        minuto = (
            status.get("currentPeriodStartTimestamp")
            or status.get("periodStartTimestamp")
        )

        if minuto:
            return "LIVE"

        descricao = (
            status.get("description")
            or status.get("type")
            or ""
        )

        return str(descricao)

    return "LIVE"


# =========================================================
# EXTRAIR ESTATÍSTICAS
# =========================================================

def extrair_numeros_estatisticas(dados):

    resultado = {
        "posse_home": 0,
        "posse_away": 0,
        "chutes_home": 0,
        "chutes_away": 0,
        "chutes_alvo_home": 0,
        "chutes_alvo_away": 0,
        "escanteios_home": 0,
        "escanteios_away": 0,
        "ataques_home": 0,
        "ataques_away": 0
    }

    if not dados:
        return resultado

    # A API pode retornar blocos diferentes.
    # Percorremos recursivamente procurando os valores.

    def procurar(obj):

        if isinstance(obj, dict):

            for chave, valor in obj.items():

                chave_lower = str(chave).lower()

                if isinstance(valor, (int, float)):

                    if "possession" in chave_lower:
                        pass

                procurar(valor)

        elif isinstance(obj, list):

            for item in obj:
                procurar(item)

    procurar(dados)

    return resultado


# =========================================================
# PRESSÃO
# =========================================================

def calcular_pressao(evento, estatisticas):

    if not estatisticas:
        return 0

    score = 0

    texto = str(estatisticas).lower()

    # Indicadores gerais encontrados no retorno
    # são utilizados como sinais auxiliares.

    if "corner" in texto:
        score += 15

    if "shot" in texto:
        score += 15

    if "possession" in texto:
        score += 10

    if "attack" in texto:
        score += 10

    return min(score, 100)


# =========================================================
# ANÁLISE LIVE
# =========================================================

def analisar_live(evento):

    event_id = (
        evento.get("id")
        or evento.get("eventId")
    )

    if not event_id:
        return None

    home, away = extrair_times(evento)

    nome_home = nome_time(home)
    nome_away = nome_time(away)

    home_id = (
        home.get("id")
        if isinstance(home, dict)
        else None
    )

    away_id = (
        away.get("id")
        if isinstance(away, dict)
        else None
    )

    estatisticas = obter_estatisticas(event_id)

    pressao = calcular_pressao(
        evento,
        estatisticas
    )

    sinais = []

    # =====================================================
    # SHOTMAP
    # =====================================================

    if home_id:

        shot_home = obter_shotmap(
            event_id,
            home_id
        )

        if shot_home:
            sinais.append(
                f"📍 {nome_home}: dados de finalizações disponíveis"
            )

    if away_id:

        shot_away = obter_shotmap(
            event_id,
            away_id
        )

        if shot_away:
            sinais.append(
                f"📍 {nome_away}: dados de finalizações disponíveis"
            )

    # =====================================================
    # SINAIS
    # =====================================================

    if pressao >= 40:

        sinais.append(
            "🔥 Pressão ofensiva detectada"
        )

    if "corner" in str(estatisticas).lower():

        sinais.append(
            "🚩 Dados de escanteios disponíveis"
        )

    if "possession" in str(estatisticas).lower():

        sinais.append(
            "⚽ Dados de posse disponíveis"
        )

    if "shot" in str(estatisticas).lower():

        sinais.append(
            "🎯 Dados de finalizações disponíveis"
        )

    if not sinais:

        sinais.append(
            "ℹ️ Aguardando dados estatísticos suficientes."
        )

    home_score, away_score = obter_placar(evento)

    return {
        "event_id": event_id,
        "home": nome_home,
        "away": nome_away,
        "placar": f"{home_score} x {away_score}",
        "pressao": pressao,
        "sinais": sinais
    }


# =========================================================
# FORMATAR JOGO
# =========================================================

def formatar_jogo(evento):

    home, away = extrair_times(evento)

    nome_home = nome_time(home)
    nome_away = nome_time(away)

    home_score, away_score = obter_placar(evento)

    status = status_evento(evento)

    return (
        f"⚽ <b>{nome_home}</b> x <b>{nome_away}</b>\n"
        f"📊 Placar: {home_score} x {away_score}\n"
        f"⏱ Status: {status}"
    )


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,

        "🤖 <b>BOT DE ANÁLISE LIVE ATIVADO</b>\n\n"

        "Fonte de dados: SportAPI7\n\n"

        "O bot acompanha jogos de futebol e procura "
        "sinais estatísticos durante a partida.\n\n"

        "📌 Comandos:\n\n"

        "/jogos - jogos ao vivo e próximas 24h\n"
        "/live - jogos ao vivo\n"
        "/prejogo - análise pré-jogo\n"
        "/analisar - análise de um jogo\n"
        "/status - status do monitor\n"
        "/parar - parar monitoramento\n"
        "/continuar - continuar monitoramento\n\n"

        "⚠️ As análises são estatísticas e não garantem resultados.",

        parse_mode="HTML"
    )


# =========================================================
# /STATUS
# =========================================================

@bot.message_handler(commands=["status"])
def status_bot(message):

    estado = "ATIVO 🟢" if MONITORANDO else "PAUSADO 🔴"

    bot.send_message(
        message.chat.id,

        f"🤖 <b>Status:</b> {estado}\n"
        f"📡 API: SportAPI7\n"
        f"⏱ Intervalo: {INTERVALO_LIVE}s",

        parse_mode="HTML"
    )


# =========================================================
# /PARAR
# =========================================================

@bot.message_handler(commands=["parar"])
def parar(message):

    global MONITORANDO

    MONITORANDO = False

    bot.send_message(
        message.chat.id,
        "⏸ Monitoramento pausado."
    )


# =========================================================
# /CONTINUAR
# =========================================================

@bot.message_handler(commands=["continuar"])
def continuar(message):

    global MONITORANDO

    MONITORANDO = True

    bot.send_message(
        message.chat.id,
        "▶️ Monitoramento ativado novamente."
    )


# =========================================================
# /LIVE
# =========================================================

@bot.message_handler(commands=["live"])
def live(message):

    jogos = obter_jogos_live()

    if not jogos:

        bot.send_message(
            message.chat.id,
            "🔴 Nenhum jogo ao vivo encontrado pela API."
        )

        return

    texto = "🔴 <b>JOGOS AO VIVO</b>\n\n"

    for jogo in jogos[:30]:

        texto += (
            formatar_jogo(jogo)
            + "\n\n"
        )

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# =========================================================
# /JOGOS
# =========================================================

@bot.message_handler(commands=["jogos"])
def jogos(message):

    # Primeiro: jogos ao vivo

    live = obter_jogos_live()

    # Depois: hoje e amanhã
    from datetime import datetime, timedelta

    agora = datetime.now()

    datas = [
        agora.strftime("%Y-%m-%d"),
        (agora + timedelta(days=1)).strftime("%Y-%m-%d")
    ]

    proximos = []

    for data in datas:

        encontrados = obter_jogos_data(data)

        proximos.extend(encontrados)

    # Evitar duplicados

    todos = {}

    for jogo in live + proximos:

        event_id = (
            jogo.get("id")
            or jogo.get("eventId")
        )

        if event_id:
            todos[str(event_id)] = jogo

    jogos_finais = list(todos.values())

    if not jogos_finais:

        bot.send_message(
            message.chat.id,

            "⚠️ A SportAPI7 não retornou jogos "
            "para o período consultado.\n\n"

            "Isso normalmente significa que precisamos "
            "ajustar o endpoint de calendário da API."
        )

        return

    texto = (
        "⚽ <b>JOGOS ENCONTRADOS</b>\n\n"
    )

    for jogo in jogos_finais[:40]:

        texto += (
            formatar_jogo(jogo)
            + "\n\n"
        )

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# =========================================================
# /PREJOGO
# =========================================================

@bot.message_handler(commands=["prejogo"])
def prejogo(message):

    bot.send_message(
        message.chat.id,

        "📊 <b>ANÁLISE PRÉ-JOGO</b>\n\n"

        "Envie o ID do jogo depois do comando.\n\n"

        "Exemplo:\n"
        "/analisar 123456",

        parse_mode="HTML"
    )


# =========================================================
# /ANALISAR
# =========================================================

@bot.message_handler(commands=["analisar"])
def analisar(message):

    partes = message.text.split()

    if len(partes) < 2:

        bot.send_message(
            message.chat.id,
            "Use assim:\n/analisar ID_DO_JOGO"
        )

        return

    event_id = partes[1]

    evento = obter_evento(event_id)

    if not evento:

        bot.send_message(
            message.chat.id,
            "❌ Não consegui encontrar esse jogo na SportAPI7."
        )

        return

    resultado = analisar_live(evento)

    if not resultado:

        bot.send_message(
            message.chat.id,
            "❌ Não foi possível analisar o jogo."
        )

        return

    texto = (

        "🔎 <b>ANÁLISE DO JOGO</b>\n\n"

        f"⚽ {resultado['home']} x "
        f"{resultado['away']}\n"

        f"📊 Placar: {resultado['placar']}\n\n"

        f"🔥 Índice de pressão: "
        f"{resultado['pressao']}/100\n\n"

        "<b>Sinais encontrados:</b>\n"

    )

    for sinal in resultado["sinais"]:

        texto += f"{sinal}\n"

    texto += (
        "\n⚠️ Análise estatística. "
        "Não representa garantia de resultado."
    )

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# =========================================================
# MONITORAMENTO AUTOMÁTICO
# =========================================================

def monitorar():

    global MONITORANDO

    ultimo_alerta = {}

    while True:

        try:

            if MONITORANDO and CHAT_ID:

                jogos = obter_jogos_live()

                agora = time.time()

                for jogo in jogos:

                    resultado = analisar_live(jogo)

                    if not resultado:
                        continue

                    event_id = resultado["event_id"]

                    pressao = resultado["pressao"]

                    # Só alerta quando houver
                    # algum nível mínimo de sinal.

                    if pressao >= 40:

                        ultimo = ultimo_alerta.get(
                            event_id,
                            0
                        )

                        # Evita spam.
                        # Mesmo jogo: máximo 1 alerta a cada 15 min.

                        if agora - ultimo >= 900:

                            texto = (

                                "🚨 <b>SINAL LIVE</b>\n\n"

                                f"⚽ {resultado['home']} x "
                                f"{resultado['away']}\n"

                                f"📊 Placar: "
                                f"{resultado['placar']}\n\n"

                                f"🔥 Pressão: "
                                f"{resultado['pressao']}/100\n\n"

                            )

                            for sinal in resultado["sinais"]:

                                texto += f"{sinal}\n"

                            texto += (
                                "\n⚠️ Sinal estatístico. "
                                "Não é garantia de resultado."
                            )

                            bot.send_message(
                                CHAT_ID,
                                texto,
                                parse_mode="HTML"
                            )

                            ultimo_alerta[event_id] = agora

            time.sleep(INTERVALO_LIVE)

        except Exception as e:

            print(
                f"ERRO MONITORAMENTO: {e}"
            )

            time.sleep(30)


# =========================================================
# FLASK
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "bot": "Telegram",
        "api": "SportAPI7"
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# =========================================================
# INICIAR BOT
# =========================================================

def iniciar_bot():

    while True:

        try:

            print("Telegram conectado.")

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                f"Erro Telegram: {e}"
            )

            time.sleep(10)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print("===================================")
    print(" BOT SPORTAPI7")
    print("===================================")

    print("API: SportAPI7")
    print("Monitoramento LIVE:", INTERVALO_LIVE, "segundos")

    # Telegram em segundo plano
    threading.Thread(
        target=iniciar_bot,
        daemon=True
    ).start()

    # Monitoramento LIVE
    threading.Thread(
        target=monitorar,
        daemon=True
    ).start()

    # Render
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
