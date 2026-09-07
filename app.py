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
import time
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-bot")

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
)

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


def send_telegram_message(chat_id: int, text: str):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )


@app.route("/", methods=["GET"])
def health():
    # Render usa esta ruta para saber que el servicio está vivo.
    return jsonify(status="ok", bot="jarvis")


@app.route(f"/webhook/{WEBHOOK_SECRET if WEBHOOK_SECRET else 'hook'}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")

    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if not text:
        send_telegram_message(chat_id, "Por ahora solo entiendo texto.")
        return jsonify(ok=True)

    if text.strip().lower() in ("/start", "/reset"):
        CONVERSATIONS.pop(chat_id, None)
        send_telegram_message(chat_id, "¡Hola! Soy tu Jarvis. ¿En qué te ayudo?")
        return jsonify(ok=True)

    reply = ask_gemini(chat_id, text)
    send_telegram_message(chat_id, reply)
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
    
