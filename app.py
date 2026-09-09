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
import csv
import json
import hashlib
import unicodedata
import threading
import logging
import secrets
from datetime import datetime
from contextlib import contextmanager
from html import escape
from zoneinfo import ZoneInfo
import requests
try:
    import pymupdf as fitz
except ImportError:
    import fitz
from PIL import Image, ImageDraw, ImageFont, ImageStat
from pypdf import PdfReader
from docx import Document as WordDocument
from openpyxl import Workbook, load_workbook
from flask import Flask, request, jsonify, render_template_string, Response

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-bot")

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("HUGGINGFACE_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
PROVEEDOR_PRINCIPAL = os.environ.get("PROVEEDOR_PRINCIPAL", "groq").lower()
FALLBACK_AUTOMATICO = os.environ.get("FALLBACK_AUTOMATICO", "true").lower() not in ("0", "false", "no")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme")
OWNER_ID = os.environ.get("OWNER_ID")  # tu ID de Telegram (numérico), para que solo vos administres los canales
DATABASE_URL = os.environ.get("DATABASE_URL")

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


def _parse_channel_themes():
    raw = os.environ.get("CHANNEL_THEMES", "")
    themes = {}
    for pair in raw.split(","):
        if ":" in pair:
            name, theme = pair.split(":", 1)
            themes[name.strip().lower()] = theme.strip()
    return themes


CHANNEL_THEMES = _parse_channel_themes()
BOOK_CHANNEL = os.environ.get("BOOK_CHANNEL", "").strip().lower()
DIARY_FILE = os.environ.get("DIARY_FILE", "diario.docx")
ARGENTINA_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")

MAX_POSTS_PER_CHANNEL = 30

MAX_DOC_CHARS = 300_000  # límite de texto por documento (memoria limitada en el plan Free de Render)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
TELEGRAM_COMMANDS = [
    {"command": "help", "description": "Muestra la lista de ayuda y todos los comandos"},
    {"command": "libros", "description": "Ver los libros guardados en la biblioteca"},
    {"command": "exportar_excel", "description": "Descargar la planilla Excel de la base de datos"},
    {"command": "limpiar_duplicados", "description": "Eliminar registros duplicados"},
    {"command": "diario", "description": "Agregar una entrada al diario personal"},
    {"command": "diario_exportar", "description": "Descargar el diario en formato Word (.docx)"},
    {"command": "gasto_agregar", "description": "Registrar un gasto (<monto> <categoría> <descripción>)"},
    {"command": "gasto_listar", "description": "Mostrar historial de gastos"},
    {"command": "gasto_borrar", "description": "Eliminar un gasto por ID (<id>)"},
    {"command": "presupuesto_agregar", "description": "Definir un límite de presupuesto"},
    {"command": "presupuesto_listar", "description": "Ver presupuestos actuales"},
    {"command": "inventario_agregar", "description": "Añadir un ítem al inventario"},
    {"command": "inventario_listar", "description": "Consultar el inventario"},
    {"command": "contacto_agregar", "description": "Guardar un contacto"},
    {"command": "contacto_listar", "description": "Ver lista de contactos guardados"},
    {"command": "reset", "description": "Reiniciar el hilo de conversación con la IA"},
]
CONFIG_KEYS = (
    "PROVEEDOR_PRINCIPAL", "GROQ_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL",
    "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "DATABASE_URL", "ADMIN_PASSWORD",
    "FALLBACK_AUTOMATICO",
)

# --- Persistencia PostgreSQL ---

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    storage_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    file_type TEXT NOT NULL,
    text_content TEXT NOT NULL,
    source_chat_id BIGINT,
    source_message_id BIGINT,
    source_channel_name TEXT,
    file_md5 CHAR(32),
    author TEXT,
    category TEXT,
    edition TEXT,
    pages INTEGER,
    reading_level TEXT,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS author TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS edition TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS pages INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_level TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS rating INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_md5 CHAR(32);
CREATE INDEX IF NOT EXISTS documents_file_md5_idx ON documents (file_md5);
CREATE INDEX IF NOT EXISTS documents_created_at_idx ON documents (created_at DESC);
CREATE TABLE IF NOT EXISTS conversations (
    chat_id BIGINT PRIMARY KEY,
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS channel_posts (
    channel_name TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    text_content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (channel_name, message_id)
);
CREATE INDEX IF NOT EXISTS channel_posts_recent_idx
    ON channel_posts (channel_name, created_at DESC);
CREATE TABLE IF NOT EXISTS diary_entries (
    id SERIAL PRIMARY KEY,
    entry TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS gastos (
    id SERIAL PRIMARY KEY,
    monto NUMERIC,
    categoria TEXT,
    descripcion TEXT,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS presupuestos (
    id SERIAL PRIMARY KEY,
    categoria TEXT,
    monto_limite NUMERIC
);
CREATE TABLE IF NOT EXISTS inventario (
    id SERIAL PRIMARY KEY,
    item TEXT,
    cantidad INT,
    categoria TEXT
);
CREATE TABLE IF NOT EXISTS contactos (
    id SERIAL PRIMARY KEY,
    nombre TEXT,
    telefono TEXT,
    notas TEXT
);
"""


@contextmanager
def db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada")
    import psycopg2

    connection = psycopg2.connect(DATABASE_URL)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    if not DATABASE_URL:
        log.warning("DATABASE_URL no está configurada; la persistencia está deshabilitada")
        return
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
    log.info("Base de datos PostgreSQL inicializada")


def load_app_config():
    """Carga configuración persistida, manteniendo las variables de entorno como defaults."""
    global DATABASE_URL, GEMINI_API_KEY, GEMINI_MODEL, GROQ_API_KEY
    global OPENROUTER_API_KEY, OPENROUTER_MODEL, PROVEEDOR_PRINCIPAL
    global FALLBACK_AUTOMATICO, ADMIN_PASSWORD
    if not DATABASE_URL:
        return
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT key, value FROM app_config")
            values = {key: value for key, value in cursor.fetchall()}
    GEMINI_API_KEY = values.get("GEMINI_API_KEY", GEMINI_API_KEY)
    GEMINI_MODEL = values.get("GEMINI_MODEL", GEMINI_MODEL)
    GROQ_API_KEY = values.get("GROQ_API_KEY", GROQ_API_KEY)
    OPENROUTER_API_KEY = values.get("OPENROUTER_API_KEY", OPENROUTER_API_KEY)
    OPENROUTER_MODEL = values.get("OPENROUTER_MODEL", OPENROUTER_MODEL)
    DATABASE_URL = values.get("DATABASE_URL", DATABASE_URL)
    PROVEEDOR_PRINCIPAL = values.get("PROVEEDOR_PRINCIPAL", PROVEEDOR_PRINCIPAL).lower()
    FALLBACK_AUTOMATICO = values.get("FALLBACK_AUTOMATICO", str(FALLBACK_AUTOMATICO)).lower() not in ("0", "false", "no")
    ADMIN_PASSWORD = values.get("ADMIN_PASSWORD", ADMIN_PASSWORD)


def register_telegram_commands():
    """Publica el menú de comandos del bot en Telegram al iniciar la aplicación."""
    if not TELEGRAM_TOKEN:
        log.warning("TELEGRAM_TOKEN no está configurado; no se registraron los comandos de Telegram")
        return False
    try:
        response = requests.post(
            f"{TELEGRAM_API}/setMyCommands",
            json={"commands": TELEGRAM_COMMANDS},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            log.error("Telegram rechazó el registro de comandos: %s", result)
            return False
    except (requests.RequestException, ValueError):
        log.exception("Error registrando los comandos del bot en Telegram")
        return False
    log.info("Comandos de Telegram registrados correctamente")
    return True


def save_app_config(values):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            for key, value in values.items():
                if key in CONFIG_KEYS:
                    cursor.execute(
                        "INSERT INTO app_config (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                        (key, value),
                    )
    load_app_config()


def save_document(key, title, file_type, text, metadata=None, file_md5=None, source_chat_id=None,
                  source_message_id=None, source_channel_name=None):
    metadata = metadata or {}
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents
                    (storage_key, title, file_type, text_content, source_chat_id,
                    source_message_id, source_channel_name, file_md5, author, category,
                     edition, pages, reading_level, rating)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (storage_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    file_type = EXCLUDED.file_type,
                    text_content = EXCLUDED.text_content,
                    source_chat_id = EXCLUDED.source_chat_id,
                    source_message_id = EXCLUDED.source_message_id,
                    source_channel_name = EXCLUDED.source_channel_name,
                    file_md5 = EXCLUDED.file_md5,
                    author = EXCLUDED.author,
                    category = EXCLUDED.category,
                    edition = EXCLUDED.edition,
                    pages = EXCLUDED.pages,
                    reading_level = EXCLUDED.reading_level,
                    rating = EXCLUDED.rating,
                    created_at = NOW()
                """,
                (key, metadata.get("title") or title, file_type, text, source_chat_id,
                 source_message_id, source_channel_name, file_md5, metadata.get("author"),
                 metadata.get("category"), metadata.get("edition"), metadata.get("pages"),
                 metadata.get("reading_level"), metadata.get("rating")),
            )


def get_document(key):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT title, file_type, text_content FROM documents WHERE storage_key = %s",
                (key,),
            )
            row = cursor.fetchone()
    return {"title": row[0], "file_type": row[1], "text": row[2]} if row else None


def list_documents():
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT storage_key, title, file_type, author, category, edition,
                      pages, reading_level, rating, file_md5, created_at
                FROM documents ORDER BY created_at DESC
                """
            )
            return cursor.fetchall()


def find_duplicate_document(file_md5: str, title: str):
    normalized_title = title.strip()
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT title FROM documents
                WHERE (%s IS NOT NULL AND file_md5 = %s)
                   OR LOWER(BTRIM(title)) = LOWER(BTRIM(%s))
                ORDER BY created_at ASC LIMIT 1
                """,
                (file_md5, file_md5, normalized_title),
            )
            row = cursor.fetchone()
    return row[0] if row else None


def clean_duplicate_documents():
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH ranked AS (
                    SELECT id, title,
                           ROW_NUMBER() OVER (
                               PARTITION BY LOWER(BTRIM(title))
                               ORDER BY created_at ASC, id ASC
                           ) AS title_rank,
                           ROW_NUMBER() OVER (
                               PARTITION BY file_md5
                               ORDER BY created_at ASC, id ASC
                           ) AS hash_rank
                    FROM documents
                    WHERE file_md5 IS NOT NULL OR title IS NOT NULL
                ), duplicates AS (
                    SELECT id, title FROM ranked
                    WHERE title_rank > 1 OR (file_md5 IS NOT NULL AND hash_rank > 1)
                )
                DELETE FROM documents AS document
                USING duplicates
                WHERE document.id = duplicates.id
                RETURNING duplicates.title
                """
            )
            return [row[0] for row in cursor.fetchall()]


def delete_document(identifier: str):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            if identifier.isdigit():
                cursor.execute(
                    "DELETE FROM documents WHERE id = %s OR source_message_id = %s RETURNING title, source_chat_id, source_message_id",
                    (int(identifier), int(identifier)),
                )
            else:
                cursor.execute(
                    "DELETE FROM documents WHERE storage_key = %s OR title ILIKE %s RETURNING title, source_chat_id, source_message_id",
                    (identifier.lower(), identifier),
                )
            return cursor.fetchone()


def load_conversation(chat_id):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT messages FROM conversations WHERE chat_id = %s", (chat_id,))
            row = cursor.fetchone()
    return row[0] if row else []


def save_conversation(chat_id, messages):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO conversations (chat_id, messages, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (chat_id) DO UPDATE SET
                    messages = EXCLUDED.messages, updated_at = NOW()
                """,
                (chat_id, json.dumps(messages)),
            )


def delete_conversation(chat_id):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM conversations WHERE chat_id = %s", (chat_id,))


def save_channel_post(channel_name, channel_id, message_id, text):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO channel_posts (channel_name, channel_id, message_id, text_content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (channel_name, message_id)
                DO UPDATE SET text_content = EXCLUDED.text_content
                """,
                (channel_name, channel_id, message_id, text),
            )
            cursor.execute(
                """
                DELETE FROM channel_posts
                WHERE channel_name = %s AND message_id NOT IN (
                    SELECT message_id FROM channel_posts
                    WHERE channel_name = %s ORDER BY created_at DESC LIMIT %s
                )
                """,
                (channel_name, channel_name, MAX_POSTS_PER_CHANNEL),
            )


def get_recent_channel_posts(channel_name):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT message_id, text_content FROM channel_posts
                WHERE channel_name = %s ORDER BY created_at DESC LIMIT %s
                """,
                (channel_name, MAX_POSTS_PER_CHANNEL),
            )
            rows = cursor.fetchall()
    return [{"message_id": row[0], "text": row[1]} for row in reversed(rows)]


def save_diary_entry(text):
    timestamp = datetime.now(ARGENTINA_TIMEZONE).replace(tzinfo=None)
    line = f"[{timestamp:%d/%m/%Y %H:%M:%S}] {text}"
    document = WordDocument(DIARY_FILE) if os.path.exists(DIARY_FILE) else WordDocument()
    document.add_paragraph(line)
    document.save(DIARY_FILE)
    if DATABASE_URL:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO diary_entries (entry, created_at) VALUES (%s, %s)", (text, timestamp))
    return line


def build_diary_document():
    if os.path.exists(DIARY_FILE):
        with open(DIARY_FILE, "rb") as diary:
            return diary.read()
    document = WordDocument()
    if DATABASE_URL:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT entry, created_at FROM diary_entries ORDER BY created_at ASC, id ASC")
                for entry, created_at in cursor.fetchall():
                    document.add_paragraph(f"[{created_at:%d/%m/%Y %H:%M:%S}] {entry}")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _parse_decimal(value):
    return float(value.replace(",", "."))


def _db_rows(query, params=()):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()


def _db_execute(query, params=()):
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()


def _format_rows(title, headers, rows):
    if not rows:
        return f"<b>{escape(title)}</b>\nNo hay registros."
    lines = [f"<b>{escape(title)}</b>"]
    for row in rows:
        values = " | ".join(escape(str(value if value is not None else "")) for value in row)
        lines.append(f"<b>ID {row[0]}</b> | {values.split(' | ', 1)[1] if ' | ' in values else values}")
    return "\n".join(lines)


def handle_personal_command(chat_id, text):
    parts = text.strip().split(maxsplit=3)
    command = parts[0].lower()
    try:
        if command == "/diario":
            if len(parts) < 2:
                send_telegram_message(chat_id, "Uso: /diario <texto>")
            else:
                send_telegram_message(chat_id, f"Entrada guardada: {save_diary_entry(text.split(None, 1)[1])}")
            return True
        if command == "/diario_exportar":
            if send_telegram_document(chat_id, build_diary_document(), "diario.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
                send_telegram_message(chat_id, "Diario exportado.")
            else:
                send_telegram_message(chat_id, "No pude enviar el diario por Telegram.")
            return True

        definitions = {
            "/gasto": ("gastos", "monto, categoria, descripcion", "Gastos", "monto, categoria, descripcion, fecha"),
            "/presupuesto": ("presupuestos", "categoria, monto_limite", "Presupuestos", "categoria, monto_limite"),
            "/inventario": ("inventario", "item, cantidad, categoria", "Inventario", "item, cantidad, categoria"),
            "/contacto": ("contactos", "nombre, telefono, notas", "Contactos", "nombre, telefono, notas"),
        }
        base = next((key for key in definitions if command.startswith(key + "_")), None)
        if not base:
            return False
        table, columns, title, list_columns = definitions[base]
        action = command[len(base) + 1:]
        if action == "listar":
            rows = _db_rows(f"SELECT id, {list_columns} FROM {table} ORDER BY id ASC")
            send_telegram_message(chat_id, _format_rows(title, list_columns, rows), parse_mode="HTML")
            return True
        if action == "borrar":
            if len(parts) < 2 or not parts[1].isdigit():
                send_telegram_message(chat_id, f"Uso: {command} <id>")
                return True
            deleted = _db_execute(f"DELETE FROM {table} WHERE id = %s RETURNING id", (int(parts[1]),))
            send_telegram_message(chat_id, f"Se eliminó el ID {parts[1]}." if deleted else f"No existe el ID {parts[1]}.")
            return True
        if action == "agregar":
            arguments = text.split(maxsplit=1)[1].split()
            if base == "/gasto" and len(arguments) >= 2:
                values = (_parse_decimal(arguments[0]), arguments[1], " ".join(arguments[2:]) or None)
            elif base == "/presupuesto" and len(arguments) == 2:
                values = (arguments[0], _parse_decimal(arguments[1]))
            elif base == "/inventario" and len(arguments) >= 2:
                values = (arguments[0], int(arguments[1]), " ".join(arguments[2:]) or None)
            elif base == "/contacto" and len(arguments) >= 2:
                values = (arguments[0], arguments[1], " ".join(arguments[2:]) or None)
            else:
                send_telegram_message(chat_id, f"Uso: {command} <{columns}>")
                return True
            placeholders = ", ".join(["%s"] * len(values))
            _db_execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING id", values)
            send_telegram_message(chat_id, f"Registro agregado en {title.lower()}.")
            return True
    except (ValueError, RuntimeError):
        send_telegram_message(chat_id, "Datos inválidos o PostgreSQL no está disponible.")
        log.exception("Error procesando comando personal %s", command)
        return True
    return False


init_database()
load_app_config()
register_telegram_commands()

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


def render_pdf_cover(pdf_bytes: bytes) -> bytes | None:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if not document.page_count:
            raise ValueError("El PDF no contiene páginas")
        page = document.load_page(0)
        if not page.get_images(full=True):
            return None
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
        preview = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        preview.thumbnail((160, 220))
        light_pixels = ImageStat.Stat(preview.convert("L")).mean[0]
        if light_pixels >= 238:
            return None
        return pixmap.tobytes("jpg", jpg_quality=92)
    finally:
        document.close()


def create_cover_banner(title: str, author: str | None, category: str | None = None) -> bytes:
    category_key = (category or "").lower()
    palettes = {
        "ciencia": ((25, 54, 61), (115, 193, 184), (237, 245, 235)),
        "historia": ((76, 46, 39), (211, 164, 95), (250, 235, 204)),
        "literatura": ((48, 38, 68), (199, 151, 184), (248, 235, 241)),
        "derecho": ((29, 49, 67), (190, 166, 104), (239, 238, 222)),
        "tecnologia": ((23, 42, 57), (82, 183, 214), (229, 245, 249)),
    }
    palette = next(
        (colors for key, colors in palettes.items() if key in category_key),
        ((30, 43, 58), (216, 169, 84), (245, 241, 228)),
    )
    background, accent, text_color = palette
    image = Image.new("RGB", (1200, 1600), background)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 70
    )
    author_font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 42
    )
    small_font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26
    )
    margin = 78
    draw.rectangle((margin, margin, 1200 - margin, 1600 - margin), outline=accent, width=5)
    draw.rectangle((margin + 22, margin + 22, 1200 - margin - 22, 1600 - margin - 22),
                   outline=accent, width=1)
    draw.line((250, 300, 950, 300), fill=accent, width=3)
    draw.line((250, 1300, 950, 1300), fill=accent, width=3)
    if category:
        draw.text((600, 220), category[:40].upper(), font=small_font, fill=accent, anchor="mm")
    words = title[:140].split()
    title_lines = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=title_font)[2] <= 920:
            line = candidate
        else:
            if line:
                title_lines.append(line)
            line = word
    if line:
        title_lines.append(line)
    draw.multiline_text(
        (600, 760), "\n".join(title_lines), font=title_font, fill=text_color,
        spacing=22, align="center", anchor="mm",
    )
    if author:
        draw.text((600, 1390), author[:100], font=author_font, fill=accent, anchor="mm")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


def build_book_cover(file_type: str, file_bytes: bytes, metadata: dict) -> bytes:
    if file_type == "pdf":
        try:
            rendered_cover = render_pdf_cover(file_bytes)
            if rendered_cover:
                return rendered_cover
            log.info("La primera página PDF no parece una portada visual; se generará una portada nueva")
        except Exception:
            log.exception("No se pudo renderizar la portada PDF")
    return create_cover_banner(
        metadata.get("title", "Libro"),
        metadata.get("author"),
        metadata.get("category"),
    )


def extract_word_text(document_bytes: bytes) -> str:
    document = WordDocument(io.BytesIO(document_bytes))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(part for part in parts if part.strip())[:MAX_DOC_CHARS]


def extract_spreadsheet_text(file_bytes: bytes, file_name: str) -> str:
    if file_name.lower().endswith(".csv"):
        content = file_bytes.decode("utf-8-sig", errors="replace")
        rows = csv.reader(io.StringIO(content))
        return "\n".join(" | ".join(row) for row in rows)[:MAX_DOC_CHARS]

    workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    parts = []
    try:
        for sheet in workbook.worksheets:
            parts.append(f"[Hoja: {sheet.title}]")
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    parts.append(" | ".join(values))
                if sum(len(part) for part in parts) >= MAX_DOC_CHARS:
                    break
            if sum(len(part) for part in parts) >= MAX_DOC_CHARS:
                break
    finally:
        workbook.close()
    return "\n".join(parts)[:MAX_DOC_CHARS]


def document_type(file_name: str):
    extension = os.path.splitext(file_name.lower())[1]
    return {
        ".pdf": "pdf",
        ".docx": "docx",
        ".csv": "csv",
        ".xlsx": "xlsx",
        ".xlsm": "xlsx",
    }.get(extension)


def analyze_document_metadata(text: str, file_name: str) -> dict:
    prompt = (
        "Analizá el contenido del documento y devolvé únicamente un objeto JSON válido, "
        "sin markdown, con estas claves exactas: title (string), author (string o null), "
        "category (string o null), edition (string o null), pages (entero o null), "
        "reading_level (string o null), rating (entero de 1 a 5 o null). "
        "Estimá los valores solo cuando haya indicios razonables; rating es una valoración "
        "general de utilidad/calidad del material, no una certeza bibliográfica.\n\n"
        f"NOMBRE DEL ARCHIVO: {file_name}\nCONTENIDO:\n{text[:MAX_DOC_CHARS]}"
    )
    try:
        data = call_gemini([{"role": "user", "parts": [{"text": prompt}]}])
        parts = data["candidates"][0]["content"].get("parts", [])
        response = "".join(part.get("text", "") for part in parts).strip()
        response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response, flags=re.IGNORECASE)
        metadata = json.loads(response)
        if not isinstance(metadata, dict):
            raise ValueError("Gemini no devolvió un objeto")
    except Exception:
        log.exception("Error extrayendo metadatos de %s", file_name)
        return {"title": file_name}

    if metadata.get("pages") is not None:
        try:
            metadata["pages"] = max(0, int(metadata["pages"]))
        except (TypeError, ValueError):
            metadata["pages"] = None
    if metadata.get("rating") is not None:
        try:
            metadata["rating"] = min(5, max(1, int(metadata["rating"])))
        except (TypeError, ValueError):
            metadata["rating"] = None
    metadata["title"] = str(metadata.get("title") or file_name)
    return metadata


def generate_book_summary(text: str, metadata: dict) -> str:
    prompt = (
        "Escribí un resumen breve en español, de máximo 280 caracteres, para el pie de foto "
        "de una portada de libro. No inventes datos que no estén en el contenido. "
        f"Título: {metadata.get('title', 'sin título')}\nCONTENIDO:\n{text[:12000]}"
    )
    try:
        data = call_gemini([{"role": "user", "parts": [{"text": prompt}]}])
        parts = data["candidates"][0]["content"].get("parts", [])
        return "".join(part.get("text", "") for part in parts).strip()[:280]
    except Exception:
        log.exception("Error generando resumen de %s", metadata.get("title"))
        return "Resumen no disponible."


def evaluate_channel_content(channel_name: str, content: str) -> dict:
    theme = CHANNEL_THEMES.get(channel_name)
    if not theme:
        return {"matches": True, "reason": "El canal no tiene temática configurada."}
    prompt = (
        "Compará el contenido con la temática del canal. Devolvé únicamente JSON válido "
        "con las claves matches (boolean) y reason (string breve en español). "
        f"TEMÁTICA DEL CANAL: {theme}\nCONTENIDO:\n{content[:12000]}"
    )
    try:
        data = call_gemini([{"role": "user", "parts": [{"text": prompt}]}])
        parts = data["candidates"][0]["content"].get("parts", [])
        response = "".join(part.get("text", "") for part in parts).strip()
        response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response, flags=re.IGNORECASE)
        result = json.loads(response)
        return {
            "matches": bool(result.get("matches", True)),
            "reason": str(result.get("reason", "Sin explicación.")),
        }
    except Exception:
        log.exception("Error moderando contenido del canal %s", channel_name)
        return {"matches": True, "reason": "No se pudo evaluar automáticamente."}


def process_document_async(document: dict, notify_chat_id, source_message=None, source_channel_name=None):
    resultado = handle_incoming_document(document, source_message, source_channel_name)
    send_telegram_message(notify_chat_id, resultado)


def handle_incoming_document(document: dict, source_message=None, source_channel_name=None) -> str:
    file_name = document.get("file_name", "documento")
    file_type = document_type(file_name)
    if not file_type:
        return f"'{file_name}' no es un formato compatible (PDF, DOCX, XLSX, XLSM o CSV)."

    try:
        file_bytes = download_telegram_file(document["file_id"])
        file_md5 = hashlib.md5(file_bytes).hexdigest()
        duplicate_title = find_duplicate_document(file_md5, "")
        if duplicate_title:
            return f"El libro '{duplicate_title}' ya se encuentra en la biblioteca (Duplicado)."
        extractors = {
            "pdf": lambda: extract_pdf_text(file_bytes),
            "docx": lambda: extract_word_text(file_bytes),
            "csv": lambda: extract_spreadsheet_text(file_bytes, file_name),
            "xlsx": lambda: extract_spreadsheet_text(file_bytes, file_name),
        }
        text = extractors[file_type]()
    except Exception:
        log.exception("Error procesando documento %s", file_name)
        return f"No pude procesar '{file_name}'."

    if not text.strip():
        return f"Descargué '{file_name}' pero no pude extraerle texto."

    key = slugify(file_name.rsplit(".", 1)[0])
    source_chat_id = source_message.get("chat", {}).get("id") if source_message else None
    source_message_id = source_message.get("message_id") if source_message else None
    metadata = analyze_document_metadata(text, file_name)
    duplicate_title = find_duplicate_document(file_md5, metadata.get("title") or file_name)
    if duplicate_title:
        return f"El libro '{duplicate_title}' ya se encuentra en la biblioteca (Duplicado)."
    if source_channel_name and OWNER_ID:
        evaluation = evaluate_channel_content(source_channel_name, text)
        if not evaluation["matches"]:
            send_telegram_message(
                OWNER_ID,
                f"Aviso: material posiblemente fuera de tema en '{source_channel_name}'.\n"
                f"Archivo: {file_name}\nMotivo: {evaluation['reason']}\n"
                f"Mensaje: {source_message_id or 'desconocido'}",
            )
    try:
        save_document(key, file_name, file_type, text, metadata, file_md5, source_chat_id,
                      source_message_id, source_channel_name)
    except Exception:
        log.exception("Error guardando documento %s en PostgreSQL", file_name)
        return f"Procesé '{file_name}', pero no pude guardarlo en PostgreSQL."
    cover_published = publish_book_cover(
        file_type, file_bytes, metadata, text, source_channel_name
    )
    cover_status = " La portada fue publicada en el canal." if cover_published else ""
    return (
        f"Guardé '{file_name}' ({len(text):,} caracteres) en PostgreSQL.\n"
        f"Preguntame con: /libro {key} tu pregunta\n"
        f"Ver todos: /libros{cover_status}"
    )


def ask_about_document(key: str, question: str) -> str:
    try:
        doc = get_document(key)
    except Exception:
        log.exception("Error buscando documento %s", key)
        return "No pude consultar la base de datos. Probá de nuevo."
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


class GeminiAPIError(RuntimeError):
    """Error controlado para fallos de configuración, cuota o disponibilidad de Gemini."""


class ProviderAPIError(RuntimeError):
    """Error de un proveedor para permitir pasar automáticamente al siguiente."""


def _provider_order():
    providers = ["groq", "gemini", "openrouter"]
    principal = PROVEEDOR_PRINCIPAL if PROVEEDOR_PRINCIPAL in providers else "groq"
    ordered = [principal] + [provider for provider in providers if provider != principal]
    if not OPENROUTER_API_KEY:
        ordered = [provider for provider in ordered if provider != "openrouter"]
    return ordered if FALLBACK_AUTOMATICO else ordered[:1]


def _content_to_messages(contents: list) -> list:
    messages = []
    for content in contents:
        role = content.get("role", "user")
        if role == "model":
            role = "assistant"
        text = "".join(part.get("text", "") for part in content.get("parts", []))
        if text:
            messages.append({"role": role, "content": text})
    return messages


def _call_groq(contents: list) -> str:
    if not GROQ_API_KEY:
        raise ProviderAPIError("GROQ_API_KEY no está configurada")
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model="llama-3.3-70b-specdec",
            messages=_content_to_messages(contents),
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        raise ProviderAPIError(f"Groq no disponible: {exc}") from exc


def _call_openrouter(contents: list) -> str:
    if not OPENROUTER_API_KEY:
        raise ProviderAPIError("OPENROUTER_API_KEY/HUGGINGFACE_API_KEY no está configurada")
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={"model": OPENROUTER_MODEL, "messages": _content_to_messages(contents)},
            timeout=30,
        )
        if not response.ok:
            raise ProviderAPIError(f"OpenRouter devolvió HTTP {response.status_code}: {response.text[:200]}")
        return response.json()["choices"][0]["message"].get("content", "")
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise ProviderAPIError(f"OpenRouter no disponible: {exc}") from exc


def _as_gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def _call_gemini_direct(contents: list) -> dict:
    if not GEMINI_API_KEY:
        raise ProviderAPIError("GEMINI_API_KEY no está configurada")

    gemini_api = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
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
        try:
            r = requests.post(gemini_api, json=payload, timeout=30)
        except requests.RequestException as exc:
            if attempt == 2:
                raise ProviderAPIError("No se pudo conectar con la API de Gemini.") from exc
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (503, 429) and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        if not r.ok:
            try:
                error_message = r.json().get("error", {}).get("message", "")
            except ValueError:
                error_message = ""
            if r.status_code == 429:
                raise ProviderAPIError("Gemini agotó la cuota disponible.")
            if r.status_code == 401 or r.status_code == 403:
                raise ProviderAPIError("La GEMINI_API_KEY no es válida o no tiene permisos.")
            if r.status_code == 404:
                raise ProviderAPIError(
                    f"El modelo Gemini '{GEMINI_MODEL}' no está disponible. "
                    "Configurá GEMINI_MODEL con un modelo habilitado."
                )
            detail = f": {error_message}" if error_message else "."
            raise ProviderAPIError(f"La API de Gemini devolvió HTTP {r.status_code}{detail}")
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderAPIError("Gemini devolvió una respuesta inválida.") from exc
    raise ProviderAPIError("Gemini no está disponible en este momento.")


def call_gemini(contents: list) -> dict:
    """Consulta el proveedor principal y aplica fallback, conservando formato Gemini."""
    errors = []
    for provider in _provider_order():
        try:
            if provider == "groq":
                return _as_gemini_response(_call_groq(contents))
            if provider == "openrouter":
                return _as_gemini_response(_call_openrouter(contents))
            return _call_gemini_direct(contents)
        except ProviderAPIError as exc:
            errors.append(f"{provider}: {exc}")
            log.warning("Proveedor %s falló; probando el siguiente: %s", provider, exc)
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            log.exception("Error inesperado en proveedor %s; probando el siguiente", provider)
    raise GeminiAPIError("No hay proveedores de IA disponibles. " + " | ".join(errors))


def preguntar_ia(prompt):
    """Devuelve una respuesta de texto usando el proveedor configurado y sus respaldos."""
    data = call_gemini([{"role": "user", "parts": [{"text": str(prompt)}]}])
    return "".join(
        part.get("text", "")
        for part in data["candidates"][0]["content"].get("parts", [])
    ).strip()


def ask_gemini(chat_id: int, user_text: str) -> str:
    history = load_conversation(chat_id)
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

    except GeminiAPIError as exc:
        log.warning("Error de Gemini: %s", exc)
        reply = f"No pude consultar Gemini: {exc}"
        history.append({"role": "model", "parts": [{"text": reply}]})
        save_conversation(chat_id, history[-MAX_HISTORY:])
        return reply
    except Exception:
        log.exception("Error llamando a Gemini")
        reply = "Tuve un problema para pensar la respuesta. Probá de nuevo en un momento."
        history.append({"role": "model", "parts": [{"text": reply}]})
        save_conversation(chat_id, history[-MAX_HISTORY:])
        return reply

    history.append({"role": "model", "parts": [{"text": reply}]})
    save_conversation(chat_id, history[-MAX_HISTORY:])
    return reply


def send_telegram_message(chat_id, text: str, parse_mode=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
            timeout=15,
        )
    except requests.RequestException:
        log.exception("Error enviando mensaje de Telegram a %s", chat_id)


def notify_owner_error(context: str, exc: Exception):
    """Avisa al OWNER_ID por Telegram cuando algo se rompe en segundo plano.

    Nunca debe tirar una excepción propia: si falla el aviso, solo se loguea.
    """
    log.exception("Error en %s", context)
    if not OWNER_ID:
        return
    try:
        detail = str(exc)[:300]
        send_telegram_message(int(OWNER_ID), f"⚠️ Error en {context}: {detail}")
    except Exception:
        log.exception("No se pudo notificar el error al owner")


def send_telegram_photo(channel_id, cover: bytes, caption: str) -> bool:
    response = requests.post(
        f"{TELEGRAM_API}/sendPhoto",
        data={"chat_id": channel_id, "caption": caption},
        files={"photo": ("portada.jpg", cover, "image/jpeg")},
        timeout=30,
    )
    return response.ok and response.json().get("ok", False)


def publish_book_cover(file_type: str, file_bytes: bytes, metadata: dict,
                       text: str, source_channel_name: str | None = None) -> bool:
    channel_name = source_channel_name or BOOK_CHANNEL
    if not channel_name or channel_name not in CHANNELS:
        log.info("No hay canal de libros configurado para publicar la portada")
        return False

    stars = "⭐" * int(metadata.get("rating") or 0) or "Sin valoración"
    summary = generate_book_summary(text, metadata)
    caption = (
        f"📚 {metadata.get('title', 'Sin título')}\n"
        f"Autor: {metadata.get('author') or 'Desconocido'}\n"
        f"Categoría: {metadata.get('category') or 'Sin categoría'}\n"
        f"Valoración: {stars}\n\n"
        f"{summary}"
    )
    try:
        cover = build_book_cover(file_type, file_bytes, metadata)
        return send_telegram_photo(CHANNELS[channel_name], cover, caption)
    except Exception:
        log.exception("Error publicando la portada de %s", metadata.get("title"))
        return False


def send_telegram_document(chat_id, content: bytes, filename: str, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") -> bool:
    try:
        response = requests.post(
            f"{TELEGRAM_API}/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (filename, content, mime_type)},
            timeout=30,
        )
        return response.ok and response.json().get("ok", False)
    except (requests.RequestException, ValueError):
        log.exception("Error enviando %s por sendDocument", filename)
        return False


def export_documents_excel(chat_id: int) -> str:
    try:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM documents ORDER BY created_at DESC")
                column_names = [description[0] for description in cursor.description]
                rows = [dict(zip(column_names, row)) for row in cursor.fetchall()]

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Documentos"
        headers = ["Título", "Autor", "Categoría", "Páginas", "Nivel", "MD5", "Fecha"]
        worksheet.append(headers)
        for document in rows:
            worksheet.append([
                document.get("title"),
                document.get("author"),
                document.get("category"),
                document.get("pages"),
                document.get("reading_level"),
                document.get("file_md5"),
                document.get("created_at"),
            ])
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column_cells in worksheet.columns:
            column_letter = column_cells[0].column_letter
            longest = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_letter].width = min(max(longest + 2, 12), 45)

        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)
        if not send_telegram_document(chat_id, output.getvalue(), "documentos.xlsx"):
            return "Generé el Excel, pero Telegram no pudo enviarlo."
        return "Exportación enviada como documentos.xlsx."
    except Exception as exc:
        print(f"Error detallado exportando documentos a Excel: {exc}", flush=True)
        log.exception("Error exportando documentos a Excel")
        return "No pude generar la exportación de documentos."


def delete_telegram_message(chat_id, message_id) -> bool:
    response = requests.post(
        f"{TELEGRAM_API}/deleteMessage",
        json={"chat_id": chat_id, "message_id": int(message_id)},
        timeout=15,
    )
    return response.ok and response.json().get("ok", False)


def delete_message_from_configured_channels(message_id: str) -> bool:
    for channel_id in CHANNELS.values():
        try:
            if delete_telegram_message(channel_id, message_id):
                return True
        except Exception:
            log.exception("Error eliminando mensaje %s del canal %s", message_id, channel_id)
    return False


def moderate_text_post(channel_name: str, post: dict, text: str):
    if not OWNER_ID:
        return
    evaluation = evaluate_channel_content(channel_name, text)
    if not evaluation["matches"]:
        send_telegram_message(
            OWNER_ID,
            f"Aviso: post posiblemente fuera de tema en '{channel_name}'.\n"
            f"Mensaje: {post.get('message_id', 'desconocido')}\n"
            f"Contenido: {text[:1000]}\nMotivo: {evaluation['reason']}",
        )


def pin_last_message(channel_name: str) -> str:
    posts = get_recent_channel_posts(channel_name)
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
    posts = get_recent_channel_posts(channel_name)
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
                    OWNER_ID, f"[{name}] " + handle_incoming_document(
                        post["document"], post, name
                    )
                ),
                daemon=True,
            ).start()
        return

    text = post.get("text") or post.get("caption") or ""
    save_channel_post(name, chat_id_str, post["message_id"], text)
    if text:
        threading.Thread(
            target=moderate_text_post,
            args=(name, post, text),
            daemon=True,
        ).start()


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
        documents = list_documents()
        if not documents:
            send_telegram_message(chat_id, "Todavía no aprendí ningún PDF. Reenviame uno.")
        else:
            lista = "\n".join(
                f"• {key} ({file_type}) — {title}"
                for key, title, file_type, *_ in documents
            )
            send_telegram_message(chat_id, f"Documentos guardados:\n{lista}")
        return True

    if cmd == "/exportar_excel":
        send_telegram_message(chat_id, export_documents_excel(chat_id))
        return True

    if cmd == "/limpiar_duplicados":
        try:
            deleted_titles = clean_duplicate_documents()
        except Exception:
            log.exception("Error limpiando documentos duplicados")
            send_telegram_message(chat_id, "No pude escanear y limpiar los duplicados en PostgreSQL.")
            return True
        if not deleted_titles:
            send_telegram_message(chat_id, "No encontré libros duplicados en la biblioteca.")
        else:
            report = "\n".join(f"• {title}" for title in deleted_titles)
            send_telegram_message(
                chat_id,
                f"Eliminé {len(deleted_titles)} registro(s) duplicado(s):\n{report}",
            )
        return True

    if cmd == "/borrar_libro" and len(parts) >= 2:
        identifier = " ".join(parts[1:]).strip()
        try:
            deleted = delete_document(identifier)
        except Exception:
            log.exception("Error eliminando documento %s", identifier)
            send_telegram_message(chat_id, "No pude eliminar el documento de PostgreSQL.")
            return True
        if not deleted and identifier.isdigit():
            if delete_message_from_configured_channels(identifier):
                send_telegram_message(chat_id, f"Eliminé el mensaje {identifier} de Telegram.")
            else:
                send_telegram_message(chat_id, f"No encontré un documento ni un mensaje con '{identifier}'.")
            return True
        if not deleted:
            send_telegram_message(chat_id, f"No encontré un documento con '{identifier}'.")
            return True
        title, source_chat_id, source_message_id = deleted
        telegram_deleted = False
        if source_chat_id and source_message_id:
            try:
                telegram_deleted = delete_telegram_message(source_chat_id, source_message_id)
            except Exception:
                log.exception("Error eliminando mensaje %s del canal", source_message_id)
        elif identifier.isdigit():
            telegram_deleted = delete_message_from_configured_channels(identifier)
        suffix = " y el post de Telegram" if telegram_deleted else ""
        send_telegram_message(chat_id, f"Eliminé '{title}' de PostgreSQL{suffix}.")
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


ADMIN_HTML = """
<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Jarvis Admin</title>
<style>body{font-family:system-ui;max-width:720px;margin:40px auto;padding:0 20px}label{display:block;margin:14px 0 5px}input,select{width:100%;padding:9px;box-sizing:border-box}button{margin-top:20px;padding:10px 18px}.ok{color:green}.error{color:#a00}</style>
</head><body><h1>Configuración de Jarvis</h1>
{% if message %}<p class="{{ status }}">{{ message }}</p>{% endif %}
<form method="post">
<label>Contraseña de administrador</label><input type="password" name="admin_password" required>
<label>Proveedor principal</label><select name="PROVEEDOR_PRINCIPAL">
{% for provider, label in providers %}<option value="{{ provider }}" {% if config.PROVEEDOR_PRINCIPAL == provider %}selected{% endif %}>{{ label }}</option>{% endfor %}</select>
<label>Groq API Key</label><input type="password" name="GROQ_API_KEY" value="{{ config.GROQ_API_KEY }}">
<label>Gemini API Key</label><input type="password" name="GEMINI_API_KEY" value="{{ config.GEMINI_API_KEY }}">
<label>Modelo Gemini</label><input name="GEMINI_MODEL" value="{{ config.GEMINI_MODEL }}">
<label>OpenRouter / HuggingFace API Key</label><input type="password" name="OPENROUTER_API_KEY" value="{{ config.OPENROUTER_API_KEY }}">
<label>Modelo OpenRouter</label><input name="OPENROUTER_MODEL" value="{{ config.OPENROUTER_MODEL }}">
<label>DATABASE_URL</label><input name="DATABASE_URL" value="{{ config.DATABASE_URL }}">
<label><input type="checkbox" name="FALLBACK_AUTOMATICO" {% if config.FALLBACK_AUTOMATICO %}checked{% endif %}> Activar fallback automático</label>
<button type="submit">Guardar configuración</button></form></body></html>
"""


def _admin_password_valid(password):
    return bool(ADMIN_PASSWORD and password and secrets.compare_digest(password, ADMIN_PASSWORD))


@app.route("/admin", methods=["GET", "POST"])
def admin():
    basic_password = request.authorization.password if request.authorization else None
    submitted_password = request.form.get("admin_password")
    authenticated = _admin_password_valid(basic_password or submitted_password)
    config = {
        "PROVEEDOR_PRINCIPAL": PROVEEDOR_PRINCIPAL,
        "GROQ_API_KEY": GROQ_API_KEY or "",
        "GEMINI_API_KEY": GEMINI_API_KEY or "",
        "GEMINI_MODEL": GEMINI_MODEL,
        "OPENROUTER_API_KEY": OPENROUTER_API_KEY or "",
        "OPENROUTER_MODEL": OPENROUTER_MODEL,
        "DATABASE_URL": DATABASE_URL or "",
        "FALLBACK_AUTOMATICO": FALLBACK_AUTOMATICO,
    }
    if request.method == "POST" and authenticated:
        values = {key: request.form.get(key, "").strip() for key in CONFIG_KEYS}
        values["FALLBACK_AUTOMATICO"] = "true" if request.form.get("FALLBACK_AUTOMATICO") else "false"
        values["ADMIN_PASSWORD"] = ADMIN_PASSWORD or submitted_password
        try:
            save_app_config(values)
            config.update({key: globals().get(key, config.get(key)) for key in config})
            return render_template_string(
                ADMIN_HTML, config=config, providers=[("groq", "Groq"), ("gemini", "Gemini"), ("openrouter", "OpenRouter")],
                message="Configuración guardada.", status="ok",
            )
        except Exception:
            log.exception("Error guardando configuración desde /admin")
            return render_template_string(
                ADMIN_HTML, config=config, providers=[("groq", "Groq"), ("gemini", "Gemini"), ("openrouter", "OpenRouter")],
                message="No se pudo guardar la configuración.", status="error",
            ), 500
    if not authenticated:
        return render_template_string(
            ADMIN_HTML, config=config, providers=[("groq", "Groq"), ("gemini", "Gemini"), ("openrouter", "OpenRouter")],
            message="Ingresá la contraseña y enviá el formulario.", status="error",
        ), 401
    return render_template_string(
        ADMIN_HTML, config=config, providers=[("groq", "Groq"), ("gemini", "Gemini"), ("openrouter", "OpenRouter")],
        message=None, status="",
    )


@app.route(f"/webhook/{WEBHOOK_SECRET if WEBHOOK_SECRET else 'hook'}", methods=["POST"])
def webhook():
    # Red de seguridad: pase lo que pase adentro, Telegram SIEMPRE recibe 200.
    # Si devolvemos 500, Telegram reintenta el mismo update una y otra vez
    # (mensajes duplicados, comandos ejecutados varias veces, etc.).
    chat_id = None
    try:
        update = request.get_json(force=True, silent=True) or {}

        if update.get("channel_post"):
            handle_channel_post(update["channel_post"])
            return jsonify(ok=True)

        message = update.get("message") or update.get("edited_message")
        if not message:
            return jsonify(ok=True)

        chat_id = message.get("chat", {}).get("id")
        sender_id = str(message.get("from", {}).get("id", ""))
        text = message.get("text", "")

        if chat_id is None:
            log.warning("Update sin chat_id, se ignora: %s", update)
            return jsonify(ok=True)

        if message.get("document") and OWNER_ID and sender_id == OWNER_ID:
            send_telegram_message(chat_id, "Recibí el documento, lo estoy procesando (puede tardar un minuto)...")
            threading.Thread(
                target=process_document_async,
                args=(message["document"], chat_id, message, None),
                daemon=True,
            ).start()
            return jsonify(ok=True)

        if not text:
            send_telegram_message(chat_id, "Por ahora solo entiendo texto.")
            return jsonify(ok=True)

        if text.strip().lower() in ("/start", "/reset"):
            try:
                delete_conversation(chat_id)
            except Exception:
                log.exception("Error reiniciando conversación de %s", chat_id)
            send_telegram_message(chat_id, "¡Hola! Soy tu Jarvis. ¿En qué te ayudo?")
            return jsonify(ok=True)

        if text.startswith("/") and handle_personal_command(chat_id, text):
            return jsonify(ok=True)

        # Comandos de administración de canales: solo el dueño puede usarlos
        if text.startswith("/") and OWNER_ID and sender_id == OWNER_ID:
            if handle_owner_command(chat_id, text):
                return jsonify(ok=True)

        reply = ask_gemini(chat_id, text)
        send_telegram_message(chat_id, reply)
        return jsonify(ok=True)

    except Exception as exc:
        notify_owner_error("webhook", exc)
        if chat_id is not None:
            send_telegram_message(chat_id, "Tuve un problema procesando tu mensaje. Ya le avisé al administrador.")
        # Siempre 200: evita que Telegram reintente el mismo update en bucle.
        return jsonify(ok=True)


@app.errorhandler(Exception)
def handle_uncaught_error(exc):
    """Red de seguridad final: ninguna excepción debería tirar el proceso.

    Cubre /admin, /, y cualquier ruta futura que Roo agregue sin su propio
    try/except. El webhook ya se protege a sí mismo más arriba.
    """
    notify_owner_error(f"ruta {request.path}", exc)
    if request.path.startswith("/webhook"):
        return jsonify(ok=True)
    return jsonify(error="internal_error"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
    
