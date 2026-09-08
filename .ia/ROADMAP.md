# Roadmap

## Prioridad alta

- Añadir pruebas automatizadas para healthcheck, autenticación del propietario, comandos administrativos y procesamiento de actualizaciones.
- Validar de forma explícita las respuestas de Telegram, Gemini y Open-Meteo antes de acceder a sus campos.
- Evitar que secretos o tokens aparezcan en logs y mensajes de error.
- Añadir una política uniforme de timeouts, reintentos y errores para las integraciones externas.

## Prioridad media

- Incorporar persistencia para conversaciones, documentos y publicaciones de canales.
- Sustituir el procesamiento en hilos por una cola de trabajos si aumenta el volumen de PDFs.
- Separar configuración, integraciones, comandos y lógica de webhook en módulos pequeños.
- Añadir límites de tamaño y controles para mensajes y documentos recibidos.

## Prioridad baja

- Añadir observabilidad básica: métricas de latencia, errores y uso de memoria.
- Mejorar la extracción de PDFs escaneados mediante OCR opcional.
- Incorporar comandos de administración para limpiar documentos e historial.

## Criterio para avanzar

Cada cambio debe conservar el endpoint de healthcheck, mantener la autorización de `OWNER_ID`, evitar dependencias innecesarias y añadir o actualizar una prueba cuando modifique una ruta o comportamiento observable.# Roadmap y Mejoras Futuras

- [ ] **Persistencia de Datos:** Integrar base de datos ligera (ej. PostgreSQL / Supabase / SQLite) para evitar pérdida de conversaciones y PDFs al reiniciar Render.
- [ ] **Manejo de Contexto Largo:** Optimizar el historial de conversación (`CONVERSATIONS`) para no exceder tokens en chats extendidos.
- [ ] **Soporte para más Formatos:** Permitir procesamiento de imágenes u otros tipos de documentos.
- [ ] **Refactorización:** Separar `app.py` en módulos (ej: `handlers/`, `services/`, `config.py`) si el proyecto sigue creciendo
- [ ] .
