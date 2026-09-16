import os
import time
import requests
import telebot

from flask import Flask
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

EV_MINIMO = 3.0

HORA_ANALISE = 9
MINUTO_ANALISE = 0

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

CHAT_ID = None
ULTIMO_ENVIO = None


# ============================================================
# PRINCIPAIS COMPETIÇÕES
# ============================================================

ESPORTES = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_brazil_campeonato",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_conmebol_libertadores",
    "soccer_conmebol_sudamericana",
]


NOMES_LIGAS = {
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


# ============================================================
# SERVIDOR
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot funcionando!"


def iniciar_servidor():
    app.run(
        host="0.0.0.0",
        port=10000
    )


# ============================================================
# HORÁRIO DE BRASÍLIA
# ============================================================

def agora_brasilia():
    return datetime.now(FUSO_BRASILIA)


def converter_horario(utc_time):

    try:

        dt = datetime.fromisoformat(
            utc_time.replace("Z", "+00:00")
        )

        return dt.astimezone(
            FUSO_BRASILIA
        )

    except Exception:

        return None


def formatar_data_hora(utc_time):

    dt = converter_horario(utc_time)

    if not dt:
        return "Horário não informado"

    return dt.strftime(
        "%d/%m/%Y às %H:%M"
    )


# ============================================================
# NOME DOS MERCADOS
# ============================================================

def nome_mercado(market):

    if market == "h2h":
        return "Vencedor da partida (1X2)"

    if market == "totals":
        return "Mais/Menos 2,5 gols"

    if market == "btts":
        return "Ambas Marcam"

    return market


# ============================================================
# BUSCAR ODDS
# ============================================================

def buscar_odds(esporte, mercado):

    url = (
        f"https://api.the-odds-api.com/v4/"
        f"sports/{esporte}/odds"
    )

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": mercado,
        "oddsFormat": "decimal"
    }

    try:

        resposta = requests.get(
            url,
            params=params,
            timeout=30
        )

        if resposta.status_code == 422:

            print(
                f"⚠️ {esporte} / {mercado}: "
                "mercado não disponível."
            )

            return []

        resposta.raise_for_status()

        dados = resposta.json()

        print(
            f"✅ {NOMES_LIGAS.get(esporte, esporte)} "
            f"- {mercado}: {len(dados)} jogos"
        )

        return dados

    except Exception as e:

        print(
            f"❌ Erro {esporte} / {mercado}: {e}"
        )

        return []


# ============================================================
# BUSCAR TODOS OS JOGOS DE HOJE
# ============================================================

def buscar_jogos_do_dia():

    jogos = {}

    agora = agora_brasilia()

    inicio = agora.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    fim = inicio + timedelta(days=1)

    for esporte in ESPORTES:

        liga = NOMES_LIGAS.get(
            esporte,
            esporte
        )

        for mercado in MERCADOS:

            dados = buscar_odds(
                esporte,
                mercado
            )

            for jogo in dados:

                horario = converter_horario(
                    jogo.get("commence_time")
                )

                if not horario:
                    continue

                if not (
                    inicio <= horario < fim
                ):
                    continue

                jogo_id = jogo.get("id")

                if not jogo_id:
                    continue

                if jogo_id not in jogos:

                    jogos[jogo_id] = {
                        "id": jogo_id,
                        "league": liga,
                        "sport_key": esporte,
                        "home_team": jogo.get(
                            "home_team",
                            "?"
                        ),
                        "away_team": jogo.get(
                            "away_team",
                            "?"
                        ),
                        "commence_time":
                            jogo.get(
                                "commence_time"
                            ),
                        "markets": {}
                    }

                for bookmaker in jogo.get(
                    "bookmakers",
                    []
                ):

                    for market in bookmaker.get(
                        "markets",
                        []
                    ):

                        chave = market.get(
                            "key"
                        )

                        if chave not in MERCADOS:
                            continue

                        if chave not in jogos[jogo_id][
                            "markets"
                        ]:

                            jogos[jogo_id][
                                "markets"
                            ][chave] = []

                        jogos[jogo_id][
                            "markets"
                        ][chave].append({

                            "bookmaker":
                                bookmaker.get(
                                    "title",
                                    "Casa"
                                ),

                            "outcomes":
                                market.get(
                                    "outcomes",
                                    []
                                )
                        })

    return list(jogos.values())


# ============================================================
# PROBABILIDADE DE CADA CASA
# REMOVE A MARGEM (OVERROUND)
# ============================================================

def probabilidades_sem_margem(outcomes):

    probabilidades = {}

    inversas = []

    for outcome in outcomes:

        odd = outcome.get("price")

        if not odd or odd <= 1:
            continue

        inversa = 1 / odd

        inversas.append(
            (outcome.get("name"), inversa)
        )

    soma = sum(
        valor
        for _, valor in inversas
    )

    if soma <= 0:
        return {}

    for nome, valor in inversas:

        probabilidades[nome] = (
            valor / soma
        )

    return probabilidades


# ============================================================
# CONSENSO DO MERCADO
# ============================================================

def calcular_consenso(mercados):

    dados = {}

    for casa in mercados:

        probabilidades = (
            probabilidades_sem_margem(
                casa["outcomes"]
            )
        )

        if not probabilidades:
            continue

        for nome, prob in probabilidades.items():

            if nome not in dados:
                dados[nome] = []

            dados[nome].append(prob)

    consenso = {}

    for nome, valores in dados.items():

        # Exige pelo menos 2 casas
        if len(valores) < 2:
            continue

        consenso[nome] = (
            sum(valores) / len(valores)
        )

    return consenso


# ============================================================
# ENCONTRAR A MELHOR ODD
# ============================================================

def melhor_odd(mercados, selecao):

    melhor = None

    for casa in mercados:

        for outcome in casa["outcomes"]:

            if outcome.get("name") != selecao:
                continue

            odd = outcome.get("price")

            if not odd or odd <= 1:
                continue

            if melhor is None or odd > melhor["odd"]:

                melhor = {
                    "casa": casa["bookmaker"],
                    "odd": odd
                }

    return melhor


# ============================================================
# ANALISAR JOGO
# ============================================================

def analisar_jogo(jogo):

    oportunidades = []

    for mercado, casas in jogo["markets"].items():

        if not casas:
            continue

        consenso = calcular_consenso(
            casas
        )

        if not consenso:
            continue

        for selecao, probabilidade in consenso.items():

            if probabilidade <= 0:
                continue

            melhor = melhor_odd(
                casas,
                selecao
            )

            if not melhor:
                continue

            odd = melhor["odd"]

            odd_justa = (
                1 / probabilidade
            )

            ev = (
                (probabilidade * odd) - 1
            ) * 100

            if ev < EV_MINIMO:
                continue

            oportunidades.append({

                "jogo": jogo,

                "mercado": mercado,

                "selecao": selecao,

                "casa": melhor["casa"],

                "odd": odd,

                "probabilidade":
                    probabilidade,

                "odd_justa":
                    odd_justa,

                "ev": ev

            })

    return oportunidades


# ============================================================
# ANALISAR O DIA
# ============================================================

def analisar_oportunidades():

    print(
        "🔎 ANALISANDO OPORTUNIDADES DO DIA..."
    )

    jogos = buscar_jogos_do_dia()

    print(
        f"⚽ Total de jogos encontrados: "
        f"{len(jogos)}"
    )

    todas = []

    for jogo in jogos:

        oportunidades = analisar_jogo(
            jogo
        )

        todas.extend(
            oportunidades
        )

    todas.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    # Remove duplicidades
    resultado = []

    usadas = set()

    for oportunidade in todas:

        jogo = oportunidade["jogo"]

        chave = (
            jogo["id"],
            oportunidade["mercado"],
            oportunidade["selecao"]
        )

        if chave in usadas:
            continue

        usadas.add(chave)

        resultado.append(
            oportunidade
        )

    # Até 20 oportunidades
    return resultado[:20]


# ============================================================
# FORMATAR MENSAGEM
# ============================================================

def formatar_oportunidade(o):

    jogo = o["jogo"]

    horario = formatar_data_hora(
        jogo["commence_time"]
    )

    prob = (
        o["probabilidade"] * 100
    )

    return (
        "🔥 <b>OPORTUNIDADE EV+</b>\n\n"

        f"🏆 <b>LIGA:</b> "
        f"{jogo['league']}\n"

        f"⚽ <b>JOGO:</b> "
        f"{jogo['home_team']} x "
        f"{jogo['away_team']}\n"

        f"📅 <b>DATA/HORA:</b> "
        f"{horario} (Brasília)\n\n"

        f"🎯 <b>MERCADO:</b> "
        f"{nome_mercado(o['mercado'])}\n"

        f"🏦 <b>APOSTAR EM:</b> "
        f"{o['selecao']}\n"

        f"💼 <b>CASA:</b> "
        f"{o['casa']}\n"

        f"💰 <b>ODD:</b> "
        f"{o['odd']:.2f}\n"

        f"📊 <b>PROBABILIDADE:</b> "
        f"{prob:.1f}%\n"

        f"📐 <b>ODD JUSTA:</b> "
        f"{o['odd_justa']:.2f}\n"

        f"📈 <b>EV:</b> "
        f"+{o['ev']:.2f}%\n\n"

        "⚠️ EV baseado no consenso "
        "das odds disponíveis."
    )


# ============================================================
# ENVIO DA ANÁLISE
# ============================================================

def enviar_analise_diaria():

    global ULTIMO_ENVIO

    if CHAT_ID is None:

        print(
            "⚠️ CHAT_ID ainda não configurado."
        )

        return

    hoje = agora_brasilia().date()

    if ULTIMO_ENVIO == hoje:

        print(
            "ℹ️ Análise de hoje já enviada."
        )

        return

    oportunidades = analisar_oportunidades()

    if not oportunidades:

        bot.send_message(
            CHAT_ID,
            "📊 <b>ANÁLISE DIÁRIA</b>\n\n"
            "Hoje não encontrei oportunidades "
            f"com EV de pelo menos {EV_MINIMO:.0f}% "
            "nas principais competições analisadas.\n\n"
            "⚽ Foram considerados os jogos "
            "de hoje disponíveis na API.",
            parse_mode="HTML"
        )

        ULTIMO_ENVIO = hoje

        return

    bot.send_message(
        CHAT_ID,
        "📊 <b>ANÁLISE DIÁRIA — EV+</b>\n\n"
        "🕘 Análise realizada às 09:00 "
        "(horário de Brasília)\n"
        "⚽ Jogos de todo o dia\n"
        "🏆 Principais ligas nacionais "
        "e continentais\n\n"
        f"🔥 <b>{len(oportunidades)}</b> "
        "oportunidades encontradas:",
        parse_mode="HTML"
    )

    for oportunidade in oportunidades:

        try:

            bot.send_message(
                CHAT_ID,
                formatar_oportunidade(
                    oportunidade
                ),
                parse_mode="HTML"
            )

            time.sleep(1)

        except Exception as e:

            print(
                f"❌ Erro enviando mensagem: {e}"
            )

    ULTIMO_ENVIO = hoje

    print(
        "✅ ANÁLISE DIÁRIA ENVIADA."
    )


# ============================================================
# AGENDAMENTO — 09:00 BRASÍLIA
# ============================================================

def verificar_horario():

    print(
        "⏰ Agendamento configurado para "
        "09:00 Brasília."
    )

    while True:

        agora = agora_brasilia()

        if (
            agora.hour == HORA_ANALISE
            and agora.minute == MINUTO_ANALISE
        ):

            enviar_analise_diaria()

            time.sleep(70)

        time.sleep(20)


# ============================================================
# TELEGRAM
# ============================================================

bot = telebot.TeleBot(
    BOT_TOKEN
)


@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.reply_to(
        message,
        "✅ <b>BOT ATIVADO!</b>\n\n"
        "🕘 Vou fazer apenas <b>1 análise "
        "por dia</b>.\n\n"
        "⏰ Horário: <b>09:00 Brasília</b>\n"
        "⚽ Jogos de todo o dia\n"
        "🏆 Principais ligas nacionais "
        "e continentais\n"
        "🔥 Somente oportunidades EV+\n\n"
        "📊 Use /odds se quiser fazer "
        "uma análise manual agora.",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["odds"]
)
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🔎 <b>ANÁLISE MANUAL</b>\n\n"
        "Vou analisar os jogos de hoje "
        "nas principais competições...",
        parse_mode="HTML"
    )

    oportunidades = analisar_oportunidades()

    if not oportunidades:

        bot.send_message(
            message.chat.id,
            "❌ Não encontrei oportunidades "
            f"com EV de pelo menos {EV_MINIMO:.0f}% "
            "nos jogos disponíveis hoje."
        )

        return

    bot.send_message(
        message.chat.id,
        f"🔥 Encontrei <b>{len(oportunidades)}</b> "
        "oportunidades EV+.",
        parse_mode="HTML"
    )

    for oportunidade in oportunidades:

        try:

            bot.send_message(
                message.chat.id,
                formatar_oportunidade(
                    oportunidade
                ),
                parse_mode="HTML"
            )

            time.sleep(1)

        except Exception as e:

            print(
                f"❌ Erro enviando: {e}"
            )


@bot.message_handler(
    func=lambda message: True
)
def qualquer_mensagem(message):

    bot.reply_to(
        message,
        "Use /start para ativar o bot "
        "ou /odds para analisar agora."
    )


# ============================================================
# INICIAR
# ============================================================

if __name__ == "__main__":

    print(
        "🚀 Iniciando sistema..."
    )

    Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    Thread(
        target=verificar_horario,
        daemon=True
    ).start()

    print(
        "🤖 Telegram conectado."
    )

    bot.infinity_polling()
