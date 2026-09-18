import os
import re
import time
import unicodedata
import threading
import requests
import telebot
from difflib import SequenceMatcher
from flask import Flask

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
SEGUNDA_API_KEY = os.getenv("SEGUNDA_API_KEY")

_FALTANDO = [
    nome for nome, valor in [
        ("BOT_TOKEN", BOT_TOKEN),
        ("RAPIDAPI_KEY", RAPIDAPI_KEY),
        ("SEGUNDA_API_KEY", SEGUNDA_API_KEY),
    ]
    if not valor
]

if _FALTANDO:
    raise RuntimeError(
        "Variáveis de ambiente faltando: " + ", ".join(_FALTANDO) +
        ". Configure-as no Render (Settings > Environment) antes de iniciar o bot."
    )

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


def safe_get(d, chave, default=None):
    """Como dict.get, mas também protege contra valor explicitamente None
    (dict.get sozinho só usa o default quando a CHAVE não existe)."""
    if not isinstance(d, dict):
        return default
    valor = d.get(chave, default)
    return default if valor is None else valor


def pertence_as_ligas(nome):
    nome = texto_seguro(nome)

    if not nome:
        return False

    for liga in LIGAS:
        if liga in nome:
            return True

    return False


def nome_jogo(jogo):
    home = safe_get(jogo, "home", {})
    away = safe_get(jogo, "away", {})

    return (
        safe_get(home, "name", "Casa"),
        safe_get(away, "name", "Fora")
    )


def normalizar_nome_time(nome):
    """Remove acentos, sufixos comuns (FC, SC...) e pontuação para
    facilitar a comparação de nomes de times entre as duas APIs."""

    if not nome:
        return ""

    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    nome = nome.lower()

    for sufixo in [" fc", " sc", " cf", " afc", " ac", " sap"]:
        if nome.endswith(sufixo):
            nome = nome[: -len(sufixo)]

    nome = re.sub(r"[^a-z0-9 ]", "", nome)

    return nome.strip()


def similaridade(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


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

        if resposta.status_code == 429:
            print("SEGUNDA API: limite de requisições atingido")
            return []

        print("SEGUNDA API STATUS:", resposta.status_code)

        if resposta.status_code != 200:
            print("SEGUNDA API ERRO:", resposta.text[:500])
            return []

        dados = resposta.json()

        if dados.get("status") != "success":
            return []

        response = safe_get(dados, "response", {})

        jogos = safe_get(response, "live", [])

        print("SEGUNDA API JOGOS:", len(jogos))

        return jogos

    except Exception as e:
        print("ERRO SEGUNDA API:", e)
        return []


# ============================================================
# SPORTAPI7 — JOGOS AO VIVO (para cruzamento por nome de time)
# ============================================================

def buscar_eventos_sportapi_live():

    url = f"{SPORT_BASE}/api/v1/sport/football/events/live"

    try:

        resposta = requests.get(
            url,
            headers=SPORT_HEADERS,
            timeout=15
        )

        if resposta.status_code == 429:
            print("SPORTAPI7: limite de requisições atingido")
            return []

        if resposta.status_code != 200:
            print("SPORTAPI7 EVENTS STATUS:", resposta.status_code, resposta.text[:300])
            return []

        dados = resposta.json()

        eventos = safe_get(dados, "events", [])

        print("SPORTAPI7 EVENTOS AO VIVO:", len(eventos))

        return eventos

    except Exception as e:
        print("ERRO SPORTAPI7 EVENTS:", e)
        return []


def encontrar_evento_sportapi(jogo_segunda, eventos_sportapi):
    """Cruza um jogo da Segunda API com a lista de eventos ao vivo da
    SportAPI7 comparando os nomes dos times (os IDs das duas APIs NÃO
    são compatíveis entre si, então não dá pra cruzar por ID)."""

    home_seg, away_seg = nome_jogo(jogo_segunda)
    home_seg = normalizar_nome_time(home_seg)
    away_seg = normalizar_nome_time(away_seg)

    melhor_evento = None
    melhor_pontuacao = 0.0

    for evento in eventos_sportapi:

        home_evt = normalizar_nome_time(
            safe_get(safe_get(evento, "homeTeam", {}), "name", "")
        )
        away_evt = normalizar_nome_time(
            safe_get(safe_get(evento, "awayTeam", {}), "name", "")
        )

        pontuacao = similaridade(home_seg, home_evt) + similaridade(away_seg, away_evt)

        if pontuacao > melhor_pontuacao:
            melhor_pontuacao = pontuacao
            melhor_evento = evento

    # soma de duas similaridades (0 a 2) — exige boa confiança nos dois nomes
    LIMIAR_CONFIANCA = 1.5

    if melhor_evento and melhor_pontuacao >= LIMIAR_CONFIANCA:
        return melhor_evento

    return None


# ============================================================
# SPORTAPI7 — ESTATÍSTICAS
# ============================================================

def buscar_estatisticas(event_id):

    if not event_id:
        return {}

    url = f"{SPORT_BASE}/api/v1/event/{event_id}/statistics"

    try:

        resposta = requests.get(
            url,
            headers=SPORT_HEADERS,
            timeout=15
        )

        if resposta.status_code == 429:
            print("SPORTAPI7: limite de requisições atingido (statistics)")
            return {}

        if resposta.status_code != 200:
            print(
                "STAT STATUS:",
                resposta.status_code,
                resposta.text[:200]
            )
            return {}

        return resposta.json()

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
                    except ValueError:
                        pass

            resultado = encontrar_numero(valor, palavras)

            if resultado is not None:
                return resultado

    elif isinstance(obj, list):

        for item in obj:

            resultado = encontrar_numero(item, palavras)

            if resultado is not None:
                return resultado

    return None


# ============================================================
# ANALISAR PRESSÃO
# ============================================================

def calcular_pressao(estatisticas):

    posse = encontrar_numero(estatisticas, ["ball possession", "possession"])
    finalizacoes = encontrar_numero(estatisticas, ["shots", "total shots"])
    no_alvo = encontrar_numero(estatisticas, ["shots on target", "on target"])
    escanteios = encontrar_numero(estatisticas, ["corner"])
    ataques_perigosos = encontrar_numero(estatisticas, ["dangerous attack"])

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

    home = safe_get(jogo, "home", {})
    away = safe_get(jogo, "away", {})

    home_nome = safe_get(home, "name", "Casa")
    away_nome = safe_get(away, "name", "Fora")

    placar_home = safe_get(home, "score", 0)
    placar_away = safe_get(away, "score", 0)

    status = safe_get(jogo, "status", {})
    live_time = safe_get(status, "liveTime", {})
    minuto = safe_get(live_time, "short", "")

    pressao = calcular_pressao(estatisticas)

    linhas = []

    linhas.append(f"⚽ <b>{home_nome} x {away_nome}</b>")
    linhas.append(f"🆔 ID (use em /analisar): <code>{jogo.get('id')}</code>")
    linhas.append(f"📊 Placar: <b>{placar_home} x {placar_away}</b>")

    if minuto:
        linhas.append(f"⏱️ Tempo: <b>{minuto}</b>")

    if not estatisticas:
        linhas.append("⚠️ Estatísticas indisponíveis para esta partida.")

    linhas.append(f"🔥 Pressão estatística: <b>{pressao}/100</b>")

    if pressao >= 75:
        linhas.append("🔴 <b>PRESSÃO MUITO ALTA</b>")
        linhas.append("📌 Há sinais estatísticos fortes de domínio.")
        linhas.append(
            "🎯 <b>ENTRADA SUGERIDA:</b> analisar mercado "
            "relacionado ao time dominante ou próximo gol."
        )

    elif pressao >= 60:
        linhas.append("🟠 <b>PRESSÃO ALTA</b>")
        linhas.append("📌 O jogo apresenta sinais de pressão.")
        linhas.append(
            "🎯 <b>ENTRADA SUGERIDA:</b> aguardar confirmação "
            "antes de entrar."
        )

    else:
        linhas.append("🟢 <b>SEM PRESSÃO SUFICIENTE</b>")
        linhas.append("⏳ <b>SEM ENTRADA — aguardar mais dados.</b>")

    linhas.append("")
    linhas.append("⚠️ Análise estatística. Não é garantia de lucro.")

    return "\n".join(linhas), pressao


# ============================================================
# /LIVE
# ============================================================

@bot.message_handler(commands=["live"])
def comando_live(message):

    if not BOT_ATIVO:
        bot.send_message(message.chat.id, "⏸️ Bot pausado. Use /ativar para retomar.")
        return

    bot.send_message(message.chat.id, "🔎 Buscando jogos ao vivo...")

    jogos_segunda = buscar_segunda_api_live()

    if not jogos_segunda:
        bot.send_message(message.chat.id, "❌ Nenhum jogo ao vivo encontrado.")
        return

    eventos_sportapi = buscar_eventos_sportapi_live()

    enviados = 0
    sem_correspondencia = 0

    for jogo in jogos_segunda:

        evento_sport = encontrar_evento_sportapi(jogo, eventos_sportapi)

        if not evento_sport:
            sem_correspondencia += 1
            continue

        torneio = safe_get(evento_sport, "tournament", {})
        nome_liga = (
            safe_get(torneio, "name", "")
            or safe_get(safe_get(torneio, "uniqueTournament", {}), "name", "")
        )

        if not pertence_as_ligas(nome_liga):
            continue

        event_id = evento_sport.get("id")

        estatisticas = buscar_estatisticas(event_id)
        time.sleep(0.3)  # evita estourar limite de requisições da RapidAPI

        texto, pressao = gerar_analise(jogo, estatisticas)

        bot.send_message(message.chat.id, texto, parse_mode="HTML")

        enviados += 1

        if enviados >= 20:
            break

    if enviados == 0:
        bot.send_message(
            message.chat.id,
            "ℹ️ Nenhum jogo das ligas monitoradas foi confirmado agora.\n"
            f"({sem_correspondencia} jogo(s) ao vivo não puderam ser cruzados "
            "entre as duas APIs pelos nomes dos times.)"
        )


# ============================================================
# /ANALISAR ID
# ============================================================

@bot.message_handler(commands=["analisar"])
def comando_analisar(message):

    if not BOT_ATIVO:
        bot.send_message(message.chat.id, "⏸️ Bot pausado. Use /ativar para retomar.")
        return

    partes = message.text.split()

    if len(partes) < 2:
        bot.send_message(
            message.chat.id,
            "Use assim:\n\n/analisar ID_DO_JOGO\n\n(o ID aparece em cada jogo listado por /live)"
        )
        return

    try:
        event_id = int(partes[1])
    except ValueError:
        bot.send_message(message.chat.id, "❌ ID inválido.")
        return

    bot.send_message(message.chat.id, "🔎 Analisando partida...")

    jogos = buscar_segunda_api_live()

    jogo = next((item for item in jogos if item.get("id") == event_id), None)

    if not jogo:
        bot.send_message(message.chat.id, "❌ Essa partida não está na lista de jogos ao vivo.")
        return

    eventos_sportapi = buscar_eventos_sportapi_live()
    evento_sport = encontrar_evento_sportapi(jogo, eventos_sportapi)

    estatisticas = {}

    if evento_sport:
        estatisticas = buscar_estatisticas(evento_sport.get("id"))
    else:
        bot.send_message(
            message.chat.id,
            "⚠️ Não consegui cruzar essa partida com a SportAPI7 — a análise "
            "será feita apenas com os dados básicos, sem estatísticas."
        )

    texto, pressao = gerar_analise(jogo, estatisticas)

    bot.send_message(message.chat.id, texto, parse_mode="HTML")


# ============================================================
# /PAUSAR e /ATIVAR
# ============================================================

@bot.message_handler(commands=["pausar"])
def comando_pausar(message):
    global BOT_ATIVO
    BOT_ATIVO = False
    bot.send_message(message.chat.id, "⏸️ Bot pausado. Use /ativar para retomar.")


@bot.message_handler(commands=["ativar"])
def comando_ativar(message):
    global BOT_ATIVO
    BOT_ATIVO = True
    bot.send_message(message.chat.id, "▶️ Bot reativado.")


# ============================================================
# /STATUS
# ============================================================

@bot.message_handler(commands=["status"])
def comando_status(message):

    status_segunda = "🟢 OK"
    status_sport = "🟢 OK"

    try:
        r = requests.get(SEGUNDA_BASE + "/football-live", headers=SEGUNDA_HEADERS, timeout=10)
        if r.status_code != 200:
            status_segunda = f"🔴 erro {r.status_code}"
    except Exception:
        status_segunda = "🔴 sem resposta"

    try:
        r = requests.get(
            SPORT_BASE + "/api/v1/sport/football/events/live",
            headers=SPORT_HEADERS,
            timeout=10
        )
        if r.status_code != 200:
            status_sport = f"🔴 erro {r.status_code}"
    except Exception:
        status_sport = "🔴 sem resposta"

    estado_bot = "🟢 ativo" if BOT_ATIVO else "⏸️ pausado"

    bot.send_message(
        message.chat.id,
        "🤖 <b>STATUS DO BOT</b>\n\n"
        f"{status_sport} SportAPI7\n"
        f"{status_segunda} Segunda API\n"
        f"Bot: {estado_bot}\n"
        "📊 Análise: estatística ao vivo (cruzada entre as duas APIs por nome de time)\n"
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
        "📊 Cruzamento de duas fontes de dados (por nome de time)\n"
        "🔥 Pressão, finalizações, posse e escanteios\n\n"
        "Comandos:\n"
        "/live — jogos ao vivo\n"
        "/analisar ID — analisar partida\n"
        "/status — verificar conexão\n"
        "/pausar — pausar o bot\n"
        "/ativar — reativar o bot",
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

    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            print("ERRO TELEGRAM:", e)
            time.sleep(10)
        # infinity_polling só retorna em erro fatal de conexão;
        # o loop garante reconexão sem empilhar chamadas recursivas


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    thread = threading.Thread(target=iniciar_bot, daemon=True)
    thread.start()

    porta = int(os.environ.get("PORT", 10000))

    print(f"FLASK INICIADO NA PORTA {porta}")

    app.run(host="0.0.0.0", port=porta)
