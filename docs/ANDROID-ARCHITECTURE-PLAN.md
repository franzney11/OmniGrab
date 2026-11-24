# OmniGrab — Arquitectura Android

## Alcance

Este documento define una hoja de ruta para Android. No implementa todavía la aplicación móvil. iOS queda expresamente reservado para una fase posterior.

## Punto de partida actual

- Cliente de escritorio Windows con HTML/CSS/JavaScript servido por FastAPI local.
- pywebview + WebView2 es únicamente la capa de ventana de Windows.
- Motor de análisis y descarga basado en yt-dlp y FFmpeg.
- La cola de trabajos actual vive en memoria; las rutas y preferencias son locales al equipo.
- La pasarela de pagos actual es simulada y no debe considerarse lista para producción.

## Arquitectura objetivo

### Cliente Android

- Kotlin y Jetpack Compose para interfaz y navegación nativas.
- Storage Access Framework (SAF) para seleccionar carpetas y obtener permisos de escritura persistentes.
- Notificaciones del sistema para progreso, finalización y errores.
- WorkManager o un servicio en primer plano, según duración, visibilidad y restricciones vigentes de Android.

### Dominio y contratos

- Estabilizar DTOs para análisis, opciones, trabajos, progreso, errores y preferencias.
- Mantener la configuración de rutas separada por medio: video y audio.
- Separar los casos de uso del motor de descarga de operaciones de interfaz/host, como abrir una carpeta o mostrar un archivo.
- Mantener la política de no persistir el historial de URLs.

### Motor de descarga: decisión pendiente

Evaluar mediante una prueba técnica dos modelos antes de elegir:

1. **Motor local en Android:** integrar y validar yt-dlp/FFmpeg o alternativas mantenibles en Android, ABI y dependencias nativas, almacenamiento SAF, consumo y restricciones de ejecución en segundo plano.
2. **Backend remoto propio:** trabajos en cola y descarga/entrega desde infraestructura controlada. Implica coste operativo, autenticación, cuotas, privacidad, borrado temporal, escalabilidad y controles contra abuso.

No se debe asumir que el backend local de Windows ni sus rutas pueden reutilizarse directamente en Android. Tampoco se debe exponer un FastAPI local a la red sin autenticación y límites.

## Contratos que conviene estabilizar

- POST /api/analyze
- POST /api/download
- GET /api/jobs
- GET /api/progress/{job_id} (SSE; validar compatibilidad o sustituir por polling/WebSocket).
- GET y POST /api/settings/folders, con configuración independiente para video y audio.
- Operaciones de host como elegir destino, abrir archivo y mostrar carpeta deben vivir en adaptadores específicos de plataforma.

## Persistencia y ejecución en Android

- Usar URI de SAF en vez de rutas absolutas de Windows.
- Guardar permisos de árbol de documentos cuando el proveedor y Android lo permitan.
- El guardado automático solo puede operar en destinos autorizados por el usuario.
- Persistir estado mínimo de trabajos para poder recuperar la interfaz tras recreación o cierre del proceso; la cola en memoria actual no es suficiente.
- Diseñar cancelación, reintentos, límites de concurrencia y limpieza de temporales.

## Seguridad, privacidad y cumplimiento

- TLS y autenticación si se opta por backend remoto.
- Validación de URL y protección SSRF en cualquier servicio remoto.
- Límites de tamaño, duración, concurrencia y cuota.
- Aislamiento entre usuarios y borrado automático de temporales.
- No registrar URLs sensibles ni crear historial de enlaces.
- Revisar licencias de dependencias y términos de las plataformas de origen antes de publicar.

## Fases propuestas

1. **Preparación desktop:** cerrar settings por medio, estabilizar contratos y añadir pruebas unitarias/API.
2. **Spike Android:** prototipo de SAF, descarga en segundo plano y compatibilidad real de yt-dlp/FFmpeg; comparar motor local y remoto.
3. **Decisión técnica:** elegir el modelo con datos de rendimiento, coste, mantenimiento y privacidad.
4. **MVP Android:** análisis, selección MP4/MP3, descarga, progreso/notificaciones, rutas separadas y errores.
5. **Beta Android:** recuperación de trabajos, pruebas en dispositivos/versiones, consumo, seguridad y publicación interna.
6. **iOS futuro:** evaluación independiente con Swift/SwiftUI y restricciones de almacenamiento y ejecución de iOS; no prometer paridad hasta completar ese spike.

## Decisiones que siguen abiertas

- Motor local frente a backend remoto.
- Plataformas de origen y formatos admitidos.
- Límites y política de retención.
- Modelo de cuenta/cuotas y pagos reales.
- Estrategia de distribución y actualización.

**Recomendación:** no empezar Android hasta que el contrato de trabajos y preferencias esté probado en escritorio y se haya completado el spike comparativo del motor.