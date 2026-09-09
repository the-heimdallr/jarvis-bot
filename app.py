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
from contextlib import contextmanager
import requests
import pandas as pd
try:
    import pymupdf as fitz
except ImportError:
    import fitz
from PIL import Image, ImageDraw, ImageFont, ImageStat
from pypdf import PdfReader
from docx import Document as WordDocument
from openpyxl import load_workbook
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-bot")

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
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

MAX_POSTS_PER_CHANNEL = 30

MAX_DOC_CHARS = 300_000  # límite de texto por documento (memoria limitada en el plan Free de Render)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
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


init_database()

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


def call_gemini(contents: list) -> dict:
    if not GEMINI_API_KEY:
        raise GeminiAPIError("Falta configurar GEMINI_API_KEY en las variables de entorno.")

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
            r = requests.post(GEMINI_API, json=payload, timeout=30)
        except requests.RequestException as exc:
            if attempt == 2:
                raise GeminiAPIError("No se pudo conectar con la API de Gemini.") from exc
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
                raise GeminiAPIError("Gemini agotó la cuota disponible. Probá más tarde.")
            if r.status_code == 401 or r.status_code == 403:
                raise GeminiAPIError("La GEMINI_API_KEY no es válida o no tiene permisos.")
            if r.status_code == 404:
                raise GeminiAPIError(
                    f"El modelo Gemini '{GEMINI_MODEL}' no está disponible. "
                    "Configurá GEMINI_MODEL con un modelo habilitado."
                )
            detail = f": {error_message}" if error_message else "."
            raise GeminiAPIError(f"La API de Gemini devolvió HTTP {r.status_code}{detail}")
        try:
            return r.json()
        except ValueError as exc:
            raise GeminiAPIError("Gemini devolvió una respuesta inválida.") from exc
    raise GeminiAPIError("Gemini no está disponible en este momento.")


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


def send_telegram_message(chat_id, text: str):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )


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


def send_telegram_document(chat_id, content: bytes, filename: str) -> bool:
    try:
        response = requests.post(
            f"{TELEGRAM_API}/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30,
        )
        return response.ok and response.json().get("ok", False)
    except (requests.RequestException, ValueError):
        log.exception("Error enviando %s por sendDocument", filename)
        return False


def export_documents_excel(chat_id: int) -> str:
    try:
        rows = list_documents()
        columns = [
            "Título", "Autor", "Categoría", "Páginas", "Nivel", "MD5", "Fecha",
        ]
        dataframe = pd.DataFrame(
            [
                {
                    "Título": title,
                    "Autor": author,
                    "Categoría": category,
                    "Páginas": pages,
                    "Nivel": reading_level,
                    "MD5": file_md5,
                    "Fecha": created_at,
                }
                for key, title, file_type, author, category, edition, pages,
                reading_level, rating, file_md5, created_at in rows
            ],
            columns=columns,
        )
        output = io.BytesIO()
        dataframe.to_excel(output, index=False, engine="openpyxl")
        output.seek(0)
        if not send_telegram_document(chat_id, output.getvalue(), "documentos.xlsx"):
            return "Generé el Excel, pero Telegram no pudo enviarlo."
        return "Exportación enviada como documentos.xlsx."
    except Exception:
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
    
