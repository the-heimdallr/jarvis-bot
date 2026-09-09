# Roadmap

## Prioridad alta

- Configurar `DATABASE_URL` en Render y verificar la creación de tablas con una base PostgreSQL real.
- Añadir pruebas automatizadas para healthcheck, autenticación del propietario, comandos, persistencia y procesamiento de archivos.
- Validar explícitamente las respuestas de Telegram, Gemini, Open-Meteo y PostgreSQL.
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

La persistencia actual usa `documents`, `conversations` y `channel_posts` en PostgreSQL. Se almacena el texto extraído y los metadatos, no el binario original.

## Criterio para avanzar

Cada cambio debe conservar el healthcheck, mantener la autorización de `OWNER_ID`, evitar dependencias innecesarias y añadir o actualizar una prueba cuando modifique un comportamiento observable.
