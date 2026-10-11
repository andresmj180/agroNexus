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
import datetime
import difflib
import json
import operator
import os
import shutil
import subprocess
import threading
import webbrowser
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
    "Haz cambios pequeños y precisos."
)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
RUTA = os.path.abspath(__file__)


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


def responder(sid: str, texto: str):
    """Procesa un mensaje y devuelve (respuesta, herramientas_usadas)."""
    with LOCK:
        messages = SESIONES.setdefault(sid, [{"role": "system", "content": SYSTEM_PROMPT}])
        messages.append({"role": "user", "content": texto})
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
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; display: flex; flex-direction: row;
    background: #2B1A12; color: #F3E6D4;
    font-family: "Segoe UI", system-ui, sans-serif;
  }
  #fondo {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 15;
  }
  #sidebar {
    width: 260px; flex-shrink: 0; display: flex; flex-direction: column;
    background: #3D2817; border-right: 1px solid #6B4423; z-index: 20;
  }
  .side-marca {
    padding: 16px 16px 8px; font-size: 13px; color: #C4A484; letter-spacing: .4px;
  }
  #nueva {
    margin: 8px 12px 12px; padding: 10px 12px; border: 1px solid #6B4423;
    border-radius: 10px; background: #8B5A2B; color: #FDF6EC;
    font-size: 14px; font-weight: 600; cursor: pointer; text-align: left;
  }
  #nueva:hover { background: #A0522D; }
  #lista { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
  .conv {
    padding: 10px 12px; border-radius: 8px; cursor: pointer; font-size: 14px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #E8D5B7;
  }
  .conv:hover { background: #4A3224; }
  .conv.activa { background: #4A3224; color: #FDF6EC; }
  .vacio { padding: 10px 12px; color: #C4A484; font-size: 13px; font-style: italic; }
  #app { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 20px; background: #3D2817; border-bottom: 1px solid #6B4423;
  }
  header h1 { font-size: 18px; margin: 0; color: #E8D5B7; letter-spacing: 0.4px; }
  header button {
    background: #8B5A2B; color: #F5E6D3; border: 0; border-radius: 8px;
    padding: 8px 14px; cursor: pointer; font-size: 14px;
  }
  header button:hover { background: #A0522D; }
  #menu { display: none; margin-right: 10px; font-size: 18px; }
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
  .user { align-self: flex-end; background: #8B5A2B; color: #FDF6EC; border-bottom-right-radius: 4px; }
  .bot  { align-self: flex-start; background: #4A3224; color: #F3E6D4; border-bottom-left-radius: 4px; }
  .tool { align-self: flex-start; font-family: Consolas, monospace; font-size: 13px;
          color: #D4A574; padding: 2px 6px; }
  .error { align-self: flex-start; background: #5C2A1E; color: #E8C4B8; }
  .sys  { align-self: center; color: #C4A484; font-style: italic; font-size: 14px; }
  form {
    display: flex; gap: 10px; padding: 14px 20px 20px;
    max-width: 900px; width: 100%; margin: 0 auto;
  }
  input {
    flex: 1; padding: 14px 16px; border-radius: 12px; border: 1px solid #6B4423;
    background: #3D2817; color: #F5E6D3; font-size: 16px; outline: none;
  }
  input:focus { border-color: #C4A484; }
  form button {
    padding: 0 24px; border: 0; border-radius: 12px; background: #8B5A2B;
    color: #FDF6EC; font-size: 16px; font-weight: 600; cursor: pointer;
  }
  form button:hover { background: #A0522D; }
  form button:disabled, input:disabled { opacity: .5; cursor: not-allowed; }
  .loading {
    align-self: flex-start; max-width: 80%;
    background: #4A3224; color: #E8D5B7;
    border-bottom-left-radius: 4px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .loading-row { display: flex; align-items: center; gap: 10px; }
  .spinner {
    width: 16px; height: 16px; flex-shrink: 0;
    border: 2px solid #6B4423; border-top-color: #E8D5B7;
    border-radius: 50%; animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-bar {
    height: 4px; background: #3D2817; border-radius: 4px; overflow: hidden;
  }
  .loading-bar > span {
    display: block; height: 100%; width: 40%;
    background: #C4A484; border-radius: 4px;
    animation: slide 1.2s ease-in-out infinite;
  }
  @keyframes slide {
    0% { transform: translateX(-120%); }
    100% { transform: translateX(350%); }
  }
  .loading-meta { font-size: 12px; color: #C4A484; }
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
        <h1>🌾 agroNexus</h1>
      </div>
    </header>

    <div id="chat">
      <div class="msg sys">agroNexus listo. Escribe tu mensaje abajo.</div>
    </div>

    <form id="form">
      <input id="texto" autocomplete="off" placeholder="Escribe un mensaje..." autofocus>
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

  function uid() {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function cargar() {
    try { conversaciones = JSON.parse(localStorage.getItem(KEY) || "[]"); }
    catch (e) { conversaciones = []; }
    if (!Array.isArray(conversaciones)) conversaciones = [];
  }

  function guardar() {
    localStorage.setItem(KEY, JSON.stringify(conversaciones));
  }

  function convActual() {
    return conversaciones.find(c => c.id === actual);
  }

  function cerrarMenu() {
    sidebar.classList.remove("abierta");
    fondo.classList.remove("visible");
  }

  function pintarLista() {
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
      d.textContent = c.titulo || "Nueva conversación";
      d.addEventListener("click", () => abrir(c.id));
      lista.appendChild(d);
    });
  }

  function pintarChat(mensajes) {
    chat.innerHTML = "";
    const items = (mensajes && mensajes.length)
      ? mensajes
      : [{clase: "sys", texto: "agroNexus listo. Escribe tu mensaje abajo."}];
    items.forEach(m => {
      const div = document.createElement("div");
      div.className = "msg " + m.clase;
      div.textContent = m.texto;
      chat.appendChild(div);
    });
    chat.scrollTop = chat.scrollHeight;
  }

  function nueva() {
    const c = { id: uid(), titulo: "Nueva conversación", mensajes: [] };
    conversaciones.unshift(c);
    actual = c.id;
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

  function agregar(clase, texto) {
    const div = document.createElement("div");
    div.className = "msg " + clase;
    div.textContent = texto;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    const c = convActual();
    if (c) {
      c.mensajes.push({clase, texto});
      if (clase === "user" && c.titulo === "Nueva conversación") {
        c.titulo = texto.length > 40 ? texto.slice(0, 40) + "…" : texto;
        pintarLista();
      }
      guardar();
    }
    return div;
  }

  function bloquear(estado) {
    input.disabled = estado;
    boton.disabled = estado;
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

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const texto = input.value.trim();
    if (!texto) return;
    if (!actual) nueva();
    input.value = "";
    agregar("user", texto);
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
      const data = await post("/chat", { session: actual, message: texto });
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
        else:
            self._enviar(404, "No encontrado", "text/plain")

    def do_POST(self):
        try:
            largo = int(self.headers.get("Content-Length", 0))
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
            if not texto:
                self._enviar(400, json.dumps({"error": "Mensaje vacío"}))
                return
            try:
                respuesta, herramientas = responder(sid, texto)
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