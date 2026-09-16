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

MERCADOS = [
    "h2h",
    "totals",
    "btts"
]

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
    "soccer_conmebol_sudamericana"
]


# =========================================================
# VERIFICAÇÃO DAS CHAVES
# =========================================================

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN não configurado no Render.")

if not ODDS_API_KEY:
    raise ValueError("ODDS_API_KEY não configurado no Render.")


# =========================================================
# TELEGRAM
# =========================================================

bot = telebot.TeleBot(BOT_TOKEN)

CHAT_ID = None


# =========================================================
# FLASK / RENDER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot de apostas online funcionando."


# =========================================================
# DATA / HORA
# =========================================================

def agora_brasilia():
    return datetime.now(FUSO)


def data_amanha():
    return (
        agora_brasilia() +
        timedelta(days=1)
    ).date()


def converter_data(data_texto):

    try:
        return datetime.fromisoformat(
            data_texto.replace("Z", "+00:00")
        )

    except Exception:
        return None


# =========================================================
# NOME DOS MERCADOS
# =========================================================

def nome_mercado(mercado):

    nomes = {
        "h2h": "Resultado",
        "totals": "Total de gols",
        "btts": "Ambas marcam"
    }

    return nomes.get(
        mercado,
        mercado
    )


def nome_resultado(nome):

    if nome == "Yes":
        return "Sim"

    if nome == "No":
        return "Não"

    return nome


# =========================================================
# CHAVE DO RESULTADO
# =========================================================

def chave_resultado(outcome):

    nome = outcome.get(
        "name",
        ""
    )

    point = outcome.get(
        "point"
    )

    # Para totais:
    # Over 2.5 / Under 2.5
    if point is not None:
        return f"{nome}_{point}"

    return nome


# =========================================================
# PROBABILIDADE SEM MARGEM
# =========================================================

def calcular_probabilidades(outcomes):

    if not outcomes:
        return {}

    grupos = {}

    for outcome in outcomes:

        odd = outcome.get(
            "price"
        )

        if not isinstance(
            odd,
            (int, float)
        ):
            continue

        if odd <= 1:
            continue

        point = outcome.get(
            "point"
        )

        # H2H / BTTS
        if point is None:
            grupo = "principal"

        # Totais:
        # cada linha é calculada separadamente
        else:
            grupo = str(point)

        if grupo not in grupos:
            grupos[grupo] = []

        grupos[grupo].append(
            outcome
        )

    probabilidades = {}

    for grupo, lista in grupos.items():

        inversas = []

        for outcome in lista:

            odd = outcome.get(
                "price"
            )

            if (
                isinstance(
                    odd,
                    (int, float)
                )
                and odd > 1
            ):
                inversas.append(
                    1 / odd
                )

        soma = sum(
            inversas
        )

        if soma <= 0:
            continue

        for outcome in lista:

            odd = outcome.get(
                "price"
            )

            if not isinstance(
                odd,
                (int, float)
            ):
                continue

            if odd <= 1:
                continue

            prob = (
                (1 / odd) / soma
            )

            probabilidades[
                chave_resultado(outcome)
            ] = prob

    return probabilidades


# =========================================================
# BUSCAR ODDS
# =========================================================

def buscar_odds(
    esporte,
    mercado
):

    url = (
        "https://api.the-odds-api.com/v4/"
        f"sports/{esporte}/odds"
    )

    parametros = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": mercado,
        "oddsFormat": "decimal"
    }

    try:

        resposta = requests.get(
            url,
            params=parametros,
            timeout=30
        )

        if resposta.status_code != 200:
            return []

        dados = resposta.json()

        if not isinstance(
            dados,
            list
        ):
            return []

        return dados

    except Exception:
        return []


# =========================================================
# COLETAR JOGOS
# =========================================================

def coletar_jogos():

    jogos = {}

    amanha = data_amanha()

    # -----------------------------------------------------
    # Mantemos a busca separada por mercado.
    # Esta é a estrutura que já funcionava.
    # -----------------------------------------------------

    for esporte in ESPORTES:

        for mercado in MERCADOS:

            eventos = buscar_odds(
                esporte,
                mercado
            )

            for evento in eventos:

                commence_time = evento.get(
                    "commence_time"
                )

                if not commence_time:
                    continue

                data_evento = converter_data(
                    commence_time
                )

                if not data_evento:
                    continue

                data_brasilia = (
                    data_evento.astimezone(
                        FUSO
                    )
                )

                # Somente jogos de amanhã
                if data_brasilia.date() != amanha:
                    continue

                jogo_id = evento.get(
                    "id"
                )

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
                        "commence_time":
                            data_brasilia,
                        "markets": {
                            "h2h": {},
                            "totals": {},
                            "btts": {}
                        }
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

                        tipo = market.get(
                            "key"
                        )

                        if tipo != mercado:
                            continue

                        outcomes = market.get(
                            "outcomes",
                            []
                        )

                        if not outcomes:
                            continue

                        probabilidades = (
                            calcular_probabilidades(
                                outcomes
                            )
                        )

                        for outcome in outcomes:

                            odd = outcome.get(
                                "price"
                            )

                            if not isinstance(
                                odd,
                                (int, float)
                            ):
                                continue

                            if odd <= 1:
                                continue

                            chave = chave_resultado(
                                outcome
                            )

                            prob = probabilidades.get(
                                chave
                            )

                            if prob is None:
                                continue

                            if chave not in jogos[
                                jogo_id
                            ]["markets"][
                                tipo
                            ]:
                                jogos[
                                    jogo_id
                                ]["markets"][
                                    tipo
                                ][chave] = []

                            jogos[
                                jogo_id
                            ]["markets"][
                                tipo
                            ][chave].append({

                                "bookmaker": casa,

                                "name": outcome.get(
                                    "name",
                                    ""
                                ),

                                "point": outcome.get(
                                    "point"
                                ),

                                "price": odd,

                                "prob": prob
                            })

    return jogos


# =========================================================
# CONSENSO DAS CASAS
# =========================================================

def calcular_consenso(odds):

    probabilidades = []

    for item in odds:

        prob = item.get(
            "prob"
        )

        if isinstance(
            prob,
            (int, float)
        ):
            probabilidades.append(
                prob
            )

    if len(probabilidades) < MINIMO_CASAS:
        return None

    # MEDIANA:
    # reduz influência de uma casa muito fora
    # do padrão do mercado.
    return statistics.median(
        probabilidades
    )


# =========================================================
# MELHOR ODD
# =========================================================

def encontrar_melhor_odd(odds):

    validas = []

    for item in odds:

        odd = item.get(
            "price"
        )

        if (
            isinstance(
                odd,
                (int, float)
            )
            and odd > 1
        ):
            validas.append(
                item
            )

    if not validas:
        return None

    return max(
        validas,
        key=lambda item:
            item["price"]
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
# ANALISAR EV+
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

                # Precisamos de pelo menos
                # 3 casas para formar consenso.
                if len(odds) < MINIMO_CASAS:
                    continue

                probabilidade = (
                    calcular_consenso(
                        odds
                    )
                )

                if probabilidade is None:
                    continue

                melhor = encontrar_melhor_odd(
                    odds
                )

                if melhor is None:
                    continue

                odd = melhor[
                    "price"
                ]

                # EV
                ev = (
                    probabilidade
                    * odd
                    - 1
                ) * 100

                if ev < EV_MINIMO:
                    continue

                # Odd justa
                odd_justa = (
                    1 / probabilidade
                )

                # Kelly fracionado
                stake = calcular_kelly(
                    probabilidade,
                    odd
                )

                oportunidades.append({

                    "jogo": (
                        f"{jogo['home_team']} "
                        f"x "
                        f"{jogo['away_team']}"
                    ),

                    "data":
                        jogo[
                            "commence_time"
                        ],

                    "mercado":
                        mercado,

                    "resultado":
                        melhor[
                            "name"
                        ],

                    "point":
                        melhor.get(
                            "point"
                        ),

                    "casa":
                        melhor[
                            "bookmaker"
                        ],

                    "odd":
                        odd,

                    "probabilidade":
                        probabilidade,

                    "odd_justa":
                        odd_justa,

                    "ev":
                        ev,

                    "stake":
                        stake,

                    "casas":
                        len(odds)
                })

    # Maior EV primeiro
    oportunidades.sort(
        key=lambda item:
            item["ev"],
        reverse=True
    )

    return oportunidades[:20]


# =========================================================
# FORMATAR RESULTADO
# =========================================================

def formatar_oportunidade(
    oportunidade,
    numero
):

    data = oportunidade[
        "data"
    ]

    horario = data.strftime(
        "%d/%m/%Y %H:%M"
    )

    resultado = nome_resultado(
        oportunidade[
            "resultado"
        ]
    )

    point = oportunidade.get(
        "point"
    )

    if point is not None:
        resultado = (
            f"{resultado} {point}"
        )

    return (
        f"🔥 EV+ #{numero}\n\n"

        f"⚽ JOGO\n"
        f"{oportunidade['jogo']}\n\n"

        f"🕐 Horário: {horario}\n\n"

        f"🎯 APOSTA\n"
        f"Mercado: "
        f"{nome_mercado(oportunidade['mercado'])}\n"
        f"Entrada: {resultado}\n\n"

        f"🏦 Melhor casa: "
        f"{oportunidade['casa']}\n"
        f"💰 Odd: "
        f"{oportunidade['odd']:.2f}\n\n"

        f"📊 Probabilidade estimada: "
        f"{oportunidade['probabilidade'] * 100:.1f}%\n"

        f"📐 Odd justa: "
        f"{oportunidade['odd_justa']:.2f}\n"

        f"💎 EV: "
        f"+{oportunidade['ev']:.2f}%\n"

        f"🏦 Casas analisadas: "
        f"{oportunidade['casas']}\n"

        f"💵 Kelly 25%: "
        f"R$ {oportunidade['stake']:.2f}"
    )


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

        # -------------------------------------------------
        # IMPORTANTE:
        # agora informamos quantos jogos foram encontrados.
        # Assim sabemos se o problema é coleta ou EV.
        # -------------------------------------------------

        quantidade_jogos = len(
            jogos
        )

        if quantidade_jogos == 0:

            bot.send_message(
                chat_id,
                "❌ Nenhum jogo de amanhã foi encontrado na fonte de odds."
            )

            return

        bot.send_message(
            chat_id,
            f"⚽ {quantidade_jogos} jogos encontrados. "
            f"Calculando as oportunidades EV+..."
        )

        oportunidades = analisar_jogos(
            jogos
        )

        if not oportunidades:

            bot.send_message(
                chat_id,
                "📊 Jogos encontrados, mas nenhuma oportunidade "
                "EV+ passou pelos filtros atuais."
            )

            return

        bot.send_message(
            chat_id,
            f"✅ {len(oportunidades)} oportunidades EV+ encontradas:"
        )

        for numero, oportunidade in enumerate(
            oportunidades,
            start=1
        ):

            bot.send_message(
                chat_id,
                formatar_oportunidade(
                    oportunidade,
                    numero
                )
            )

    except Exception:

        try:

            bot.send_message(
                chat_id,
                "❌ Erro durante a análise."
            )

        except Exception:
            pass


# =========================================================
# /START
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
        "Use /odds para analisar as oportunidades EV+ de amanhã."
    )


# =========================================================
# /ODDS
# =========================================================

@bot.message_handler(
    commands=["odds"]
)
def comando_odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "⏳ Iniciando análise..."
    )

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
        "Use /odds para analisar as oportunidades EV+."
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
# TELEGRAM
# =========================================================

def iniciar_bot():

    while True:

        try:

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True
            )

        except Exception:

            time.sleep(10)


# =========================================================
# INICIALIZAÇÃO
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
