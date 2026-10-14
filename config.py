"""Configuración de agroNexus."""

import os

from openai import OpenAI


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
HOST = "127.0.0.1"  # solo accesible desde tu computadora
PORT = 8000

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
    "El código está en varios módulos. Usa leer_mi_codigo sin archivo para listarlos "
    "y con archivo='nombre.py' para leer uno. "
    "Puedes proponer cambios con editar_mi_codigo (incluye descripcion y archivo); "
    "el usuario siempre debe aprobar cada cambio. "
    "Usa crear_archivo para archivos nuevos en tu carpeta (ruta relativa; "
    "el usuario debe aprobar). "
    "Haz cambios pequeños y precisos. "
    "El usuario puede adjuntar imágenes, documentos, audio y video: "
    "analiza el contenido extraído o las imágenes que lleguen con el mensaje."
)

CARPETA = os.path.dirname(os.path.abspath(__file__))
RUTA_PRINCIPAL = os.path.join(CARPETA, "agente_grok.py")

MODULOS = (
    "agente_grok.py",
    "config.py",
    "herramientas.py",
    "adjuntos.py",
    "agente.py",
    "ui.py",
    "servidor.py",
)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
