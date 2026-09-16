import os
import requests
import telebot
from flask import Flask
from threading import Thread
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["BOT_TOKEN"]
ODDS_API_KEY = os.environ["ODDS_API_KEY"]

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def run():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )


# =========================================================
# CALCULA A PROBABILIDADE DE CONSENSO ENTRE AS CASAS
# =========================================================

def calcular_consenso(bookmakers, market_key):

    probabilidades = {}

    for bookmaker in bookmakers:

        mercados = bookmaker.get("markets", [])

        for mercado in mercados:

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

            if total > 0:

                for nome, prob in probs.items():

                    probabilidades.setdefault(
                        nome, []
                    ).append(prob / total)

    consenso = {}

    for nome, valores in probabilidades.items():

        if valores:
            consenso[nome] = sum(valores) / len(valores)

    return consenso


# =========================================================
# CALCULA EV
# =========================================================

def calcular_ev(probabilidade, odd):

    return (probabilidade * odd - 1) * 100


# =========================================================
# NOME DO MERCADO
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
# INFORMAÇÕES DE CADA MERCADO
# =========================================================

def obter_oportunidades(jogo, market_key):

    bookmakers = jogo.get("bookmakers", [])

    consenso = calcular_consenso(
        bookmakers,
        market_key
    )

    if not consenso:
        return []

    oportunidades = []

    for bookmaker in bookmakers:

        mercados = bookmaker.get("markets", [])

        for mercado in mercados:

            if mercado.get("key") != market_key:
                continue

            for outcome in mercado.get("outcomes", []):

                nome = outcome.get("name")
                odd = outcome.get("price")

                if not nome or not odd or odd <= 1:
                    continue

                if nome not in consenso:
                    continue

                probabilidade = consenso[nome]

                odd_justa = 1 / probabilidade

                ev = calcular_ev(
                    probabilidade,
                    odd
                )

                oportunidades.append({

                    "nome": nome,

                    "odd": odd,

                    "prob": probabilidade,

                    "odd_justa": odd_justa,

                    "ev": ev,

                    "casa": bookmaker.get(
                        "title",
                        "Casa não informada"
                    ),

                    "mercado": nome_mercado(
                        market_key
                    )
                })

    return oportunidades


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def start(message):

    bot.reply_to(
        message,
        "Olá! Seu bot está funcionando! 🤖\n\n"
        "Use /odds para consultar as oportunidades de futebol."
    )


# =========================================================
# /ODDS
# =========================================================

@bot.message_handler(commands=["odds"])
def odds(message):

    try:

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

        jogos = resposta.json()

        if not jogos:

            bot.reply_to(
                message,
                "⚽ Não encontrei jogos disponíveis no momento."
            )

            return

        texto = "🔥 OPORTUNIDADES DE EV+\n\n"

        encontrou_ev = False

        # EV mínimo
        EV_MINIMO = 3.0

        for jogo in jogos:

            home = jogo.get(
                "home_team",
                "Time da casa"
            )

            away = jogo.get(
                "away_team",
                "Time visitante"
            )

            # =============================================
            # DATA E HORÁRIO
            # =============================================

            commence_time = jogo.get(
                "commence_time"
            )

            if commence_time:

                data_utc = datetime.fromisoformat(
                    commence_time.replace(
                        "Z",
                        "+00:00"
                    )
                )

                horario_brasilia = data_utc.astimezone(
                    ZoneInfo("America/Sao_Paulo")
                )

                data_hora = horario_brasilia.strftime(
                    "%d/%m/%Y às %H:%M"
                )

            else:

                data_hora = "Horário indisponível"

            # =============================================
            # MERCADOS
            # =============================================

            todos = []

            for market_key in [
                "h2h",
                "totals",
                "btts"
            ]:

                oportunidades = obter_oportunidades(
                    jogo,
                    market_key
                )

                for oportunidade in oportunidades:

                    if oportunidade["ev"] >= EV_MINIMO:

                        todos.append(
                            oportunidade
                        )

            if not todos:
                continue

            # =============================================
            # ORDENA PELO MAIOR EV
            # =============================================

            todos.sort(
                key=lambda x: x["ev"],
                reverse=True
            )

            encontrou_ev = True

            texto += (
                f"⚽ {home} x {away}\n"
            )

            texto += (
                f"📅 {data_hora} (Brasília)\n"
            )

            texto += "\n"

            # Limita a 5 oportunidades por jogo
            for op in todos[:5]:

                texto += (
                    f"🎯 MERCADO: "
                    f"{op['mercado']}\n"
                )

                texto += (
                    f"🏦 APOSTAR EM: "
                    f"{op['nome']}\n"
                )

                texto += (
                    f"💼 CASA: "
                    f"{op['casa']}\n"
                )

                texto += (
                    f"💰 ODD: "
                    f"{op['odd']:.2f}\n"
                )

                texto += (
                    f"📊 PROBABILIDADE: "
                    f"{op['prob'] * 100:.1f}%\n"
                )

                texto += (
                    f"📐 ODD JUSTA: "
                    f"{op['odd_justa']:.2f}\n"
                )

                texto += (
                    f"📈 EV: "
                    f"+{op['ev']:.2f}%\n"
                )

                texto += (
                    "━━━━━━━━━━━━━━\n"
                )

            texto += "\n"

        # =============================================
        # NENHUMA OPORTUNIDADE
        # =============================================

        if not encontrou_ev:

            texto = (
                "🔎 Nenhuma oportunidade encontrada "
                "com EV de mercado ≥ 3% no momento.\n\n"
                "⚽ Mercados analisados:\n"
                "• Vencedor da partida (1X2)\n"
                "• Mais/Menos 2,5 gols\n"
                "• Ambas Marcam"
            )

        bot.reply_to(
            message,
            texto
        )

    except requests.exceptions.HTTPError as e:

        bot.reply_to(
            message,
            f"❌ Erro da The Odds API:\n{e}"
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"❌ Erro ao consultar as odds:\n{e}"
        )


# =========================================================
# MENSAGENS NÃO RECONHECIDAS
# =========================================================

@bot.message_handler(func=lambda message: True)
def responder(message):

    bot.reply_to(
        message,
        "🤖 Comando não reconhecido.\n\n"
        "Use /odds para consultar as oportunidades."
    )


# =========================================================
# INICIA O SERVIDOR
# =========================================================

Thread(
    target=run
).start()

bot.infinity_polling()
