"""Extracción de texto e imágenes de archivos adjuntos."""

import base64
import html
import io
import json
import os
import re
import shutil
import subprocess
import zipfile


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
