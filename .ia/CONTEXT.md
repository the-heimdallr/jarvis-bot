# Contexto del Proyecto: Jarvis Telegram Bot

## Descripción General
Bot de Telegram actuando como asistente personal ("Jarvis") integrado con la API de Google Gemini (gemini-flash-latest). Desplegado en el plan Free de Render con Flask y Gunicorn.

## Stack Tecnológico
- **Lenguaje:** Python 3
- **Framework Web:** Flask 3.0.3
- **Servidor WSGI:** Gunicorn 22.0.0
- **Librerías principales:** Requests, PyPDF 5.0.1
- **Procesamiento de IA:** Google Gemini API (`v1beta/models/gemini-flash-latest`)
- **Infraestructura:** Render (Web Service Free) + Webhook de Telegram API

## Arquitectura y Componentes Clave (`app.py`)
1. **Webhook Handler (`/webhook/<WEBHOOK_SECRET>`):**
   - Recibe eventos de Telegram (mensajes privados, posts en canales y adjuntos PDF).
2. **Procesamiento de PDFs (Biblioteca de estudio):**
   - Extrae texto con `pypdf`, procesa de forma asíncrona mediante hilos (`threading.Thread`) y almacena temporalmente en memoria RAM (`DOCUMENTS`).
3. **Sistemas de Herramientas / Function Calling:**
   - Implementa integración nativa con Gemini mediante `TOOLS` y la función `get_weather` (usando Open-Meteo API).
4. **Administración y Canales:**
   - Comandos restringidos por `OWNER_ID` (`/canales`, `/post`, `/pin`, `/resumen`, `/libros`, `/libro`).
   - Gestión de publicaciones recientes en memoria RAM (`RECENT_POSTS`).

## Variables de Entorno Requeridas
- `TELEGRAM_TOKEN`: Token de BotFather.
- `GEMINI_API_KEY`: API Key de Google AI Studio.
- `WEBHOOK_SECRET`: Clave de seguridad de la ruta webhook.
- `OWNER_ID`: ID numérico de Telegram del administrador.
- `CHANNELS`: Mapeo en formato `nombre1:id1,nombre2:id2`.

## Limitaciones Conocidas
- **Estado Volátil:** La memoria de chats (`CONVERSATIONS`), posteos (`RECENT_POSTS`) y documentos (`DOCUMENTS`) reside en RAM y se reinicia si el server duerme en Render.
- **Límite de PDF:** Máximo de 300,000 caracteres por documento debido a restricciones de memoria en Render
- Free.
