import os
import time
import requests
import telebot

from flask import Flask
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo


# =========================================================
# CONFIGURAÇÕES
# =========================================================

TOKEN = os.environ["BOT_TOKEN"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]

EV_MINIMO = 3.0
INTERVALO_ANALISE = 600

CHAT_ID = None

oportunidades_enviadas = set()


# =========================================================
# BOT E SERVIDOR
# =========================================================

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def iniciar_servidor():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )


# =========================================================
# DATA E HORÁRIO DE BRASÍLIA
# =========================================================

def formatar_data_hora(commence_time):

    if not commence_time:
        return "Horário indisponível"

    try:

        data_utc = datetime.fromisoformat(
            commence_time.replace("Z", "+00:00")
        )

        horario_brasilia = data_utc.astimezone(
            ZoneInfo("America/Sao_Paulo")
        )

        return horario_brasilia.strftime(
            "%d/%m/%Y às %H:%M"
        )

    except Exception:

        return "Horário indisponível"


# =========================================================
# NOME DOS MERCADOS
# =========================================================

def nome_mercado(market_key):

    if market_key == "h2h":
        return "Vencedor da partida (1X2)"

    if market_key == "totals":
        return "Mais/Menos 2,5 gols"

    if market_key == "btts":
        return "Ambas Marcam"

    return market_key


# =========================================================
# PROBABILIDADE DE CONSENSO
# =========================================================

def calcular_consenso(bookmakers, market_key):

    probabilidades = {}

    for bookmaker in bookmakers:

        for mercado in bookmaker.get("markets", []):

            if mercado.get("key") != market_key:
                continue

            outcomes = mercado.get("outcomes", [])

            if not outcomes:
                continue

            probs = {}

            for outcome in outcomes:

                nome = outcome.get("name")
                odd = outcome.get("price")

                if nome and odd and odd > 1:

                    probs[nome] = 1 / odd

            total = sum(probs.values())

            if total <= 0:
                continue

            for nome, prob in probs.items():

                probabilidades.setdefault(
                    nome,
                    []
                ).append(
                    prob / total
                )

    consenso = {}

    for nome, valores in probabilidades.items():

        if valores:

            consenso[nome] = (
                sum(valores) / len(valores)
            )

    return consenso


# =========================================================
# CÁLCULO DO EV
# =========================================================

def calcular_ev(probabilidade, odd):

    return (
        (probabilidade * odd) - 1
    ) * 100


# =========================================================
# ANALISA UM JOGO
# =========================================================

def analisar_jogo(jogo):

    bookmakers = jogo.get("bookmakers", [])

    if len(bookmakers) < 2:
        return []

    oportunidades = []

    mercados = [
        "h2h",
        "totals",
        "btts"
    ]

    for market_key in mercados:

        consenso = calcular_consenso(
            bookmakers,
            market_key
        )

        if not consenso:
            continue

        for bookmaker in bookmakers:

            for mercado in bookmaker.get(
                "markets",
                []
            ):

                if mercado.get("key") != market_key:
                    continue

                for outcome in mercado.get(
                    "outcomes",
                    []
                ):

                    nome = outcome.get("name")
                    odd = outcome.get("price")

                    if not nome:
                        continue

                    if not odd or odd <= 1:
                        continue

                    if nome not in consenso:
                        continue

                    probabilidade = consenso[nome]

                    if probabilidade <= 0:
                        continue

                    odd_justa = 1 / probabilidade

                    ev = calcular_ev(
                        probabilidade,
                        odd
                    )

                    if ev < EV_MINIMO:
                        continue

                    oportunidades.append({

                        "mercado": nome_mercado(
                            market_key
                        ),

                        "apostar_em": nome,

                        "casa": bookmaker.get(
                            "title",
                            "Casa não informada"
                        ),

                        "odd": odd,

                        "prob": probabilidade,

                        "odd_justa": odd_justa,

                        "ev": ev
                    })

    return oportunidades


# =========================================================
# BUSCA ODDS
# =========================================================

def buscar_jogos():

    url = (
        "https://api.the-odds-api.com/v4/"
        "sports/soccer/odds"
    )

    params = {

        "apiKey": ODDS_API_KEY,

        "regions": "eu",

        "markets": "h2h,totals,btts",

        "oddsFormat": "decimal"
    }

    resposta = requests.get(
        url,
        params=params,
        timeout=20
    )

    resposta.raise_for_status()

    return resposta.json()


# =========================================================
# FORMATA A OPORTUNIDADE
# =========================================================

def formatar_oportunidade(
    jogo,
    oportunidade
):

    home = jogo.get(
        "home_team",
        "Time da casa"
    )

    away = jogo.get(
        "away_team",
        "Time visitante"
    )

    data_hora = formatar_data_hora(
        jogo.get("commence_time")
    )

    texto = ""

    texto += "🔥 <b>OPORTUNIDADE EV+</b>\n\n"

    texto += (
        f"⚽ <b>{home} x {away}</b>\n"
    )

    texto += (
        f"📅 <b>{data_hora} (Brasília)</b>\n\n"
    )

    texto += (
        f"🎯 <b>MERCADO: "
        f"{oportunidade['mercado']}</b>\n"
    )

    texto += (
        f"🏦 <b>APOSTAR EM: "
        f"{oportunidade['apostar_em']}</b>\n"
    )

    texto += (
        f"💼 <b>CASA: "
        f"{oportunidade['casa']}</b>\n"
    )

    texto += (
        f"💰 <b>ODD: "
        f"{oportunidade['odd']:.2f}</b>\n"
    )

    texto += (
        f"📊 <b>PROBABILIDADE: "
        f"{oportunidade['prob'] * 100:.1f}%</b>\n"
    )

    texto += (
        f"📐 <b>ODD JUSTA: "
        f"{oportunidade['odd_justa']:.2f}</b>\n"
    )

    texto += (
        f"📈 <b>EV: "
        f"+{oportunidade['ev']:.2f}%</b>\n\n"
    )

    texto += (
        "⚠️ EV baseado no consenso das odds."
    )

    return texto


# =========================================================
# IDENTIFICADOR DA OPORTUNIDADE
# =========================================================

def criar_id_oportunidade(
    jogo,
    oportunidade
):

    return (
        f"{jogo.get('id')}_"
        f"{oportunidade['mercado']}_"
        f"{oportunidade['apostar_em']}_"
        f"{oportunidade['odd']:.2f}"
    )


# =========================================================
# ANÁLISE AUTOMÁTICA
# =========================================================

def analisar_automaticamente():

    global CHAT_ID

    while True:

        try:

            if CHAT_ID is not None:

                jogos = buscar_jogos()

                for jogo in jogos:

                    oportunidades = analisar_jogo(
                        jogo
                    )

                    for oportunidade in oportunidades:

                        identificador = (
                            criar_id_oportunidade(
                                jogo,
                                oportunidade
                            )
                        )

                        if identificador in oportunidades_enviadas:
                            continue

                        oportunidades_enviadas.add(
                            identificador
                        )

                        texto = formatar_oportunidade(
                            jogo,
                            oportunidade
                        )

                        try:

                            bot.send_message(
                                CHAT_ID,
                                texto,
                                parse_mode="HTML"
                            )

                        except Exception as erro:

                            print(
                                "Erro ao enviar alerta:",
                                erro
                            )

        except Exception as erro:

            print(
                "Erro na análise automática:",
                erro
            )

        time.sleep(
            INTERVALO_ANALISE
        )


# =========================================================
# COMANDO START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.reply_to(
        message,
        "Olá! Seu bot está funcionando! 🤖\n\n"
        "✅ Telegram conectado\n"
        "✅ The Odds API conectada\n"
        "✅ Análise automática ativada\n\n"
        "Use /odds para fazer uma análise manual."
    )


# =========================================================
# COMANDO ODDS
# =========================================================

@bot.message_handler(
    commands=["odds"]
)
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    try:

        bot.send_message(
            message.chat.id,
            "🔎 Analisando as odds..."
        )

        jogos = buscar_jogos()

        oportunidades_totais = []

        for jogo in jogos:

            oportunidades = analisar_jogo(
                jogo
            )

            for oportunidade in oportunidades:

                oportunidades_totais.append(
                    (
                        jogo,
                        oportunidade
                    )
                )

        if not oportunidades_totais:

            bot.send_message(
                message.chat.id,
                "🔎 Nenhuma oportunidade encontrada "
                f"com EV ≥ {EV_MINIMO:.1f}% no momento."
            )

            return

        oportunidades_totais.sort(
            key=lambda x: x[1]["ev"],
            reverse=True
        )

        for jogo, oportunidade in oportunidades_totais[:20]:

            texto = formatar_oportunidade(
                jogo,
                oportunidade
            )

            bot.send_message(
                message.chat.id,
                texto,
                parse_mode="HTML"
            )

    except requests.exceptions.HTTPError as erro:

        bot.send_message(
            message.chat.id,
            f"❌ Erro da The Odds API:\n{erro}"
        )

    except Exception as erro:

        bot.send_message(
            message.chat.id,
            f"❌ Erro na análise:\n{erro}"
        )


# =========================================================
# COMANDOS NÃO RECONHECIDOS
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def responder(message):

    bot.reply_to(
        message,
        "🤖 Comando não reconhecido.\n\n"
        "Use /odds para analisar as oportunidades."
    )


# =========================================================
# INICIA FLASK
# =========================================================

Thread(
    target=iniciar_servidor,
    daemon=True
).start()


# =========================================================
# INICIA ANÁLISE AUTOMÁTICA
# =========================================================

Thread(
    target=analisar_automaticamente,
    daemon=True
).start()


# =========================================================
# INICIA TELEGRAM
# =========================================================

bot.infinity_polling()
