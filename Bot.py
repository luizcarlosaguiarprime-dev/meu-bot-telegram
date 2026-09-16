import os
import math
import time
import threading
import statistics
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import telebot
from flask import Flask


# =========================================================
# CONFIGURAÇÕES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "").strip()

BANCA = 1000.00

# Kelly fracionado
KELLY_FRACAO = 0.25

# Filtro principal
EV_MINIMO = 5.0
EV_MAXIMO = 20.0

# Número mínimo de casas
MINIMO_CASAS = 3

# Diferença mínima entre modelo e mercado
# para considerar o modelo relevante
DIFERENCA_MODELO_MINIMA = 0.025

# Peso do modelo
PESO_MODELO = 0.60

# Peso do mercado
PESO_MERCADO = 0.40

TIMEZONE = ZoneInfo("America/Sao_Paulo")

ODDS_URL = "https://api.the-odds-api.com/v4/sports/{}/odds"

FOOTBALL_DATA_URL = "https://api.football-data.org/v4"


# =========================================================
# FLASK / TELEGRAM
# =========================================================

app = Flask(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

CHAT_ID = None


# =========================================================
# CACHE
# =========================================================

CACHE_FORMA = {}

CACHE_IDS = None


# =========================================================
# COMPETIÇÕES
# =========================================================

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
    "btts"
]


# =========================================================
# DATA / NOMES
# =========================================================

def agora_brasilia():
    return datetime.now(TIMEZONE)


def normalizar_nome(nome):

    if not nome:
        return ""

    nome = nome.lower().strip()

    substituicoes = [
        (" fc ", " "),
        (" fc", ""),
        ("fc ", ""),
        (" cf ", " "),
        (" cf", ""),
        (" afc ", " "),
        (" afc", ""),
        (" sc ", " "),
        (" sc", ""),
        (" fk ", " "),
        (" fk", ""),
        ("  ", " "),
    ]

    nome = f" {nome} "

    for antigo, novo in substituicoes:
        nome = nome.replace(
            antigo,
            novo
        )

    return nome.strip()


# =========================================================
# POISSON
# =========================================================

def poisson(k, lamb):

    if lamb <= 0:
        return 0.0

    return (
        math.exp(-lamb)
        * (lamb ** k)
        / math.factorial(k)
    )


# =========================================================
# ODDS API
# =========================================================

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

        if not isinstance(dados, list):
            return []

        return dados

    except Exception:
        return []


# =========================================================
# PROBABILIDADE SEM MARGEM
# =========================================================

def probabilidades_mercado(resultados):

    if not resultados:
        return {}

    probabilidades = {}

    for resultado, odd in resultados.items():

        try:

            odd = float(odd)

            if odd <= 1:
                continue

            probabilidades[resultado] = 1.0 / odd

        except Exception:
            continue

    soma = sum(
        probabilidades.values()
    )

    if soma <= 0:
        return {}

    for resultado in probabilidades:

        probabilidades[resultado] /= soma

    return probabilidades


# =========================================================
# CONSENSO DAS CASAS
# =========================================================

def coletar_consenso(jogo, mercado):

    selecoes = {}

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

            # =================================================
            # H2H / BTTS
            # =================================================

            if mercado in [
                "h2h",
                "btts"
            ]:

                odds = {}

                for outcome in outcomes:

                    nome = outcome.get(
                        "name"
                    )

                    odd = outcome.get(
                        "price"
                    )

                    if nome and odd:

                        try:
                            odds[nome] = float(
                                odd
                            )
                        except Exception:
                            pass

                probs = probabilidades_mercado(
                    odds
                )

                if not probs:
                    continue

                for selecao, prob in probs.items():

                    if selecao not in selecoes:

                        selecoes[selecao] = {
                            "probabilidades": [],
                            "odds": [],
                            "casas": set()
                        }

                    selecoes[
                        selecao
                    ][
                        "probabilidades"
                    ].append(prob)

                    if selecao in odds:

                        selecoes[
                            selecao
                        ][
                            "odds"
                        ].append(
                            (
                                odds[selecao],
                                nome_casa
                            )
                        )

                        selecoes[
                            selecao
                        ][
                            "casas"
                        ].add(
                            nome_casa
                        )

            # =================================================
            # TOTALS
            # =================================================

            elif mercado == "totals":

                grupos = {}

                for outcome in outcomes:

                    nome = outcome.get(
                        "name"
                    )

                    ponto = outcome.get(
                        "point"
                    )

                    odd = outcome.get(
                        "price"
                    )

                    if (
                        nome is None
                        or ponto is None
                        or odd is None
                    ):
                        continue

                    try:

                        ponto = float(ponto)
                        odd = float(odd)

                    except Exception:
                        continue

                    chave = (
                        f"{nome}_{ponto}"
                    )

                    if chave not in grupos:
                        grupos[chave] = {}

                    grupos[chave][nome] = odd

                for chave, odds in grupos.items():

                    probs = probabilidades_mercado(
                        odds
                    )

                    if not probs:
                        continue

                    try:

                        ponto = float(
                            chave.rsplit(
                                "_",
                                1
                            )[1]
                        )

                    except Exception:
                        continue

                    for selecao, prob in probs.items():

                        chave_selecao = (
                            f"{selecao}_{ponto}"
                        )

                        if (
                            chave_selecao
                            not in selecoes
                        ):

                            selecoes[
                                chave_selecao
                            ] = {
                                "selecao": selecao,
                                "point": ponto,
                                "probabilidades": [],
                                "odds": [],
                                "casas": set()
                            }

                        selecoes[
                            chave_selecao
                        ][
                            "probabilidades"
                        ].append(prob)

                        if selecao in odds:

                            selecoes[
                                chave_selecao
                            ][
                                "odds"
                            ].append(
                                (
                                    odds[selecao],
                                    nome_casa
                                )
                            )

                            selecoes[
                                chave_selecao
                            ][
                                "casas"
                            ].add(
                                nome_casa
                            )

    # =====================================================
    # TRANSFORMAR EM CONSENSO FINAL
    # =====================================================

    resultado = {}

    for chave, dados in selecoes.items():

        probabilidades = dados.get(
            "probabilidades",
            []
        )

        casas = dados.get(
            "casas",
            set()
        )

        odds = dados.get(
            "odds",
            []
        )

        if not probabilidades:
            continue

        if len(casas) < MINIMO_CASAS:
            continue

        if not odds:
            continue

        # Mediana é mais resistente
        # a uma casa discrepante.
        probabilidade = statistics.median(
            probabilidades
        )

        # Melhor odd disponível
        melhor_odd, melhor_casa = max(
            odds,
            key=lambda x: x[0]
        )

        dispersao = (
            max(probabilidades)
            - min(probabilidades)
        )

        resultado[chave] = {

            "selecao": dados.get(
                "selecao",
                chave
            ),

            "point": dados.get(
                "point"
            ),

            "probabilidade": probabilidade,

            "odd": melhor_odd,

            "casa": melhor_casa,

            "casas": len(casas),

            "dispersao": dispersao,

            "lista_casas": sorted(
                casas
            )
        }

    return resultado


# =========================================================
# FOOTBALL-DATA.ORG
# =========================================================

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


# =========================================================
# BUSCAR IDS DAS EQUIPES
# =========================================================

def buscar_ids_equipes():

    global CACHE_IDS

    if CACHE_IDS is not None:
        return CACHE_IDS

    if not FOOTBALL_DATA_TOKEN:
        CACHE_IDS = {}
        return CACHE_IDS

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

            nome = equipe.get(
                "name"
            )

            team_id = equipe.get(
                "id"
            )

            if nome and team_id:

                ids[
                    normalizar_nome(nome)
                ] = team_id

    CACHE_IDS = ids

    return CACHE_IDS


# =========================================================
# HISTÓRICO DA EQUIPE
# =========================================================

def obter_jogos_equipe(
    team_id,
    quantidade=10
):

    data_final = agora_brasilia().date()

    data_inicial = (
        data_final
        - timedelta(days=150)
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
        key=lambda x: x.get(
            "utcDate",
            ""
        ),
        reverse=True
    )

    return jogos[:quantidade]


# =========================================================
# FORMA DA EQUIPE
# =========================================================

def calcular_forma_equipe(
    team_id
):

    if team_id in CACHE_FORMA:

        return CACHE_FORMA[
            team_id
        ]

    jogos = obter_jogos_equipe(
        team_id,
        10
    )

    if not jogos:

        CACHE_FORMA[
            team_id
        ] = None

        return None

    gols_marcados = []
    gols_sofridos = []

    casa_marcados = []
    casa_sofridos = []

    fora_marcados = []
    fora_sofridos = []

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

        home_id = home.get(
            "id"
        )

        away_id = away.get(
            "id"
        )

        if home_id == team_id:

            marcados = hg
            sofridos = ag

            casa_marcados.append(hg)
            casa_sofridos.append(ag)

            if hg > ag:
                resultados.append("V")

            elif hg == ag:
                resultados.append("E")

            else:
                resultados.append("D")

        elif away_id == team_id:

            marcados = ag
            sofridos = hg

            fora_marcados.append(ag)
            fora_sofridos.append(hg)

            if ag > hg:
                resultados.append("V")

            elif ag == hg:
                resultados.append("E")

            else:
                resultados.append("D")

        else:
            continue

        gols_marcados.append(
            marcados
        )

        gols_sofridos.append(
            sofridos
        )

    if len(gols_marcados) < 5:

        CACHE_FORMA[
            team_id
        ] = None

        return None

    # =====================================================
    # PESOS
    # =====================================================

    pesos = []

    for i in range(
        len(gols_marcados)
    ):

        if i < 5:
            pesos.append(1.5)

        else:
            pesos.append(1.0)

    soma_pesos = sum(
        pesos
    )

    media_marcados = (
        sum(
            g * p
            for g, p in zip(
                gols_marcados,
                pesos
            )
        )
        / soma_pesos
    )

    media_sofridos = (
        sum(
            g * p
            for g, p in zip(
                gols_sofridos,
                pesos
            )
        )
        / soma_pesos
    )

    def media(lista, padrao):

        if not lista:
            return padrao

        return sum(lista) / len(lista)

    resultado = {

        "jogos": len(
            gols_marcados
        ),

        "gols_marcados":
            media_marcados,

        "gols_sofridos":
            media_sofridos,

        "casa_marcados":
            media(
                casa_marcados,
                media_marcados
            ),

        "casa_sofridos":
            media(
                casa_sofridos,
                media_sofridos
            ),

        "fora_marcados":
            media(
                fora_marcados,
                media_marcados
            ),

        "fora_sofridos":
            media(
                fora_sofridos,
                media_sofridos
            ),

        "forma":
            resultados[:5]
    }

    CACHE_FORMA[
        team_id
    ] = resultado

    return resultado


# =========================================================
# MODELO DE GOLS
# =========================================================

def modelo_poisson(
    forma_casa,
    forma_fora
):

    if (
        not forma_casa
        or not forma_fora
    ):
        return None

    # =====================================================
    # ATAQUE
    # =====================================================

    ataque_casa = (
        forma_casa[
            "casa_marcados"
        ] * 0.65

        + forma_casa[
            "gols_marcados"
        ] * 0.35
    )

    ataque_fora = (
        forma_fora[
            "fora_marcados"
        ] * 0.65

        + forma_fora[
            "gols_marcados"
        ] * 0.35
    )

    # =====================================================
    # DEFESA
    # =====================================================

    defesa_casa = (
        forma_casa[
            "casa_sofridos"
        ] * 0.65

        + forma_casa[
            "gols_sofridos"
        ] * 0.35
    )

    defesa_fora = (
        forma_fora[
            "fora_sofridos"
        ] * 0.65

        + forma_fora[
            "gols_sofridos"
        ] * 0.35
    )

    # =====================================================
    # EXPECTATIVA DE GOLS
    # =====================================================

    lambda_casa = (
        ataque_casa * 0.55
        + defesa_fora * 0.45
    )

    lambda_fora = (
        ataque_fora * 0.55
        + defesa_casa * 0.45
    )

    # Limites de segurança
    lambda_casa = max(
        0.20,
        min(
            lambda_casa,
            4.00
        )
    )

    lambda_fora = max(
        0.20,
        min(
            lambda_fora,
            4.00
        )
    )

    # =====================================================
    # PROBABILIDADES
    # =====================================================

    prob_casa = 0.0
    prob_empate = 0.0
    prob_fora = 0.0

    prob_over_15 = 0.0
    prob_over_25 = 0.0
    prob_over_35 = 0.0

    prob_btts = 0.0

    # 0-12 gols para reduzir
    # truncamento do modelo
    for gols_casa in range(0, 13):

        p_casa = poisson(
            gols_casa,
            lambda_casa
        )

        for gols_fora in range(0, 13):

            p_fora = poisson(
                gols_fora,
                lambda_fora
            )

            prob = (
                p_casa
                * p_fora
            )

            if gols_casa > gols_fora:

                prob_casa += prob

            elif gols_casa == gols_fora:

                prob_empate += prob

            else:

                prob_fora += prob

            total = (
                gols_casa
                + gols_fora
            )

            if total >= 2:
                prob_over_15 += prob

            if total >= 3:
                prob_over_25 += prob

            if total >= 4:
                prob_over_35 += prob

            if (
                gols_casa > 0
                and gols_fora > 0
            ):
                prob_btts += prob

    # =====================================================
    # NORMALIZAR 1X2
    # =====================================================

    soma_1x2 = (
        prob_casa
        + prob_empate
        + prob_fora
    )

    if soma_1x2 > 0:

        prob_casa /= soma_1x2
        prob_empate /= soma_1x2
        prob_fora /= soma_1x2

    return {

        "casa":
            prob_casa,

        "empate":
            prob_empate,

        "fora":
            prob_fora,

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


# =========================================================
# PROBABILIDADE DO MODELO PARA CADA SELEÇÃO
# =========================================================

def probabilidade_modelo(
    mercado,
    selecao,
    ponto,
    home,
    away,
    modelo
):

    if not modelo:
        return None

    # =====================================================
    # 1X2
    # =====================================================

    if mercado == "h2h":

        if selecao == home:
            return modelo["casa"]

        if selecao == away:
            return modelo["fora"]

        if selecao.lower() in [
            "draw",
            "empate"
        ]:
            return modelo["empate"]

    # =====================================================
    # BTTS
    # =====================================================

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

    # =====================================================
    # TOTALS
    # =====================================================

    if mercado == "totals":

        if ponto is None:
            return None

        try:
            ponto = float(ponto)
        except Exception:
            return None

        if selecao.lower() == "over":

            if ponto == 1.5:
                return modelo["over_1.5"]

            if ponto == 2.5:
                return modelo["over_2.5"]

            if ponto == 3.5:
                return modelo["over_3.5"]

        if selecao.lower() == "under":

            if ponto == 1.5:
                return modelo["under_1.5"]

            if ponto == 2.5:
                return modelo["under_2.5"]

            if ponto == 3.5:
                return modelo["under_3.5"]

    return None


# =========================================================
# MODELO DO JOGO
# =========================================================

def obter_modelo_jogo(
    home,
    away,
    ids_equipes
):

    if not ids_equipes:
        return None

    home_id = ids_equipes.get(
        normalizar_nome(home)
    )

    away_id = ids_equipes.get(
        normalizar_nome(away)
    )

    if not home_id or not away_id:
        return None

    forma_home = calcular_forma_equipe(
        home_id
    )

    forma_away = calcular_forma_equipe(
        away_id
    )

    return modelo_poisson(
        forma_home,
        forma_away
    )


# =========================================================
# ANALISAR JOGO
# =========================================================

def analisar_jogo(
    jogo,
    mercado,
    consenso,
    ids_equipes
):

    home = jogo.get(
        "home_team"
    )

    away = jogo.get(
        "away_team"
    )

    if not home or not away:
        return []

    modelo = obter_modelo_jogo(
        home,
        away,
        ids_equipes
    )

    resultados = []

    for chave, dados in consenso.items():

        selecao = dados.get(
            "selecao",
            chave
        )

        ponto = dados.get(
            "point"
        )

        prob_market = dados.get(
            "probabilidade"
        )

        odd = dados.get(
            "odd"
        )

        if (
            prob_market is None
            or odd is None
        ):
            continue

        try:

            prob_market = float(
                prob_market
            )

            odd = float(
                odd
            )

        except Exception:
            continue

        if odd <= 1:
            continue

        # =================================================
        # PROBABILIDADE DO MODELO
        # =================================================

        prob_modelo = probabilidade_modelo(
            mercado,
            selecao,
            ponto,
            home,
            away,
            modelo
        )

        # =================================================
        # COMBINAÇÃO
        # =================================================

        if prob_modelo is not None:

            diferenca = (
                prob_modelo
                - prob_market
            )

            # Só mistura o modelo se ele
            # estiver razoavelmente afastado
            # do mercado.
            if abs(diferenca) >= DIFERENCA_MODELO_MINIMA:

                prob_final = (
                    prob_modelo
                    * PESO_MODELO
                    +
                    prob_market
                    * PESO_MERCADO
                )

                origem = (
                    "Poisson + mercado"
                )

                modelo_utilizado = True

            else:

                # Quando modelo e mercado
                # estão muito próximos,
                # usamos o consenso de mercado.
                prob_final = prob_market

                origem = (
                    "consenso das casas"
                )

                modelo_utilizado = False

        else:

            prob_final = prob_market

            origem = (
                "consenso das casas"
            )

            modelo_utilizado = False

        # =================================================
        # EV
        # =================================================

        ev = (
            (
                prob_final
                * odd
            ) - 1
        ) * 100

        # =================================================
        # ODD JUSTA
        # =================================================

        if prob_final > 0:

            odd_justa = (
                1 / prob_final
            )

        else:

            odd_justa = 0

        # =================================================
        # KELLY
        # =================================================

        stake = calcular_kelly(
            prob_final,
            odd
        )

        # =================================================
        # CONSISTÊNCIA DO MERCADO
        # =================================================

        dispersao = dados.get(
            "dispersao",
            0
        )

        if dispersao <= 0.025:

            consistencia = "alta"

        elif dispersao <= 0.05:

            consistencia = "média"

        else:

            consistencia = "baixa"

        # =================================================
        # DIFERENÇA MODELO / MERCADO
        # =================================================

        if prob_modelo is not None:

            diferenca_modelo = (
                prob_modelo
                - prob_market
            )

        else:

            diferenca_modelo = None

        resultados.append({

            "home": home,

            "away": away,

            "mercado": mercado,

            "selecao": selecao,

            "point": ponto,

            "odd": odd,

            "casa": dados.get(
                "casa",
                "Mercado"
            ),

            "probabilidade":
                prob_final,

            "prob_market":
                prob_market,

            "prob_modelo":
                prob_modelo,

            "diferenca_modelo":
                diferenca_modelo,

            "odd_justa":
                odd_justa,

            "ev":
                ev,

            "stake":
                stake,

            "casas":
                dados.get(
                    "casas",
                    0
                ),

            "dispersao":
                dispersao,

            "consistencia":
                consistencia,

            "origem":
                origem,

            "modelo":
                modelo_utilizado
        })

    return resultados


# =========================================================
# KELLY
# =========================================================

def calcular_kelly(
    probabilidade,
    odd
):

    if (
        probabilidade <= 0
        or odd <= 1
    ):
        return 0.0

    b = odd - 1

    q = 1 - probabilidade

    kelly = (
        (
            b
            * probabilidade
        )
        - q
    ) / b

    if kelly < 0:
        return 0.0

    kelly *= KELLY_FRACAO

    # Limite máximo de 5%
    kelly = min(
        kelly,
        0.05
    )

    return BANCA * kelly


# =========================================================
# ANALISAR TODOS OS JOGOS
# =========================================================

def analisar_tudo():

    # Limpa cache de forma
    # a cada nova análise.
    CACHE_FORMA.clear()

    amanha = (
        agora_brasilia().date()
        + timedelta(days=1)
    )

    jogos_unicos = {}

    # =====================================================
    # COLETA ORIGINAL
    # =====================================================

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

                jogos_unicos[
                    jogo_id
                ][
                    "mercados"
                ][
                    mercado
                ] = evento

    total_jogos = len(
        jogos_unicos
    )

    if total_jogos == 0:

        return {
            "total_jogos": 0,
            "oportunidades": [],
            "melhores": []
        }

    # =====================================================
    # IDS
    # =====================================================

    ids_equipes = buscar_ids_equipes()

    todas = []

    # =====================================================
    # ANALISAR
    # =====================================================

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

            if not consenso:
                continue

            resultados = analisar_jogo(
                evento,
                mercado,
                consenso,
                ids_equipes
            )

            todas.extend(
                resultados
            )

    # =====================================================
    # REMOVER DUPLICADOS
    # =====================================================

    unicos = {}

    for op in todas:

        chave = (
            op["home"],
            op["away"],
            op["mercado"],
            op["selecao"],
            op.get("point")
        )

        atual = unicos.get(
            chave
        )

        if (
            atual is None
            or op["ev"] > atual["ev"]
        ):

            unicos[chave] = op

    finais = list(
        unicos.values()
    )

    # =====================================================
    # ORDENAR
    # =====================================================

    finais.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    # =====================================================
    # EV+
    # =====================================================

    oportunidades = [

        op

        for op in finais

        if (
            op["ev"] >= EV_MINIMO
            and
            op["ev"] <= EV_MAXIMO
        )
    ]

    oportunidades.sort(
        key=lambda x: x["ev"],
        reverse=True
    )

    # =====================================================
    # MELHORES 5
    # =====================================================

    melhores = finais[:5]

    return {

        "total_jogos":
            total_jogos,

        "oportunidades":
            oportunidades[:20],

        "melhores":
            melhores
    }


# =========================================================
# FORMATAR OP
# =========================================================

def formatar_oportunidade(
    op,
    numero,
    abaixo_do_filtro=False
):

    if abaixo_do_filtro:

        titulo = (
            f"🔎 MELHORES EV #{numero}"
        )

    else:

        titulo = (
            f"🔥 EV+ #{numero}"
        )

    if op["modelo"]:

        modelo_texto = (
            "Poisson + mercado"
        )

    else:

        modelo_texto = (
            "consenso das casas"
        )

    texto = (

        f"{titulo}\n\n"

        f"⚽ {op['home']} x "
        f"{op['away']}\n"

        f"🕐 Amanhã\n\n"

        f"📊 Mercado: "
        f"{op['mercado']}\n"

        f"🎯 Entrada: "
        f"{op['selecao']}\n"
    )

    # Mostrar ponto para totals
    if (
        op["mercado"] == "totals"
        and op.get("point") is not None
    ):

        texto += (
            f"📏 Linha: "
            f"{op['point']:.1f}\n"
        )

    texto += (

        f"\n"

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
        f"R$ {op['stake']:.2f}\n\n"

        f"🏦 Casas consideradas: "
        f"{op['casas']}\n"

        f"📊 Consistência: "
        f"{op['consistencia']}\n"

        f"🧠 Modelo: "
        f"{modelo_texto}\n"
    )

    if op["prob_modelo"] is not None:

        texto += (

            f"📊 Prob. modelo: "
            f"{op['prob_modelo'] * 100:.1f}%\n"

            f"📊 Prob. mercado: "
            f"{op['prob_market'] * 100:.1f}%\n"
        )

    texto += (

        "\n⚠️ Estimativa estatística. "
        "Não garante resultado."
    )

    return texto


# =========================================================
# START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(

        message.chat.id,

        "🤖 Múltiplas do LC conectado!\n\n"

        "Use /odds para analisar "
        "as oportunidades de amanhã."
    )


# =========================================================
# ODDS
# =========================================================

@bot.message_handler(
    commands=["odds"]
)
def odds(message):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(

        message.chat.id,

        "🔎 Analisando as odds "
        "e os dados estatísticos..."
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

    # =====================================================
    # ZERO JOGOS
    # =====================================================

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

    # =====================================================
    # EV+
    # =====================================================

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

        for i, op in enumerate(
            oportunidades,
            start=1
        ):

            try:

                bot.send_message(

                    message.chat.id,

                    formatar_oportunidade(
                        op,
                        i
                    )
                )

                time.sleep(0.4)

            except Exception:
                pass

        return

    # =====================================================
    # SEM EV+
    # =====================================================

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

        "🔎 Enviando as 5 melhores "
        "seleções encontradas."
    )

    if melhores:

        for i, op in enumerate(
            melhores,
            start=1
        ):

            try:

                bot.send_message(

                    message.chat.id,

                    formatar_oportunidade(
                        op,
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
            f"com pelo menos "
            f"{MINIMO_CASAS} casas."
        )


# =========================================================
# OUTRAS MENSAGENS
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def outras_mensagens(message):

    bot.send_message(

        message.chat.id,

        "🤖 Comandos disponíveis:\n\n"

        "/start — conectar o bot\n"

        "/odds — analisar "
        "oportunidades de amanhã"
    )


# =========================================================
# ROTINA AUTOMÁTICA
# =========================================================

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

                        for i, op in enumerate(
                            oportunidades,
                            start=1
                        ):

                            bot.send_message(

                                CHAT_ID,

                                formatar_oportunidade(
                                    op,
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

                            "🔎 Enviando as 5 melhores."
                        )

                        for i, op in enumerate(
                            melhores,
                            start=1
                        ):

                            bot.send_message(

                                CHAT_ID,

                                formatar_oportunidade(
                                    op,
                                    i,
                                    abaixo_do_filtro=True
                                )
                            )

                            time.sleep(0.4)

                time.sleep(61)

        except Exception:
            pass

        time.sleep(30)


# =========================================================
# FLASK
# =========================================================

@app.route("/")
def home():

    return "Múltiplas do LC online."


@app.route("/health")
def health():

    return "OK"


# =========================================================
# SERVIDOR
# =========================================================

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


# =========================================================
# INICIAR
# =========================================================

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
