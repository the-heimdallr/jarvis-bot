# Roadmap

## Prioridad alta

- Configurar `DATABASE_URL` en Render y verificar la creación de tablas con una base PostgreSQL real.
- Añadir pruebas automatizadas para healthcheck, autenticación del propietario, comandos, persistencia, exportación y procesamiento de archivos.
- Unificar timeouts, reintentos y mensajes de error de las integraciones externas.
- Evitar que secretos o tokens aparezcan en logs y mensajes de error.

## Prioridad media

- Sustituir el procesamiento en hilos por una cola de trabajos si aumenta el volumen de documentos.
- Separar configuración, integraciones, comandos y lógica del webhook en módulos pequeños.
- Añadir límites de tamaño y controles adicionales para mensajes y archivos recibidos.
- Incorporar observabilidad básica de latencia, errores y uso de memoria.

## Prioridad baja

- Añadir soporte opcional para archivos `.xls`.
- Mejorar la extracción de PDFs escaneados mediante OCR.
- Incorporar comandos para limpiar documentos e historial.

## Esquema implementado

La persistencia actual usa `documents`, `conversations` y `channel_posts` en PostgreSQL. `documents` almacena título, autor, categoría, edición, páginas, nivel de lectura y valoración de 1 a 5, además del texto extraído y el origen; no almacena el binario original.

## Funcionalidades recientes

- `/exportar_excel` genera una planilla con todos los documentos y sus metadatos.
- `/borrar_libro <nombre_o_id>` elimina documentos y posts de canal cuando Telegram lo permite.
- `CHANNEL_THEMES` configura la temática por canal y Gemini evalúa cada post o archivo nuevo.
- Las portadas PDF se renderizan con PyMuPDF y las portadas de respaldo se generan con Pillow; `sendPhoto` las publica en `BOOK_CHANNEL` o en el canal de origen.
- `documents.file_md5` evita guardar o publicar copias repetidas; `/limpiar_duplicados` elimina duplicados históricos y conserva el registro más antiguo.

Cada cambio debe conservar el healthcheck y la autorización de `OWNER_ID`, evitar dependencias innecesarias y añadir pruebas cuando exista una suite.
