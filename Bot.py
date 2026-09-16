import os
import time
import requests
import telebot

from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# =========================
# CONFIGURAÇÕES
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

EV_MINIMO = 3.0
EV_MAXIMO = 15.0

MINIMO_CASAS = 4

KELLY_FRACAO = 0.25
BANCA = 1000.00

TZ = ZoneInfo("America/Sao_Paulo")

CHAT_ID = None
ULTIMO_ENVIO = None


# =========================
# BOT
# =========================

bot = telebot.TeleBot(BOT_TOKEN)


# =========================
# FLASK / RENDER
# =========================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def iniciar_servidor():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# =========================
# CAMPEONATOS
# =========================

CAMPEONATOS = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_brazil_campeonato": "Brasileirão",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "soccer_conmebol_libertadores": "Libertadores",
    "soccer_conmebol_sudamericana": "Sul-Americana",
}


MERCADOS = [
    "h2h",
    "totals",
    "btts"
]


# =========================
# BUSCAR ODDS
# =========================

def buscar_odds(esporte):

    url = f"https://api.the-odds-api.com/v4/sports/{esporte}/odds"

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": ",".join(MERCADOS),
        "oddsFormat": "decimal"
    }

    resposta = requests.get(url, params=params, timeout=30)

    if resposta.status_code == 401:
        print("Erro 401: chave da Odds API inválida.")
        return []

    if resposta.status_code == 422:
        print("Erro 422: mercado ou campeonato não disponível.")
        return []

    if resposta.status_code != 200:
        print("Erro Odds API:", resposta.status_code)
        return []

    return resposta.json()


# =========================
# PROBABILIDADES
# =========================

def calcular_probabilidades(bookmakers):

    probabilidades = {}

    for bookmaker in bookmakers:

        for mercado in bookmaker.get("markets", []):

            outcomes = mercado.get("outcomes", [])

            if not outcomes:
                continue

            # Soma das probabilidades implícitas
            soma = 0

            for outcome in outcomes:

                odd = outcome.get("price")

                if odd and odd > 1:
                    soma += 1 / odd

            if soma <= 0:
                continue

            # Remove aproximadamente a margem da casa
            for outcome in outcomes:

                nome = outcome.get("name")
                odd = outcome.get("price")

                if not nome or not odd or odd <= 1:
                    continue

                prob = (1 / odd) / soma

                if nome not in probabilidades:
                    probabilidades[nome] = []

                probabilidades[nome].append(prob)

    return probabilidades


# =========================
# ANÁLISE DE UM JOGO
# =========================

def analisar_jogo(evento):

    bookmakers = evento.get("bookmakers", [])

    if len(bookmakers) < MINIMO_CASAS:
        return []

    resultados = []

    # Organizar todos os mercados
    mercados = {}

    for bookmaker in bookmakers:

        for mercado in bookmaker.get("markets", []):

            key = mercado.get("key")

            if key not in mercados:
                mercados[key] = []

            mercados[key].append(bookmaker)

    for mercado_key, casas in mercados.items():

        if len(casas) < MINIMO_CASAS:
            continue

        probabilidades = calcular_probabilidades(casas)

        if not probabilidades:
            continue

        # Encontrar melhor odd para cada resultado
        melhores = {}

        for bookmaker in casas:

            nome_casa = bookmaker.get("title", "Casa")

            for mercado in bookmaker.get("markets", []):

                if mercado.get("key") != mercado_key:
                    continue

                for outcome in mercado.get("outcomes", []):

                    nome = outcome.get("name")
                    odd = outcome.get("price")

                    if not nome or not odd:
                        continue

                    if nome not in melhores or odd > melhores[nome]["odd"]:
                        melhores[nome] = {
                            "odd": odd,
                            "casa": nome_casa
                        }

        for resultado, probs in probabilidades.items():

            # Precisamos de pelo menos 4 casas para formar consenso
            if len(probs) < MINIMO_CASAS:
                continue

            # Média das probabilidades sem extremos
            probs_ordenadas = sorted(probs)

            if len(probs_ordenadas) >= 5:
                probs_filtradas = probs_ordenadas[1:-1]
            else:
                probs_filtradas = probs_ordenadas

            probabilidade = sum(probs_filtradas) / len(probs_filtradas)

            if resultado not in melhores:
                continue

            odd = melhores[resultado]["odd"]
            casa = melhores[resultado]["casa"]

            # Evitar odds extremamente altas
            if odd > 10:
                continue

            odd_justa = 1 / probabilidade

            ev = ((probabilidade * odd) - 1) * 100

            # Filtros
            if ev < EV_MINIMO:
                continue

            if ev > EV_MAXIMO:
                continue

            # A melhor odd precisa estar realmente acima da odd justa
            if odd <= odd_justa:
                continue

            # Kelly
            b = odd - 1
            q = 1 - probabilidade

            kelly = ((b * probabilidade) - q) / b

            if kelly <= 0:
                continue

            stake = BANCA * kelly * KELLY_FRACAO

            # Limite de segurança
            if stake > BANCA * 0.05:
                stake = BANCA * 0.05

            resultados.append({
                "mercado": mercado_key,
                "resultado": resultado,
                "odd": odd,
                "casa": casa,
                "probabilidade": probabilidade,
                "odd_justa": odd_justa,
                "ev": ev,
                "stake": stake,
                "casas": len(probs)
            })

    return resultados


# =========================
# FORMATAR MERCADO
# =========================

def nome_mercado(mercado):

    if mercado == "h2h":
        return "Vencedor da partida (1X2)"

    if mercado == "totals":
        return "Mais/Menos gols"

    if mercado == "btts":
        return "Ambas marcam"

    return mercado


# =========================
# ANALISAR TODOS OS JOGOS
# =========================

def analisar():

    agora = datetime.now(TZ)
    amanha = agora.date() + timedelta(days=1)

    oportunidades = []

    for esporte, liga in CAMPEONATOS.items():

        try:

            eventos = buscar_odds(esporte)

            for evento in eventos:

                data_evento = evento.get("commence_time")

                if not data_evento:
                    continue

                try:
                    data = datetime.fromisoformat(
                        data_evento.replace("Z", "+00:00")
                    ).astimezone(TZ)
                except:
                    continue

                # Somente jogos de amanhã
                if data.date() != amanha:
                    continue

                analises = analisar_jogo(evento)

                for analise in analises:

                    analise["liga"] = liga
                    analise["home"] = evento.get("home_team")
                    analise["away"] = evento.get("away_team")
                    analise["data"] = data

                    oportunidades.append(analise)

        except Exception as e:

            print(f"Erro em {liga}: {e}")

    # Ordenar pelo EV
    oportunidades.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    # Evitar excesso de oportunidades do mesmo jogo
    finais = []
    jogos = set()

    for op in oportunidades:

        chave = (
            op["home"],
            op["away"],
            op["mercado"]
        )

        if chave in jogos:
            continue

        jogos.add(chave)
        finais.append(op)

        if len(finais) >= 15:
            break

    return finais


# =========================
# ENVIAR ANÁLISE
# =========================

def enviar_analise():

    global CHAT_ID

    if CHAT_ID is None:
        print("CHAT_ID ainda não definido.")
        return

    try:

        oportunidades = analisar()

        if not oportunidades:

            bot.send_message(
                CHAT_ID,
                "🔎 Nenhuma oportunidade EV+ encontrada para amanhã dentro dos filtros definidos."
            )

            return

        bot.send_message(
            CHAT_ID,
            f"📊 Foram encontradas {len(oportunidades)} oportunidades EV+."
        )

        for op in oportunidades:

            data = op["data"]

            mensagem = (
                "🔥 *OPORTUNIDADE EV+*\n\n"

                f"🏆 *LIGA:* {op['liga']}\n"
                f"⚽ *JOGO:* {op['home']} x {op['away']}\n"
                f"📅 *DATA/HORA:* {data.strftime('%d/%m/%Y às %H:%M')} (Brasília)\n\n"

                f"🎯 *MERCADO:* {nome_mercado(op['mercado'])}\n"
                f"🏦 *APOSTAR EM:* {op['resultado']}\n"
                f"💼 *MELHOR CASA:* {op['casa']}\n"
                f"💰 *MELHOR ODD:* {op['odd']:.2f}\n\n"

                f"📊 *PROBABILIDADE ESTIMADA:* {op['probabilidade'] * 100:.1f}%\n"
                f"📐 *ODD JUSTA:* {op['odd_justa']:.2f}\n"
                f"📈 *EV ESTIMADO:* +{op['ev']:.2f}%\n"
                f"💵 *STAKE SUGERIDA:* R$ {op['stake']:.2f}\n\n"

                f"🏦 *CASAS ANALISADAS:* {op['casas']}\n\n"

                "📌 Stake baseada em Kelly fracionado (25%).\n"
                "⚠️ EV é uma estimativa baseada nas odds disponíveis e não garante lucro."
            )

            bot.send_message(
                CHAT_ID,
                mensagem,
                parse_mode="Markdown"
            )

            time.sleep(0.5)

    except Exception as e:

        print("Erro na análise:", e)

        bot.send_message(
            CHAT_ID,
            f"❌ Erro durante a análise: {e}"
        )


# =========================
# TELEGRAM
# =========================

@bot.message_handler(commands=["start"])
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "🤖 Bot conectado!\n\n"
        "Use /odds para analisar as oportunidades de amanhã."
    )


@bot.message_handler(commands=["odds"])
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        CHAT_ID,
        "🔎 Analisando as odds disponíveis...\n"
        "Isso pode levar alguns segundos."
    )

    Thread(
        target=enviar_analise
    ).start()


@bot.message_handler(func=lambda message: True)
def qualquer_mensagem(message):

    bot.send_message(
        message.chat.id,
        "Use /odds para executar uma análise ou /start para iniciar o bot."
    )


# =========================
# ENVIO AUTOMÁTICO 09:00
# =========================

def agendador():

    global ULTIMO_ENVIO

    while True:

        try:

            agora = datetime.now(TZ)

            if agora.hour == 9 and agora.minute == 0:

                data_atual = agora.date()

                if ULTIMO_ENVIO != data_atual:

                    enviar_analise()

                    ULTIMO_ENVIO = data_atual

            time.sleep(30)

        except Exception as e:

            print("Erro no agendador:", e)

            time.sleep(30)


# =========================
# INICIAR
# =========================

if __name__ == "__main__":

    Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    Thread(
        target=agendador,
        daemon=True
    ).start()

    print("🤖 Bot iniciado!")

    bot.infinity_polling(
        skip_pending=True
    )
