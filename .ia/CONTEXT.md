# Contexto del proyecto

## Estado actual

Jarvis es un bot de Telegram implementado en `app.py` con Flask, Gunicorn, PostgreSQL y Gemini. Recibe actualizaciones por webhook y responde en español.

## Componentes

- `GET /` sirve el healthcheck JSON.
- `POST /webhook/<WEBHOOK_SECRET>` procesa mensajes privados, publicaciones de canales y archivos adjuntos.
- Gemini mantiene un historial limitado a `MAX_HISTORY = 12` mensajes por chat.
- Los comandos protegidos por `OWNER_ID` incluyen `/canales`, `/post`, `/pin`, `/resumen`, `/libros`, `/libro <clave> <pregunta>`, `/exportar_excel`, `/borrar_libro <nombre_o_id>` y `/limpiar_duplicados`.
- El procesamiento de documentos se ejecuta en hilos en segundo plano.
- Al registrar un documento, PDF se renderiza con PyMuPDF a JPEG de alta resolución; DOCX y fallos de renderizado usan un banner generado con Pillow.
- La portada se publica con `sendPhoto` en el canal de origen o en `BOOK_CHANNEL`, con título, autor, categoría, estrellas y resumen de Gemini.

## Persistencia PostgreSQL

La aplicación usa `DATABASE_URL` y crea o migra estas tablas al iniciar:

- `documents`: clave, título, tipo, texto, hash MD5, autor, categoría, edición, páginas, nivel de lectura, valoración de 1 a 5, origen y fecha.
- `conversations`: historial JSONB por chat y fecha de actualización.
- `channel_posts`: nombre e ID del canal, `message_id`, texto y fecha; conserva los últimos 30 posts.

Gemini extrae o estima metadatos al guardar cada documento. Antes de guardar o publicar, el bot compara el hash MD5 y el título normalizado con la biblioteca. `CHANNEL_THEMES` configura la temática por canal; Gemini evalúa posts y archivos nuevos y avisa al `OWNER_ID` cuando parecen fuera de tema.

## Archivos y dependencias

- PDF: `pypdf`.
- Word: `python-docx`, incluyendo párrafos y tablas de `.docx`.
- Excel: `openpyxl` para `.xlsx` y `.xlsm`.
- Exportación: `pandas` y `openpyxl` generan `documentos.xlsx`.
- Portadas: `PyMuPDF` renderiza la primera página PDF y `Pillow` crea banners de respaldo.
- Límite de texto: `MAX_DOC_CHARS = 300000` caracteres por documento.
- `.xls` no está soportado actualmente.

## Configuración y despliegue

- Variables: `TELEGRAM_TOKEN`, `GEMINI_API_KEY`, `WEBHOOK_SECRET`, `OWNER_ID`, `CHANNELS`, `CHANNEL_THEMES`, `BOOK_CHANNEL` y `DATABASE_URL`.
- `CHANNEL_THEMES` usa `nombre:temática,nombre2:temática2`.
- `BOOK_CHANNEL` es el nombre configurado en `CHANNELS` que recibe portadas de documentos privados.
- Instalación: `pip install -r requirements.txt`.
- Producción: `gunicorn app:app`.
- Desarrollo: `python app.py`.
- Puerto: `PORT`, con valor predeterminado `10000`.

## Limitaciones conocidas

- `DATABASE_URL` debe apuntar a una base PostgreSQL accesible.
- No hay cola de trabajos; los archivos se procesan en hilos.
- No hay suite de pruebas automatizadas.
- Las credenciales se leen del entorno y no deben escribirse en el repositorio.
