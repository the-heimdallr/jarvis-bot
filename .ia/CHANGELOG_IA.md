# Changelog de IA

## 2026-09-08 - Persistencia y documentos multiformato

- Añadida conexión PostgreSQL mediante `DATABASE_URL`.
- Creadas las tablas `documents`, `conversations` y `channel_posts` al iniciar la aplicación.
- Migrados a PostgreSQL los documentos, el historial de conversación y las publicaciones recientes de canales.
- Añadida extracción de PDF, DOCX, CSV, XLSX y XLSM.
- Añadidas `psycopg2-binary`, `python-docx` y `openpyxl` a `requirements.txt`.
- Guardados metadatos de chat, mensaje y canal para documentos recibidos desde Telegram.
- Validada la sintaxis de `app.py` y la extracción funcional de DOCX, XLSX y CSV.
- Los datos que solo estaban en RAM antes de esta migración no pueden recuperarse automáticamente.

## Estado inicial

- Bot Flask integrado con Telegram, Gemini y Open-Meteo.
- Procesamiento inicial de PDFs mediante `pypdf`.
- Administración de canales mediante comandos protegidos por `OWNER_ID`.
