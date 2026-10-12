"""
agroNexus - Agente de IA con interfaz web, auto-edición y commits a GitHub
(Grok - gateway Reto Agente 2026)

USO:
  1. Crea clave.txt en esta carpeta con tu API key (o define la variable RETO_KEY)
  2. py -3.15 agente_grok.py
  3. Se abre solo el navegador en http://localhost:8000
  (Para detenerlo: Ctrl + C en la terminal)

NOTA: cuando el agente proponga editar su código, el diff aparece en la
TERMINAL y debes responder s/n ahí. La página espera mientras tanto.
"""

import ast
import base64
import datetime
import difflib
import html
import io
import json
import operator
import os
import re
import shutil
import subprocess
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openai import OpenAI


# ============ CONFIGURACIÓN ============
def _cargar_key() -> str:
    """Lee la key de la variable RETO_KEY o, si no existe, del archivo clave.txt."""
    key = os.environ.get("RETO_KEY", "").strip()
    if key:
        return key
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clave.txt")
    try:
        with open(ruta, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


API_KEY = _cargar_key()
BASE_URL = "https://api.reto.pltk.mx/v1"
MODEL = "grok-4.6"
HOST = "127.0.0.1"   # solo accesible desde tu computadora
PORT = 8000
# =======================================

if not API_KEY:
    raise SystemExit(
        "Falta la API key: crea el archivo clave.txt (solo con la key adentro) "
        "en la misma carpeta, o define la variable de entorno RETO_KEY."
    )

SYSTEM_PROMPT = (
    "Eres un asistente útil y conciso. Responde en español. "
    "Usa las herramientas disponibles cuando ayuden a dar una respuesta correcta. "
    "Usa buscar_web para noticias, datos actuales o hechos inciertos. "
    "Usa memoria_leer al inicio si puede haber contexto guardado; "
    "usa memoria_guardar para preferencias o hechos que el usuario quiera recordar "
    "en otras sesiones; memoria_borrar para olvidar. "
    "Puedes leer y proponer cambios a tu propio código con leer_mi_codigo y "
    "editar_mi_codigo (incluye siempre una descripcion breve para el commit); "
    "el usuario siempre debe aprobar cada cambio. "
    "Haz cambios pequeños y precisos. "
    "El usuario puede adjuntar imágenes, documentos, audio y video: "
    "analiza el contenido extraído o las imágenes que lleguen con el mensaje."
)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
RUTA = os.path.abspath(__file__)


def _asegurar_gitignore_icono() -> None:
    """Agrega icon/ a .gitignore si aún no está."""
    ruta_gi = os.path.join(os.path.dirname(RUTA), ".gitignore")
    try:
        with open(ruta_gi, encoding="utf-8") as f:
            contenido = f.read()
    except FileNotFoundError:
        contenido = ""
    except OSError:
        return
    lineas = {
        ln.strip().rstrip("/")
        for ln in contenido.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    if "icon" in lineas:
        return
    extra = "" if not contenido or contenido.endswith("\n") else "\n"
    try:
        with open(ruta_gi, "a", encoding="utf-8") as f:
            f.write(f"{extra}icon/\n")
    except OSError:
        pass


def _encontrar_icono():
    """Devuelve (ruta, mime) de la primera imagen en la carpeta icon."""
    carpeta = os.path.join(os.path.dirname(RUTA), "icon")
    tipos = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
        ".svg": "image/svg+xml",
    }
    try:
        nombres = sorted(os.listdir(carpeta))
    except OSError:
        return None, None
    for nombre in nombres:
        ext = os.path.splitext(nombre)[1].lower()
        if ext in tipos:
            return os.path.join(carpeta, nombre), tipos[ext]
    return None, None


ICONO_RUTA, ICONO_TIPO = _encontrar_icono()
_asegurar_gitignore_icono()


# ----------------------------- HERRAMIENTAS -----------------------------
# Para agregar una herramienta: 1) escribe la función, 2) descríbela en TOOLS,
# 3) regístrala en FUNCIONES.

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("Expresión no permitida")


def calculadora(expresion: str) -> str:
    return str(_eval(ast.parse(expresion, mode="eval").body))


def hora_actual() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def leer_mi_codigo() -> str:
    with open(RUTA, encoding="utf-8") as f:
        codigo = f.read()
    return codigo.replace(API_KEY, "<API_KEY>")  # la key nunca se envía al modelo


def subir_a_github(mensaje: str) -> str:
    """Hace commit solo de este archivo y lo sube con push."""
    carpeta = os.path.dirname(RUTA)
    nombre = os.path.basename(RUTA)
    pasos = (
        ["git", "add", nombre],              # solo este archivo, nunca "git add ."
        ["git", "commit", "-m", mensaje],
        ["git", "push"],
    )
    try:
        for cmd in pasos:
            r = subprocess.run(cmd, cwd=carpeta, capture_output=True,
                               text=True, timeout=60)
            if r.returncode != 0:
                detalle = (r.stderr or r.stdout).strip()
                return f"Git falló en '{cmd[1]}': {detalle}"
        return "Commit y push realizados en GitHub."
    except Exception as e:
        return f"Error con git: {e}"


def editar_mi_codigo(texto_viejo: str, texto_nuevo: str,
                     descripcion: str = "Cambio del agente") -> str:
    texto_viejo = texto_viejo.replace("<API_KEY>", API_KEY)
    texto_nuevo = texto_nuevo.replace("<API_KEY>", API_KEY)

    with open(RUTA, encoding="utf-8") as f:
        codigo = f.read()
    if codigo.count(texto_viejo) != 1:
        return "Error: texto_viejo debe aparecer exactamente una vez en el archivo."

    nuevo = codigo.replace(texto_viejo, texto_nuevo)
    try:
        ast.parse(nuevo)  # valida que el código siga siendo Python válido
    except SyntaxError as e:
        return f"Error de sintaxis, no se aplicó el cambio: {e}"

    diff = "".join(difflib.unified_diff(
        codigo.splitlines(True), nuevo.splitlines(True), "actual", "propuesto"))
    print("\n=== CAMBIO PROPUESTO POR EL AGENTE ===\n" + diff.replace(API_KEY, "<API_KEY>"))
    print(f"Mensaje del commit: Agente: {descripcion}")
    if input("¿Aplicar este cambio y subirlo a GitHub? (s/n): ").strip().lower() != "s":
        return "El usuario rechazó el cambio."

    shutil.copy(RUTA, RUTA + ".bak")  # respaldo por si algo sale mal
    with open(RUTA, "w", encoding="utf-8") as f:
        f.write(nuevo)

    resultado_git = subir_a_github(f"Agente: {descripcion}")
    return (f"Cambio aplicado (respaldo en .bak). {resultado_git} "
            "Surte efecto al reiniciar el programa.")


MEMORIA_ARCHIVO = os.path.join(os.path.dirname(RUTA), "memoria.json")


def _cargar_memoria() -> dict:
    try:
        with open(MEMORIA_ARCHIVO, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        return {"_error": str(e)}


def _guardar_memoria(data: dict) -> None:
    tmp = MEMORIA_ARCHIVO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MEMORIA_ARCHIVO)


def memoria_leer(clave: str = "") -> str:
    data = _cargar_memoria()
    if data.get("_error"):
        return f"Error leyendo memoria: {data['_error']}"
    clave = (clave or "").strip()
    if clave:
        if clave not in data:
            return f"No hay nada guardado en '{clave}'."
        return json.dumps({clave: data[clave]}, ensure_ascii=False)
    if not data:
        return "Memoria vacía."
    return json.dumps(data, ensure_ascii=False, indent=2)


def memoria_guardar(clave: str, valor: str) -> str:
    clave = (clave or "").strip()
    if not clave:
        return "Error: la clave no puede estar vacía."
    data = _cargar_memoria()
    data.pop("_error", None)
    data[clave] = valor
    _guardar_memoria(data)
    return f"Memoria guardada: {clave}"


def memoria_borrar(clave: str) -> str:
    clave = (clave or "").strip()
    data = _cargar_memoria()
    data.pop("_error", None)
    if clave not in data:
        return f"No existe la clave '{clave}'."
    del data[clave]
    _guardar_memoria(data)
    return f"Memoria borrada: {clave}"


def buscar_web(consulta: str) -> str:
    import html as html_lib
    import re
    import urllib.parse
    import urllib.request

    consulta = (consulta or "").strip()
    if not consulta:
        return "Error: consulta vacía."

    def _texto(s: str) -> str:
        s = re.sub(r"<[^>]+>", " ", s or "")
        s = html_lib.unescape(s)
        return re.sub(r"\s+", " ", s).strip()

    data = urllib.parse.urlencode({"q": consulta}).encode("utf-8")
    req = urllib.request.Request(
        "https://lite.duckduckgo.com/lite/",
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; agroNexus/1.0)",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Error de red: {e}"

    titulos = re.findall(
        r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>',
        raw,
        flags=re.I | re.S,
    )
    snippets = [_texto(s) for s in re.findall(
        r'class="result-snippet">(.*?)</td>', raw, flags=re.I | re.S
    )]

    lineas, vistos, n = [], set(), 0
    for href, title in titulos:
        href = html_lib.unescape(href)
        if "duckduckgo.com" in href or href in vistos:
            continue
        vistos.add(href)
        n += 1
        snip = snippets[n - 1] if n - 1 < len(snippets) else ""
        lineas.append(f"{n}. {_texto(title)}\n   {href}\n   {snip}")
        if n >= 5:
            break
    return "\n".join(lineas) if lineas else "Sin resultados."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculadora",
            "description": "Evalúa una expresión aritmética, por ejemplo '(12 + 3) * 4'.",
            "parameters": {
                "type": "object",
                "properties": {"expresion": {"type": "string"}},
                "required": ["expresion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hora_actual",
            "description": "Devuelve la fecha y hora actuales del sistema.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leer_mi_codigo",
            "description": "Devuelve el código fuente completo de este programa.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editar_mi_codigo",
            "description": "Reemplaza un fragmento exacto del código fuente por otro y, "
                           "si el usuario aprueba, lo sube a GitHub como commit. "
                           "Primero usa leer_mi_codigo. texto_viejo debe aparecer una sola vez.",
            "parameters": {
                "type": "object",
                "properties": {
                    "texto_viejo": {"type": "string"},
                    "texto_nuevo": {"type": "string"},
                    "descripcion": {
                        "type": "string",
                        "description": "Mensaje breve del commit (qué cambió y por qué)",
                    },
                },
                "required": ["texto_viejo", "texto_nuevo", "descripcion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_web",
            "description": "Busca en la web (DuckDuckGo) y devuelve títulos, enlaces y "
                           "fragmentos. Úsala para noticias, datos actuales o hechos inciertos.",
            "parameters": {
                "type": "object",
                "properties": {"consulta": {"type": "string"}},
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memoria_leer",
            "description": "Lee la memoria persistente. Sin clave, devuelve todo; "
                           "con clave, solo ese dato.",
            "parameters": {
                "type": "object",
                "properties": {"clave": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memoria_guardar",
            "description": "Guarda un hecho o preferencia del usuario para otras sesiones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clave": {"type": "string"},
                    "valor": {"type": "string"},
                },
                "required": ["clave", "valor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memoria_borrar",
            "description": "Elimina una clave de la memoria persistente.",
            "parameters": {
                "type": "object",
                "properties": {"clave": {"type": "string"}},
                "required": ["clave"],
            },
        },
    },
]

FUNCIONES = {
    "calculadora": calculadora,
    "hora_actual": hora_actual,
    "leer_mi_codigo": leer_mi_codigo,
    "editar_mi_codigo": editar_mi_codigo,
    "buscar_web": buscar_web,
    "memoria_leer": memoria_leer,
    "memoria_guardar": memoria_guardar,
    "memoria_borrar": memoria_borrar,
}


def ejecutar_herramienta(nombre: str, args: dict) -> str:
    try:
        return str(FUNCIONES[nombre](**args))
    except Exception as e:
        return f"Error: {e}"


# ------------------------------- AGENTE --------------------------------
SESIONES = {}          # id de sesión -> historial de mensajes
LOCK = threading.Lock()

_TIPOS_IMAGEN = ("image/jpeg", "image/png", "image/gif", "image/webp")
_MAX_ADJUNTO = 20 * 1024 * 1024  # 20 MB por archivo
_MAX_TEXTO_ADJUNTO = 80_000
_MAX_ADJUNTOS = 8

_MIME_POR_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".json": "application/json", ".xml": "application/xml",
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".js": "text/javascript", ".py": "text/x-python", ".log": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".rtf": "application/rtf",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
    ".webm": "video/webm", ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
}


def _decodificar_data_url(data_url: str) -> tuple[str, bytes]:
    data_url = (data_url or "").strip()
    if not data_url.startswith("data:"):
        raise ValueError("El adjunto debe enviarse como data URL.")
    try:
        header, b64 = data_url.split(",", 1)
    except ValueError as e:
        raise ValueError("Data URL inválido.") from e
    mime = header[5:].split(";")[0].strip().lower() or "application/octet-stream"
    if ";base64" not in header.lower():
        raise ValueError("El adjunto debe ir en base64.")
    if len(b64) > _MAX_ADJUNTO * 4 // 3 + 200:
        raise ValueError("El archivo supera el límite de 20 MB.")
    try:
        crudo = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise ValueError("No se pudo decodificar el archivo.") from e
    if len(crudo) > _MAX_ADJUNTO:
        raise ValueError("El archivo supera el límite de 20 MB.")
    return mime, crudo


def _truncar_texto(texto: str, limite: int = _MAX_TEXTO_ADJUNTO) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    return texto[:limite] + f"\n\n[…truncado: {len(texto) - limite} caracteres más]"


def _decodificar_bytes(crudo: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return crudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return crudo.decode("utf-8", errors="replace")


def _texto_de_xml(xml: str) -> str:
    xml = re.sub(r"</(w:p|text:p|a:p|p)\b[^>]*>", "\n", xml, flags=re.I)
    xml = re.sub(r"<[^>]+>", " ", xml)
    return html.unescape(re.sub(r"[ \t]+", " ", xml))


def _texto_zip_xml(crudo: bytes, internos: list[str]) -> str:
    partes = []
    with zipfile.ZipFile(io.BytesIO(crudo)) as z:
        nombres = z.namelist()
        for interno in internos:
            candidatos = [n for n in nombres if n == interno or n.startswith(interno)]
            for n in sorted(candidatos):
                if n.endswith("/"):
                    continue
                try:
                    partes.append(_texto_de_xml(_decodificar_bytes(z.read(n))))
                except Exception:
                    continue
    return "\n".join(p for p in partes if p.strip())


def _texto_pdf(crudo: bytes) -> str:
    tmp = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(crudo)
            tmp = f.name
        r = subprocess.run(
            ["pdftotext", "-q", "-layout", tmp, "-"],
            capture_output=True, timeout=20,
        )
        if r.returncode == 0 and r.stdout.strip():
            return _decodificar_bytes(r.stdout)
    except Exception:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    textos = []
    for m in re.finditer(rb"stream\r?\n(.+?)\r?\nendstream", crudo, flags=re.S):
        chunk = m.group(1)
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n") or chunk.startswith(b"\r"):
            chunk = chunk[1:]
        try:
            chunk = __import__("zlib").decompress(chunk)
        except Exception:
            pass
        try:
            s = chunk.decode("latin-1", errors="ignore")
        except Exception:
            continue
        for t in re.findall(r"\((?:\\.|[^\\)])*\)", s):
            t = t[1:-1]
            t = t.replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")
            t = re.sub(r"\\(\d{1,3})", lambda x: chr(int(x.group(1), 8) % 256), t)
            t = t.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
            if t.strip():
                textos.append(t)
    return "\n".join(textos)


def _es_texto_plano(mime: str, nombre: str) -> bool:
    ext = os.path.splitext(nombre)[1].lower()
    if mime.startswith("text/") or mime in (
        "application/json", "application/xml", "application/javascript",
        "application/rtf",
    ):
        return True
    return ext in {
        ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".css",
        ".js", ".py", ".log", ".ini", ".yaml", ".yml", ".toml", ".rst",
        ".tex", ".sql", ".bat", ".sh", ".rtf", ".tsv",
    }


def _ffprobe(crudo: bytes, ext: str) -> str:
    tmp = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=ext or ".bin", delete=False) as f:
            f.write(crudo)
            tmp = f.name
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration,format_name,size,bit_rate:stream=codec_type,codec_name,width,height,sample_rate,channels",
                "-of", "json", tmp,
            ],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return ""
        info = json.loads(r.stdout)
        fmt = info.get("format") or {}
        lineas = []
        if fmt.get("format_name"):
            lineas.append(f"formato: {fmt['format_name']}")
        if fmt.get("duration"):
            try:
                lineas.append(f"duración: {float(fmt['duration']):.1f}s")
            except (TypeError, ValueError):
                pass
        for i, st in enumerate(info.get("streams") or [], 1):
            bits = [st.get("codec_type"), st.get("codec_name")]
            if st.get("width") and st.get("height"):
                bits.append(f"{st['width']}x{st['height']}")
            if st.get("sample_rate"):
                bits.append(f"{st['sample_rate']} Hz")
            if st.get("channels"):
                bits.append(f"{st['channels']} canal(es)")
            lineas.append("stream %s: %s" % (i, ", ".join(str(b) for b in bits if b)))
        return "\n".join(lineas)
    except Exception:
        return ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _fotogramas_video(crudo: bytes, ext: str, cuantos: int = 3) -> list[str]:
    """Extrae fotogramas JPEG en data URL si ffmpeg está disponible."""
    tmp_in = tmp_dir = None
    salidas = []
    try:
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix="agro_vid_")
        fd, tmp_in = tempfile.mkstemp(suffix=ext or ".mp4", dir=tmp_dir)
        os.close(fd)
        with open(tmp_in, "wb") as f:
            f.write(crudo)
        patron = os.path.join(tmp_dir, "frame_%02d.jpg")
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_in, "-vf",
                "fps=1/max(1\\,duration/3)", "-frames:v", str(cuantos),
                "-q:v", "4", patron,
            ],
            capture_output=True, timeout=40,
        )
        if r.returncode != 0:
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_in, "-frames:v", "1", "-q:v", "4",
                 os.path.join(tmp_dir, "frame_01.jpg")],
                capture_output=True, timeout=30,
            )
        for nombre in sorted(os.listdir(tmp_dir)):
            if not nombre.lower().endswith(".jpg"):
                continue
            with open(os.path.join(tmp_dir, nombre), "rb") as f:
                jpg = f.read()
            if jpg:
                salidas.append("data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii"))
            if len(salidas) >= cuantos:
                break
    except Exception:
        return salidas
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return salidas


def _extraer_adjunto(nombre: str, mime: str, crudo: bytes) -> dict:
    """Devuelve texto extraído e imágenes (data URLs) para enviar al modelo."""
    nombre = (nombre or "archivo").strip() or "archivo"
    ext = os.path.splitext(nombre)[1].lower()
    mime = (mime or _MIME_POR_EXT.get(ext) or "application/octet-stream").split(";")[0].strip().lower()
    imagenes = []
    notas = [f"Archivo: {nombre} ({mime}, {len(crudo)} bytes)"]

    if mime in _TIPOS_IMAGEN or ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        if mime not in _TIPOS_IMAGEN:
            mime = _MIME_POR_EXT.get(ext, "image/jpeg")
        data_url = f"data:{mime};base64," + base64.b64encode(crudo).decode("ascii")
        return {"nombre": nombre, "mime": mime, "texto": "", "imagenes": [data_url]}

    texto = ""
    if ext == ".pdf" or mime == "application/pdf":
        texto = _texto_pdf(crudo)
        if not texto.strip():
            notas.append("PDF sin texto extraíble (puede ser un escaneo).")
    elif ext == ".docx" or "wordprocessingml" in mime:
        texto = _texto_zip_xml(crudo, ["word/document.xml"])
    elif ext == ".xlsx" or "spreadsheetml" in mime:
        texto = _texto_zip_xml(crudo, ["xl/sharedStrings.xml", "xl/worksheets/"])
    elif ext == ".pptx" or "presentationml" in mime:
        texto = _texto_zip_xml(crudo, ["ppt/slides/"])
    elif ext == ".odt" or ext == ".ods" or "opendocument" in mime:
        texto = _texto_zip_xml(crudo, ["content.xml"])
    elif _es_texto_plano(mime, nombre):
        texto = _decodificar_bytes(crudo)
    elif mime.startswith("audio/") or ext in {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}:
        info = _ffprobe(crudo, ext or ".mp3")
        notas.append("Audio adjunto. No hay transcripción automática.")
        if info:
            notas.append(info)
        else:
            notas.append("Instala ffmpeg/ffprobe para leer duración y códec.")
    elif mime.startswith("video/") or ext in {".mp4", ".webm", ".mov", ".mkv", ".avi"}:
        info = _ffprobe(crudo, ext or ".mp4")
        notas.append("Video adjunto.")
        if info:
            notas.append(info)
        imagenes = _fotogramas_video(crudo, ext or ".mp4")
        if imagenes:
            notas.append(f"Se extraeron {len(imagenes)} fotograma(s) para analizar.")
        else:
            notas.append("Instala ffmpeg para extraer fotogramas del video.")
    else:
        muestra = crudo[:400]
        ascii_ok = sum(32 <= b < 127 or b in (9, 10, 13) for b in muestra)
        if ascii_ok > 300:
            texto = _decodificar_bytes(crudo)
        else:
            notas.append("Tipo binario no reconocido: no se pudo extraer texto.")

    bloques = notas[:]
    if texto.strip():
        bloques.append("Contenido extraído:\n" + _truncar_texto(texto))
    return {"nombre": nombre, "mime": mime, "texto": "\n".join(bloques), "imagenes": imagenes}


def _preparar_adjuntos(lista) -> list:
    if not lista:
        return []
    if not isinstance(lista, list):
        raise ValueError("Adjuntos inválidos.")
    if len(lista) > _MAX_ADJUNTOS:
        raise ValueError(f"Máximo {_MAX_ADJUNTOS} archivos por mensaje.")
    preparados = []
    for item in lista:
        if not isinstance(item, dict):
            raise ValueError("Adjunto inválido.")
        nombre = str(item.get("name") or item.get("nombre") or "archivo")
        data_url = str(item.get("data") or item.get("url") or "")
        mime, crudo = _decodificar_data_url(data_url)
        mime_cli = str(item.get("type") or item.get("mime") or mime).split(";")[0].strip().lower()
        if mime_cli:
            mime = mime_cli
        preparados.append(_extraer_adjunto(nombre, mime, crudo))
    return preparados


def _preparar_imagen(data_url: str) -> str:
    """Compatibilidad: valida un data URL de imagen y lo devuelve."""
    data_url = (data_url or "").strip()
    if not data_url:
        return ""
    mime, crudo = _decodificar_data_url(data_url)
    if mime not in _TIPOS_IMAGEN:
        raise ValueError("Tipo de imagen no permitido. Usa JPEG, PNG, GIF o WebP.")
    return f"data:{mime};base64," + base64.b64encode(crudo).decode("ascii")


def responder(sid: str, texto: str, imagen: str = "", adjuntos: list | None = None):
    """Procesa un mensaje y devuelve (respuesta, herramientas_usadas)."""
    with LOCK:
        messages = SESIONES.setdefault(sid, [{"role": "system", "content": SYSTEM_PROMPT}])
        partes = []
        imagenes = []
        if imagen:
            imagenes.append(imagen)
        for adj in adjuntos or []:
            if adj.get("texto"):
                partes.append(adj["texto"])
            imagenes.extend(adj.get("imagenes") or [])
        cuerpo = (texto or "").strip()
        if partes:
            extra = "\n\n".join(partes)
            cuerpo = (cuerpo + "\n\n" + extra).strip() if cuerpo else extra
        if not cuerpo and imagenes:
            cuerpo = "Analiza el contenido adjunto."
        if imagenes:
            content = [{"type": "text", "text": cuerpo}]
            for url in imagenes:
                content.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": cuerpo})
        usadas = []

        for _ in range(8):  # límite de seguridad para evitar bucles infinitos
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS,
            )
            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                return msg.content or "", usadas

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                resumen = json.dumps(args, ensure_ascii=False)
                if len(resumen) > 120:
                    resumen = resumen[:120] + "…"
                usadas.append(f"{call.function.name}({resumen})")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": ejecutar_herramienta(call.function.name, args),
                })

        return "Se alcanzó el límite de pasos sin una respuesta final.", usadas


# --------------------------- PÁGINA WEB (HTML) --------------------------
HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agroNexus</title>
<link rel="icon" href="/icon">
<style>
  :root {
    --oscuro: #4A3224;
    --crema: #F3EBDD;
    --taupe: #6E6B47;
    --fondo: #E6D4B0;
    --elevado: #F6F0E4;
    --acento-hover: #5A5739;
    --texto: #4A3224;
    --texto-suave: #8A7355;
    --error-bg: #A8483C;
    --error-texto: #FFFFFF;
    --sidebar-hover: #5A5739;
    --blanco: #FFFFFF;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; display: flex; flex-direction: row;
    background: var(--fondo); color: var(--texto);
    font-family: "Segoe UI", system-ui, sans-serif;
  }
  #fondo {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 15;
  }
  #sidebar {
    width: 260px; flex-shrink: 0; display: flex; flex-direction: column;
    background: var(--fondo); border-right: 1px solid var(--taupe); z-index: 20;
  }
  .side-marca {
    padding: 16px 16px 8px; font-size: 13px; color: var(--texto-suave); letter-spacing: .4px;
  }
  #nueva {
    margin: 8px 12px 12px; padding: 10px 12px; border: 1px solid var(--taupe);
    border-radius: 10px; background: var(--taupe); color: var(--blanco);
    font-size: 14px; font-weight: 600; cursor: pointer; text-align: left;
  }
  #nueva:hover { background: var(--acento-hover); color: var(--blanco); }
  #lista { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
  .conv {
    display: flex; align-items: center; gap: 4px;
    padding: 6px 6px 6px 12px; border-radius: 8px; cursor: pointer; font-size: 14px;
    color: var(--texto); position: relative;
  }
  .conv-titulo {
    flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .conv:hover { background: var(--crema); }
  .conv.activa { background: var(--taupe); color: var(--blanco); }
  .conv-mas {
    flex-shrink: 0; width: 28px; height: 28px; border: 0; border-radius: 8px;
    background: transparent; color: var(--texto-suave); font-size: 18px; line-height: 1;
    cursor: pointer; padding: 0;
  }
  .conv-mas:hover { background: var(--elevado); color: var(--texto); }
  .conv.activa .conv-mas { color: var(--blanco); }
  .conv.activa .conv-mas:hover { background: var(--acento-hover); }
  .menu-conv {
    position: fixed; z-index: 40; min-width: 168px; padding: 6px;
    background: var(--elevado); border: 1px solid var(--taupe);
    border-radius: 10px; box-shadow: 0 8px 24px rgba(63,54,44,.18);
  }
  .menu-conv-item {
    display: block; width: 100%; text-align: left;
    padding: 8px 12px; border: 0; border-radius: 8px;
    background: transparent; color: var(--texto); font-size: 14px; cursor: pointer;
  }
  .menu-conv-item:hover { background: var(--fondo); color: var(--error-bg); }
  .vacio { padding: 10px 12px; color: var(--texto-suave); font-size: 13px; font-style: italic; }
  #app { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 20px; background: var(--oscuro); border-bottom: 1px solid var(--taupe);
  }
  header h1 { font-size: 18px; margin: 0; color: var(--blanco); letter-spacing: 0.4px; }
  header button {
    background: var(--taupe); color: var(--blanco); border: 0; border-radius: 8px;
    padding: 8px 14px; cursor: pointer; font-size: 14px;
  }
  header button:hover { background: var(--acento-hover); }
  #menu { display: none; margin-right: 10px; font-size: 18px; color: var(--blanco); }
  @media (max-width: 720px) {
    #sidebar {
      position: fixed; left: 0; top: 0; bottom: 0;
      transform: translateX(-100%); transition: transform .2s ease;
    }
    #sidebar.abierta { transform: translateX(0); }
    #fondo.visible { display: block; }
    #menu { display: inline-block; }
  }
  #chat {
    flex: 1; overflow-y: auto; padding: 20px;
    display: flex; flex-direction: column; gap: 12px;
    max-width: 900px; width: 100%; margin: 0 auto;
  }
  .msg {
    max-width: 80%; padding: 12px 16px; border-radius: 14px;
    line-height: 1.5; white-space: pre-wrap; word-wrap: break-word;
  }
  .user { align-self: flex-end; background: var(--taupe); color: var(--blanco); border-bottom-right-radius: 4px; }
  .bot  { align-self: flex-start; background: var(--elevado); color: var(--texto); border-bottom-left-radius: 4px; }
  .tool { align-self: flex-start; font-family: Consolas, monospace; font-size: 13px;
          color: var(--texto-suave); padding: 2px 6px; }
  .error { align-self: flex-start; background: var(--error-bg); color: var(--error-texto); }
  .sys  { align-self: center; color: var(--texto-suave); font-style: italic; font-size: 14px; }
  form {
    display: flex; gap: 10px; padding: 14px 20px 20px;
    max-width: 900px; width: 100%; margin: 0 auto;
  }
  input {
    flex: 1; padding: 14px 16px; border-radius: 12px; border: 1px solid var(--oscuro);
    background: var(--oscuro); color: var(--blanco); font-size: 16px; outline: none;
  }
  input:focus { border-color: var(--taupe); }
  input::placeholder { color: rgba(255,255,255,.65); }
  form button {
    padding: 0 24px; border: 0; border-radius: 12px; background: var(--taupe);
    color: var(--blanco); font-size: 16px; font-weight: 600; cursor: pointer;
  }
  form button:hover { background: var(--acento-hover); }
  form button:disabled, input:disabled { opacity: .5; cursor: not-allowed; }
  .loading {
    align-self: flex-start; max-width: 80%;
    background: var(--elevado); color: var(--texto);
    border-bottom-left-radius: 4px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .loading-row { display: flex; align-items: center; gap: 10px; }
  .spinner {
    width: 16px; height: 16px; flex-shrink: 0;
    border: 2px solid var(--taupe); border-top-color: var(--oscuro);
    border-radius: 50%; animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-bar {
    height: 4px; background: var(--fondo); border-radius: 4px; overflow: hidden;
  }
  .loading-bar > span {
    display: block; height: 100%; width: 40%;
    background: var(--taupe); border-radius: 4px;
    animation: slide 1.2s ease-in-out infinite;
  }
  @keyframes slide {
    0% { transform: translateX(-120%); }
    100% { transform: translateX(350%); }
  }
  .loading-meta { font-size: 12px; color: var(--texto-suave); }
  .msg img.adjunto {
    max-width: min(320px, 100%); max-height: 240px;
    border-radius: 10px; display: block; margin-top: 8px; object-fit: contain;
  }
  .msg audio.adjunto, .msg video.adjunto {
    display: block; margin-top: 8px; max-width: min(360px, 100%);
  }
  .msg video.adjunto { max-height: 240px; border-radius: 10px; }
  .chip-archivo {
    display: inline-flex; align-items: center; gap: 8px;
    margin-top: 8px; padding: 8px 12px; border-radius: 10px;
    background: var(--fondo); color: var(--texto); font-size: 13px;
    max-width: 100%;
  }
  .user .chip-archivo { background: var(--acento-hover); color: var(--blanco); }
  .chip-archivo span {
    min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #preview {
    display: none; max-width: 900px; width: 100%; margin: 0 auto;
    padding: 0 20px 8px; display: none; flex-wrap: wrap; gap: 10px;
  }
  #preview.visible { display: flex; }
  .preview-box { display: inline-block; position: relative; max-width: 220px; }
  .preview-box img, .preview-box video {
    max-height: 110px; max-width: 200px; border-radius: 10px;
    border: 1px solid var(--taupe); display: block; object-fit: cover;
  }
  .preview-box audio { width: 200px; }
  .preview-chip {
    padding: 10px 14px 10px 12px; background: var(--elevado);
    border: 1px solid var(--taupe); border-radius: 10px; font-size: 13px;
    max-width: 220px; color: var(--texto);
  }
  .preview-chip strong {
    display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .preview-chip small { color: var(--texto-suave); }
  .quitar-adj {
    position: absolute; top: -8px; right: -8px;
    width: 22px; height: 22px; border: 0; border-radius: 50%;
    background: var(--error-bg); color: var(--blanco); cursor: pointer; font-size: 14px;
    line-height: 22px; padding: 0;
  }
  #adjuntar, #microfono {
    padding: 0 16px; background: var(--oscuro); border: 1px solid var(--oscuro);
    color: var(--blanco); font-size: 18px; font-weight: 400;
  }
  #adjuntar:hover, #microfono:hover { background: var(--acento-hover); }
  #microfono.grabando {
    background: var(--error-bg); border-color: var(--error-bg);
    animation: pulso-mic .9s ease-in-out infinite;
  }
  @keyframes pulso-mic {
    0%, 100% { opacity: 1; }
    50% { opacity: .65; }
  }
</style>
</head>
<body>
  <div id="fondo"></div>
  <aside id="sidebar">
    <div class="side-marca">Conversaciones</div>
    <button id="nueva" type="button">+ Nueva conversación</button>
    <div id="lista"></div>
  </aside>
  <div id="app">
    <header>
      <div style="display:flex;align-items:center;gap:8px">
        <button id="menu" type="button" aria-label="Menú">☰</button>
        <img src="/icon" alt="Logo agroNexus" width="40" height="40" style="flex-shrink:0;display:block;border-radius:50%;object-fit:cover">
        <h1>agroNexus</h1>
      </div>
    </header>

    <div id="chat">
      <div class="msg sys">agroNexus listo. Escribe tu mensaje abajo.</div>
    </div>

    <div id="preview"></div>
    <form id="form">
      <input id="archivo" type="file" multiple hidden>
      <button id="adjuntar" type="button" title="Adjuntar archivos, documentos, audio o video">📎</button>
      <button id="microfono" type="button" title="Hablar: transcribe en tiempo real" aria-label="Hablar">🎤</button>
      <input id="texto" autocomplete="off" placeholder="Escribe o pulsa 🎤 para dictar..." autofocus>
      <button id="enviar" type="submit">Enviar</button>
    </form>
  </div>

<script>
  const chat = document.getElementById("chat");
  const form = document.getElementById("form");
  const input = document.getElementById("texto");
  const boton = document.getElementById("enviar");
  const lista = document.getElementById("lista");
  const sidebar = document.getElementById("sidebar");
  const fondo = document.getElementById("fondo");
  const KEY = "agronexus_conversaciones";

  let conversaciones = [];
  let actual = null;
  let pendientes = [];
  const MAX_ADJ = 8;
  const MAX_BYTES = 20 * 1024 * 1024;
  const archivo = document.getElementById("archivo");
  const adjuntar = document.getElementById("adjuntar");
  const preview = document.getElementById("preview");

  function uid() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function tamano(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function pintarPreview() {
    preview.innerHTML = "";
    if (!pendientes.length) {
      preview.classList.remove("visible");
      return;
    }
    preview.classList.add("visible");
    pendientes.forEach((p, i) => {
      const box = document.createElement("div");
      box.className = "preview-box";
      const tipo = p.type || "";
      if (tipo.startsWith("image/")) {
        const img = document.createElement("img");
        img.src = p.data;
        img.alt = p.name;
        box.appendChild(img);
      } else if (tipo.startsWith("video/")) {
        const v = document.createElement("video");
        v.src = p.data;
        v.muted = true;
        box.appendChild(v);
      } else if (tipo.startsWith("audio/")) {
        const a = document.createElement("audio");
        a.src = p.data;
        a.controls = true;
        box.appendChild(a);
      } else {
        const chip = document.createElement("div");
        chip.className = "preview-chip";
        chip.innerHTML = "<strong></strong><small></small>";
        chip.querySelector("strong").textContent = p.name;
        chip.querySelector("small").textContent = tamano(p.size || 0);
        box.appendChild(chip);
      }
      const x = document.createElement("button");
      x.type = "button";
      x.className = "quitar-adj";
      x.title = "Quitar";
      x.textContent = "×";
      x.addEventListener("click", () => {
        pendientes.splice(i, 1);
        pintarPreview();
      });
      box.appendChild(x);
      preview.appendChild(box);
    });
  }

  function quitarImagen() {
    pendientes = [];
    archivo.value = "";
    pintarPreview();
  }

  function leerArchivo(f) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({
        name: f.name,
        type: f.type || "application/octet-stream",
        size: f.size,
        data: reader.result,
      });
      reader.onerror = () => reject(new Error("No se pudo leer " + f.name));
      reader.readAsDataURL(f);
    });
  }

  function cargar() {
    try { conversaciones = JSON.parse(localStorage.getItem(KEY) || "[]"); }
    catch (e) { conversaciones = []; }
    if (!Array.isArray(conversaciones)) conversaciones = [];
  }

  function guardar() {
    try { localStorage.setItem(KEY, JSON.stringify(conversaciones)); }
    catch (e) { /* cuota de localStorage; la conversación sigue en memoria */ }
  }

  function convActual() {
    return conversaciones.find(c => c.id === actual);
  }

  function cerrarMenu() {
    sidebar.classList.remove("abierta");
    fondo.classList.remove("visible");
  }

  function cerrarMenuConv() {
    document.querySelectorAll(".menu-conv").forEach(m => m.remove());
  }

  function borrarConv(id) {
    cerrarMenuConv();
    const c = conversaciones.find(x => x.id === id);
    const nombre = (c && c.titulo) || "esta conversación";
    if (!confirm("¿Borrar «" + nombre + "»?")) return;
    conversaciones = conversaciones.filter(x => x.id !== id);
    guardar();
    fetch("/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: id }),
    }).catch(() => {});
    if (actual === id) {
      if (conversaciones.length) abrir(conversaciones[0].id);
      else nueva();
    } else {
      pintarLista();
    }
  }

  function menuConv(btn, id) {
    cerrarMenuConv();
    const menu = document.createElement("div");
    menu.className = "menu-conv";
    const item = document.createElement("button");
    item.type = "button";
    item.className = "menu-conv-item";
    item.textContent = "Borrar conversación";
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      borrarConv(id);
    });
    menu.appendChild(item);
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    let top = r.bottom + 4;
    let left = r.right - menu.offsetWidth;
    if (left < 8) left = 8;
    if (top + menu.offsetHeight > window.innerHeight - 8) {
      top = r.top - menu.offsetHeight - 4;
    }
    menu.style.top = top + "px";
    menu.style.left = left + "px";
  }

  function pintarLista() {
    cerrarMenuConv();
    lista.innerHTML = "";
    if (!conversaciones.length) {
      const v = document.createElement("div");
      v.className = "vacio";
      v.textContent = "Sin conversaciones aún";
      lista.appendChild(v);
      return;
    }
    conversaciones.forEach(c => {
      const d = document.createElement("div");
      d.className = "conv" + (c.id === actual ? " activa" : "");
      const titulo = document.createElement("span");
      titulo.className = "conv-titulo";
      titulo.textContent = c.titulo || "Nueva conversación";
      const mas = document.createElement("button");
      mas.type = "button";
      mas.className = "conv-mas";
      mas.setAttribute("aria-label", "Más opciones");
      mas.title = "Más opciones";
      mas.textContent = "⋮";
      mas.addEventListener("click", (e) => {
        e.stopPropagation();
        if (document.querySelector(".menu-conv")) {
          cerrarMenuConv();
          return;
        }
        menuConv(mas, c.id);
      });
      d.appendChild(titulo);
      d.appendChild(mas);
      d.addEventListener("click", () => abrir(c.id));
      lista.appendChild(d);
    });
  }

  function pintarAdjunto(div, a) {
    const tipo = (a && a.type) || "";
    const data = (a && a.data) || "";
    const nombre = (a && a.name) || "archivo";
    if (tipo.startsWith("image/") && data) {
      const img = document.createElement("img");
      img.className = "adjunto";
      img.src = data;
      img.alt = nombre;
      div.appendChild(img);
      return;
    }
    if (tipo.startsWith("audio/") && data) {
      const au = document.createElement("audio");
      au.className = "adjunto";
      au.src = data;
      au.controls = true;
      div.appendChild(au);
      return;
    }
    if (tipo.startsWith("video/") && data) {
      const v = document.createElement("video");
      v.className = "adjunto";
      v.src = data;
      v.controls = true;
      div.appendChild(v);
      return;
    }
    const chip = document.createElement("div");
    chip.className = "chip-archivo";
    const span = document.createElement("span");
    span.textContent = "📎 " + nombre;
    chip.appendChild(span);
    div.appendChild(chip);
  }

  function pintarMensaje(m) {
    const div = document.createElement("div");
    div.className = "msg " + m.clase;
    if (m.texto) div.textContent = m.texto;
    const listaAdj = Array.isArray(m.adjuntos) ? m.adjuntos.slice() : [];
    if (m.imagen) listaAdj.unshift({type: "image/jpeg", data: m.imagen, name: "imagen"});
    listaAdj.forEach(a => pintarAdjunto(div, a));
    return div;
  }

  function pintarChat(mensajes) {
    chat.innerHTML = "";
    const items = (mensajes && mensajes.length)
      ? mensajes
      : [{clase: "sys", texto: "agroNexus listo. Escribe tu mensaje abajo."}];
    items.forEach(m => chat.appendChild(pintarMensaje(m)));
    chat.scrollTop = chat.scrollHeight;
  }

  function nueva() {
    const c = { id: uid(), titulo: "Nueva conversación", mensajes: [] };
    conversaciones.unshift(c);
    actual = c.id;
    quitarImagen();
    guardar();
    pintarLista();
    pintarChat([]);
    cerrarMenu();
    input.focus();
  }

  function abrir(id) {
    const c = conversaciones.find(x => x.id === id);
    if (!c) return;
    actual = id;
    pintarLista();
    pintarChat(c.mensajes);
    cerrarMenu();
    input.focus();
  }

  function agregar(clase, texto, imagen, adjuntos) {
    const item = {
      clase,
      texto: texto || "",
      imagen: imagen || "",
      adjuntos: Array.isArray(adjuntos) ? adjuntos : [],
    };
    const div = pintarMensaje(item);
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    const c = convActual();
    if (c) {
      c.mensajes.push(item);
      if (clase === "user" && c.titulo === "Nueva conversación") {
        const t = texto || (item.adjuntos[0] && item.adjuntos[0].name) || "Archivo adjunto";
        c.titulo = t.length > 40 ? t.slice(0, 40) + "…" : t;
        pintarLista();
      }
      guardar();
    }
    return div;
  }

  function bloquear(estado) {
    input.disabled = estado;
    boton.disabled = estado;
    adjuntar.disabled = estado;
    if (!estado) input.focus();
  }

  async function post(ruta, datos) {
    const r = await fetch(ruta, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datos),
    });
    const json = await r.json();
    if (!r.ok) throw new Error(json.error || "Error del servidor");
    return json;
  }

  cargar();
  if (conversaciones.length) {
    actual = conversaciones[0].id;
    pintarLista();
    pintarChat(conversaciones[0].mensajes);
  } else {
    nueva();
  }

  document.getElementById("nueva").addEventListener("click", nueva);
  document.getElementById("menu").addEventListener("click", () => {
    sidebar.classList.add("abierta");
    fondo.classList.add("visible");
  });
  fondo.addEventListener("click", cerrarMenu);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".menu-conv") && !e.target.closest(".conv-mas")) {
      cerrarMenuConv();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") cerrarMenuConv();
  });

  adjuntar.addEventListener("click", () => archivo.click());
  archivo.addEventListener("change", async () => {
    const files = Array.from(archivo.files || []);
    archivo.value = "";
    for (const f of files) {
      if (pendientes.length >= MAX_ADJ) {
        alert("Máximo " + MAX_ADJ + " archivos por mensaje.");
        break;
      }
      if (f.size > MAX_BYTES) {
        alert(f.name + " supera 20 MB.");
        continue;
      }
      try {
        pendientes.push(await leerArchivo(f));
      } catch (err) {
        alert(err.message);
      }
    }
    pintarPreview();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const texto = input.value.trim();
    const adjuntos = pendientes.slice();
    if (!texto && !adjuntos.length) return;
    if (!actual) nueva();
    input.value = "";
    quitarImagen();
    agregar("user", texto, "", adjuntos);
    const carga = document.createElement("div");
    carga.className = "msg loading";
    carga.innerHTML =
      '<div class="loading-row"><div class="spinner"></div><span>Pensando…</span></div>' +
      '<div class="loading-bar"><span></span></div>' +
      '<div class="loading-meta">0 s · no se puede estimar el tiempo restante' +
      ' · si pide editar código, responde s/n en la terminal</div>';
    chat.appendChild(carga);
    chat.scrollTop = chat.scrollHeight;
    const meta = carga.querySelector(".loading-meta");
    const t0 = Date.now();
    const tick = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      meta.textContent = s + " s · no se puede estimar el tiempo restante" +
        " · si pide editar código, responde s/n en la terminal";
    }, 250);
    bloquear(true);

    try {
      const data = await post("/chat", {
        session: actual,
        message: texto,
        attachments: adjuntos.map(a => ({name: a.name, type: a.type, data: a.data})),
      });
      clearInterval(tick);
      carga.remove();
      (data.tools || []).forEach(t => agregar("tool", "🔧 " + t));
      agregar("bot", data.reply);
    } catch (err) {
      clearInterval(tick);
      carga.remove();
      agregar("error", "Error: " + err.message);
    }
    bloquear(false);
  });
</script>
</body>
</html>
"""


# ------------------------------ SERVIDOR -------------------------------
class Handler(BaseHTTPRequestHandler):
    def _enviar(self, codigo, cuerpo, tipo="application/json"):
        datos = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
        try:
            self.send_response(codigo)
            self.send_header("Content-Type", f"{tipo}; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
        except (ConnectionResetError, BrokenPipeError):
            pass  # el navegador cerró la conexión; no hay a quién responder

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._enviar(200, HTML, "text/html")
        elif self.path == "/icon" and ICONO_RUTA:
            try:
                with open(ICONO_RUTA, "rb") as f:
                    datos = f.read()
                self._enviar(200, datos, ICONO_TIPO)
            except OSError:
                self._enviar(404, "No encontrado", "text/plain")
        else:
            self._enviar(404, "No encontrado", "text/plain")

    def do_POST(self):
        try:
            largo = int(self.headers.get("Content-Length", 0))
            if largo > 25 * 1024 * 1024:
                self._enviar(400, json.dumps({"error": "Cuerpo demasiado grande"}))
                return
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except Exception:
            self._enviar(400, json.dumps({"error": "JSON inválido"}))
            return

        sid = str(datos.get("session", "default"))

        if self.path == "/reset":
            SESIONES.pop(sid, None)
            self._enviar(200, json.dumps({"ok": True}))

        elif self.path == "/chat":
            texto = str(datos.get("message", "")).strip()
            try:
                imagen = _preparar_imagen(str(datos.get("image") or ""))
                adjuntos = _preparar_adjuntos(
                    datos.get("attachments") or datos.get("adjuntos") or []
                )
            except ValueError as e:
                self._enviar(400, json.dumps({"error": str(e)}, ensure_ascii=False))
                return
            if not texto and not imagen and not adjuntos:
                self._enviar(400, json.dumps({"error": "Mensaje vacío"}))
                return
            try:
                respuesta, herramientas = responder(sid, texto, imagen, adjuntos)
                self._enviar(200, json.dumps(
                    {"reply": respuesta, "tools": herramientas}, ensure_ascii=False))
            except Exception as e:
                self._enviar(500, json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            self._enviar(404, json.dumps({"error": "Ruta no encontrada"}))

    def log_message(self, formato, *args):
        pass  # silencia los logs de cada petición


if __name__ == "__main__":
    servidor = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Agente web listo en {url}  (Ctrl + C para detener)")
    threading.Timer(1, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")