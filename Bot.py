import os
import statistics
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import telebot
from flask import Flask


# =========================================================
# CONFIGURAÇÕES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

EV_MINIMO = 3.0
MINIMO_CASAS = 3
KELLY_FRACAO = 0.25
BANCA = 1000.00

FUSO = ZoneInfo("America/Sao_Paulo")

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

MERCADOS = [
    "h2h",
    "totals",
    "btts",
]


# =========================================================
# TELEGRAM
# =========================================================

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN não configurado.")

if not ODDS_API_KEY:
    raise ValueError("ODDS_API_KEY não configurado.")

bot = telebot.TeleBot(BOT_TOKEN)

CHAT_ID = None


# =========================================================
# FLASK PARA O RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot de apostas online."


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def agora_brasilia():
    return datetime.now(FUSO)


def data_amanha():
    return (agora_brasilia() + timedelta(days=1)).date()


def converter_data(data_texto):
    try:
        return datetime.fromisoformat(
            data_texto.replace("Z", "+00:00")
        )
    except Exception:
        return None


def nome_mercado(mercado):
    nomes = {
        "h2h": "Resultado",
        "totals": "Total de gols",
        "btts": "Ambas marcam",
    }

    return nomes.get(mercado, mercado)


def nome_resultado(outcome):
    nome = outcome.get("name", "")

    if nome == "Yes":
        return "Sim"

    if nome == "No":
        return "Não"

    return nome


def chave_outcome(outcome):
    """
    Cria uma chave única para o resultado.

    Para totais:
        Over + ponto
        Under + ponto

    Para os demais:
        nome do resultado
    """

    nome = outcome.get("name", "")
    ponto = outcome.get("point")

    if ponto is not None:
        return f"{nome}_{ponto}"

    return nome


# =========================================================
# PROBABILIDADE SEM MARGEM
# =========================================================

def probabilidades_sem_margem(outcomes):
    """
    Remove a margem da casa de apostas.

    Para H2H e BTTS:
        normaliza todos os resultados juntos.

    Para TOTALS:
        normaliza Over/Under apenas dentro da mesma linha.
        Exemplo:
            Over 2.5 + Under 2.5
        não mistura com:
            Over 3.5 + Under 3.5
    """

    if not outcomes:
        return {}

    grupos = {}

    for outcome in outcomes:
        odd = outcome.get("price")

        if not isinstance(odd, (int, float)):
            continue

        if odd <= 1:
            continue

        ponto = outcome.get("point")

        if ponto is None:
            grupo = "sem_ponto"
        else:
            grupo = str(ponto)

        if grupo not in grupos:
            grupos[grupo] = []

        grupos[grupo].append(outcome)

    probabilidades = {}

    for grupo, lista in grupos.items():

        inversas = []

        for outcome in lista:
            odd = outcome.get("price")

            if isinstance(odd, (int, float)) and odd > 1:
                inversas.append(1 / odd)

        soma = sum(inversas)

        if soma <= 0:
            continue

        for outcome in lista:

            odd = outcome.get("price")

            if not isinstance(odd, (int, float)):
                continue

            if odd <= 1:
                continue

            probabilidade = (1 / odd) / soma

            probabilidades[
                chave_outcome(outcome)
            ] = probabilidade

    return probabilidades


# =========================================================
# BUSCA DE ODDS
# =========================================================

def buscar_odds(esporte):
    """
    Faz uma única consulta por campeonato trazendo
    os três mercados.

    Isso evita fazer 3 chamadas separadas para cada campeonato.
    """

    url = (
        f"https://api.the-odds-api.com/v4/sports/"
        f"{esporte}/odds"
    )

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": ",".join(MERCADOS),
        "oddsFormat": "decimal",
    }

    try:

        resposta = requests.get(
            url,
            params=params,
            timeout=30
        )

        if resposta.status_code != 200:
            return []

        dados = resposta.json()

        if not isinstance(dados, list):
            return []

        return dados

    except Exception:
        return []


# =========================================================
# COLETA E ORGANIZAÇÃO DOS JOGOS
# =========================================================

def coletar_jogos():
    """
    Busca os jogos de amanhã em todos os campeonatos.
    """

    jogos = {}

    amanha = data_amanha()

    for esporte in ESPORTES:

        eventos = buscar_odds(esporte)

        for evento in eventos:

            commence_time = evento.get("commence_time")

            if not commence_time:
                continue

            data_evento = converter_data(commence_time)

            if not data_evento:
                continue

            data_evento_brasilia = data_evento.astimezone(FUSO)

            if data_evento_brasilia.date() != amanha:
                continue

            jogo_id = evento.get("id")

            if not jogo_id:
                continue

            if jogo_id not in jogos:

                jogos[jogo_id] = {
                    "id": jogo_id,
                    "home_team": evento.get(
                        "home_team",
                        ""
                    ),
                    "away_team": evento.get(
                        "away_team",
                        ""
                    ),
                    "commence_time": data_evento_brasilia,
                    "markets": {
                        "h2h": {},
                        "totals": {},
                        "btts": {},
                    },
                }

            bookmakers = evento.get(
                "bookmakers",
                []
            )

            for bookmaker in bookmakers:

                casa = bookmaker.get(
                    "title",
                    "Casa"
                )

                mercados = bookmaker.get(
                    "markets",
                    []
                )

                for market in mercados:

                    tipo = market.get("key")

                    if tipo not in MERCADOS:
                        continue

                    outcomes = market.get(
                        "outcomes",
                        []
                    )

                    if not outcomes:
                        continue

                    # ---------------------------------
                    # H2H
                    # ---------------------------------

                    if tipo == "h2h":

                        probabilidades = (
                            probabilidades_sem_margem(
                                outcomes
                            )
                        )

                        for outcome in outcomes:

                            chave = chave_outcome(
                                outcome
                            )

                            odd = outcome.get(
                                "price"
                            )

                            prob = probabilidades.get(
                                chave
                            )

                            if prob is None:
                                continue

                            if not isinstance(
                                odd,
                                (int, float)
                            ):
                                continue

                            jogos[jogo_id][
                                "markets"
                            ][tipo].setdefault(
                                chave,
                                []
                            ).append({
                                "bookmaker": casa,
                                "name": outcome.get(
                                    "name",
                                    ""
                                ),
                                "point": outcome.get(
                                    "point"
                                ),
                                "price": odd,
                                "prob": prob,
                            })

                    # ---------------------------------
                    # TOTALS
                    # ---------------------------------

                    elif tipo == "totals":

                        probabilidades = (
                            probabilidades_sem_margem(
                                outcomes
                            )
                        )

                        for outcome in outcomes:

                            chave = chave_outcome(
                                outcome
                            )

                            odd = outcome.get(
                                "price"
                            )

                            prob = probabilidades.get(
                                chave
                            )

                            if prob is None:
                                continue

                            if not isinstance(
                                odd,
                                (int, float)
                            ):
                                continue

                            jogos[jogo_id][
                                "markets"
                            ][tipo].setdefault(
                                chave,
                                []
                            ).append({
                                "bookmaker": casa,
                                "name": outcome.get(
                                    "name",
                                    ""
                                ),
                                "point": outcome.get(
                                    "point"
                                ),
                                "price": odd,
                                "prob": prob,
                            })

                    # ---------------------------------
                    # BTTS
                    # ---------------------------------

                    elif tipo == "btts":

                        probabilidades = (
                            probabilidades_sem_margem(
                                outcomes
                            )
                        )

                        for outcome in outcomes:

                            chave = chave_outcome(
                                outcome
                            )

                            odd = outcome.get(
                                "price"
                            )

                            prob = probabilidades.get(
                                chave
                            )

                            if prob is None:
                                continue

                            if not isinstance(
                                odd,
                                (int, float)
                            ):
                                continue

                            jogos[jogo_id][
                                "markets"
                            ][tipo].setdefault(
                                chave,
                                []
                            ).append({
                                "bookmaker": casa,
                                "name": outcome.get(
                                    "name",
                                    ""
                                ),
                                "point": outcome.get(
                                    "point"
                                ),
                                "price": odd,
                                "prob": prob,
                            })

    return jogos


# =========================================================
# CONSENSO DE PROBABILIDADE
# =========================================================

def calcular_consenso(odds):
    """
    Usa a mediana das probabilidades das casas.

    A mediana é menos afetada por uma casa muito fora
    do padrão do mercado.
    """

    probabilidades = []

    for item in odds:

        prob = item.get("prob")

        if isinstance(prob, (int, float)):
            probabilidades.append(prob)

    if len(probabilidades) < MINIMO_CASAS:
        return None

    return statistics.median(
        probabilidades
    )


# =========================================================
# MELHOR ODD
# =========================================================

def melhor_odd(odds):
    if not odds:
        return None

    validas = [
        item
        for item in odds
        if isinstance(
            item.get("price"),
            (int, float)
        )
        and item.get("price") > 1
    ]

    if not validas:
        return None

    return max(
        validas,
        key=lambda x: x["price"]
    )


# =========================================================
# KELLY
# =========================================================

def calcular_kelly(
    probabilidade,
    odd
):

    if odd <= 1:
        return 0

    b = odd - 1
    q = 1 - probabilidade

    kelly = (
        (b * probabilidade - q)
        / b
    )

    if kelly < 0:
        kelly = 0

    valor = (
        BANCA
        * kelly
        * KELLY_FRACAO
    )

    return valor


# =========================================================
# ANÁLISE EV+
# =========================================================

def analisar_jogos(jogos):

    oportunidades = []

    for jogo_id, jogo in jogos.items():

        for mercado in MERCADOS:

            mercados = jogo[
                "markets"
            ].get(
                mercado,
                {}
            )

            for chave, odds in mercados.items():

                if len(odds) < MINIMO_CASAS:
                    continue

                consenso = calcular_consenso(
                    odds
                )

                if consenso is None:
                    continue

                melhor = melhor_odd(
                    odds
                )

                if melhor is None:
                    continue

                odd = melhor["price"]

                ev = (
                    consenso * odd - 1
                ) * 100

                if ev < EV_MINIMO:
                    continue

                fair_odd = (
                    1 / consenso
                    if consenso > 0
                    else 0
                )

                stake = calcular_kelly(
                    consenso,
                    odd
                )

                oportunidades.append({
                    "jogo": (
                        f"{jogo['home_team']} "
                        f"x "
                        f"{jogo['away_team']}"
                    ),
                    "data": jogo[
                        "commence_time"
                    ],
                    "mercado": mercado,
                    "resultado": melhor[
                        "name"
                    ],
                    "point": melhor.get(
                        "point"
                    ),
                    "casa": melhor[
                        "bookmaker"
                    ],
                    "odd": odd,
                    "probabilidade": consenso,
                    "fair_odd": fair_odd,
                    "ev": ev,
                    "stake": stake,
                    "casas": len(odds),
                })

    oportunidades.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    return oportunidades[:20]


# =========================================================
# FORMATAÇÃO
# =========================================================

def formatar_oportunidade(
    oportunidade,
    numero
):

    data = oportunidade["data"]

    horario = data.strftime(
        "%d/%m %H:%M"
    )

    resultado = nome_resultado({
        "name": oportunidade[
            "resultado"
        ]
    })

    point = oportunidade.get(
        "point"
    )

    if point is not None:

        resultado = (
            f"{resultado} {point}"
        )

    texto = (
        f"🔥 OPORTUNIDADE EV+ #{numero}\n\n"
        f"⚽ {oportunidade['jogo']}\n"
        f"🕐 {horario}\n"
        f"📊 Mercado: "
        f"{nome_mercado(oportunidade['mercado'])}\n"
        f"🎯 Entrada: {resultado}\n"
        f"🏦 Casa: {oportunidade['casa']}\n"
        f"💰 Odd: {oportunidade['odd']:.2f}\n\n"
        f"📈 Probabilidade estimada: "
        f"{oportunidade['probabilidade'] * 100:.1f}%\n"
        f"📐 Odd justa: "
        f"{oportunidade['fair_odd']:.2f}\n"
        f"💎 EV: "
        f"+{oportunidade['ev']:.2f}%\n"
        f"🏦 Casas analisadas: "
        f"{oportunidade['casas']}\n"
        f"💵 Kelly 25%: "
        f"R$ {oportunidade['stake']:.2f}\n"
    )

    return texto


# =========================================================
# EXECUTAR ANÁLISE
# =========================================================

def executar_analise(chat_id):

    try:

        bot.send_message(
            chat_id,
            "🔎 Analisando as odds disponíveis para amanhã..."
        )

        jogos = coletar_jogos()

        if not jogos:

            bot.send_message(
                chat_id,
                "❌ Não encontrei jogos disponíveis para amanhã."
            )

            return

        oportunidades = analisar_jogos(
            jogos
        )

        if not oportunidades:

            bot.send_message(
                chat_id,
                "🔎 Nenhuma oportunidade EV+ encontrada para amanhã dentro dos filtros definidos."
            )

            return

        bot.send_message(
            chat_id,
            f"✅ Encontrei {len(oportunidades)} oportunidades EV+:"
        )

        for i, oportunidade in enumerate(
            oportunidades,
            start=1
        ):

            try:

                bot.send_message(
                    chat_id,
                    formatar_oportunidade(
                        oportunidade,
                        i
                    )
                )

            except Exception:
                continue

    except Exception as erro:

        bot.send_message(
            chat_id,
            "❌ Ocorreu um erro durante a análise."
        )


# =========================================================
# COMANDO /START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def comando_start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🤖 Bot conectado!\n\n"
        "Comandos disponíveis:\n\n"
        "📊 /odds - analisar oportunidades EV+ de amanhã\n\n"
        "O bot analisa odds de diferentes casas disponíveis na fonte de dados."
    )


# =========================================================
# COMANDO /ODDS
# =========================================================

@bot.message_handler(
    commands=["odds"]
)
def comando_odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    thread = threading.Thread(
        target=executar_analise,
        args=(message.chat.id,)
    )

    thread.daemon = True
    thread.start()


# =========================================================
# OUTRAS MENSAGENS
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def outras_mensagens(message):

    bot.send_message(
        message.chat.id,
        "Use /odds para analisar as oportunidades EV+ de amanhã."
    )


# =========================================================
# ANÁLISE AUTOMÁTICA ÀS 09:00
# =========================================================

def agendamento_diario():

    global CHAT_ID

    while True:

        agora = agora_brasilia()

        if (
            agora.hour == 9
            and agora.minute == 0
        ):

            if CHAT_ID:

                executar_analise(
                    CHAT_ID
                )

            time.sleep(60)

        time.sleep(20)


# =========================================================
# INICIAR TELEGRAM
# =========================================================

def iniciar_bot():

    while True:

        try:

            print(
                "🤖 Iniciando bot do Telegram..."
            )

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True
            )

        except Exception as erro:

            print(
                f"Erro no Telegram: {erro}"
            )

            time.sleep(10)


# =========================================================
# INICIAR
# =========================================================

if __name__ == "__main__":

    threading.Thread(
        target=agendamento_diario,
        daemon=True
    ).start()

    threading.Thread(
        target=iniciar_bot,
        daemon=True
    ).start()

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
