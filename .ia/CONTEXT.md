# Contexto del proyecto

## Estado actual

Jarvis es un bot de Telegram implementado en `app.py` con Flask, Gunicorn y Gemini. Recibe actualizaciones por webhook y responde en español.

## Componentes

- `GET /` sirve el healthcheck JSON.
- `POST /webhook/<WEBHOOK_SECRET>` procesa mensajes privados, publicaciones de canales y archivos adjuntos.
- Gemini mantiene un historial limitado a `MAX_HISTORY = 12` mensajes por chat.
- `get_weather` consulta geocodificación y pronóstico mediante Open-Meteo.
- Los comandos de administración son `/canales`, `/post`, `/pin`, `/resumen`, `/libros` y `/libro <clave> <pregunta>`.
- La autorización administrativa se basa en `OWNER_ID`.
- El procesamiento de documentos se ejecuta en hilos en segundo plano.

## Persistencia PostgreSQL

La aplicación usa `DATABASE_URL` y crea las tablas con `CREATE TABLE IF NOT EXISTS` al iniciar:

- `documents`: `storage_key`, título, `file_type`, texto extraído, chat/mensaje de origen, canal y fecha. Soporta `pdf`, `docx`, `csv` y `xlsx`.
- `conversations`: `chat_id`, historial JSONB y fecha de actualización.
- `channel_posts`: nombre e ID del canal, `message_id`, texto y fecha; conserva los últimos 30 posts por canal.

Se persiste el texto extraído y los metadatos, no el binario original. Los documentos recibidos desde chats privados del propietario y desde canales configurados se guardan en `documents`.

## Formatos de documentos

- PDF: `pypdf`.
- Word: `python-docx`, incluyendo párrafos y tablas de `.docx`.
- CSV: lector estándar `csv`, con decodificación UTF-8 tolerante.
- Excel: `openpyxl` para `.xlsx` y `.xlsm`.
- Límite de texto: `MAX_DOC_CHARS = 300000` caracteres por documento.
- `.xls` no está soportado actualmente.

## Dependencias

Además de Flask, Requests, Gunicorn y PyPDF, `requirements.txt` incluye:

- `psycopg2-binary==2.9.9`
- `python-docx==1.1.2`
- `openpyxl==3.1.5`

## Configuración y despliegue

- Variables: `TELEGRAM_TOKEN`, `GEMINI_API_KEY`, `WEBHOOK_SECRET`, `OWNER_ID`, `CHANNELS` y `DATABASE_URL`.
- Instalación: `pip install -r requirements.txt`.
- Producción: `gunicorn app:app`.
- Desarrollo: `python app.py`.
- Puerto: `PORT`, con valor predeterminado `10000`.

## Limitaciones conocidas

- `DATABASE_URL` debe apuntar a una base PostgreSQL accesible; sin ella, la persistencia no puede operar.
- Los datos que solo estaban en RAM antes de esta migración no pueden recuperarse automáticamente.
- No hay cola de trabajos; los archivos se procesan en hilos.
- Las respuestas de Telegram, Gemini y Open-Meteo aún necesitan validación uniforme y manejo de errores común.
- No hay suite de pruebas automatizadas.
- Las credenciales se leen del entorno y no deben escribirse en el repositorio.
