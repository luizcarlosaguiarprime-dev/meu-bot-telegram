import os
import math
import time
import threading
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import telebot
from flask import Flask


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

FOOTBALL_DATA_TOKEN = os.getenv(
    "FOOTBALL_DATA_TOKEN",
    ""
).strip()

BANCA = 1000.00
KELLY_FRACAO = 0.25

EV_MINIMO = 5.0
EV_MAXIMO = 20.0

MINIMO_CASAS = 3

TIMEZONE = ZoneInfo("America/Sao_Paulo")

ODDS_URL = "https://api.the-odds-api.com/v4/sports/{}/odds"

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"

app = Flask(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

CHAT_ID = None


# ============================================================
# COMPETIÇÕES
# ============================================================

COMPETICOES = [
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


# ============================================================
# UTILIDADES
# ============================================================

def agora_brasilia():
    return datetime.now(TIMEZONE)


def normalizar_nome(nome):

    if not nome:
        return ""

    nome = nome.lower()

    substituicoes = {
        "fc ": "",
        " cf": "",
        " afc": "",
        " sc": "",
        " fc": "",
        " fk ": " ",
        "  ": " ",
    }

    for antigo, novo in substituicoes.items():
        nome = nome.replace(antigo, novo)

    return nome.strip()


def poisson(k, lamb):

    if lamb <= 0:
        return 0

    return (
        math.exp(-lamb)
        * (lamb ** k)
    ) / math.factorial(k)


# ============================================================
# ODDS
# ============================================================

def buscar_odds(esporte, mercado):

    if not ODDS_API_KEY:
        return []

    try:

        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": mercado,
            "oddsFormat": "decimal",
        }

        resposta = requests.get(
            ODDS_URL.format(esporte),
            params=params,
            timeout=25
        )

        if resposta.status_code != 200:
            return []

        dados = resposta.json()

        return (
            dados
            if isinstance(dados, list)
            else []
        )

    except Exception:
        return []


# ============================================================
# PROBABILIDADES DO MERCADO
# ============================================================

def probabilidades_mercado(resultados):

    if not resultados:
        return {}

    probabilidades = {}

    for resultado, odd in resultados.items():

        try:

            odd = float(odd)

            if odd <= 1:
                continue

            probabilidades[resultado] = 1 / odd

        except Exception:
            continue

    soma = sum(probabilidades.values())

    if soma <= 0:
        return {}

    for resultado in probabilidades:
        probabilidades[resultado] /= soma

    return probabilidades


# ============================================================
# COLETA E CONSENSO DAS CASAS
# ============================================================

def coletar_consenso(jogo, mercado):

    casas = {}

    for bookmaker in jogo.get(
        "bookmakers",
        []
    ):

        nome_casa = bookmaker.get(
            "title",
            "Casa"
        )

        for market in bookmaker.get(
            "markets",
            []
        ):

            if market.get("key") != mercado:
                continue

            outcomes = market.get(
                "outcomes",
                []
            )

            # ------------------------------------------------
            # H2H
            # ------------------------------------------------

            if mercado == "h2h":

                odds = {}

                for outcome in outcomes:

                    nome = outcome.get("name")
                    odd = outcome.get("price")

                    if nome and odd:
                        odds[nome] = odd

                probs = probabilidades_mercado(
                    odds
                )

                if probs:

                    casas[nome_casa] = {
                        "odds": odds,
                        "probabilidades": probs
                    }

            # ------------------------------------------------
            # BTTS
            # ------------------------------------------------

            elif mercado == "btts":

                odds = {}

                for outcome in outcomes:

                    nome = outcome.get("name")
                    odd = outcome.get("price")

                    if nome and odd:
                        odds[nome] = odd

                probs = probabilidades_mercado(
                    odds
                )

                if probs:

                    casas[nome_casa] = {
                        "odds": odds,
                        "probabilidades": probs
                    }

            # ------------------------------------------------
            # TOTALS
            # ------------------------------------------------

            elif mercado == "totals":

                grupos = {}

                for outcome in outcomes:

                    nome = outcome.get("name")
                    ponto = outcome.get("point")
                    odd = outcome.get("price")

                    if (
                        nome is None
                        or ponto is None
                        or odd is None
                    ):
                        continue

                    chave = f"{nome}_{ponto}"

                    if chave not in grupos:
                        grupos[chave] = {}

                    grupos[chave][nome] = odd

                for chave, odds in grupos.items():

                    probs = probabilidades_mercado(
                        odds
                    )

                    if probs:

                        casas[
                            f"{nome_casa}_{chave}"
                        ] = {
                            "odds": odds,
                            "probabilidades": probs
                        }

    return casas


# ============================================================
# FOOTBALL-DATA.ORG
# ============================================================

def football_data_request(endpoint):

    if not FOOTBALL_DATA_TOKEN:
        return None

    try:

        headers = {
            "X-Auth-Token":
            FOOTBALL_DATA_TOKEN
        }

        resposta = requests.get(
            FOOTBALL_DATA_URL + endpoint,
            headers=headers,
            timeout=20
        )

        if resposta.status_code != 200:
            return None

        return resposta.json()

    except Exception:
        return None


# ============================================================
# JOGOS DA EQUIPE
# ============================================================

def obter_jogos_equipe(
    team_id,
    quantidade=10
):

    data_final = agora_brasilia().date()

    data_inicial = (
        data_final
        - timedelta(days=120)
    )

    endpoint = (
        f"/teams/{team_id}/matches"
        f"?dateFrom={data_inicial}"
        f"&dateTo={data_final}"
        f"&status=FINISHED"
    )

    dados = football_data_request(
        endpoint
    )

    if not dados:
        return []

    jogos = dados.get(
        "matches",
        []
    )

    jogos = sorted(
        jogos,
        key=lambda x:
        x.get("utcDate", ""),
        reverse=True
    )

    return jogos[:quantidade]


# ============================================================
# FORMA DA EQUIPE
# ============================================================

def calcular_forma_equipe(team_id):

    jogos = obter_jogos_equipe(
        team_id,
        10
    )

    if not jogos:
        return None

    gols_marcados = []
    gols_sofridos = []

    gols_casa_marcados = []
    gols_casa_sofridos = []

    gols_fora_marcados = []
    gols_fora_sofridos = []

    resultados = []

    for jogo in jogos:

        home = jogo.get(
            "homeTeam",
            {}
        )

        away = jogo.get(
            "awayTeam",
            {}
        )

        score = jogo.get(
            "score",
            {}
        ).get(
            "fullTime",
            {}
        )

        hg = score.get("home")
        ag = score.get("away")

        if hg is None or ag is None:
            continue

        home_id = home.get("id")
        away_id = away.get("id")

        if home_id == team_id:

            marcados = hg
            sofridos = ag

            gols_casa_marcados.append(hg)
            gols_casa_sofridos.append(ag)

            if hg > ag:
                resultados.append("V")

            elif hg == ag:
                resultados.append("E")

            else:
                resultados.append("D")

        elif away_id == team_id:

            marcados = ag
            sofridos = hg

            gols_fora_marcados.append(ag)
            gols_fora_sofridos.append(hg)

            if ag > hg:
                resultados.append("V")

            elif ag == hg:
                resultados.append("E")

            else:
                resultados.append("D")

        else:
            continue

        gols_marcados.append(marcados)
        gols_sofridos.append(sofridos)

    if not gols_marcados:
        return None

    pesos = []

    for i in range(
        len(gols_marcados)
    ):

        if i < 5:
            pesos.append(1.5)
        else:
            pesos.append(1.0)

    soma_pesos = sum(pesos)

    media_marcados = sum(
        x * p
        for x, p in zip(
            gols_marcados,
            pesos
        )
    ) / soma_pesos

    media_sofridos = sum(
        x * p
        for x, p in zip(
            gols_sofridos,
            pesos
        )
    ) / soma_pesos

    def media(lista, padrao):

        if not lista:
            return padrao

        return sum(lista) / len(lista)

    return {
        "jogos": len(
            gols_marcados
        ),

        "gols_marcados":
            media_marcados,

        "gols_sofridos":
            media_sofridos,

        "casa_marcados":
            media(
                gols_casa_marcados,
                media_marcados
            ),

        "casa_sofridos":
            media(
                gols_casa_sofridos,
                media_sofridos
            ),

        "fora_marcados":
            media(
                gols_fora_marcados,
                media_marcados
            ),

        "fora_sofridos":
            media(
                gols_fora_sofridos,
                media_sofridos
            ),

        "forma":
            resultados[:5]
    }


# ============================================================
# POISSON
# ============================================================

def modelo_poisson(
    formacasa,
    formafora
):

    if not formacasa or not formafora:
        return None

    ataque_casa = (
        formacasa["casa_marcados"] * 0.60
        + formacasa["gols_marcados"] * 0.40
    )

    defesa_fora = (
        formafora["fora_sofridos"] * 0.60
        + formafora["gols_sofridos"] * 0.40
    )

    ataque_fora = (
        formafora["fora_marcados"] * 0.60
        + formafora["gols_marcados"] * 0.40
    )

    defesa_casa = (
        formacasa["casa_sofridos"] * 0.60
        + formacasa["gols_sofridos"] * 0.40
    )

    lambda_casa = (
        ataque_casa * 0.55
        + defesa_fora * 0.45
    )

    lambda_fora = (
        ataque_fora * 0.55
        + defesa_casa * 0.45
    )

    lambda_casa = max(
        0.15,
        min(lambda_casa, 4.5)
    )

    lambda_fora = max(
        0.15,
        min(lambda_fora, 4.5)
    )

    prob_casa = 0
    prob_empate = 0
    prob_fora = 0

    prob_over_15 = 0
    prob_over_25 = 0
    prob_over_35 = 0

    prob_btts = 0

    for gols_casa in range(0, 11):

        p_casa = poisson(
            gols_casa,
            lambda_casa
        )

        for gols_fora in range(0, 11):

            p_fora = poisson(
                gols_fora,
                lambda_fora
            )

            p = p_casa * p_fora

            if gols_casa > gols_fora:
                prob_casa += p

            elif gols_casa == gols_fora:
                prob_empate += p

            else:
                prob_fora += p

            total = (
                gols_casa
                + gols_fora
            )

            if total >= 2:
                prob_over_15 += p

            if total >= 3:
                prob_over_25 += p

            if total >= 4:
                prob_over_35 += p

            if (
                gols_casa > 0
                and gols_fora > 0
            ):
                prob_btts += p

    return {
        "casa": prob_casa,
        "empate": prob_empate,
        "fora": prob_fora,

        "over_1.5":
            prob_over_15,

        "under_1.5":
            1 - prob_over_15,

        "over_2.5":
            prob_over_25,

        "under_2.5":
            1 - prob_over_25,

        "over_3.5":
            prob_over_35,

        "under_3.5":
            1 - prob_over_35,

        "btts_yes":
            prob_btts,

        "btts_no":
            1 - prob_btts,

        "lambda_casa":
            lambda_casa,

        "lambda_fora":
            lambda_fora
    }


# ============================================================
# COMBINAR PROBABILIDADES
# ============================================================

def combinar_probabilidades(
    prob_mercado,
    prob_modelo
):

    if prob_modelo is None:
        return prob_mercado

    return (
        prob_modelo * 0.60
        + prob_mercado * 0.40
    )


# ============================================================
# KELLY
# ============================================================

def calcular_kelly(
    probabilidade,
    odd
):

    b = odd - 1
    q = 1 - probabilidade

    if b <= 0:
        return 0

    kelly = (
        (b * probabilidade) - q
    ) / b

    if kelly < 0:
        kelly = 0

    kelly *= KELLY_FRACAO

    kelly = min(
        kelly,
        0.05
    )

    return BANCA * kelly


# ============================================================
# MODELO PARA CADA SELEÇÃO
# ============================================================

def probabilidade_modelo_para_selecao(
    mercado,
    selecao,
    jogo,
    modelo
):

    if not modelo:
        return None

    casa = jogo["home_team"]
    fora = jogo["away_team"]

    if mercado == "h2h":

        if selecao == casa:
            return modelo["casa"]

        if selecao == fora:
            return modelo["fora"]

        if selecao.lower() in [
            "draw",
            "empate"
        ]:
            return modelo["empate"]

    if mercado == "btts":

        if selecao.lower() in [
            "yes",
            "sim"
        ]:
            return modelo["btts_yes"]

        if selecao.lower() in [
            "no",
            "não",
            "nao"
        ]:
            return modelo["btts_no"]

    if mercado == "totals":

        if selecao.lower() == "over":

            ponto = jogo.get(
                "point"
            )

            if ponto == 1.5:
                return modelo[
                    "over_1.5"
                ]

            if ponto == 2.5:
                return modelo[
                    "over_2.5"
                ]

            if ponto == 3.5:
                return modelo[
                    "over_3.5"
                ]

        if selecao.lower() == "under":

            ponto = jogo.get(
                "point"
            )

            if ponto == 1.5:
                return modelo[
                    "under_1.5"
                ]

            if ponto == 2.5:
                return modelo[
                    "under_2.5"
                ]

            if ponto == 3.5:
                return modelo[
                    "under_3.5"
                ]

    return None


# ============================================================
# IDS DAS EQUIPES
# ============================================================

def buscar_ids_equipes():

    if not FOOTBALL_DATA_TOKEN:
        return {}

    ids = {}

    competicoes = [
        "BSA",
        "PL",
        "BL1",
        "SA",
        "FL1",
        "PD"
    ]

    for codigo in competicoes:

        dados = football_data_request(
            f"/competitions/{codigo}/teams"
        )

        if not dados:
            continue

        for equipe in dados.get(
            "teams",
            []
        ):

            nome = equipe.get("name")
            team_id = equipe.get("id")

            if nome and team_id:

                ids[
                    normalizar_nome(nome)
                ] = team_id

    return ids


# ============================================================
# ANALISAR JOGO
# ============================================================

def analisar_jogo(
    jogo,
    mercado,
    consenso,
    ids_equipes
):

    home = jogo.get("home_team")
    away = jogo.get("away_team")

    if not home or not away:
        return []

    resultados = []

    modelo = None

    if ids_equipes:

        home_id = ids_equipes.get(
            normalizar_nome(home)
        )

        away_id = ids_equipes.get(
            normalizar_nome(away)
        )

        if home_id and away_id:

            forma_home = (
                calcular_forma_equipe(
                    home_id
                )
            )

            forma_away = (
                calcular_forma_equipe(
                    away_id
                )
            )

            modelo = modelo_poisson(
                forma_home,
                forma_away
            )

    for grupo, dados in consenso.items():

        odds = dados.get(
            "odds",
            {}
        )

        probs = dados.get(
            "probabilidades",
            {}
        )

        for selecao, odd in odds.items():

            if selecao not in probs:
                continue

            try:
                odd = float(odd)
            except Exception:
                continue

            if odd <= 1:
                continue

            prob_market = probs[selecao]

            ponto = None

            if mercado == "totals":

                partes = grupo.split("_")

                try:
                    ponto = float(
                        partes[-1]
                    )
                except Exception:
                    continue

            jogo_modelo = {
                "home_team": home,
                "away_team": away,
                "point": ponto
            }

            prob_modelo = (
                probabilidade_modelo_para_selecao(
                    mercado,
                    selecao,
                    jogo_modelo,
                    modelo
                )
            )

            if prob_modelo is not None:

                prob_final = (
                    combinar_probabilidades(
                        prob_market,
                        prob_modelo
                    )
                )

                origem = (
                    "Poisson + mercado"
                )

            else:

                prob_final = prob_market
                origem = (
                    "consenso das casas"
                )

            # EV usando a odd desta seleção
            ev = (
                (prob_final * odd) - 1
            ) * 100

            # ------------------------------------------------
            # Melhor odd disponível
            # ------------------------------------------------

            melhor_casa = None
            melhor_odd = 0

            for outro_grupo, outro_dados in consenso.items():

                outra_odd = (
                    outro_dados
                    .get("odds", {})
                    .get(selecao)
                )

                if outra_odd:

                    try:

                        outra_odd = float(
                            outra_odd
                        )

                        if outra_odd > melhor_odd:

                            melhor_odd = outra_odd
                            melhor_casa = (
                                outro_grupo
                            )

                    except Exception:
                        pass

            if melhor_odd <= 1:
                melhor_odd = odd

            if not melhor_casa:
                melhor_casa = "Mercado"

            # Remove possíveis identificadores
            # de totals do nome da casa
            if mercado == "totals":
                melhor_casa = melhor_casa.rsplit(
                    "_",
                    2
                )[0]

            odd_justa = (
                1 / prob_final
                if prob_final > 0
                else 0
            )

            stake = calcular_kelly(
                prob_final,
                melhor_odd
            )

            # Recalcula o EV usando a MELHOR odd
            ev_melhor_odd = (
                (prob_final * melhor_odd)
                - 1
            ) * 100

            resultados.append({

                "home": home,
                "away": away,

                "mercado": mercado,

                "selecao": selecao,

                "odd": melhor_odd,

                "casa": melhor_casa,

                "probabilidade":
                    prob_final,

                "odd_justa":
                    odd_justa,

                "ev":
                    ev_melhor_odd,

                "stake":
                    stake,

                "origem":
                    origem,

                "modelo":
                    modelo is not None
            })

    return resultados


# ============================================================
# ANÁLISE COMPLETA
# ============================================================

def analisar_tudo():

    amanha = (
        agora_brasilia().date()
        + timedelta(days=1)
    )

    jogos_unicos = {}

    # Mantemos exatamente a coleta separada
    # que estava funcionando.
    for esporte in COMPETICOES:

        for mercado in MERCADOS:

            eventos = buscar_odds(
                esporte,
                mercado
            )

            for evento in eventos:

                data_evento = evento.get(
                    "commence_time"
                )

                if not data_evento:
                    continue

                try:

                    data_utc = datetime.fromisoformat(
                        data_evento.replace(
                            "Z",
                            "+00:00"
                        )
                    )

                    data_local = (
                        data_utc
                        .astimezone(
                            TIMEZONE
                        )
                        .date()
                    )

                except Exception:
                    continue

                if data_local != amanha:
                    continue

                home = evento.get(
                    "home_team"
                )

                away = evento.get(
                    "away_team"
                )

                if not home or not away:
                    continue

                jogo_id = (
                    f"{normalizar_nome(home)}_"
                    f"{normalizar_nome(away)}"
                )

                if jogo_id not in jogos_unicos:

                    jogos_unicos[jogo_id] = {
                        "evento": evento,
                        "mercados": {}
                    }

                jogos_unicos[jogo_id][
                    "mercados"
                ][mercado] = evento

    # Quantidade real de partidas
    # encontradas para amanhã.
    total_jogos = len(
        jogos_unicos
    )

    if not jogos_unicos:

        return {
            "total_jogos": 0,
            "oportunidades": [],
            "melhores": []
        }

    ids_equipes = (
        buscar_ids_equipes()
    )

    todas_oportunidades = []

    for jogo_id, dados_jogo in jogos_unicos.items():

        evento = dados_jogo[
            "evento"
        ]

        for mercado, evento_mercado in dados_jogo[
            "mercados"
        ].items():

            consenso = coletar_consenso(
                evento_mercado,
                mercado
            )

            # Conta casas únicas
            casas_unicas = set()

            for grupo in consenso:

                if mercado == "totals":

                    # Nome da casa fica antes
                    # dos dois últimos campos.
                    partes = grupo.rsplit(
                        "_",
                        2
                    )

                    nome_casa = (
                        partes[0]
                        if len(partes) == 3
                        else grupo
                    )

                else:

                    nome_casa = grupo

                if nome_casa:
                    casas_unicas.add(
                        nome_casa
                    )

            if len(casas_unicas) < MINIMO_CASAS:
                continue

            resultados = analisar_jogo(
                evento,
                mercado,
                consenso,
                ids_equipes
            )

            todas_oportunidades.extend(
                resultados
            )

    # --------------------------------------------------------
    # Remove duplicações
    # --------------------------------------------------------

    finais = []
    vistos = set()

    todas_oportunidades.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    for op in todas_oportunidades:

        chave = (
            op["home"],
            op["away"],
            op["mercado"],
            op["selecao"]
        )

        if chave in vistos:
            continue

        vistos.add(chave)

        finais.append(op)

    # --------------------------------------------------------
    # EV+ dentro do filtro
    # --------------------------------------------------------

    oportunidades_ev = [
        op
        for op in finais
        if (
            op["ev"] >= EV_MINIMO
            and op["ev"] <= EV_MAXIMO
        )
    ]

    oportunidades_ev.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    # --------------------------------------------------------
    # Melhores oportunidades gerais
    # --------------------------------------------------------

    melhores = sorted(
        finais,
        key=lambda x: x["ev"],
        reverse=True
    )[:5]

    return {
        "total_jogos": total_jogos,
        "oportunidades":
            oportunidades_ev[:20],
        "melhores":
            melhores
    }


# ============================================================
# FORMATAÇÃO
# ============================================================

def formatar_oportunidade(
    op,
    numero,
    abaixo_do_filtro=False
):

    if op["modelo"]:
        modelo_texto = (
            "Poisson + mercado"
        )
    else:
        modelo_texto = (
            "consenso das casas"
        )

    titulo = (
        f"🔎 MELHORES EV #{numero}"
        if abaixo_do_filtro
        else f"🔥 EV+ #{numero}"
    )

    return (
        f"{titulo}\n\n"

        f"⚽ {op['home']} x "
        f"{op['away']}\n"

        f"🕐 Amanhã\n\n"

        f"📊 Mercado: "
        f"{op['mercado']}\n"

        f"🎯 Entrada: "
        f"{op['selecao']}\n\n"

        f"🏦 Melhor casa: "
        f"{op['casa']}\n"

        f"💰 Odd: "
        f"{op['odd']:.2f}\n\n"

        f"📈 Probabilidade estimada: "
        f"{op['probabilidade'] * 100:.1f}%\n"

        f"📐 Odd justa: "
        f"{op['odd_justa']:.2f}\n"

        f"💎 EV: "
        f"{op['ev']:+.2f}%\n"

        f"💵 Kelly 25%: "
        f"R$ {op['stake']:.2f}\n"

        f"🧠 Modelo: "
        f"{modelo_texto}\n\n"

        f"⚠️ Estimativa estatística. "
        f"Não garante resultado."
    )


# ============================================================
# /START
# ============================================================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,

        "🤖 Múltiplas do LC conectado!\n\n"

        "Use /odds para analisar as "
        "oportunidades de amanhã."
    )


# ============================================================
# /ODDS
# ============================================================

@bot.message_handler(
    commands=["odds"]
)
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🔎 Analisando as odds e "
        "os dados estatísticos..."
    )

    resultado = analisar_tudo()

    total_jogos = resultado[
        "total_jogos"
    ]

    oportunidades = resultado[
        "oportunidades"
    ]

    melhores = resultado[
        "melhores"
    ]

    # --------------------------------------------------------
    # NENHUM JOGO
    # --------------------------------------------------------

    if total_jogos == 0:

        bot.send_message(
            message.chat.id,

            "📊 ANÁLISE CONCLUÍDA\n\n"

            "⚽ Jogos analisados: 0\n\n"

            "Nenhum jogo de amanhã "
            "foi encontrado nas "
            "competições configuradas."
        )

        return

    # --------------------------------------------------------
    # EXISTEM EV+
    # --------------------------------------------------------

    if oportunidades:

        bot.send_message(
            message.chat.id,

            "📊 ANÁLISE CONCLUÍDA\n\n"

            f"⚽ Jogos analisados: "
            f"{total_jogos}\n"

            f"🔥 Oportunidades EV+: "
            f"{len(oportunidades)}\n\n"

            f"Filtro EV: "
            f"{EV_MINIMO:.0f}% a "
            f"{EV_MAXIMO:.0f}%\n"

            f"Mínimo de casas: "
            f"{MINIMO_CASAS}"
        )

        for i, oportunidade in enumerate(
            oportunidades,
            start=1
        ):

            try:

                bot.send_message(
                    message.chat.id,

                    formatar_oportunidade(
                        oportunidade,
                        i
                    )
                )

                time.sleep(0.4)

            except Exception:
                pass

        return

    # --------------------------------------------------------
    # NENHUM EV+
    # --------------------------------------------------------

    bot.send_message(
        message.chat.id,

        "📊 ANÁLISE CONCLUÍDA\n\n"

        f"⚽ Jogos analisados: "
        f"{total_jogos}\n\n"

        "❌ Nenhuma oportunidade "
        "EV+ encontrada dentro "
        "dos filtros atuais.\n\n"

        f"EV mínimo: "
        f"{EV_MINIMO:.0f}%\n"

        f"EV máximo: "
        f"{EV_MAXIMO:.0f}%\n"

        f"Mínimo de casas: "
        f"{MINIMO_CASAS}\n\n"

        "🔎 Abaixo estão as "
        "5 melhores oportunidades "
        "encontradas, mesmo estando "
        "abaixo do EV mínimo."
    )

    # --------------------------------------------------------
    # MOSTRA OS 5 MELHORES
    # --------------------------------------------------------

    if melhores:

        for i, oportunidade in enumerate(
            melhores,
            start=1
        ):

            try:

                bot.send_message(
                    message.chat.id,

                    formatar_oportunidade(
                        oportunidade,
                        i,
                        abaixo_do_filtro=True
                    )
                )

                time.sleep(0.4)

            except Exception:
                pass

    else:

        bot.send_message(
            message.chat.id,

            "ℹ️ Nenhuma seleção válida "
            "com pelo menos "
            f"{MINIMO_CASAS} casas foi "
            "encontrada."
        )


# ============================================================
# OUTRAS MENSAGENS
# ============================================================

@bot.message_handler(
    func=lambda message: True
)
def outras_mensagens(message):

    bot.send_message(
        message.chat.id,

        "🤖 Comandos disponíveis:\n\n"

        "/start — conectar o bot\n"

        "/odds — analisar oportunidades "
        "de amanhã"
    )


# ============================================================
# ENVIO AUTOMÁTICO
# ============================================================

def rotina_diaria():

    while True:

        try:

            agora = agora_brasilia()

            if (
                agora.hour == 9
                and agora.minute == 0
            ):

                if CHAT_ID:

                    resultado = analisar_tudo()

                    total_jogos = resultado[
                        "total_jogos"
                    ]

                    oportunidades = resultado[
                        "oportunidades"
                    ]

                    melhores = resultado[
                        "melhores"
                    ]

                    if oportunidades:

                        bot.send_message(
                            CHAT_ID,

                            "🌅 ANÁLISE DIÁRIA\n\n"

                            f"⚽ Jogos analisados: "
                            f"{total_jogos}\n"

                            f"🔥 EV+ encontrados: "
                            f"{len(oportunidades)}"
                        )

                        for i, oportunidade in enumerate(
                            oportunidades,
                            start=1
                        ):

                            bot.send_message(
                                CHAT_ID,

                                formatar_oportunidade(
                                    oportunidade,
                                    i
                                )
                            )

                            time.sleep(0.4)

                    else:

                        bot.send_message(
                            CHAT_ID,

                            "🌅 ANÁLISE DIÁRIA\n\n"

                            f"⚽ Jogos analisados: "
                            f"{total_jogos}\n\n"

                            "❌ Nenhuma oportunidade "
                            "EV+ dentro dos filtros.\n\n"

                            "🔎 Enviando as 5 "
                            "melhores encontradas."
                        )

                        for i, oportunidade in enumerate(
                            melhores,
                            start=1
                        ):

                            bot.send_message(
                                CHAT_ID,

                                formatar_oportunidade(
                                    oportunidade,
                                    i,
                                    abaixo_do_filtro=True
                                )
                            )

                            time.sleep(0.4)

                time.sleep(61)

        except Exception:
            pass

        time.sleep(30)


# ============================================================
# FLASK / RENDER
# ============================================================

@app.route("/")
def home():

    return "Múltiplas do LC online."


@app.route("/health")
def health():

    return "OK"


def iniciar_servidor():

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


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=iniciar_servidor,
        daemon=True
    ).start()

    threading.Thread(
        target=rotina_diaria,
        daemon=True
    ).start()

    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30
    )
