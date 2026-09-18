import os
import time
import threading
import requests
import telebot
from flask import Flask

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Chave já cadastrada no Render
API_FOOTBALL_KEY = (
    os.getenv("API_FOOTBALL_KEY")
    or os.getenv("API_SPORTS_KEY")
    or os.getenv("APISPORTS_KEY")
)

API_BASE = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_FOOTBALL_KEY
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não configurado no Render.")

if not API_FOOTBALL_KEY:
    raise RuntimeError(
        "API_FOOTBALL_KEY não configurada no Render."
    )

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ============================================================
# CONFIGURAÇÕES DAS COMPETIÇÕES
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
    11: "Copa Sul-Americana",
}

# ============================================================
# CONTROLE
# ============================================================

BOT_ATIVO = True

# Limite simples para evitar excesso de chamadas
CHAMADAS = []
LIMITE_CHAMADAS = 9
JANELA_SEGUNDOS = 60


# ============================================================
# CONTROLE DE REQUISIÇÕES
# ============================================================

def pode_consultar():
    agora = time.time()

    while CHAMADAS and agora - CHAMADAS[0] > JANELA_SEGUNDOS:
        CHAMADAS.pop(0)

    if len(CHAMADAS) >= LIMITE_CHAMADAS:
        return False

    CHAMADAS.append(agora)
    return True


def api_get(endpoint, params=None):
    if not pode_consultar():
        return None, "Limite temporário de consultas atingido."

    try:
        resposta = requests.get(
            API_BASE + endpoint,
            headers=HEADERS,
            params=params,
            timeout=20
        )

        if resposta.status_code != 200:
            return None, (
                f"API retornou HTTP {resposta.status_code}: "
                f"{resposta.text[:300]}"
            )

        dados = resposta.json()

        erros = dados.get("errors")

        if erros:
            return None, f"Erro API: {erros}"

        return dados, None

    except requests.exceptions.Timeout:
        return None, "Tempo limite da API excedido."

    except requests.exceptions.RequestException as e:
        return None, f"Erro de conexão: {e}"

    except Exception as e:
        return None, f"Erro inesperado: {e}"


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def valor_statisticas(lista, nome):
    for item in lista or []:
        if item.get("type") == nome:
            valor = item.get("value")

            if valor is None:
                return 0

            if isinstance(valor, str):
                valor = valor.replace("%", "").strip()

            try:
                return float(valor)
            except:
                return 0

    return 0


def inteiro(valor):
    try:
        return int(float(valor))
    except:
        return 0


def percentual(valor):
    try:
        return f"{float(valor):.0f}%"
    except:
        return "0%"


def nome_liga(fixture):
    liga = fixture.get("league", {})
    league_id = liga.get("id")

    return (
        LIGAS.get(league_id)
        or liga.get("name")
        or "Competição"
    )


def horario_jogo(fixture):
    data = fixture.get("fixture", {}).get("date", "")

    if not data:
        return "Horário não informado"

    try:
        # A API está sendo chamada com timezone de São Paulo.
        return data.replace("T", " ")[:16]
    except:
        return data


def minuto_jogo(fixture):
    elapsed = (
        fixture
        .get("fixture", {})
        .get("status", {})
        .get("elapsed")
    )

    return elapsed or 0


def status_jogo(fixture):
    return (
        fixture
        .get("fixture", {})
        .get("status", {})
        .get("long")
        or "Ao vivo"
    )


# ============================================================
# BUSCAR JOGOS AO VIVO
# ============================================================

def buscar_jogos_ao_vivo():
    dados, erro = api_get(
        "/fixtures",
        {
            "live": "all",
            "timezone": "America/Sao_Paulo"
        }
    )

    if erro:
        return [], erro

    fixtures = dados.get("response", [])

    jogos = []

    for fixture in fixtures:

        league_id = fixture.get("league", {}).get("id")

        if league_id in LIGAS:
            jogos.append(fixture)

    return jogos, None


# ============================================================
# BUSCAR DADOS DO JOGO
# ============================================================

def buscar_estatisticas(fixture_id):
    dados, erro = api_get(
        "/fixtures/statistics",
        {
            "fixture": fixture_id
        }
    )

    if erro:
        return [], erro

    return dados.get("response", []), None


def buscar_fixture(fixture_id):
    dados, erro = api_get(
        "/fixtures",
        {
            "id": fixture_id,
            "timezone": "America/Sao_Paulo"
        }
    )

    if erro:
        return None, erro

    resposta = dados.get("response", [])

    if not resposta:
        return None, "Jogo não encontrado."

    return resposta[0], None


# ============================================================
# PRESSÃO
# ============================================================

def calcular_pressao(estatisticas):
    if not estatisticas or len(estatisticas) < 2:
        return None

    casa = estatisticas[0]
    fora = estatisticas[1]

    def extrair(equipe):
        return {
            "posse": valor_statisticas(
                equipe.get("statistics"), "Ball Possession"
            ),
            "finalizacoes": valor_statisticas(
                equipe.get("statistics"), "Total Shots"
            ),
            "finalizacoes_alvo": valor_statisticas(
                equipe.get("statistics"), "Shots on Goal"
            ),
            "escanteios": valor_statisticas(
                equipe.get("statistics"), "Corner Kicks"
            ),
            "ataques_perigosos": valor_statisticas(
                equipe.get("statistics"), "Dangerous Attacks"
            ),
            "cartoes": valor_statisticas(
                equipe.get("statistics"), "Yellow Cards"
            ),
        }

    c = extrair(casa)
    f = extrair(fora)

    # Pontuação relativa.
    # Serve para indicar domínio/pressão, não é uma probabilidade
    # de vitória.
    peso = {
        "posse": 0.10,
        "finalizacoes": 0.20,
        "finalizacoes_alvo": 0.30,
        "escanteios": 0.15,
        "ataques_perigosos": 0.25,
    }

    def pontuacao(dados):
        return (
            dados["posse"] * peso["posse"]
            + dados["finalizacoes"] * peso["finalizacoes"]
            + dados["finalizacoes_alvo"] * peso["finalizacoes_alvo"]
            + dados["escanteios"] * peso["escanteios"]
            + dados["ataques_perigosos"] * peso["ataques_perigosos"]
        )

    score_casa = pontuacao(c)
    score_fora = pontuacao(f)

    total = score_casa + score_fora

    if total > 0:
        dominio_casa = (score_casa / total) * 100
        dominio_fora = (score_fora / total) * 100
    else:
        dominio_casa = 50
        dominio_fora = 50

    return {
        "casa": c,
        "fora": f,
        "score_casa": score_casa,
        "score_fora": score_fora,
        "dominio_casa": dominio_casa,
        "dominio_fora": dominio_fora,
    }


# ============================================================
# ANÁLISE DO JOGO
# ============================================================

def analisar_jogo(fixture):
    fixture_data = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    goals = fixture.get("goals", {})

    fixture_id = fixture_data.get("id")

    casa = teams.get("home", {}).get("name", "Casa")
    fora = teams.get("away", {}).get("name", "Fora")

    gols_casa = goals.get("home")
    gols_fora = goals.get("away")

    estatisticas, erro = buscar_estatisticas(fixture_id)

    pressao = calcular_pressao(estatisticas)

    mensagem = []

    mensagem.append(
        f"⚽ <b>{casa} x {fora}</b>"
    )

    mensagem.append(
        f"🏆 {nome_liga(fixture)}"
    )

    mensagem.append(
        f"🕐 {horario_jogo(fixture)}"
    )

    mensagem.append(
        f"⏱️ {minuto_jogo(fixture)}' — {status_jogo(fixture)}"
    )

    mensagem.append(
        f"📊 Placar: <b>{gols_casa or 0} x {gols_fora or 0}</b>"
    )

    mensagem.append("")

    if erro:
        mensagem.append(
            f"⚠️ Não foi possível carregar as estatísticas: {erro}"
        )

        return "\n".join(mensagem)

    if not pressao:
        mensagem.append(
            "📊 Estatísticas detalhadas ainda não disponíveis."
        )

        return "\n".join(mensagem)

    c = pressao["casa"]
    f = pressao["fora"]

    mensagem.append("📈 <b>ESTATÍSTICAS AO VIVO</b>")

    mensagem.append(
        f"Possession: {percentual(c['posse'])} x "
        f"{percentual(f['posse'])}"
    )

    mensagem.append(
        f"Finalizações: {inteiro(c['finalizacoes'])} x "
        f"{inteiro(f['finalizacoes'])}"
    )

    mensagem.append(
        f"No alvo: {inteiro(c['finalizacoes_alvo'])} x "
        f"{inteiro(f['finalizacoes_alvo'])}"
    )

    mensagem.append(
        f"Escanteios: {inteiro(c['escanteios'])} x "
        f"{inteiro(f['escanteios'])}"
    )

    mensagem.append(
        f"Ataques perigosos: "
        f"{inteiro(c['ataques_perigosos'])} x "
        f"{inteiro(f['ataques_perigosos'])}"
    )

    mensagem.append("")

    mensagem.append("🔥 <b>PRESSÃO RELATIVA</b>")

    mensagem.append(
        f"{casa}: {pressao['dominio_casa']:.0f}%"
    )

    mensagem.append(
        f"{fora}: {pressao['dominio_fora']:.0f}%"
    )

    diferenca = abs(
        pressao["dominio_casa"]
        - pressao["dominio_fora"]
    )

    if diferenca >= 30:
        if pressao["dominio_casa"] > pressao["dominio_fora"]:
            dominante = casa
        else:
            dominante = fora

        mensagem.append(
            f"🔴 <b>Pressão forte:</b> {dominante}"
        )

    elif diferenca >= 15:
        if pressao["dominio_casa"] > pressao["dominio_fora"]:
            dominante = casa
        else:
            dominante = fora

        mensagem.append(
            f"🟠 <b>Pressão moderada:</b> {dominante}"
        )

    else:
        mensagem.append(
            "🟡 <b>Jogo equilibrado</b>"
        )

    mensagem.append("")

    mensagem.append(
        "ℹ️ Análise estatística ao vivo. "
        "Não é garantia de resultado."
    )

    return "\n".join(mensagem)


# ============================================================
# TELEGRAM — /START
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):

    texto = (
        "🤖 <b>BOT DE ANÁLISE DE FUTEBOL</b>\n\n"
        "🟢 Bot conectado.\n\n"
        "📡 Fonte de dados: API-Sports / API-Football\n\n"
        "Comandos disponíveis:\n\n"
        "⚽ /live — Jogos ao vivo\n"
        "🔎 /analisar ID — Analisar um jogo\n"
        "📡 /status — Ver conexão\n"
        "⏸️ /pausar — Pausar bot\n"
        "▶️ /ativar — Ativar bot\n\n"
        "A análise considera estatísticas "
        "e pressão do jogo em tempo real.\n\n"
        "ℹ️ As análises são informativas e "
        "não garantem resultados."
    )

    bot.reply_to(message, texto)


# ============================================================
# /STATUS
# ============================================================

@bot.message_handler(commands=["status"])
def status(message):

    dados, erro = api_get(
        "/status"
    )

    if erro:
        bot.reply_to(
            message,
            f"🔴 <b>API OFFLINE</b>\n\n{erro}"
        )
        return

    bot.reply_to(
        message,
        "🟢 <b>SISTEMA ONLINE</b>\n\n"
        "Telegram: conectado ✅\n"
        "API-Sports: conectada ✅\n"
        "Render: ativo ✅\n\n"
        f"Bot ativo: {'SIM' if BOT_ATIVO else 'NÃO'}"
    )


# ============================================================
# /LIVE
# ============================================================

@bot.message_handler(commands=["live"])
def live(message):

    if not BOT_ATIVO:
        bot.reply_to(
            message,
            "⏸️ O bot está pausado.\n"
            "Use /ativar para ativá-lo."
        )
        return

    bot.send_message(
        message.chat.id,
        "🔎 <b>Buscando jogos ao vivo...</b>"
    )

    jogos, erro = buscar_jogos_ao_vivo()

    if erro:
        bot.send_message(
            message.chat.id,
            f"🔴 Erro ao consultar API:\n{erro}"
        )
        return

    if not jogos:
        bot.send_message(
            message.chat.id,
            "⚽ Nenhum jogo ao vivo encontrado "
            "nas competições configuradas."
        )
        return

    texto = "🔴 <b>JOGOS AO VIVO</b>\n\n"

    for jogo in jogos:

        fixture_id = jogo.get("fixture", {}).get("id")

        casa = (
            jogo.get("teams", {})
            .get("home", {})
            .get("name", "Casa")
        )

        fora = (
            jogo.get("teams", {})
            .get("away", {})
            .get("name", "Fora")
        )

        gols_casa = jogo.get("goals", {}).get("home") or 0
        gols_fora = jogo.get("goals", {}).get("away") or 0

        minuto = minuto_jogo(jogo)

        texto += (
            f"⚽ <b>{casa} x {fora}</b>\n"
            f"🏆 {nome_liga(jogo)}\n"
            f"📊 {gols_casa} x {gols_fora}\n"
            f"⏱️ {minuto}'\n"
            f"🆔 <code>{fixture_id}</code>\n\n"
        )

    texto += (
        "👉 Use <code>/analisar ID</code> "
        "para analisar um jogo."
    )

    bot.send_message(
        message.chat.id,
        texto
    )


# ============================================================
# /ANALISAR ID
# ============================================================

@bot.message_handler(commands=["analisar"])
def analisar(message):

    partes = message.text.split()

    if len(partes) < 2:
        bot.reply_to(
            message,
            "Use assim:\n\n"
            "<code>/analisar 123456</code>"
        )
        return

    try:
        fixture_id = int(partes[1])
    except:
        bot.reply_to(
            message,
            "❌ O ID do jogo precisa ser numérico."
        )
        return

    bot.send_message(
        message.chat.id,
        "🔎 <b>Analisando jogo...</b>"
    )

    fixture, erro = buscar_fixture(fixture_id)

    if erro:
        bot.send_message(
            message.chat.id,
            f"🔴 {erro}"
        )
        return

    resultado = analisar_jogo(fixture)

    bot.send_message(
        message.chat.id,
        resultado
    )


# ============================================================
# /PAUSAR
# ============================================================

@bot.message_handler(commands=["pausar"])
def pausar(message):

    global BOT_ATIVO

    BOT_ATIVO = False

    bot.reply_to(
        message,
        "⏸️ <b>Bot pausado.</b>\n\n"
        "Use /ativar para ativar novamente."
    )


# ============================================================
# /ATIVAR
# ============================================================

@bot.message_handler(commands=["ativar"])
def ativar(message):

    global BOT_ATIVO

    BOT_ATIVO = True

    bot.reply_to(
        message,
        "▶️ <b>Bot ativado novamente.</b>"
    )


# ============================================================
# FLASK / RENDER
# ============================================================

@app.route("/")
def home():

    return (
        "Bot funcionando! "
        "API-Sports/API-Football conectado."
    )


@app.route("/health")
def health():

    return {
        "status": "online",
        "bot_ativo": BOT_ATIVO,
        "api": "API-Sports/API-Football"
    }


# ============================================================
# SERVIDOR WEB
# ============================================================

def iniciar_servidor():

    porta = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=porta
    )


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_bot():

    while True:

        try:
            print("🤖 Iniciando Telegram...")

            bot.remove_webhook()

            time.sleep(2)

            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                f"❌ Erro no Telegram: {e}"
            )

            time.sleep(10)


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    print(
        "======================================"
    )

    print(
        "🤖 BOT DE FUTEBOL"
    )

    print(
        "📡 API-Sports/API-Football"
    )

    print(
        "🚀 Iniciando..."
    )

    print(
        "======================================"
    )

    servidor = threading.Thread(
        target=iniciar_servidor,
        daemon=True
    )

    servidor.start()

    iniciar_bot()
