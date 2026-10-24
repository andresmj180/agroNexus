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
import uuid
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
    "Usa crear_archivo para archivos nuevos en tu carpeta (ruta relativa; "
    "el usuario debe aprobar). "
    "Haz cambios pequeños y precisos. "
    "El usuario puede adjuntar imágenes, documentos, audio y video: "
    "analiza el contenido extraído o las imágenes que lleguen con el mensaje."
)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
RUTA = os.path.abspath(__file__)


def _asegurar_gitignore_icono() -> None:
    """Agrega icon/ e historial.json a .gitignore si aún no están."""
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
    faltan = []
    if "icon" not in lineas:
        faltan.append("icon/")
    if "historial.json" not in lineas:
        faltan.append("historial.json")
    if "datos_herramientas.json" not in lineas:
        faltan.append("datos_herramientas.json")
    if not faltan:
        return
    extra = "" if not contenido or contenido.endswith("\n") else "\n"
    try:
        with open(ruta_gi, "a", encoding="utf-8") as f:
            f.write(extra + "\n".join(faltan) + "\n")
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


_CARPETA_AGENTE = os.path.dirname(RUTA)
_MAX_ARCHIVO_NUEVO = 512 * 1024  # 512 KB
_ARCHIVOS_PROTEGIDOS = {
    os.path.basename(RUTA),
    "clave.txt",
    "memoria.json",
    ".gitignore",
}


def _ruta_segura_nueva(ruta_relativa: str) -> str:
    """Resuelve una ruta relativa dentro de la carpeta del agente."""
    rel = (ruta_relativa or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel:
        raise ValueError("Usa una ruta relativa, por ejemplo 'notas/idea.md'.")
    partes = [p for p in rel.split("/") if p]
    if not partes or any(p == ".." or p in (".", "") for p in partes):
        raise ValueError("Ruta inválida.")
    destino = os.path.abspath(os.path.join(_CARPETA_AGENTE, *partes))
    raiz = os.path.abspath(_CARPETA_AGENTE)
    if destino != raiz and not destino.startswith(raiz + os.sep):
        raise ValueError("La ruta sale de la carpeta del agente.")
    if os.path.isdir(destino):
        raise ValueError("Esa ruta es una carpeta, no un archivo.")
    nombre = os.path.basename(destino)
    if nombre in _ARCHIVOS_PROTEGIDOS or nombre.endswith(".bak"):
        raise ValueError(f"No se puede crear ni sobrescribir '{nombre}'.")
    if nombre == ".env" or nombre.startswith(".git"):
        raise ValueError("No se pueden crear archivos sensibles de git/entorno.")
    return destino


def crear_archivo(ruta: str, contenido: str,
                  descripcion: str = "Nuevo archivo del agente",
                  sobrescribir: bool = False) -> str:
    """Crea un archivo de texto en la carpeta del agente (con aprobación)."""
    destino = _ruta_segura_nueva(ruta)
    rel = os.path.relpath(destino, _CARPETA_AGENTE).replace("\\", "/")
    contenido = contenido if contenido is not None else ""
    if not isinstance(contenido, str):
        return "Error: el contenido debe ser texto."
    if len(contenido.encode("utf-8")) > _MAX_ARCHIVO_NUEVO:
        return "Error: el archivo supera 512 KB."

    existe = os.path.isfile(destino)
    if existe and not sobrescribir:
        return (f"Error: '{rel}' ya existe. "
                "Pasa sobrescribir=true si quieres reemplazarlo.")

    preview = contenido if len(contenido) <= 800 else contenido[:800] + "\n…"
    print("\n=== ARCHIVO PROPUESTO POR EL AGENTE ===")
    print(f"Ruta: {rel}")
    print(f"Acción: {'sobrescribir' if existe else 'crear'}")
    print(f"Mensaje del commit: Agente: {descripcion}")
    print("--- contenido ---")
    print(preview.replace(API_KEY, "pk_QOMU0zSjBcOtBHnctxj57KxEONrfZOuQyMdACbXN"))
    print("--- fin ---")
    if input("¿Crear este archivo y subirlo a GitHub? (s/n): ").strip().lower() != "s":
        return "El usuario rechazó la creación del archivo."

    os.makedirs(os.path.dirname(destino) or _CARPETA_AGENTE, exist_ok=True)
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        f.write(contenido)

    carpeta = _CARPETA_AGENTE
    pasos = (
        ["git", "add", rel],
        ["git", "commit", "-m", f"Agente: {descripcion}"],
        ["git", "push"],
    )
    try:
        for cmd in pasos:
            r = subprocess.run(cmd, cwd=carpeta, capture_output=True,
                               text=True, timeout=60)
            if r.returncode != 0:
                detalle = (r.stderr or r.stdout).strip()
                return (f"Archivo guardado en '{rel}', pero git falló "
                        f"en '{cmd[1]}': {detalle}")
        return f"Archivo creado: {rel}. Commit y push realizados en GitHub."
    except Exception as e:
        return f"Archivo guardado en '{rel}', pero hubo un error con git: {e}"


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


# --------- Herramientas de finca (menú) ---------
_DATOS_HERR = os.path.join(os.path.dirname(RUTA), "datos_herramientas.json")
_UMBRAL_ROYA = 5.0   # % incidencia, guía Cenicafé (variedades susceptibles)
_UMBRAL_BROCA = 2.0  # % frutos brocados, umbral de manejo


def _cargar_datos_herr() -> dict:
    try:
        with open(_DATOS_HERR, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _guardar_datos_herr(data: dict) -> None:
    tmp = _DATOS_HERR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _DATOS_HERR)


def _http_json(url: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "agroNexus/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _geocodificar(municipio: str) -> tuple:
    import urllib.parse
    nombre = (municipio or "").strip()
    if not nombre:
        raise ValueError("Municipio vacío.")
    q = urllib.parse.urlencode({
        "name": nombre, "count": 3, "language": "es", "country": "CO",
    })
    data = _http_json("https://geocoding-api.open-meteo.com/v1/search?" + q)
    results = data.get("results") or []
    if not results:
        q = urllib.parse.urlencode({"name": nombre, "count": 1, "language": "es"})
        data = _http_json("https://geocoding-api.open-meteo.com/v1/search?" + q)
        results = data.get("results") or []
    if not results:
        raise ValueError(f"No encontré '{nombre}' en el mapa.")
    r = results[0]
    etiqueta = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
    return float(r["latitude"]), float(r["longitude"]), etiqueta


def _pronostico(lat: float, lon: float) -> dict:
    import urllib.parse
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weather_code,temperature_2m_max,precipitation_sum,"
                 "precipitation_probability_max,sunshine_duration,cloud_cover_mean",
        "timezone": "America/Bogota",
        "forecast_days": 7,
    })
    return _http_json("https://api.open-meteo.com/v1/forecast?" + q)


def _resolver_coords(municipio: str = "", latitud: str = "",
                     longitud: str = "") -> tuple:
    try:
        if str(latitud).strip() and str(longitud).strip():
            lat = float(str(latitud).replace(",", "."))
            lon = float(str(longitud).replace(",", "."))
            return lat, lon, f"{lat:.3f}, {lon:.3f}"
    except ValueError:
        pass
    mun = (municipio or "").strip()
    if mun:
        return _geocodificar(mun)
    return 5.0689, -75.5174, "Manizales (predeterminado)"


def _dia_secado(sol_s, lluvia_mm, prob_lluvia, nubes) -> str:
    horas_sol = (sol_s or 0) / 3600.0
    lluvia = lluvia_mm or 0
    prob = prob_lluvia or 0
    nubes = nubes or 0
    if lluvia >= 5 or prob >= 70:
        return "esperar"
    if horas_sol >= 6 and lluvia < 1 and nubes < 60:
        return "secar"
    if horas_sol >= 4 and lluvia < 2 and prob < 50:
        return "posible"
    return "secadora"


def ventana_secado(municipio: str = "", latitud: str = "",
                   longitud: str = "") -> str:
    """Días con sol suficiente para secar café, según el pronóstico."""
    lat, lon, donde = _resolver_coords(municipio, latitud, longitud)
    data = _pronostico(lat, lon)
    daily = data.get("daily") or {}
    fechas = daily.get("time") or []
    if not fechas:
        return "No pude leer el pronóstico. Intenta de nuevo."
    n = len(fechas)

    def col(clave):
        v = daily.get(clave) or []
        return list(v) + [None] * (n - len(v))

    sols, lluvias = col("sunshine_duration"), col("precipitation_sum")
    probs, nubes = col("precipitation_probability_max"), col("cloud_cover_mean")
    tmax = col("temperature_2m_max")
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    lineas = [f"Ventana de secado — {donde}", ""]
    secar = []
    for i, fecha in enumerate(fechas):
        y, m, d = fecha.split("-")
        dt = datetime.date(int(y), int(m), int(d))
        nombre = dias[dt.weekday()]
        horas = (sols[i] or 0) / 3600.0
        lluvia = lluvias[i] or 0
        prob = probs[i] or 0
        rec = _dia_secado(sols[i], lluvias[i], probs[i], nubes[i])
        t = tmax[i]
        t_txt = f", {t:.0f} °C" if t is not None else ""
        if rec == "secar":
            marca, secar_txt = "SECAR al sol", nombre
        elif rec == "posible":
            marca, secar_txt = "posible, vigile nubes", nombre + "?"
        elif rec == "secadora":
            marca, secar_txt = "usar secadora o marquesina", None
        else:
            marca, secar_txt = "esperar (lluvia)", None
        if secar_txt:
            secar.append(secar_txt)
        lineas.append(
            f"{nombre} {d}/{m}: {horas:.1f} h sol, {lluvia:.1f} mm{t_txt} "
            f"({int(prob)}% lluvia) → {marca}"
        )
    lineas.append("")
    if secar:
        lineas.append("Mejores días: " + ", ".join(secar) + ".")
    else:
        lineas.append("Esta semana no hay sol claro: priorice secadora o espere.")
    return "\n".join(lineas)


def monitoreo_lote(lote: str, roya: str = "", broca: str = "",
                   fecha: str = "") -> str:
    """Guarda % de roya/broca, calcula tendencia y compara con umbral Cenicafé."""
    lote = (lote or "").strip()
    if not lote:
        return "Indica el lote."

    def _pct(v):
        if v is None or str(v).strip() == "":
            return None
        x = float(str(v).replace(",", "."))
        if x < 0 or x > 100:
            raise ValueError("El porcentaje debe estar entre 0 y 100.")
        return x

    r, b = _pct(roya), _pct(broca)
    if r is None and b is None:
        return "Indica al menos el % de roya o el % de broca."
    if not (fecha or "").strip():
        fecha = datetime.date.today().isoformat()
    data = _cargar_datos_herr()
    regs = data.setdefault("monitoreos", [])
    regs.append({"lote": lote, "fecha": fecha, "roya": r, "broca": b})
    _guardar_datos_herr(data)
    hist = [x for x in regs if str(x.get("lote", "")).lower() == lote.lower()]
    hist.sort(key=lambda x: str(x.get("fecha") or ""))
    lineas = [f"Monitoreo — lote {lote} ({fecha})", ""]
    if r is not None:
        if r >= _UMBRAL_ROYA:
            estado = "POR ENCIMA del umbral: actuar (manejo según guía Cenicafé)."
        else:
            estado = "por debajo del umbral."
        lineas.append(
            f"Roya: {r:.1f}% de hojas. Umbral: {_UMBRAL_ROYA:.0f}%. Está {estado}"
        )
    if b is not None:
        if b >= _UMBRAL_BROCA:
            estado = "POR ENCIMA del umbral: recoja granos del suelo y evalúe control."
        else:
            estado = "por debajo del umbral."
        lineas.append(
            f"Broca: {b:.1f}% de frutos. Umbral: {_UMBRAL_BROCA:.0f}%. Está {estado}"
        )
    if len(hist) >= 2:
        lineas.append("")
        lineas.append("Tendencia (últimos registros):")
        for h in hist[-5:]:
            bits = [h.get("fecha")]
            if h.get("roya") is not None:
                bits.append(f"roya {h['roya']}%")
            if h.get("broca") is not None:
                bits.append(f"broca {h['broca']}%")
            lineas.append(" · ".join(str(x) for x in bits if x is not None))

        def _trend(key):
            vals = [h.get(key) for h in hist if h.get(key) is not None]
            if len(vals) < 2:
                return None
            if vals[-1] > vals[-2] + 0.3:
                return "sube"
            if vals[-1] < vals[-2] - 0.3:
                return "baja"
            return "estable"

        extra = []
        tr, tb = _trend("roya"), _trend("broca")
        if tr:
            extra.append(f"roya {tr}")
        if tb:
            extra.append(f"broca {tb}")
        if extra:
            lineas.append("Resumen: " + ", ".join(extra) + ".")
    else:
        lineas.append("\nPrimer dato de este lote. Siga midiendo para ver tendencia.")
    return "\n".join(lineas)


def plan_semana(lote: str = "", etapa: str = "cosecha",
                municipio: str = "") -> str:
    """Lista corta de labores según pronóstico, etapa e historial del lote."""
    lat, lon, donde = _resolver_coords(municipio, "", "")
    try:
        data = _pronostico(lat, lon)
        daily = data.get("daily") or {}
        fechas = daily.get("time") or []
        lluvias = daily.get("precipitation_sum") or []
        sols = daily.get("sunshine_duration") or []
        probs = daily.get("precipitation_probability_max") or []
    except Exception:
        fechas, lluvias, sols, probs = [], [], [], []
    etapa = (etapa or "cosecha").strip().lower()
    labores, secar_dias, lluvia_dias = [], [], []
    dias_n = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
    for i, fecha in enumerate(fechas):
        y, m, d = fecha.split("-")
        dt = datetime.date(int(y), int(m), int(d))
        nom = f"{dias_n[dt.weekday()]} {d}/{m}"
        rec = _dia_secado(
            sols[i] if i < len(sols) else 0,
            lluvias[i] if i < len(lluvias) else 0,
            probs[i] if i < len(probs) else 0,
            0,
        )
        if rec in ("secar", "posible"):
            secar_dias.append(nom)
        mm = lluvias[i] if i < len(lluvias) else 0
        if (mm or 0) >= 5:
            lluvia_dias.append(nom)
    if etapa in ("cosecha", "maduracion"):
        labores.append("Cosechar en seco: pases cada 15–20 días, solo pintón/maduro.")
        if secar_dias:
            labores.append("Secar café en: " + ", ".join(secar_dias) + ".")
        else:
            labores.append("Poca ventana de sol: use secadora o marquesina.")
    if etapa in ("crecimiento", "floracion", "llenado"):
        if lluvia_dias:
            labores.append("No abonar en aguacero (" + ", ".join(lluvia_dias[:3]) + ").")
        else:
            labores.append("Ventana para abonar si el suelo está húmedo, no encharcado.")
    if etapa == "floracion":
        labores.append("Evite aspersiones fuertes en pico de flor. Revise cuaje.")
    if etapa == "llenado":
        labores.append("Ronda nutricional (K) si corresponde al plan de fertilización.")
    if etapa == "postcosecha":
        if secar_dias:
            labores.append("Secado solar: " + ", ".join(secar_dias) + ".")
        labores.append("Limpieza de tanques, silos y marquesina.")
    datah = _cargar_datos_herr()
    lote_n = (lote or "").strip()
    hist = [
        x for x in (datah.get("monitoreos") or [])
        if not lote_n or str(x.get("lote", "")).lower() == lote_n.lower()
    ]
    hist.sort(key=lambda x: str(x.get("fecha") or ""))
    ultimo = hist[-1] if hist else None
    if ultimo:
        if ultimo.get("roya") is not None and ultimo["roya"] >= _UMBRAL_ROYA:
            labores.append(
                f"Roya en {ultimo['roya']}% (lote {ultimo.get('lote')}): "
                "programar control esta semana."
            )
        elif ultimo.get("roya") is not None:
            labores.append(
                f"Revisar roya (último {ultimo['roya']}% el {ultimo.get('fecha')}); "
                f"umbral {int(_UMBRAL_ROYA)}%."
            )
        if ultimo.get("broca") is not None and ultimo["broca"] >= _UMBRAL_BROCA:
            labores.append(
                f"Broca en {ultimo['broca']}%: recoja granos del suelo y evalúe control."
            )
        elif ultimo.get("broca") is not None:
            labores.append(
                f"Muestreo de broca (último {ultimo['broca']}%); "
                f"umbral {int(_UMBRAL_BROCA)}%."
            )
    else:
        labores.append(
            "Haga un muestreo de roya y broca (20 árboles / lote) y regístrelo."
        )
    if not labores:
        labores.append("Semana de mantenimiento: plateo, sombrío y caminos.")
    titulo = f"Plan de la semana — {lote_n or 'su lote'}"
    if donde:
        titulo += f" ({donde})"
    lineas = [titulo, f"Etapa: {etapa}", ""]
    for i, lab in enumerate(labores, 1):
        lineas.append(f"{i}. {lab}")
    return "\n".join(lineas)


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
            "name": "crear_archivo",
            "description": "Crea un archivo de texto nuevo en la carpeta del agente "
                           "(ruta relativa, p. ej. 'notas/idea.md'). El usuario debe "
                           "aprobar. No sirve para editar este programa: usa editar_mi_codigo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {
                        "type": "string",
                        "description": "Ruta relativa al archivo, por ejemplo 'docs/readme.md'",
                    },
                    "contenido": {
                        "type": "string",
                        "description": "Contenido completo del archivo (texto UTF-8)",
                    },
                    "descripcion": {
                        "type": "string",
                        "description": "Mensaje breve del commit (qué archivo y por qué)",
                    },
                    "sobrescribir": {
                        "type": "boolean",
                        "description": "Si true, reemplaza un archivo que ya existe",
                    },
                },
                "required": ["ruta", "contenido", "descripcion"],
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
    {
        "type": "function",
        "function": {
            "name": "ventana_secado",
            "description": "Con el pronóstico, indica qué días hay sol suficiente "
                           "para secar café y cuándo esperar o usar secadora.",
            "parameters": {
                "type": "object",
                "properties": {
                    "municipio": {"type": "string"},
                    "latitud": {"type": "string"},
                    "longitud": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monitoreo_lote",
            "description": "Guarda % de hojas con roya o frutos con broca, calcula "
                           "tendencia por lote y lo compara con umbrales Cenicafé.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lote": {"type": "string"},
                    "roya": {"type": "string"},
                    "broca": {"type": "string"},
                    "fecha": {"type": "string"},
                },
                "required": ["lote"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_semana",
            "description": "Junta pronóstico, etapa del lote e historial y entrega "
                           "una lista corta de labores de la semana.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lote": {"type": "string"},
                    "etapa": {"type": "string"},
                    "municipio": {"type": "string"},
                },
            },
        },
    },
]

FUNCIONES = {
    "calculadora": calculadora,
    "hora_actual": hora_actual,
    "leer_mi_codigo": leer_mi_codigo,
    "editar_mi_codigo": editar_mi_codigo,
    "crear_archivo": crear_archivo,
    "buscar_web": buscar_web,
    "memoria_leer": memoria_leer,
    "memoria_guardar": memoria_guardar,
    "memoria_borrar": memoria_borrar,
    "ventana_secado": ventana_secado,
    "monitoreo_lote": monitoreo_lote,
    "plan_semana": plan_semana,
}


def ejecutar_herramienta(nombre: str, args: dict) -> str:
    try:
        return str(FUNCIONES[nombre](**args))
    except Exception as e:
        return f"Error: {e}"


# ------------------------------- AGENTE --------------------------------
SESIONES = {}          # id de sesión -> historial de mensajes
LOCK = threading.RLock()
HISTORIAL_ARCHIVO = os.path.join(os.path.dirname(RUTA), "historial.json")
HISTORIAL_META = {}    # id -> {titulo, creada, actualizada}


def _contenido_sin_imagenes(content):
    if not isinstance(content, list):
        return content
    out = []
    for p in content:
        if isinstance(p, dict) and p.get("type") == "image_url":
            out.append({"type": "text", "text": "[imagen adjunta]"})
        else:
            out.append(p)
    return out


def _msg_a_dict(msg) -> dict:
    if isinstance(msg, dict):
        role = msg.get("role", "assistant")
        content = msg.get("content")
        d = {"role": role, "content": _contenido_sin_imagenes(content)}
        if msg.get("tool_calls"):
            d["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            d["tool_call_id"] = msg["tool_call_id"]
    else:
        d = {
            "role": getattr(msg, "role", "assistant"),
            "content": _contenido_sin_imagenes(getattr(msg, "content", None)),
        }
        tcs = getattr(msg, "tool_calls", None)
        if tcs:
            d["tool_calls"] = []
            for tc in tcs:
                fn = getattr(tc, "function", None)
                d["tool_calls"].append({
                    "id": getattr(tc, "id", ""),
                    "type": "function",
                    "function": {
                        "name": getattr(fn, "name", "") if fn else "",
                        "arguments": getattr(fn, "arguments", "") if fn else "",
                    },
                })
    if d.get("role") == "tool" and isinstance(d.get("content"), str) and len(d["content"]) > 2000:
        d["content"] = d["content"][:2000] + "\n[…truncado]"
    return d


def _titulo_de_mensajes(messages) -> str:
    for m in messages:
        d = _msg_a_dict(m)
        if d.get("role") != "user":
            continue
        c = d.get("content")
        if isinstance(c, list):
            c = " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
        texto = " ".join(str(c or "").split())
        if texto:
            return (texto[:48] + "…") if len(texto) > 48 else texto
    return "Nueva conversación"


def _touch_conversacion(sid: str, messages=None) -> None:
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    meta = HISTORIAL_META.get(sid) or {}
    if not meta.get("creada"):
        meta["creada"] = ahora
    meta["actualizada"] = ahora
    if messages:
        auto = _titulo_de_mensajes(messages)
        if not meta.get("titulo") or meta.get("titulo") in ("Nueva conversación", "Conversación"):
            meta["titulo"] = auto
    HISTORIAL_META[sid] = meta


def _cargar_historial() -> None:
    try:
        with open(HISTORIAL_ARCHIVO, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        return
    if not isinstance(data, dict):
        return
    convs = data.get("conversaciones")
    if not isinstance(convs, dict):
        return
    with LOCK:
        for sid, item in convs.items():
            if not isinstance(item, dict):
                continue
            msgs = item.get("mensajes")
            if isinstance(msgs, list) and msgs:
                SESIONES[sid] = msgs
            HISTORIAL_META[sid] = {
                "titulo": str(item.get("titulo") or "Conversación"),
                "creada": str(item.get("creada") or ""),
                "actualizada": str(item.get("actualizada") or ""),
            }


def _guardar_historial() -> None:
    convs = {}
    with LOCK:
        sids = set(SESIONES) | set(HISTORIAL_META)
        for sid in sids:
            messages = SESIONES.get(sid) or []
            meta = HISTORIAL_META.get(sid) or {}
            convs[sid] = {
                "titulo": meta.get("titulo") or _titulo_de_mensajes(messages),
                "creada": meta.get("creada") or "",
                "actualizada": meta.get("actualizada") or "",
                "mensajes": [_msg_a_dict(m) for m in messages],
            }
    tmp = HISTORIAL_ARCHIVO + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"conversaciones": convs}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORIAL_ARCHIVO)
    except OSError:
        pass


def listar_conversaciones() -> list:
    with LOCK:
        items = []
        sids = set(SESIONES) | set(HISTORIAL_META)
        for sid in sids:
            meta = HISTORIAL_META.get(sid) or {}
            msgs = SESIONES.get(sid) or []
            items.append({
                "id": sid,
                "titulo": meta.get("titulo") or _titulo_de_mensajes(msgs),
                "creada": meta.get("creada") or "",
                "actualizada": meta.get("actualizada") or "",
            })
        items.sort(key=lambda x: x.get("actualizada") or "", reverse=True)
        return items[:50]


def mensajes_visibles(sid: str) -> list:
    with LOCK:
        messages = SESIONES.get(sid) or []
    visibles = []
    for m in messages:
        d = _msg_a_dict(m)
        role = d.get("role")
        if role not in ("user", "assistant") or d.get("tool_calls"):
            continue
        c = d.get("content")
        if isinstance(c, list):
            c = "\n".join(
                str(p.get("text", "")) for p in c
                if isinstance(p, dict) and p.get("text")
            )
        texto = (c or "").strip()
        if texto:
            visibles.append({"role": role, "content": texto})
    return visibles


def nueva_conversacion() -> str:
    sid = str(uuid.uuid4())
    with LOCK:
        SESIONES[sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
        _touch_conversacion(sid, SESIONES[sid])
        HISTORIAL_META[sid]["titulo"] = "Nueva conversación"
    _guardar_historial()
    return sid


def eliminar_conversacion(sid: str) -> None:
    with LOCK:
        SESIONES.pop(sid, None)
        HISTORIAL_META.pop(sid, None)
    _guardar_historial()


def renombrar_conversacion(sid: str, titulo: str) -> bool:
    titulo = (titulo or "").strip()[:80] or "Conversación"
    with LOCK:
        if sid not in HISTORIAL_META and sid not in SESIONES:
            return False
        meta = HISTORIAL_META.get(sid) or {}
        meta["titulo"] = titulo
        HISTORIAL_META[sid] = meta
    _guardar_historial()
    return True


_cargar_historial()

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


# --------------------------- ARCHIVOS ESTÁTICOS --------------------------
_CARPETA_STATIC = os.path.join(os.path.dirname(RUTA), "static")
_CARPETA_IMG = os.path.join(os.path.dirname(RUTA), "img")
_RUTA_INDEX = os.path.join(_CARPETA_STATIC, "index.html")
_MIME_STATIC = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".json": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _ruta_estatica_segura(relativa: str):
    """Resuelve un archivo dentro de static/ sin salir de esa carpeta."""
    rel = (relativa or "").replace("\\", "/").lstrip("/")
    if rel.startswith("static/"):
        rel = rel[7:]
    partes = [p for p in rel.split("/") if p]
    if not partes or any(p == ".." or p == "." for p in partes):
        return None
    destino = os.path.abspath(os.path.join(_CARPETA_STATIC, *partes))
    raiz = os.path.abspath(_CARPETA_STATIC)
    if destino != raiz and not destino.startswith(raiz + os.sep):
        return None
    if not os.path.isfile(destino):
        return None
    return destino


def _listar_img() -> list:
    try:
        return [
            n for n in os.listdir(_CARPETA_IMG)
            if os.path.isfile(os.path.join(_CARPETA_IMG, n))
            and os.path.splitext(n)[1].lower() in _MIME_STATIC
        ]
    except OSError:
        return []


def _icono_por_clave(clave: str) -> str | None:
    """Elige el archivo de img/ cuyo nombre encaja con la herramienta."""
    clave = (clave or "").lower()
    pistas = {
        "ventana_secado": ("ventanasolarsecado", "ventana", "secado", "solar"),
        "monitoreo_lote": ("monitoreoroyaybroca", "monitoreo", "roya", "broca"),
        "plan_semana": ("plansemanal", "plan", "semana", "semanal"),
    }
    preferidas = pistas.get(clave, (clave,))
    nombres = _listar_img()
    if not nombres:
        return None
    bajos = {n.lower(): n for n in nombres}
    for pista in preferidas:
        for bajo, real in bajos.items():
            stem = os.path.splitext(bajo)[0]
            if pista in stem.replace(" ", "").replace("_", "").replace("-", ""):
                return real
    return None


def _ruta_img_segura(relativa: str):
    """Resuelve un archivo dentro de img/ sin salir de esa carpeta."""
    rel = (relativa or "").replace("\\", "/").lstrip("/")
    if rel.startswith("img/"):
        rel = rel[4:]
    partes = [p for p in rel.split("/") if p]
    if not partes or any(p == ".." or p == "." for p in partes):
        return None
    destino = os.path.abspath(os.path.join(_CARPETA_IMG, *partes))
    raiz = os.path.abspath(_CARPETA_IMG)
    if destino != raiz and not destino.startswith(raiz + os.sep):
        return None
    if not os.path.isfile(destino):
        return None
    return destino


_MENU_INJECT = (
    '\n<link rel="stylesheet" href="/static/menu-herramientas.css">'
    '\n<script src="/static/menu-herramientas.js" defer></script>\n'
)


def _html_con_menu(html_txt: str) -> str:
    if "menu-herramientas.js" in html_txt:
        return html_txt
    if "</head>" in html_txt:
        return html_txt.replace("</head>", _MENU_INJECT + "</head>", 1)
    if "</body>" in html_txt:
        return html_txt.replace("</body>", _MENU_INJECT + "</body>", 1)
    return html_txt + _MENU_INJECT


# ------------------------------ SERVIDOR -------------------------------
class Handler(BaseHTTPRequestHandler):
    def _enviar(self, codigo, cuerpo, tipo="application/json"):
        datos = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
        try:
            self.send_response(codigo)
            if tipo.startswith("image/") or tipo.startswith("font/"):
                self.send_header("Content-Type", tipo)
            else:
                self.send_header("Content-Type", f"{tipo}; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
        except (ConnectionResetError, BrokenPipeError):
            pass  # el navegador cerró la conexión; no hay a quién responder

    def do_GET(self):
        ruta = self.path.split("?", 1)[0]
        if ruta in ("/", "/index.html"):
            try:
                with open(_RUTA_INDEX, encoding="utf-8") as f:
                    self._enviar(200, _html_con_menu(f.read()), "text/html")
            except OSError:
                self._enviar(404, "No encontrado", "text/plain")
        elif ruta == "/icon" and ICONO_RUTA:
            try:
                with open(ICONO_RUTA, "rb") as f:
                    datos = f.read()
                self._enviar(200, datos, ICONO_TIPO)
            except OSError:
                self._enviar(404, "No encontrado", "text/plain")
        elif ruta.startswith("/static/"):
            destino = _ruta_estatica_segura(ruta)
            if not destino:
                self._enviar(404, "No encontrado", "text/plain")
                return
            ext = os.path.splitext(destino)[1].lower()
            tipo = _MIME_STATIC.get(ext, "application/octet-stream")
            try:
                with open(destino, "rb") as f:
                    datos = f.read()
                self._enviar(200, datos, tipo)
            except OSError:
                self._enviar(404, "No encontrado", "text/plain")
        elif ruta.startswith("/img/"):
            destino = _ruta_img_segura(ruta)
            if not destino:
                self._enviar(404, "No encontrado", "text/plain")
                return
            ext = os.path.splitext(destino)[1].lower()
            tipo = _MIME_STATIC.get(ext, "application/octet-stream")
            try:
                with open(destino, "rb") as f:
                    datos = f.read()
                self._enviar(200, datos, tipo)
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
            with LOCK:
                SESIONES[sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
                _touch_conversacion(sid, SESIONES[sid])
                if HISTORIAL_META.get(sid, {}).get("titulo") not in (
                    None, "Nueva conversación", "Conversación",
                ):
                    pass
                else:
                    HISTORIAL_META.setdefault(sid, {})["titulo"] = "Nueva conversación"
            _guardar_historial()
            self._enviar(200, json.dumps({"ok": True}))

        elif self.path == "/conversaciones":
            self._enviar(200, json.dumps(
                {"conversaciones": listar_conversaciones()}, ensure_ascii=False))

        elif self.path == "/conversacion/nueva":
            nuevo = nueva_conversacion()
            self._enviar(200, json.dumps({"id": nuevo}, ensure_ascii=False))

        elif self.path == "/conversacion/cargar":
            msgs = mensajes_visibles(sid)
            with LOCK:
                meta = HISTORIAL_META.get(sid) or {}
            self._enviar(200, json.dumps({
                "id": sid,
                "titulo": meta.get("titulo") or "Conversación",
                "mensajes": msgs,
            }, ensure_ascii=False))

        elif self.path == "/conversacion/eliminar":
            eliminar_conversacion(sid)
            self._enviar(200, json.dumps({"ok": True}))

        elif self.path == "/conversacion/renombrar":
            titulo = str(datos.get("titulo") or datos.get("title") or "")
            ok = renombrar_conversacion(sid, titulo)
            self._enviar(200 if ok else 404, json.dumps({"ok": ok}))

        elif self.path == "/herramienta":
            nombre = str(datos.get("herramienta") or datos.get("nombre") or "").strip()
            permitidas = ("ventana_secado", "monitoreo_lote", "plan_semana")
            if nombre not in permitidas:
                self._enviar(400, json.dumps(
                    {"error": "Herramienta no reconocida"}, ensure_ascii=False))
                return
            args = {}
            for clave in ("municipio", "latitud", "longitud", "lote",
                          "roya", "broca", "fecha", "etapa"):
                if clave in datos and datos[clave] is not None:
                    args[clave] = str(datos[clave])
            try:
                resultado = ejecutar_herramienta(nombre, args)
                self._enviar(200, json.dumps(
                    {"resultado": resultado}, ensure_ascii=False))
            except Exception as e:
                self._enviar(500, json.dumps({"error": str(e)}, ensure_ascii=False))

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
                with LOCK:
                    _touch_conversacion(sid, SESIONES.get(sid) or [])
                _guardar_historial()
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