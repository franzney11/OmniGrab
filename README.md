# OmniGrab

**Aplicación de escritorio local para Windows** para descargar vídeo y audio con una interfaz integrada. No se abre una pestaña del navegador y el usuario final no tiene que ejecutar ningún `.bat`.

- Interfaz: HTML/CSS/JavaScript dentro de una ventana nativa mediante pywebview.
- Backend local: Python + FastAPI + yt-dlp + FFmpeg.
- Todo el procesamiento ocurre en el PC. No se suben los vídeos a un servidor de OmniGrab.
- Código abierto; la compilación de Windows se genera con GitHub Actions.

## Funciones

- Pestañas **Video | Audio | Descargas**.
- Descargas en segundo plano mientras se navega por las pestañas.
- Cola con estado pendiente, descargando, procesando, completado o error; muestra progreso, tamaño, velocidad y ETA cuando están disponibles.
- Botón **Mostrar carpeta** para localizar el archivo descargado.
- Una descarga activa por enlace: al iniciar una descarga, las opciones de ese mismo vídeo quedan sustituidas por el progreso. Se vuelven a habilitar al terminar. Se puede navegar por las demás pestañas durante el proceso.
- Los enlaces no se guardan en un historial propio ni en almacenamiento local. El campo se vacía tras cada intento de análisis.
- Los trabajos y su cola viven en memoria; al cerrar la aplicación se detiene el proceso y la lista de esa sesión no se conserva.

## Regla Gratis / PRO

| Petición | Resultado |
|---|---|
| Resolución solicitada igual o inferior a la máxima nativa | **Gratis** |
| Resolución superior a la máxima nativa | **PRO**: reescalado clásico con FFmpeg; consume 1 crédito simulado |
| MP3 a 128 / 192 / 256 / 320 kbps | **Gratis** |

La validación se realiza en el servidor local, no solo en la interfaz. El proveedor de pago actual es `MockProvider`: **no cobra dinero real**. No publiques esta pasarela simulada como si aceptara pagos reales.

## Descargar la aplicación para Windows

1. Sube este repositorio a GitHub.
2. En GitHub, abre **Actions** y ejecuta **Build OmniGrab for Windows** (Run workflow), o publica una etiqueta `v1.0.0`.
3. Al terminar, descarga el artefacto **OmniGrab-Windows**.
4. Dentro encontrarás `OmniGrab-Setup.exe`. Ejecútalo para instalar la aplicación y crear accesos directos.

La compilación se realiza en un runner Windows de GitHub Actions. Este repositorio no incluye un `.exe` precompilado porque el entorno de desarrollo no es Windows. El instalador generado no estará firmado digitalmente, por lo que Windows SmartScreen o algunos antivirus podrían mostrar un aviso de editor desconocido/falso positivo.

La aplicación usa Microsoft Edge WebView2 para mostrar su interfaz. En Windows 11 normalmente ya está disponible; si falta, instala el **WebView2 Runtime** oficial de Microsoft.

## Ejecutar desde código fuente (desarrollo)

Requiere Python 3.10 o superior y WebView2 en Windows:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-desktop.txt
python desktop_main.py
```

Esto es solo para desarrollo. El usuario final utiliza el instalador y abre OmniGrab desde el menú Inicio o el acceso directo.

## Ubicación de los archivos

- Descargas terminadas: `Descargas\OmniGrab`.
- Datos locales/créditos: `%LOCALAPPDATA%\OmniGrab\data`.
- Temporales: `%LOCALAPPDATA%\OmniGrab\temp`.

No se almacenan los enlaces analizados en un historial persistente. La cola de descargas es de sesión y desaparece al cerrar la aplicación.

## Estructura

```text
omnigrab/
├── backend/main.py
├── frontend/index.html
├── desktop_main.py
├── requirements-desktop.txt
├── installer/OmniGrab.iss
└── .github/workflows/build-windows.yml
```

## API local

- `POST /api/analyze` — analiza un enlace.
- `POST /api/download` — inicia un trabajo; rechaza otra descarga del mismo enlace mientras siga activa.
- `GET /api/jobs` — estado de la cola en memoria.
- `POST /api/open/{job_id}` — muestra el archivo o la carpeta de salida.

## Aviso

Descarga únicamente contenido que tengas derecho a guardar y respeta las condiciones de cada plataforma. El reescalado actual usa Lanczos + unsharp; no es una IA generativa ni recupera detalle real.
