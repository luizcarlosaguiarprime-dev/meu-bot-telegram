import os
import time
import threading
import requests
import telebot
from flask import Flask

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
SEGUNDA_API_KEY = os.getenv("SEGUNDA_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)

app = Flask(__name__)

CHAT_ID = None
BOT_ATIVO = True

# ============================================================
# COMPETIÇÕES DE INTERESSE
# ============================================================

LIGAS = [
    "brasileirao",
    "brasileiro",
    "premier league",
    "la liga",
    "bundesliga",
    "serie a",
    "ligue 1",
    "champions league",
    "uefa champions",
    "europa league",
    "conference league",
    "libertadores",
    "sudamericana",
    "copa sudamericana",
]

# ============================================================
# SPORTAPI7
# ============================================================

SPORT_HOST = "sportapi7.p.rapidapi.com"

SPORT_HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": SPORT_HOST
}

SPORT_BASE = "https://" + SPORT_HOST


# ============================================================
# SEGUNDA API
# ============================================================

SEGUNDA_HOST = "free-api-live-football-data.p.rapidapi.com"

SEGUNDA_HEADERS = {
    "x-rapidapi-key": SEGUNDA_API_KEY,
    "x-rapidapi-host": SEGUNDA_HOST
}

SEGUNDA_BASE = "https://" + SEGUNDA_HOST


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def texto_seguro(valor):
    if valor is None:
        return ""
    return str(valor).lower().strip()


def pertence_as_ligas(nome):
    nome = texto_seguro(nome)

    for liga in LIGAS:
        if liga in nome:
            return True

    return False


def nome_jogo(jogo):
    home = jogo.get("home", {})
    away = jogo.get("away", {})

    return (
        home.get("name", "Casa"),
        away.get("name", "Fora")
    )


# ============================================================
# SEGUNDA API — JOGOS AO VIVO
# ============================================================

def buscar_segunda_api_live():

    url = SEGUNDA_BASE + "/football-live"

    try:

        resposta = requests.get(
            url,
            headers=SEGUNDA_HEADERS,
            timeout=15
        )

        print("SEGUNDA API STATUS:", resposta.status_code)

        if resposta.status_code != 200:
            print("SEGUNDA API ERRO:", resposta.text[:500])
            return []

        dados = resposta.json()

        if dados.get("status") != "success":
            return []

        response = dados.get("response", {})

        jogos = response.get("live", [])

        print("SEGUNDA API JOGOS:", len(jogos))

        return jogos

    except Exception as e:
        print("ERRO SEGUNDA API:", e)
        return []


# ============================================================
# SPORTAPI7 — DETALHES DA PARTIDA
# ============================================================

def buscar_detalhes_sportapi(event_id):

    url = f"{SPORT_BASE}/api/v1/event/{event_id}"

    try:

        resposta = requests.get(
            url,
            headers=SPORT_HEADERS,
            timeout=15
        )

        if resposta.status_code != 200:
            return {}

        dados = resposta.json()

        return dados.get("event", dados)

    except Exception as e:
        print("ERRO DETALHES SPORTAPI:", e)
        return {}


# ============================================================
# SPORTAPI7 — ESTATÍSTICAS
# ============================================================

def buscar_estatisticas(event_id):

    url = f"{SPORT_BASE}/api/v1/event/{event_id}/statistics"

    try:

        resposta = requests.get(
            url,
            headers=SPORT_HEADERS,
            timeout=15
        )

        if resposta.status_code != 200:
            print(
                "STAT STATUS:",
                resposta.status_code,
                resposta.text[:200]
            )
            return {}

        dados = resposta.json()

        return dados

    except Exception as e:
        print("ERRO ESTATISTICAS:", e)
        return {}


# ============================================================
# EXTRAIR NÚMEROS DAS ESTATÍSTICAS
# ============================================================

def encontrar_numero(obj, palavras):

    if isinstance(obj, dict):

        for chave, valor in obj.items():

            chave_txt = texto_seguro(chave)

            if any(p in chave_txt for p in palavras):

                if isinstance(valor, (int, float)):
                    return float(valor)

                if isinstance(valor, str):

                    valor_limpo = (
                        valor.replace("%", "")
                        .replace(",", ".")
                    )

                    try:
                        return float(valor_limpo)
                    except:
                        pass

            resultado = encontrar_numero(
                valor,
                palavras
            )

            if resultado is not None:
                return resultado

    elif isinstance(obj, list):

        for item in obj:

            resultado = encontrar_numero(
                item,
                palavras
            )

            if resultado is not None:
                return resultado

    return None


# ============================================================
# ANALISAR PRESSÃO
# ============================================================

def calcular_pressao(estatisticas):

    posse = encontrar_numero(
        estatisticas,
        ["ball possession", "possession"]
    )

    finalizacoes = encontrar_numero(
        estatisticas,
        ["shots", "total shots"]
    )

    no_alvo = encontrar_numero(
        estatisticas,
        ["shots on target", "on target"]
    )

    escanteios = encontrar_numero(
        estatisticas,
        ["corner"]
    )

    ataques_perigosos = encontrar_numero(
        estatisticas,
        ["dangerous attack"]
    )

    pontos = 0

    if posse is not None:
        if posse >= 60:
            pontos += 25
        elif posse >= 55:
            pontos += 15

    if finalizacoes is not None:
        if finalizacoes >= 10:
            pontos += 20
        elif finalizacoes >= 7:
            pontos += 12

    if no_alvo is not None:
        if no_alvo >= 4:
            pontos += 25
        elif no_alvo >= 2:
            pontos += 15

    if escanteios is not None:
        if escanteios >= 6:
            pontos += 15
        elif escanteios >= 4:
            pontos += 8

    if ataques_perigosos is not None:
        if ataques_perigosos >= 40:
            pontos += 15
        elif ataques_perigosos >= 25:
            pontos += 8

    return min(pontos, 100)


# ============================================================
# GERAR ANÁLISE
# ============================================================

def gerar_analise(jogo, estatisticas):

    home = jogo.get("home", {})
    away = jogo.get("away", {})

    home_nome = home.get("name", "Casa")
    away_nome = away.get("name", "Fora")

    placar_home = home.get("score", 0)
    placar_away = away.get("score", 0)

    status = jogo.get("status", {})

    minuto = status.get("liveTime", {}).get(
        "short",
        ""
    )

    pressao = calcular_pressao(estatisticas)

    linhas = []

    linhas.append(
        f"⚽ <b>{home_nome} x {away_nome}</b>"
    )

    linhas.append(
        f"📊 Placar: <b>{placar_home} x {placar_away}</b>"
    )

    if minuto:
        linhas.append(
            f"⏱️ Tempo: <b>{minuto}</b>"
        )

    linhas.append(
        f"🔥 Pressão estatística: <b>{pressao}/100</b>"
    )

    # --------------------------------------------------------
    # LEITURA DA PRESSÃO
    # --------------------------------------------------------

    if pressao >= 75:

        linhas.append(
            "🔴 <b>PRESSÃO MUITO ALTA</b>"
        )

        linhas.append(
            "📌 Há sinais estatísticos fortes de domínio."
        )

        linhas.append(
            "🎯 <b>ENTRADA SUGERIDA:</b> analisar mercado "
            "relacionado ao time dominante ou próximo gol."
        )

    elif pressao >= 60:

        linhas.append(
            "🟠 <b>PRESSÃO ALTA</b>"
        )

        linhas.append(
            "📌 O jogo apresenta sinais de pressão."
        )

        linhas.append(
            "🎯 <b>ENTRADA SUGERIDA:</b> aguardar confirmação "
            "antes de entrar."
        )

    else:

        linhas.append(
            "🟢 <b>SEM PRESSÃO SUFICIENTE</b>"
        )

        linhas.append(
            "⏳ <b>SEM ENTRADA — aguardar mais dados.</b>"
        )

    linhas.append("")
    linhas.append(
        "⚠️ Análise estatística. Não é garantia de lucro."
    )

    return "\n".join(linhas), pressao


# ============================================================
# /LIVE
# ============================================================

@bot.message_handler(commands=["live"])
def comando_live(message):

    bot.send_message(
        message.chat.id,
        "🔎 Buscando jogos ao vivo..."
    )

    jogos = buscar_segunda_api_live()

    if not jogos:

        bot.send_message(
            message.chat.id,
            "❌ Nenhum jogo ao vivo encontrado."
        )

        return

    enviados = 0

    for jogo in jogos:

        # ----------------------------------------------------
        # IMPORTANTE:
        # a segunda API fornece leagueId, mas não o nome.
        # Por enquanto mostramos os jogos encontrados.
        # A SportAPI7 será usada para tentar identificar
        # a competição.
        # ----------------------------------------------------

        event_id = jogo.get("id")

        detalhes = buscar_detalhes_sportapi(
            event_id
        )

        nome_liga = ""

        if isinstance(detalhes, dict):

            torneio = detalhes.get(
                "tournament",
                {}
            )

            if isinstance(torneio, dict):

                nome_liga = (
                    torneio.get("name")
                    or torneio.get("uniqueTournament", {}).get("name", "")
                )

        # Se conseguimos identificar a liga,
        # filtramos.
        if nome_liga and not pertence_as_ligas(nome_liga):
            continue

        estatisticas = buscar_estatisticas(
            event_id
        )

        texto, pressao = gerar_analise(
            jogo,
            estatisticas
        )

        bot.send_message(
            message.chat.id,
            texto,
            parse_mode="HTML"
        )

        enviados += 1

        if enviados >= 20:
            break

    if enviados == 0:

        bot.send_message(
            message.chat.id,
            "ℹ️ A segunda API encontrou jogos, "
            "mas não consegui confirmar as competições "
            "pelos dados disponíveis da SportAPI7."
        )


# ============================================================
# /ANALISAR ID
# ============================================================

@bot.message_handler(commands=["analisar"])
def comando_analisar(message):

    partes = message.text.split()

    if len(partes) < 2:

        bot.send_message(
            message.chat.id,
            "Use assim:\n\n"
            "/analisar ID_DO_JOGO"
        )

        return

    try:
        event_id = int(partes[1])

    except:

        bot.send_message(
            message.chat.id,
            "❌ ID inválido."
        )

        return

    bot.send_message(
        message.chat.id,
        "🔎 Analisando partida..."
    )

    jogo = None

    jogos = buscar_segunda_api_live()

    for item in jogos:

        if item.get("id") == event_id:
            jogo = item
            break

    if not jogo:

        bot.send_message(
            message.chat.id,
            "❌ Essa partida não está na lista de jogos ao vivo."
        )

        return

    estatisticas = buscar_estatisticas(
        event_id
    )

    texto, pressao = gerar_analise(
        jogo,
        estatisticas
    )

    bot.send_message(
        message.chat.id,
        texto,
        parse_mode="HTML"
    )


# ============================================================
# /STATUS
# ============================================================

@bot.message_handler(commands=["status"])
def comando_status(message):

    bot.send_message(
        message.chat.id,
        "🤖 <b>BOT ONLINE</b>\n\n"
        "🟢 SportAPI7: configurada\n"
        "🟢 Segunda API: configurada\n"
        "🟢 Monitoramento: disponível\n"
        "📊 Análise: estatística ao vivo\n"
        "❌ EV/Kelly: desativados",
        parse_mode="HTML"
    )


# ============================================================
# /START
# ============================================================

@bot.message_handler(commands=["start"])
def comando_start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🤖 <b>BOT ATIVADO</b>\n\n"
        "⚽ Análise de futebol ao vivo\n"
        "📊 Cruzamento de duas fontes de dados\n"
        "🔥 Pressão, finalizações, posse e escanteios\n\n"
        "Comandos:\n"
        "/live — jogos ao vivo\n"
        "/analisar ID — analisar partida\n"
        "/status — verificar conexão",
        parse_mode="HTML"
    )


# ============================================================
# FLASK
# ============================================================

@app.route("/")
def home():

    return "Bot funcionando!"


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_bot():

    print("BOT TELEGRAM INICIANDO...")

    try:

        bot.remove_webhook()

        time.sleep(2)

        bot.infinity_polling(
            timeout=30,
            long_polling_timeout=30
        )

    except Exception as e:

        print("ERRO TELEGRAM:", e)

        time.sleep(10)

        iniciar_bot()


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    thread = threading.Thread(
        target=iniciar_bot,
        daemon=True
    )

    thread.start()

    porta = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    print(
        f"FLASK INICIADO NA PORTA {porta}"
    )

    app.run(
        host="0.0.0.0",
        port=porta
    )
