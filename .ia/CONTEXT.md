# Contexto del proyecto

## Estado actual

Jarvis es un bot de Telegram implementado en un único archivo, `app.py`, con Flask y desplegable en Render mediante Gunicorn. Recibe actualizaciones de Telegram por webhook y usa Gemini para responder en español.

## Componentes de `app.py`

- Configuración mediante variables de entorno: `TELEGRAM_TOKEN`, `GEMINI_API_KEY`, `WEBHOOK_SECRET`, `OWNER_ID` y `CHANNELS`.
- Endpoint `GET /` para healthcheck, con respuesta JSON `{ "status": "ok", "bot": "jarvis" }`.
- Endpoint `POST /webhook/<WEBHOOK_SECRET>` para actualizaciones de Telegram.
- Conversación temporal por chat en `CONVERSATIONS`, limitada por `MAX_HISTORY = 12`.
- Integración con Gemini mediante `call_gemini`, con reintentos para respuestas HTTP 503 y 429.
- Herramienta de Gemini `get_weather`, basada en las APIs gratuitas de geocodificación y pronóstico de Open-Meteo.
- Biblioteca temporal de PDFs en `DOCUMENTS`, con extracción mediante `pypdf` y límite de `300000` caracteres por documento.
- Administración de canales configurados en `CHANNELS`: publicar (`/post`), fijar el último mensaje (`/pin`), resumir (`/resumen`) y listar (`/canales`).
- Comandos de documentos: `/libros` y `/libro <clave> <pregunta>`.
- Procesamiento asíncrono de PDFs mediante `threading.Thread`.
- Historial temporal de publicaciones por canal en `RECENT_POSTS`, limitado a 30 publicaciones por canal.
- Autorización de operaciones administrativas mediante comparación del remitente con `OWNER_ID`.

## Dependencias

Definidas en `requirements.txt`:

- Flask 3.0.3
- requests 2.32.3
- gunicorn 22.0.0
- pypdf 5.0.1

## Despliegue y ejecución

- Instalación: `pip install -r requirements.txt`
- Producción: `gunicorn app:app`
- Desarrollo local: `python app.py`
- Puerto: `PORT`, con valor predeterminado `10000`.
- Telegram debe configurarse para enviar actualizaciones a `/webhook/<WEBHOOK_SECRET>`.

## Limitaciones y riesgos conocidos

- La memoria de conversaciones, documentos y publicaciones se pierde al reiniciar o dormir el servicio de Render.
- No hay persistencia en base de datos ni cola de trabajos.
- Las llamadas a Telegram, Gemini y Open-Meteo dependen de servicios externos y no tienen una capa común de manejo de errores.
- Las respuestas de Telegram no comprueban explícitamente el cuerpo de error después de cada `POST`.
- El secreto del webhook forma parte de la ruta definida al arrancar el proceso; cambiarlo requiere actualizar la configuración de Telegram y reiniciar el servicio.
- No hay suite de pruebas automatizadas ni validación formal del esquema de los payloads de Telegram/Gemini.
- Las credenciales se leen del entorno y no deben escribirse en el repositorio.

## Convenciones funcionales actuales

- Las respuestas dirigidas al usuario están en español rioplatense e informal.
- Las excepciones de integraciones se registran con `log.exception` y normalmente producen un mensaje de recuperación para el usuario.
- Solo el propietario identificado por `OWNER_ID` puede ejecutar comandos administrativos y cargar PDFs desde un chat privado.# Contexto del Proyecto: Jarvis Telegram Bot

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
