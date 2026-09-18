import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import telebot
from telebot.apihelper import ApiTelegramException
from flask import Flask


BOT_TOKEN = os.getenv("BOT_TOKEN")

API_FOOTBALL_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_SPORTS_KEY")
    or os.getenv("APISPORTS_KEY")
)

API_BASE = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_FOOTBALL_KEY or ""
}

TZ = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__)

bot = None

BOT_ATIVO = True


# ============================================================
# PRINCIPAIS COMPETIÇÕES
# ============================================================

LIGAS = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    71: "Brasileirão Série A",
    13: "Copa Libertadores",
    11: "Copa Sul-Americana"
}

LIGA_IDS = "-".join(map(str, LIGAS))


# ============================================================
# CONTROLE DE REQUISIÇÕES (10/min no plano grátis da API-Football)
#
# Diferente da versão anterior, isso ESPERA em vez de simplesmente
# recusar a chamada — assim uma resposta vazia da API significa
# "a API não tinha dado", nunca "nós nos autolimitamos" (as duas
# coisas estavam se confundindo nas mensagens antes).
# ============================================================

historico_chamadas = []
LIMITE_POR_MINUTO = 9  # margem de segurança abaixo do limite real (10)


def aguardar_limite():

    agora = time.time()

    while historico_chamadas and agora - historico_chamadas[0] > 60:
        historico_chamadas.pop(0)

    if len(historico_chamadas) >= LIMITE_POR_MINUTO:

        espera = 60 - (agora - historico_chamadas[0]) + 0.2

        if espera > 0:
            print(f"Aguardando {espera:.1f}s para respeitar limite de requisições/min...")
            time.sleep(espera)

    historico_chamadas.append(time.time())


# ============================================================
# API
# ============================================================

def api_get(endpoint, params=None):

    if not API_FOOTBALL_KEY:
        print("ERRO: API_FOOTBALL_KEY não configurada.")
        return None

    aguardar_limite()

    try:

        resposta = requests.get(
            API_BASE + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=20
        )

        print(f"API {endpoint} | HTTP {resposta.status_code}")

        if resposta.status_code == 429:
            print("LIMITE DIÁRIO/MINUTO DA API-FOOTBALL ATINGIDO.")
            return None

        if resposta.status_code != 200:
            print(resposta.text[:500])
            return None

        dados = resposta.json()

        if dados.get("errors"):
            print("API errors:", dados["errors"])

        return dados

    except Exception as e:
        print("API error:", e)
        return None


# ============================================================
# STATUS DA API
#
# Corrigido: a versão anterior lia campos ("account.status",
# "requests.current"/"limit_day") que não existem na resposta real
# da API-Football, então /status sempre reportava "offline" mesmo
# funcionando. Agora o online/offline vem do HTTP 200 em si (que é
# garantido pela doc), e a cota vem dos HEADERS de rate limit
# (x-ratelimit-requests-limit/remaining), que são documentados e
# estáveis — em vez de adivinhar nomes de campo no corpo da resposta.
# ============================================================

def verificar_api():

    if not API_FOOTBALL_KEY:
        return False, "API_FOOTBALL_KEY não configurada."

    try:
        resposta = requests.get(
            API_BASE + "/status",
            headers=HEADERS,
            timeout=15
        )
    except Exception as e:
        return False, f"Sem resposta da API ({e})."

    if resposta.status_code != 200:
        return False, f"HTTP {resposta.status_code}: {resposta.text[:200]}"

    limite_dia = resposta.headers.get("x-ratelimit-requests-limit", "?")
    restante_dia = resposta.headers.get("x-ratelimit-requests-remaining", "?")

    dados = resposta.json().get("response", {}) or {}
    account = dados.get("account", {}) or {}
    subscription = dados.get("subscription", {}) or {}

    email = account.get("email", "—")
    plano = subscription.get("plan", "—")

    mensagem = (
        f"Conta: {email}\n"
        f"Plano: {plano}\n"
        f"Requisições hoje: {restante_dia} restantes de {limite_dia}"
    )

    return True, mensagem


# ============================================================
# JOGOS DO DIA (1 requisição em vez de 1 por liga)
# ============================================================

def jogos_do_dia(data):

    dados = api_get("/fixtures", {"date": data, "timezone": "America/Sao_Paulo"})

    if not dados:
        return []

    resultados = []

    for jogo in dados.get("response", []):

        liga_id = jogo.get("league", {}).get("id")

        if liga_id in LIGAS:
            jogo["_nome_liga_bot"] = LIGAS[liga_id]
            resultados.append(jogo)

    return resultados


# ============================================================
# ÚLTIMOS 10 JOGOS
# ============================================================

def ultimos_10(team_id):

    if not team_id:
        return []

    dados = api_get("/fixtures", {"team": team_id, "last": 10})

    if not dados:
        return []

    jogos = dados.get("response", [])

    finalizados = [
        j for j in jogos
        if j.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")
    ]

    return finalizados[:10]


# ============================================================
# RESUMO
# ============================================================

def resumo(jogos, team_id):

    if not jogos:
        return {"disponivel": False}

    vitorias = empates = derrotas = 0
    gols_marcados = gols_sofridos = 0
    over15 = over25 = ambas = 0
    total = 0

    for jogo in jogos:

        home = jogo.get("teams", {}).get("home", {})
        away = jogo.get("teams", {}).get("away", {})
        gols = jogo.get("goals", {})
        gh, ga = gols.get("home"), gols.get("away")

        if gh is None or ga is None:
            continue

        if home.get("id") == team_id:
            marcados, sofridos = gh, ga
        elif away.get("id") == team_id:
            marcados, sofridos = ga, gh
        else:
            continue

        total += 1
        gols_marcados += marcados
        gols_sofridos += sofridos

        if marcados > sofridos:
            vitorias += 1
        elif marcados == sofridos:
            empates += 1
        else:
            derrotas += 1

        total_gols = marcados + sofridos

        if total_gols >= 2:
            over15 += 1
        if total_gols >= 3:
            over25 += 1
        if marcados > 0 and sofridos > 0:
            ambas += 1

    if total == 0:
        return {"disponivel": False}

    # aproveitamento em % dos pontos possíveis (3 por vitória) —
    # fica na mesma escala 0-100 que over15/over25/ambas, então dá
    # pra comparar "força" de mercados diferentes sem distorcer.
    aproveitamento = round((vitorias * 3 + empates) / (total * 3) * 100)

    return {
        "disponivel": True,
        "jogos": total,
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "gols_marcados": gols_marcados,
        "gols_sofridos": gols_sofridos,
        "aproveitamento": aproveitamento,
        "over15": round(over15 / total * 100),
        "over25": round(over25 / total * 100),
        "ambas": round(ambas / total * 100)
    }


def texto_resumo(nome, dados, emoji):

    if not dados.get("disponivel"):
        return f"{emoji} **{nome}**\n⚠️ Histórico recente indisponível.\n"

    return (
        f"{emoji} **{nome}**\n"
        f"📊 Jogos analisados: {dados['jogos']}\n"
        f"🏆 {dados['vitorias']}V {dados['empates']}E {dados['derrotas']}D "
        f"(aproveitamento {dados['aproveitamento']}%)\n"
        f"⚽ Gols: {dados['gols_marcados']} marcados / {dados['gols_sofridos']} sofridos\n"
        f"📈 Over 1,5: {dados['over15']}% | Over 2,5: {dados['over25']}% | "
        f"Ambas marcam: {dados['ambas']}%\n"
    )


# ============================================================
# ANÁLISE PRÉ-JOGO
# ============================================================

def analisar_jogo(jogo):

    home = jogo.get("teams", {}).get("home", {})
    away = jogo.get("teams", {}).get("away", {})
    home_id, away_id = home.get("id"), away.get("id")

    home_resumo = resumo(ultimos_10(home_id), home_id)
    away_resumo = resumo(ultimos_10(away_id), away_id)

    oportunidades = []

    if home_resumo.get("disponivel") and away_resumo.get("disponivel"):

        diferenca = home_resumo["aproveitamento"] - away_resumo["aproveitamento"]

        if abs(diferenca) >= 8:
            favorito = home.get("name") if diferenca > 0 else away.get("name")
            oportunidades.append({
                "titulo": f"{favorito} ou empate (dupla chance)",
                "mercado": "Dupla possibilidade",
                "forca": min(50 + abs(diferenca), 95),
                "motivo": f"aproveitamento {abs(diferenca)} pontos percentuais melhor nos últimos jogos"
            })

        over25 = (home_resumo["over25"] + away_resumo["over25"]) / 2
        over15 = (home_resumo["over15"] + away_resumo["over15"]) / 2
        ambas = (home_resumo["ambas"] + away_resumo["ambas"]) / 2

        if over25 >= 60:
            oportunidades.append({
                "titulo": "Mais de 2,5 gols",
                "mercado": "Mais/Menos gols",
                "forca": over25,
                "motivo": f"Over 2,5 em média de {round(over25)}% dos últimos jogos dos dois times"
            })
        elif over15 >= 75:
            oportunidades.append({
                "titulo": "Mais de 1,5 gols",
                "mercado": "Mais/Menos gols",
                "forca": over15,
                "motivo": f"Over 1,5 em média de {round(over15)}% dos últimos jogos dos dois times"
            })
        elif ambas >= 60:
            oportunidades.append({
                "titulo": "Ambas marcam",
                "mercado": "Ambas Marcam",
                "forca": ambas,
                "motivo": f"ambas marcam em média de {round(ambas)}% dos últimos jogos"
            })

    if not oportunidades:
        oportunidades.append({
            "titulo": "Aguardar mais dados",
            "mercado": "Sem entrada estatística",
            "forca": 0,
            "motivo": "não há sinal estatístico forte o bastante nos últimos jogos"
        })

    oportunidades.sort(key=lambda x: x["forca"], reverse=True)

    return home_resumo, away_resumo, oportunidades[:2]


def formatar_jogo(jogo):

    fixture = jogo.get("fixture", {})
    home = jogo.get("teams", {}).get("home", {})
    away = jogo.get("teams", {}).get("away", {})

    data_hora = fixture.get("date", "")

    if "T" in data_hora:
        data, hora = data_hora.split("T")[0], data_hora.split("T")[1][:5]
        p = data.split("-")
        data = f"{p[2]}/{p[1]}/{p[0]}"
    else:
        data, hora = data_hora, ""

    home_resumo, away_resumo, oportunidades = analisar_jogo(jogo)

    liga = jogo.get("_nome_liga_bot") or jogo.get("league", {}).get("name", "Competição")

    texto = (
        f"⚽ **{home.get('name')} x {away.get('name')}**\n"
        f"🏆 {liga}\n"
        f"🆔 ID: `{fixture.get('id')}`\n"
        f"🕐 {data} {hora}\n\n"
        "📊 **ÚLTIMAS 10 PARTIDAS**\n\n"
    )

    texto += texto_resumo(home.get("name"), home_resumo, "🏠")
    texto += "\n"
    texto += texto_resumo(away.get("name"), away_resumo, "✈️")
    texto += "\n🎯 **MELHOR OPORTUNIDADE**\n\n"

    for i, op in enumerate(oportunidades, 1):
        texto += (
            f"**{i}. {op['titulo']}**\n"
            f"📌 Mercado: {op['mercado']}\n"
            f"📊 Confiança: **{round(op['forca'])}/100**\n"
            f"• {op['motivo']}\n\n"
        )

    texto += "⚠️ Análise estatística informativa. Não garante resultado."

    return texto


# ============================================================
# JOGOS AO VIVO
# ============================================================

def jogos_live():

    dados = api_get("/fixtures", {"live": LIGA_IDS, "timezone": "America/Sao_Paulo"})

    if not dados:
        return []

    jogos = []

    for jogo in dados.get("response", []):
        liga_id = jogo.get("league", {}).get("id")
        if liga_id in LIGAS:
            jogo["_nome_liga_bot"] = LIGAS[liga_id]
            jogos.append(jogo)

    return jogos


def estatisticas_live(fixture_id):

    dados = api_get("/fixtures/statistics", {"fixture": fixture_id})

    if not dados:
        return None

    return dados.get("response", [])


def stat(lista, nome):

    for item in lista or []:

        if item.get("type") != nome:
            continue

        valor = item.get("value")

        if valor is None:
            return None

        try:
            return float(str(valor).replace("%", "").replace(",", "."))
        except (ValueError, TypeError):
            return None

    return None


def fmt(valor):

    if valor is None:
        return "—"

    if float(valor).is_integer():
        return str(int(valor))

    return f"{valor:.1f}"


def analisar_live(jogo):

    fixture = jogo.get("fixture", {})
    home = jogo.get("teams", {}).get("home", {})
    away = jogo.get("teams", {}).get("away", {})

    gols_home = jogo.get("goals", {}).get("home", 0) or 0
    gols_away = jogo.get("goals", {}).get("away", 0) or 0
    minuto = fixture.get("status", {}).get("elapsed", "?")

    stats = estatisticas_live(fixture.get("id"))

    if stats is None:
        return (
            "🔴 **JOGO AO VIVO**\n\n"
            f"⚽ **{home.get('name')} {gols_home} x {gols_away} {away.get('name')}**\n"
            f"⏱️ Minuto: {minuto}'\n\n"
            "📊 **ESTATÍSTICAS**\n"
            "⚠️ Ainda não disponíveis na API para esta partida.\n"
        )

    home_stats, away_stats = [], []

    for equipe in stats:
        team_id = equipe.get("team", {}).get("id")
        if team_id == home.get("id"):
            home_stats = equipe.get("statistics", [])
        elif team_id == away.get("id"):
            away_stats = equipe.get("statistics", [])

    home_posse = stat(home_stats, "Ball Possession")
    away_posse = stat(away_stats, "Ball Possession")
    home_shots = stat(home_stats, "Total Shots")
    away_shots = stat(away_stats, "Total Shots")
    home_on = stat(home_stats, "Shots on Goal")
    away_on = stat(away_stats, "Shots on Goal")
    home_corners = stat(home_stats, "Corner Kicks")
    away_corners = stat(away_stats, "Corner Kicks")

    texto = (
        "🔴 **JOGO AO VIVO**\n\n"
        f"⚽ **{home.get('name')} {gols_home} x {gols_away} {away.get('name')}**\n"
        f"⏱️ Minuto: {minuto}'\n\n"
        "📊 **ESTATÍSTICAS**\n\n"
        f"🏠 {home.get('name')}\n"
        f"• Posse: {fmt(home_posse)}%\n"
        f"• Chutes: {fmt(home_shots)} (no gol: {fmt(home_on)})\n"
        f"• Escanteios: {fmt(home_corners)}\n\n"
        f"✈️ {away.get('name')}\n"
        f"• Posse: {fmt(away_posse)}%\n"
        f"• Chutes: {fmt(away_shots)} (no gol: {fmt(away_on)})\n"
        f"• Escanteios: {fmt(away_corners)}\n\n"
    )

    # --------------------------------------------------------
    # PRESSÃO POR LADO E MELHOR ENTRADA
    # (Nota: "Dangerous Attacks" não existe na API-Football —
    # removido; era um campo de outro provedor que nunca respondia.)
    # --------------------------------------------------------

    def pressao(posse, chutes, no_alvo, escanteios):
        pontos = 0
        if posse is not None:
            pontos += 30 if posse >= 60 else (18 if posse >= 55 else 0)
        if chutes is not None:
            pontos += 25 if chutes >= 10 else (14 if chutes >= 6 else 0)
        if no_alvo is not None:
            pontos += 30 if no_alvo >= 4 else (16 if no_alvo >= 2 else 0)
        if escanteios is not None:
            pontos += 15 if escanteios >= 6 else (8 if escanteios >= 3 else 0)
        return min(pontos, 100)

    pressao_home = pressao(home_posse, home_shots, home_on, home_corners)
    pressao_away = pressao(away_posse, away_shots, away_on, away_corners)

    diferenca = pressao_home - pressao_away
    soma = pressao_home + pressao_away
    gols_atuais = gols_home + gols_away

    texto += f"🏠 Pressão {home.get('name')}: **{pressao_home}/100**\n"
    texto += f"✈️ Pressão {away.get('name')}: **{pressao_away}/100**\n\n"

    if abs(diferenca) >= 25 and max(pressao_home, pressao_away) >= 50:
        favorito = home.get("name") if diferenca > 0 else away.get("name")
        texto += f"🎯 **MELHOR ENTRADA: {favorito} (pressão dominante)**\n"
    elif soma >= 120:
        proxima_linha = gols_atuais + 0.5
        texto += f"🎯 **MELHOR ENTRADA: Mais de {proxima_linha:g} gols (ambos pressionando)**\n"
    elif soma >= 70:
        texto += "🟠 Sinais de pressão dos dois lados — ainda sem entrada clara.\n"
    else:
        texto += "⏳ **SEM ENTRADA CLARA no momento.**\n"

    texto += "\n⚠️ Análise estatística informativa. Não garante resultado."

    return texto


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_bot():

    global bot

    if not BOT_TOKEN:
        print("ERRO: BOT_TOKEN não configurado. O bot do Telegram não vai iniciar.")
        return

    bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

    @bot.message_handler(commands=["start"])
    def start(message):
        bot.send_message(
            message.chat.id,
            "🤖 **BOT DE ANÁLISE DE FUTEBOL**\n\n"
            "🟢 Bot conectado.\n\n"
            "📡 Fonte: API-Sports / API-Football\n\n"
            "⚽ /jogos — Jogos do dia (com melhor oportunidade)\n"
            "🔴 /live — Jogos ao vivo com melhor entrada\n"
            "🔎 /analisar ID — Analisar um jogo específico\n"
            "📡 /status — Ver conexão e cota da API\n"
            "⏸️ /pausar — Pausar bot\n"
            "▶️ /ativar — Ativar bot\n\n"
            "ℹ️ As análises são informativas e não garantem resultados."
        )

    @bot.message_handler(commands=["status"])
    def status(message):
        ok, info = verificar_api()
        prefixo = "🟢 **API ONLINE**\n\n" if ok else "🔴 **API OFFLINE**\n\n"
        bot.send_message(message.chat.id, prefixo + info)

    @bot.message_handler(commands=["pausar"])
    def pausar(message):
        global BOT_ATIVO
        BOT_ATIVO = False
        bot.send_message(message.chat.id, "⏸️ Bot pausado.")

    @bot.message_handler(commands=["ativar"])
    def ativar(message):
        global BOT_ATIVO
        BOT_ATIVO = True
        bot.send_message(message.chat.id, "▶️ Bot ativado.")

    @bot.message_handler(commands=["jogos"])
    def jogos(message):

        if not BOT_ATIVO:
            bot.send_message(message.chat.id, "⏸️ Bot pausado.")
            return

        bot.send_message(message.chat.id, "🔎 **Buscando jogos das principais ligas...**")

        try:
            data = datetime.now(TZ).strftime("%Y-%m-%d")
            lista = jogos_do_dia(data)

            if not lista:
                bot.send_message(
                    message.chat.id,
                    "⚽ Nenhum jogo encontrado nas principais competições para hoje."
                )
                return

            for jogo in lista[:20]:
                bot.send_message(message.chat.id, formatar_jogo(jogo))
                time.sleep(1)

        except Exception as e:
            print("Erro /jogos:", e)
            bot.send_message(message.chat.id, "❌ Erro ao buscar os jogos.")

    @bot.message_handler(commands=["live"])
    def live(message):

        if not BOT_ATIVO:
            bot.send_message(message.chat.id, "⏸️ Bot pausado.")
            return

        bot.send_message(message.chat.id, "🔴 **Consultando jogos ao vivo...**")

        try:
            lista = jogos_live()

            if not lista:
                bot.send_message(
                    message.chat.id,
                    "⚽ Nenhum jogo ao vivo nas principais ligas agora."
                )
                return

            for jogo in lista[:10]:
                bot.send_message(message.chat.id, analisar_live(jogo))
                time.sleep(1)

        except Exception as e:
            print("Erro /live:", e)
            bot.send_message(message.chat.id, "❌ Erro ao consultar jogos ao vivo.")

    @bot.message_handler(commands=["analisar"])
    def analisar(message):

        partes = message.text.split()

        if len(partes) < 2:
            bot.send_message(message.chat.id, "Use /analisar ID (o ID aparece em /jogos)")
            return

        try:
            fixture_id = int(partes[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ ID inválido.")
            return

        dados = api_get("/fixtures", {"id": fixture_id, "timezone": "America/Sao_Paulo"})

        if not dados or not dados.get("response"):
            bot.send_message(message.chat.id, "❌ Partida não encontrada.")
            return

        jogo = dados["response"][0]
        league_id = jogo.get("league", {}).get("id")

        if league_id not in LIGAS:
            bot.send_message(
                message.chat.id,
                "⚠️ Esta partida não pertence às principais competições configuradas."
            )
            return

        jogo["_nome_liga_bot"] = LIGAS[league_id]
        bot.send_message(message.chat.id, formatar_jogo(jogo))

    # --------------------------------------------------------
    # Loop de polling. O 409 (Conflict) é esperado por alguns
    # segundos durante um redeploy, enquanto a instância antiga
    # ainda está desligando — tratado à parte, sem traceback
    # completo, e com uma pausa maior antes de tentar de novo.
    # --------------------------------------------------------

    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
        except ApiTelegramException as e:
            if getattr(e, "error_code", None) == 409:
                print("Outra instância ainda está ativa (409) — aguardando ela desligar...")
                time.sleep(15)
            else:
                print("Telegram (API):", e)
                time.sleep(10)
        except Exception as e:
            print("Telegram:", e)
            time.sleep(10)


# ============================================================
# RENDER
# ============================================================

@app.route("/")
def home():
    return "Bot funcionando!"


@app.route("/health")
def health():
    return {
        "status": "online",
        "bot": "telegram",
        "api": "API-Sports / API-Football",
        "ligas": list(LIGAS.values())
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("🤖 BOT DE FUTEBOL")
    print("📡 API-Sports / API-Football")
    print("=" * 50)

    if BOT_TOKEN and API_FOOTBALL_KEY:
        print("✅ Variáveis de ambiente encontradas.")
    else:
        print("ERRO: variáveis ausentes (BOT_TOKEN / API_FOOTBALL_KEY).")

    print("🤖 Iniciando Telegram...")

    threading.Thread(target=iniciar_bot, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))

    app.run(host="0.0.0.0", port=port)
