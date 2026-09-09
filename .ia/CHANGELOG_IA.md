# Changelog de IA

## 2026-09-09

- Ampliada `documents` con autor, categoría, edición, páginas, nivel de lectura y valoración de 1 a 5.
- Añadido análisis JSON de metadatos mediante Gemini al recibir documentos.
- Añadido `/exportar_excel`, restringido a `OWNER_ID`, con envío de `documentos.xlsx` por Telegram.
- Añadido `/borrar_libro <nombre_o_id>` para eliminar el registro y el mensaje de canal cuando existe su origen.
- Añadida moderación temática de posts y archivos mediante `CHANNEL_THEMES`, con aviso privado al propietario.
- Añadida extracción de la primera página PDF como portada JPEG con PyMuPDF.
- Añadidos banners JPEG de respaldo con Pillow para DOCX o PDFs sin portada renderizable.
- Añadida publicación multimedia con `sendPhoto` en el canal, incluyendo metadatos, estrellas y resumen generado por Gemini.
- Añadido hash MD5 por documento y detección de duplicados por hash o título antes de guardar/publicar.
- Añadido `/limpiar_duplicados`, restringido a `OWNER_ID`, que conserva el registro más antiguo y envía el informe de eliminados.

## Estado inicial

- Bot Flask integrado con Telegram, Gemini y Open-Meteo.
- Procesamiento inicial de PDFs mediante `pypdf`.
- Administración de canales mediante comandos protegidos por `OWNER_ID`.
