# Tu Jarvis en Telegram — guía de despliegue (100% gratis, sin tarjeta)

## Archivos
- `app.py` — el bot (Flask + Gemini)
- `requirements.txt` — dependencias

## Paso 1 — Crear el bot en Telegram
1. Abrí Telegram y buscá **@BotFather**.
2. Enviale `/newbot`, elegí un nombre y un usuario (debe terminar en "bot").
3. Te da un **token** tipo `123456789:ABCdefGhIJKlmNoPQRstuVwxYZ`. Guardalo.

## Paso 2 — Conseguir la clave de Gemini (gratis)
1. Entrá a https://aistudio.google.com/apikey
2. Iniciá sesión con tu cuenta de Google y creá una clave (**API key**).
3. Guardala.

## Paso 3 — Subir el código a GitHub
1. Creá un repositorio nuevo en https://github.com (gratis, sin tarjeta).
2. Subí `app.py` y `requirements.txt` a ese repositorio.
   - Se puede hacer desde la web de GitHub con "Add file → Upload files", sin usar la terminal.

## Paso 4 — Desplegar en Render (gratis, sin tarjeta)
1. Creá cuenta en https://render.com (podés entrar con GitHub).
2. Click en **New → Web Service**.
3. Elegí el repositorio que subiste.
4. Configurá:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Plan:** Free
5. En la sección **Environment**, agregá estas variables:
   - `TELEGRAM_TOKEN` → el token de @BotFather
   - `GEMINI_API_KEY` → la clave de Gemini
   - `WEBHOOK_SECRET` → inventá una palabra secreta, por ejemplo `javi2026secreto`
6. Click en **Create Web Service**. Cuando termina el deploy, Render te da una URL pública, algo como:
   `https://jarvis-bot-xxxx.onrender.com`

## Paso 5 — Conectar Telegram con tu bot en Render
Reemplazá `TU_TOKEN`, `TU_URL` y `TU_SECRETO` por tus datos reales, y abrí esta URL en el navegador (una sola vez):

```
https://api.telegram.org/botTU_TOKEN/setWebhook?url=TU_URL/webhook/TU_SECRETO
```

Ejemplo real:
```
https://api.telegram.org/bot123456789:ABCdef/setWebhook?url=https://jarvis-bot-xxxx.onrender.com/webhook/javi2026secreto
```

Si devuelve `"ok":true`, ya está conectado.

## Paso 6 — Probarlo
Andá a Telegram, abrí el chat con tu bot, mandale `/start` y después cualquier mensaje. Debería responderte usando Gemini.

## Notas importantes
- El plan gratuito de Render "duerme" el servicio si no recibe mensajes por un rato. El primer mensaje después de la inactividad puede tardar hasta ~1 minuto en responder — es normal, no está roto.
- La memoria de la conversación es temporal (se pierde si el servicio se reinicia). Si más adelante querés que recuerde todo permanentemente, se puede sumar una base de datos gratuita.
- Cuando tengas tu servidor dedicado, migrar es simple: mismo código, mismo `requirements.txt`, solo cambia dónde corre.
