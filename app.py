"""
Bot de Telegram con IA (Gemini) - listo para desplegar en Render (plan Free)

Cómo funciona:
1. Telegram te manda cada mensaje nuevo a la URL /webhook de este servidor.
2. Este servidor le pasa el mensaje a Gemini.
3. Gemini responde y este servidor le contesta al usuario por Telegram.

Variables de entorno necesarias (se configuran en Render, no acá):
- TELEGRAM_TOKEN   -> el token que te dio @BotFather
- GEMINI_API_KEY   -> la clave gratuita de https://aistudio.google.com/apikey
- WEBHOOK_SECRET   -> una palabra secreta inventada por vos (protege el webhook)
"""

import os
import io
import re
import time
import gc
import unicodedata
import threading
import logging
import requests
from pypdf import PdfReader
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-bot")

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme")
OWNER_ID = os.environ.get("OWNER_ID")  # tu ID de Telegram (numérico), para que solo vos administres los canales

# CHANNELS: "nombre1:id1,nombre2:id2" -> ej "ciberdefensa:-1001234567890,cienciaspoliticas:-1009876543210"
def _parse_channels():
    raw = os.environ.get("CHANNELS", "")
    channels = {}
    for pair in raw.split(","):
        if ":" in pair:
            name, cid = pair.split(":", 1)
            channels[name.strip().lower()] = cid.strip()
    return channels

CHANNELS = _parse_channels()

# Guarda los últimos posts de cada canal en RAM (se pierde si el server reinicia)
RECENT_POSTS = {name: [] for name in CHANNELS}
MAX_POSTS_PER_CHANNEL = 30

# Documentos (PDFs) que el bot fue aprendiendo. En RAM: se pierde si el server reinicia.
DOCUMENTS = {}  # clave corta -> {"title": ..., "text": ...}
MAX_DOC_CHARS = 300_000  # límite de texto por documento (memoria limitada en el plan Free de Render)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
)

# --- Manejo de documentos PDF (biblioteca de estudio) ---

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "documento"


def download_telegram_file(file_id: str) -> bytes:
    info = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=15).json()
    file_path = info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    return requests.get(file_url, timeout=60).content


def extract_pdf_text(pdf_bytes: bytes) -> str:
    text_parts = []
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for page in reader.pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            continue
        if sum(len(t) for t in text_parts) > MAX_DOC_CHARS:
            break
    del reader
    gc.collect()
    return "\n".join(text_parts)[:MAX_DOC_CHARS]


def process_document_async(document: dict, notify_chat_id):
    resultado = handle_incoming_document(document)
    send_telegram_message(notify_chat_id, resultado)


def handle_incoming_document(document: dict) -> str:
    file_name = document.get("file_name", "documento.pdf")
    if not file_name.lower().endswith(".pdf"):
        return f"'{file_name}' no es un PDF — por ahora solo puedo leer PDFs."

    try:
        pdf_bytes = download_telegram_file(document["file_id"])
        text = extract_pdf_text(pdf_bytes)
    except Exception:
        log.exception("Error procesando PDF")
        return "No pude procesar ese PDF."

    if not text.strip():
        return f"Descargué '{file_name}' pero no pude extraerle texto (puede ser un PDF escaneado como imagen)."

    key = slugify(file_name.rsplit(".", 1)[0])
    DOCUMENTS[key] = {"title": file_name, "text": text}
    return (
        f"Aprendí '{file_name}' ({len(text):,} caracteres).\n"
        f"Preguntame con: /libro {key} tu pregunta\n"
        f"Ver todos: /libros"
    )


def ask_about_document(key: str, question: str) -> str:
    doc = DOCUMENTS.get(key)
    if not doc:
        return f"No tengo ningún documento guardado como '{key}'. Usá /libros para ver la lista."

    prompt = (
        f"Basándote únicamente en el siguiente documento titulado '{doc['title']}', "
        f"respondé en español la pregunta del usuario. Si la respuesta no está en el "
        f"documento, decilo claramente.\n\n"
        f"DOCUMENTO:\n{doc['text']}\n\n"
        f"PREGUNTA: {question}"
    )
    try:
        data = call_gemini([{"role": "user", "parts": [{"text": prompt}]}])
        parts = data["candidates"][0]["content"].get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip() or "No pude generar una respuesta."
    except Exception:
        log.exception("Error consultando documento")
        return "Tuve un problema para responder sobre ese documento. Probá de nuevo."


# Memoria simple en RAM: guarda los últimos mensajes por chat.
# Se pierde si el servidor gratis de Render "duerme" y se reinicia.
# Para memoria permanente habría que sumar una base de datos (se puede agregar después).
CONVERSATIONS = {}
MAX_HISTORY = 12  # cantidad de mensajes (usuario+bot) que recuerda por chat


def get_weather(location: str) -> dict:
    """Consulta el clima actual y el pronóstico usando Open-Meteo (gratis, sin API key)."""
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "es"},
            timeout=15,
        ).json()
        if not geo.get("results"):
            return {"error": f"No encontré la ubicación '{location}'"}

        place = geo["results"][0]
        lat, lon = place["latitude"], place["longitude"]

        forecast = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=15,
        ).json()

        return {
            "lugar": place.get("name"),
            "pais": place.get("country"),
            "actual": forecast.get("current"),
            "hoy_y_manana": forecast.get("daily"),
        }
    except Exception:
        log.exception("Error consultando el clima")
        return {"error": "No pude consultar el clima ahora mismo"}


TOOLS = [{
    "functionDeclarations": [{
        "name": "get_weather",
        "description": "Devuelve el clima actual y el pronóstico de hoy y mañana para una ciudad.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Nombre de la ciudad, ej: 'Formosa, Argentina'",
                }
            },
            "required": ["location"],
        },
    }]
}]

AVAILABLE_FUNCTIONS = {"get_weather": get_weather}


def call_gemini(contents: list) -> dict:
    payload = {
        "contents": contents,
        "tools": TOOLS,
        "systemInstruction": {
            "parts": [{
                "text": (
                    "Sos Jarvis, el asistente personal de Javier. "
                    "Respondé siempre en español, de forma clara y directa. "
                    "Usá las herramientas disponibles cuando la pregunta las necesite."
                )
            }]
        },
    }
    for attempt in range(3):
        r = requests.post(GEMINI_API, json=payload, timeout=30)
        if r.status_code in (503, 429) and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def ask_gemini(chat_id: int, user_text: str) -> str:
    history = CONVERSATIONS.get(chat_id, [])
    history.append({"role": "user", "parts": [{"text": user_text}]})
    history = history[-MAX_HISTORY:]

    try:
        data = call_gemini(history)
        candidate = data["candidates"][0]["content"]
        parts = candidate.get("parts", [])

        function_call_part = next((p for p in parts if "functionCall" in p), None)

        if function_call_part:
            fn_name = function_call_part["functionCall"]["name"]
            fn_args = function_call_part["functionCall"].get("args", {})
            fn = AVAILABLE_FUNCTIONS.get(fn_name)
            result = fn(**fn_args) if fn else {"error": "función no disponible"}

            history.append({"role": "model", "parts": parts})
            history.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": fn_name,
                        "response": {"result": result},
                    }
                }],
            })

            data = call_gemini(history)
            candidate = data["candidates"][0]["content"]
            parts = candidate.get("parts", [])

        reply = "".join(p.get("text", "") for p in parts).strip()
        if not reply:
            reply = "No pude generar una respuesta."

    except Exception:
        log.exception("Error llamando a Gemini")
        reply = "Tuve un problema para pensar la respuesta. Probá de nuevo en un momento."
        history.append({"role": "model", "parts": [{"text": reply}]})
        CONVERSATIONS[chat_id] = history[-MAX_HISTORY:]
        return reply

    history.append({"role": "model", "parts": [{"text": reply}]})
    CONVERSATIONS[chat_id] = history[-MAX_HISTORY:]
    return reply


def send_telegram_message(chat_id, text: str):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )


def pin_last_message(channel_name: str) -> str:
    posts = RECENT_POSTS.get(channel_name, [])
    if not posts:
        return f"No tengo mensajes registrados todavía de '{channel_name}'."
    last = posts[-1]
    r = requests.post(
        f"{TELEGRAM_API}/pinChatMessage",
        json={"chat_id": CHANNELS[channel_name], "message_id": last["message_id"]},
        timeout=15,
    )
    return "Mensaje fijado." if r.ok else f"No pude fijarlo: {r.text}"


def summarize_channel(channel_name: str) -> str:
    posts = RECENT_POSTS.get(channel_name, [])
    if not posts:
        return f"Todavía no vi mensajes nuevos en '{channel_name}' desde que arrancó el bot."
    texto = "\n---\n".join(p["text"] for p in posts[-15:] if p.get("text"))
    prompt = (
        f"Resumí en español, en viñetas claras, los temas tratados en estos "
        f"últimos posts del canal '{channel_name}':\n\n{texto}"
    )
    try:
        data = call_gemini([{"role": "user", "parts": [{"text": prompt}]}])
        parts = data["candidates"][0]["content"].get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip() or "No pude armar el resumen."
    except Exception:
        log.exception("Error resumiendo canal")
        return "Tuve un problema armando el resumen. Probá de nuevo."


def handle_channel_post(post: dict):
    chat_id_str = str(post["chat"]["id"])
    name = next((n for n, cid in CHANNELS.items() if cid == chat_id_str), None)
    if not name:
        # Canal no configurado todavía: le avisamos al dueño su ID para que lo agregue.
        if OWNER_ID:
            titulo = post["chat"].get("title", "sin título")
            send_telegram_message(
                OWNER_ID,
                f"Vi un post en un canal no configurado.\n"
                f"Título: {titulo}\nID: {chat_id_str}\n\n"
                f"Agregalo a la variable CHANNELS en Render, ej:\n"
                f"nombre_que_quieras:{chat_id_str}",
            )
        return

    if post.get("document"):
        if OWNER_ID:
            threading.Thread(
                target=lambda: send_telegram_message(
                    OWNER_ID, f"[{name}] " + handle_incoming_document(post["document"])
                ),
                daemon=True,
            ).start()
        return

    text = post.get("text") or post.get("caption") or ""
    RECENT_POSTS.setdefault(name, []).append({
        "message_id": post["message_id"],
        "text": text,
    })
    RECENT_POSTS[name] = RECENT_POSTS[name][-MAX_POSTS_PER_CHANNEL:]


def handle_owner_command(chat_id: int, text: str) -> bool:
    """Devuelve True si el texto era un comando de administración y ya fue atendido."""
    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd == "/canales":
        if not CHANNELS:
            send_telegram_message(chat_id, "No tenés canales configurados en CHANNELS todavía.")
        else:
            lista = "\n".join(f"• {n}" for n in CHANNELS)
            send_telegram_message(chat_id, f"Canales configurados:\n{lista}")
        return True

    if cmd == "/post" and len(parts) == 3:
        name, msg = parts[1].lower(), parts[2]
        if name not in CHANNELS:
            send_telegram_message(chat_id, f"No conozco el canal '{name}'. Usá /canales para ver la lista.")
        else:
            send_telegram_message(CHANNELS[name], msg)
            send_telegram_message(chat_id, f"Publicado en {name}.")
        return True

    if cmd == "/pin" and len(parts) == 2:
        name = parts[1].lower()
        if name not in CHANNELS:
            send_telegram_message(chat_id, f"No conozco el canal '{name}'.")
        else:
            send_telegram_message(chat_id, pin_last_message(name))
        return True

    if cmd == "/resumen" and len(parts) == 2:
        name = parts[1].lower()
        if name not in CHANNELS:
            send_telegram_message(chat_id, f"No conozco el canal '{name}'.")
        else:
            send_telegram_message(chat_id, summarize_channel(name))
        return True

    if cmd == "/libros":
        if not DOCUMENTS:
            send_telegram_message(chat_id, "Todavía no aprendí ningún PDF. Reenviame uno.")
        else:
            lista = "\n".join(f"• {k} — {d['title']}" for k, d in DOCUMENTS.items())
            send_telegram_message(chat_id, f"Documentos guardados:\n{lista}")
        return True

    if cmd == "/libro" and len(parts) == 3:
        key, question = parts[1].lower(), parts[2]
        send_telegram_message(chat_id, ask_about_document(key, question))
        return True

    return False


@app.route("/", methods=["GET"])
def health():
    # Render usa esta ruta para saber que el servicio está vivo.
    return jsonify(status="ok", bot="jarvis")


@app.route(f"/webhook/{WEBHOOK_SECRET if WEBHOOK_SECRET else 'hook'}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}

    if update.get("channel_post"):
        handle_channel_post(update["channel_post"])
        return jsonify(ok=True)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]
    sender_id = str(message.get("from", {}).get("id", ""))
    text = message.get("text", "")

    if message.get("document") and OWNER_ID and sender_id == OWNER_ID:
        send_telegram_message(chat_id, "Recibí el PDF, lo estoy procesando (puede tardar un minuto)...")
        threading.Thread(
            target=process_document_async,
            args=(message["document"], chat_id),
            daemon=True,
        ).start()
        return jsonify(ok=True)

    if not text:
        send_telegram_message(chat_id, "Por ahora solo entiendo texto.")
        return jsonify(ok=True)

    if text.strip().lower() in ("/start", "/reset"):
        CONVERSATIONS.pop(chat_id, None)
        send_telegram_message(chat_id, "¡Hola! Soy tu Jarvis. ¿En qué te ayudo?")
        return jsonify(ok=True)

    # Comandos de administración de canales: solo el dueño puede usarlos
    if text.startswith("/") and OWNER_ID and sender_id == OWNER_ID:
        if handle_owner_command(chat_id, text):
            return jsonify(ok=True)

    reply = ask_gemini(chat_id, text)
    send_telegram_message(chat_id, reply)
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
    
