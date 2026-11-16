#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lenguaje real de recepción — Hotel El Óvalo Mariátegui
-------------------------------------------------------
Reglas léxicas construidas a partir de los AUDIOS DE REFERENCIA del 04–05/11/2026
(ver `data/audios_referencia/referencias.json`). Se usan cuando la transcripción viene
de Whisper local (sin Gemini) y como pista adicional para el prompt de Gemini.

- `normalizar_numeros_es()`  : "trescientos dos" -> "302", "setenta y cinco soles" -> "75 soles",
                                "78 37 64 19" -> "78376419" (DNI dictado en grupos).
- `clasificar_por_lexico()`  : devuelve tipo de interacción + banderas (pago pendiente, pago mixto,
                                relevo de tienda, equipaje, continuar, liquidación, audio de fondo).
- Las frases pueden ampliarse sin tocar código en `C:\\Hostal_Sistema\\lexico_recepcion.json`.

Este módulo SOLO lee; no escribe en la BD ni en Google Sheets.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

LEXICO_JSON_PATHS = [
    Path(r"C:\Hostal_Sistema\lexico_recepcion.json"),
    Path(__file__).resolve().parent / "data" / "lexico_recepcion.json",
]

# ---------------------------------------------------------------------------
# 1. Frases reales por intención (fuente: audios de referencia + vocabulario del dueño)
# ---------------------------------------------------------------------------
LEXICO_BASE: Dict[str, List[str]] = {
    # Check-in / cobro real en mostrador
    "checkin": [
        "su dni", "tu dni", "me dicta su dni", "dni me puede dictar", "me puede dictar", "tu numero de dni",
        "su dni por favor", "el dni por favor", "su documento", "es para dos", "como te llamas",
        "de que numero es", "que numero es", "cual era su habitacion", "que habitacion es",
        "tu nombrecito", "de donde viene", "a donde viene", "es de aqui de moquegua", "tengo disponible",
        "le doy la", "te di la", "te di", "cochera tiene", "con cochera", "firme", "aceptado",
        "va a pagar", "vas a pagar", "efectivo o yape", "efectivo o en yape", "aca esta el qr", "aca el qr",
    ],
    "medio_pago": [
        "efectivo o yape", "efectivo o en yape", "en yape", "por yape", "en efectivo", "aca esta el qr",
        "aca el qr", "el qr", "maelen", "plin", "visa", "tarjeta", "efectivo",
    ],
    "pago_pendiente": [
        "mas tarde le doy", "mas tarde le pago", "mas tarde te doy", "le pago a la vuelta",
        "te pago a la vuelta", "despues le pago", "ahorita vuelvo y pago", "pago en la noche",
        "dejo a cuenta", "a cuenta", "va a pagar manana", "paga manana",
    ],
    # Saldos / deudas de huéspedes y vueltos pendientes (fuente: 20:35:47 y 21:02:31)
    "saldo_pendiente": [
        "tiene deuda", "solo ha pagado", "debe", "deben", "debemos vuelto", "vuelto pendiente",
        "cargada a", "cargado a", "cargar a la cuenta", "le agrego",
    ],
    "pago_mixto": ["en los dos", "una parte en yape", "parte en efectivo", "en yape y efectivo", "en yape y en efectivo"],
    # Tarifa por horas (fuente: 20:38:52 "¿a cuánto está el cuarto? 55... quiero descansar... 40")
    "por_horas": ["por horas", "unas horas", "un rato", "quiero descansar", "solo descansar", "hasta las", "medio dia"],
    # Cotización sin cobro
    "cotizacion": [
        "cuanto esta", "cuanto cuesta", "cuanto sale", "a cuanto esta", "tienes habitacion", "tiene habitacion",
        "habitacion libre", "tiene cuartos", "tienes cuartos", "tienes con tarjetero", "tarjetero", "pos",
        "ha subido", "no estaba", "tengo fotos", "de uno o de dos", "ventana a la calle", "aca tengo los precios",
        "para cuantas personas", "tiene cochera", "si me da boleta", "boleta o factura",
    ],
    # Renovación / continuar
    "continuar": [
        "voy a continuar", "va a continuar", "me quedo esta noche", "una noche mas", "me quedo hasta",
        "queda hasta", "renovar", "otra noche",
    ],
    # Liquidación de cuenta
    "liquidacion": ["sacar mi cuenta", "sacar la cuenta", "cuanto debo", "cuanto le debo", "mi cuenta", "la cuenta"],
    # Encargo de equipaje / salida temporal
    "equipaje": [
        "te dejo mi mochila", "te dejo mi maleta", "dejo mi mochila", "dejo mi maleta", "guardar equipaje",
        "regreso mas tarde", "voy a regresar", "vuelvo dentro de", "ahorita mismo vuelvo", "hasta mas tarde",
    ],
    # Pases / visitas (206 y 208)
    "pase": ["pase", "pases", "visita", "pagar mi pase", "tiene pase", "su dni de la visita"],
    # Salidas de caja / gastos
    "gasto_caja": [
        "recojo", "recojo de efectivo", "sacate", "de tu pago", "te autopagas", "autopagas",
        "turno noche", "adelanto", "pago a", "vuelto a cliente",
    ],
    # Cambio de billetes por sencillo: NO sale dinero (fuente: 21:13:16)
    "cambio_sencillo": ["para hacer el cambio", "sencillito", "en sencillo", "cambio de billete", "me cambias"],
    # Deuda personal entre empleadas: NO es ingreso del hotel (fuente: 20:09:25 / 20:10:52)
    "deuda_interna": ["ya le he dado a la eleiza", "le he dado a la eleiza", "te debo", "le debo a", "me debes", "mi plata"],
    # Venta de vitrina en mostrador (fuente: 20:19:29 "me da una agüita sin gas")
    "venta_tienda": [
        "me da una", "me das una", "aguita", "agua sin gas", "agua con gas", "una gaseosa", "un gatorade",
        "dos gatorade", "2 gatorade", "gay trade", "2 cocas", "dos cocas", "una coca", "1 coca", "2 aguas", "dos aguas",
        "una galleta", "un chicle", "un preservativo", "un shampoo", "un jabon", "prestobarba",
    ],
    # Coordinación administrativa / revisión de cuadre entre administrador y recepción (NO es cobro nuevo a cliente)
    "coordinacion_admin": [
        "todo el mes", "por todo el mes", "factura 15 dias", "15 dias y me van a abonar", "octubre ya me abonaron",
        "me van a abonar", "ya se ha separado por todo el mes", "vuelto mal dado", "vuelto malo", "faltaba 10 soles",
        "auditoria de audio", "auditoria, corrupcion", "pago inicial, juan carlos", "a la 1 y media te pago",
    ],
    # Relevo de inventario de vitrina
    "relevo_tienda": ["falta la tienda", "la tienda", "inventario", "he contado", "contado", "vitrina"],
    "faltante_sobrante": ["falta", "faltan", "sobra", "sobran", "sobraba", "faltaba"],
    # Mantenimiento / equipos
    "mantenimiento": [
        "tomacorriente", "enchufe", "mueble", "arreglar", "reparar", "malogrado", "mantenimiento",
        "no llega", "sin senal", "no hay senal", "wifi", "wi-fi", "internet", "foco", "parpadea", "gotea", "fuga de agua",
        "no prende", "no funciona", "cortocircuito", "tecla", "edredon", "mancha",
    ],
    # Audio de fondo (TV, live de ventas, TikTok, novelas/dramas): NO es operación del hotel
    "audio_fondo": [
        "los envios son diarios", "shalom", "se estan conectando", "aprovechen", "chicas,", "link en",
        "dale like", "suscribete", "postulante", "el material es", "tela", "en vivo",
        "senor chen", "disculparte", "heladito", "leche quemada", "truchas", "jefe de",
        "empezamos en la ducha", "el truco es si realmente", "patatuez", "hayan de gustos",
        "senor montalvo", "montalvo", "ciudad fortuna", "grupo suarez", "grupo shen", "señorita shen",
        "acta de matrimonio", "prueba de paternidad", "hombre mas rico", "tianxion", "dr. lee",
        "creator search insights", "asistente creativo de tiktok", "tortillitas de huevo",
        "salvaste la vida de mia", "sarbaste la vida de mia", "tratar a hugo", "faja aguada",
        "imprenta union", "padrinas de promocion", "chicharrones mas",
    ],
    # Alerta de desvío Yape
    "alerta_yape": ["yape de aqui esta mal", "el qr esta mal", "qr de aqui esta mal", "no pasa el yape", "yapeame a este"],
    # Alerta de registro con datos falsos (fuente: 20:53:49 "podrías ponerle un nombre fake...")
    "alerta_registro": ["nombre fake", "un nombre fake", "dni fake", "nombre falso", "quien va a saber", "ponle cualquier nombre", "inventale"],
}

# Mínimos por horas indicados por el dueño (S/)
MINIMO_POR_HORAS = {"simple": 40.0, "matrimonial": 55.0, "compartido": 30.0}

PRODUCTOS_TIENDA = [
    "galleta", "general", "sublime", "chicle", "coca cola", "coca-cola", "coca", "gaseosa", "kr", "agua",
    "gatorade", "lays", "preservativo", "shampoo", "h&s", "jabon", "cepillo", "prestobarba", "toalla", "leche",
]


def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def cargar_lexico() -> Dict[str, List[str]]:
    """Fusiona el léxico base con las frases nuevas que el hostal agregue en lexico_recepcion.json."""
    lex = {k: list(v) for k, v in LEXICO_BASE.items()}
    for p in LEXICO_JSON_PATHS:
        if p.exists():
            try:
                extra = json.loads(p.read_text(encoding="utf-8"))
                for k, frases in (extra.get("frases") or {}).items():
                    lex.setdefault(k, [])
                    for f in frases:
                        f_n = _sin_tildes(str(f).lower().strip())
                        if f_n and f_n not in lex[k]:
                            lex[k].append(f_n)
            except Exception:
                pass
            break
    return lex


# ---------------------------------------------------------------------------
# 2. Números en palabras (español peruano hablado) -> dígitos
# ---------------------------------------------------------------------------
_UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_CENTENAS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300,
    "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600,
    "setecientos": 700, "ochocientos": 800, "novecientos": 900,
}
_PALABRAS_NUM = set(_UNIDADES) | set(_DECENAS) | set(_CENTENAS) | {"mil"}


def _convertir_grupo(tokens: List[str]) -> int:
    total, actual = 0, 0
    for tk in tokens:
        if tk in _CENTENAS:
            actual += _CENTENAS[tk]
        elif tk in _DECENAS:
            actual += _DECENAS[tk]
        elif tk in _UNIDADES:
            actual += _UNIDADES[tk]
        elif tk == "mil":
            total += (actual or 1) * 1000
            actual = 0
    return total + actual


def _rango(tk: str) -> int:
    """0 = unidad (0-29), 1 = decena (30-90), 2 = centena, 3 = mil."""
    if tk in _UNIDADES:
        return 0
    if tk in _DECENAS:
        return 1
    if tk in _CENTENAS:
        return 2
    return 3


def _puede_continuar(grupo: List[str], tk: str, hubo_y: bool) -> bool:
    """
    Reglas del español hablado para saber si `tk` sigue formando el MISMO número:
    - "trescientos dos" (centena + unidad) -> 302      - "setenta y cinco" (decena + y + unidad) -> 75
    - "siete cuatro uno uno" (unidad + unidad) -> dígitos sueltos de un DNI dictado
    """
    if not grupo:
        return True
    ult = grupo[-1]
    r_ult, r_tk = _rango(ult), _rango(tk)
    if tk == "mil":
        return r_ult <= 2 and "mil" not in grupo
    if r_ult == 3:  # después de "mil"
        return True
    if r_ult == 2:  # centena -> decena o unidad
        return r_tk in (0, 1)
    if r_ult == 1:  # decena -> solo unidad con "y" ("setenta y cinco")
        return r_tk == 0 and hubo_y and _UNIDADES[tk] <= 9
    return False  # unidad -> cualquier cosa = número nuevo


def _limpiar_eco_prompt_whisper(texto: str) -> str:
    """
    Elimina alucinaciones literales de Whisper cuando repite fragmentos del antiguo PROMPT_HOSTAL
    en clips de ruido de fondo o silencio.
    """
    if not texto:
        return ""
    patrones_eco = [
        r"¡?\bpase\s+de\s+(?:10|diez)\s+soles\b[,.!]?(?:\s*visita\b[,.!]?)?",
        r"\b(?:matrimonial\s+(?:75|setenta\s+y\s+cinco)(?:\s+soles)?[,\s]*)?(?:simple\s+(?:55|cincuenta\s+y\s+cinco)[,\s]*)?por\s+horas\s+(?:45|40|cuarenta)(?:\s+soles)?(?:[,\s]*visita)?(?:[,\s]*por\s+horas\s+(?:45|40))*[,.!]?",
        r"\b(?:ac[aá]\s+est[aá]\s+el\s+qr[,\s]*)?(?:voy\s+a\s+continuar[,\s]*)?(?:me\s+quedo\s+(?:1|una)\s+noche(?:\s+m[aá]s)?[,\s]*)?(?:quiero\s+sacar\s+mi\s+cuenta[,\s]*)?(?:te\s+dejo\s+mi\s+mochila[,\s]*)?(?:m[aá]s\s+tarde\s+le\s+doy[,\s]*)?recojo\s+de\s+efectivo\b[,.!]?",
        r"\b(?:sublime[,\s]+galleta[,\s]+)?(?:la\s+noche[,\s]+)?(?:ac[aá]\s+est[aá]\s+el\s+qr[,\s]+)?(?:me\s+quedo\s+(?:1|una)\s+noche[,\s]+)?falta[,\s]+sobra[,\s]+sublime[,\s]+galleta\b[,.!]?",
        r"(?:¿?\bva\s+a\s+pagar\s+en\s+efectivo\s+o\s+en\s+yape\?\s*){2,}",
        r"\bte\s+dejo\s+mi\s+55\s+soles[,\s]*simple\s+55[,\s]*por\s+horas\s+40\b[,.!]?",
        r"\bpatrimonial\s+75[,\s]*por\s+horas\s+45\b[,.!]?",
    ]
    out = texto
    for pat in patrones_eco:
        out = re.sub(pat, " ", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,.;:-")
    return out


def normalizar_numeros_es(texto: str) -> str:
    """Convierte números dichos en palabras a dígitos y une DNIs dictados en grupos o dígito a dígito."""
    if not texto:
        return ""
    # Correcciones fonéticas frecuentes de Whisper local y peruanismos en mostrador de recepción
    texto_pre = _limpiar_eco_prompt_whisper(texto)
    if not texto_pre:
        return ""
    reemplazos_peru = [
        (r"\b(?:se)?tenta\s+y\s+(?:cinco|5)\b", "setenta y cinco"),
        (r"\b(?:se)?senta\s+y\s+(?:cinco|5)\b", "sesenta y cinco"),
        (r"\b(?:cua)?renta\s+y\s+(?:cinco|5)\b", "cuarenta y cinco"),
        (r"\b(?:ochenta|ochen)\s+y\s+(?:cinco|5)\b", "ochenta y cinco"),
        (r"\b(?:noventa|noven)\s+y\s+(?:cinco|5)\b", "noventa y cinco"),
        (r"\bencer[ií]a\b", "sería"),
        (r"\bgay\s*trade\b", "Gatorade"),
        (r"\b(?:yapeo|yapeado|yapeaste|yapear|yapea|yapeame|yapéame)\b", "pago en Yape"),
        (r"\b(?:plineo|plineado|plinear|plinea)\b", "pago en Plin"),
        (r"\b(?:una\s+luca|1\s+luca)\b", "1 sol"),
        (r"\b(\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|veinte|treinta|cuarenta|cincuenta|cien)\s+lu(?:cas|quitas)\b", r"\1 soles"),
        (r"\b(?:una\s+china|50\s+centimos|50\s+centimetros)\b", "0.50 soles"),
        (r"\bsencillito\b", "sencillo"),
        (r"\b(?:maelen|milen|mailen)\s*(?:hogar)?\b", "Maelen Hogar"),
    ]
    for pat_p, rep_p in reemplazos_peru:
        texto_pre = re.sub(pat_p, rep_p, texto_pre, flags=re.IGNORECASE)
    # Whisper a veces transcribe "setenta y cinco" como "7.5" en contexto de tarifa de habitación
    if re.search(r"\b(matrimonial|contina|con\s+tina|simple|doble|habitaci[oó]n|cuarto)\b", texto_pre, flags=re.IGNORECASE):
        texto_pre = re.sub(r"(?<!\d)7[.,]5(?!\d)", "75", texto_pre)
    tokens = re.findall(r"\w+|[^\w\s]|\s+", texto_pre, flags=re.UNICODE)
    salida: List[str] = []
    grupo: List[str] = []
    intermedio: List[str] = []  # espacios / "y" entre palabras numéricas
    hubo_y = False

    def _cerrar_grupo():
        nonlocal grupo
        if grupo:
            salida.append(str(_convertir_grupo(grupo)))
            grupo = []

    for tk in tokens:
        tk_n = _sin_tildes(tk.lower())
        if tk_n in _PALABRAS_NUM:
            if grupo and _puede_continuar(grupo, tk_n, hubo_y):
                grupo.append(tk_n)
                intermedio = []
            else:
                _cerrar_grupo()
                if intermedio:
                    # separa números consecutivos con un espacio ("7 4 1 1") y conserva un "y" literal
                    salida.append(" y " if hubo_y else " ")
                intermedio = []
                grupo = [tk_n]
            hubo_y = False
        elif grupo and (tk.isspace() or tk_n == "y"):
            intermedio.append(tk)
            if tk_n == "y":
                hubo_y = True
        else:
            _cerrar_grupo()
            if intermedio:
                salida.extend(intermedio)
                intermedio = []
            hubo_y = False
            salida.append(tk)
    _cerrar_grupo()
    salida.extend(intermedio)
    out = "".join(salida)

    # DNI dictado en grupos: "78 37 64 19" / "78-37-64-19" / "7837 6419" -> "78376419"
    def _unir_dni(m: re.Match) -> str:
        digitos = re.sub(r"\D", "", m.group(0))
        if len(digitos) in (8, 9, 11) and not re.search(r"000$|^000|(\d)\1{3}", digitos):
            return digitos
        return m.group(0)

    out = re.sub(r"\b\d{1,4}(?:[\s.\-,]+\d{1,4}){1,7}\b", _unir_dni, out)
    return out


# ---------------------------------------------------------------------------
# 3. Clasificación por léxico
# ---------------------------------------------------------------------------
def _contiene(t: str, frases: List[str]) -> List[str]:
    return [f for f in frases if f and re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", t)]


def extraer_montos(texto_norm: str) -> List[float]:
    """Montos hablados en soles (requiere contexto monetario para no confundir con habitación/DNI/días/horas)."""
    t = _sin_tildes(texto_norm.lower())
    # Neutralizar expresiones de hora ("hasta las 6 y 50", "a la 1 y 21") y de duración ("15 dias", "2 horas")
    t_limpio = re.sub(
        r"\b(?:a\s+las|a\s+la|hasta\s+las|son\s+las|como\s+las)\s+\d{1,2}(?:\s+y\s+\d{1,2}|\s*:\s*\d{2})?\b",
        " ",
        t,
    )
    t_limpio = re.sub(
        r"\b\d{1,4}\s*(?:dias?|horas?|minutos?|mes(?:es)?|anos?|pisos?|personas?|camas?|noches?|cuartos?|habitaciones|veces|am|pm|centimos?|centimetros?)\b",
        " ",
        t_limpio,
    )
    # Neutralizar secuencias de dictado de DNI en grupos ("42, 38, 3, 4, 3")
    t_limpio = re.sub(r"\b\d{1,2}(?:\s*,\s*\d{1,2}){2,}\b", " ", t_limpio)
    montos: List[float] = []

    def _add(v: float):
        if 2 <= v <= 2000 and v not in montos:
            montos.append(v)

    for m in re.finditer(r"(?:s/\.?\s*)?(\d{1,4}(?:[.,]\d{1,2})?)\s*(soles|sol|so|lucas|en yape|en efectivo|por yape|nomas|no mas|seria|de tu pago)\b", t_limpio):
        try:
            _add(float(m.group(1).replace(",", ".")))
        except Exception:
            pass
    patrones_previos = [
        r"(?:esta|sale|seria|son|cobro|cuesta|total|poner|pon|sacate(?: ahorita)?|matrimonial|simple|doble|triple|cuarto|habitacion)[\s,:.?¿!¡\-–—]+(\d{2,4})(?![\d/])",
        r"(\d{2,3})\s+(?:esta|y\s+de\s+\d{2,3})\b",
        r"\b(\d{2,3})\s*,\s*¿?verdad\b",
    ]
    for pat in patrones_previos:
        for m in re.finditer(pat, t_limpio):
            v = float(m.group(1))
            # Un número con forma de habitación (101-409) o piso truncado ("habitación 300 y 207") sin "soles" se trata como habitación
            es_piso_hab = m.group(1) in ("100", "200", "300", "400") and re.search(r"\b(?:habitacion|cuarto)\b", m.group(0))
            if not re.fullmatch(r"[1-4]0[1-9]", m.group(1)) and not es_piso_hab:
                _add(v)
    return montos


def parsear_pago_mixto(texto_norm: str) -> List[Tuple[float, str]]:
    """'25 en yape y 50 en efectivo' -> [(25,'Yape'), (50,'Efectivo')]."""
    t = _sin_tildes(texto_norm.lower())
    partes = []
    for m in re.finditer(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:soles\s*)?(?:en|por)\s+(yape|plin|efectivo|visa|tarjeta)", t):
        medio = {"yape": "Yape", "plin": "Yape", "efectivo": "Efectivo", "visa": "VISA", "tarjeta": "VISA"}[m.group(2)]
        partes.append((float(m.group(1).replace(",", ".")), medio))
    return partes


def parsear_relevo_tienda(texto_norm: str) -> Dict[str, List[str]]:
    """Detecta productos mencionados con 'falta' / 'sobra' durante el relevo de vitrina."""
    t = _sin_tildes(texto_norm.lower())
    res: Dict[str, List[str]] = {"falta": [], "sobra": []}
    for oracion in re.split(r"[.;!?\n]|,\s*(?=y\s)", t):
        clave = "falta" if re.search(r"\bfalta(n|ba)?\b", oracion) else ("sobra" if re.search(r"\bsobra(n|ba)?\b", oracion) else "")
        if not clave:
            continue
        for p in PRODUCTOS_TIENDA:
            if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", oracion):
                etiqueta = p.replace("coca-cola", "coca cola")
                # evita duplicar "coca" si ya está "coca cola", o "general" si ya está "galleta general"
                if any(etiqueta != e and etiqueta in e for e in res[clave]):
                    continue
                res[clave] = [e for e in res[clave] if not (e != etiqueta and e in etiqueta)]
                if etiqueta == "general" and "galleta" in res[clave]:
                    res[clave][res[clave].index("galleta")] = "galleta general"
                    continue
                if etiqueta not in res[clave]:
                    res[clave].append(etiqueta)
    return res


def analizar_demanda_habitacion(texto: str) -> Dict[str, Any]:
    """
    Analiza qué tipo de habitación busca el cliente en mostrador, si el tipo solicitado
    estaba ocupado/agotado, qué característica era indispensable para el cliente y qué
    alternativa ofreció recepción.
    """
    texto_norm = normalizar_numeros_es(texto or "")
    t = _sin_tildes(texto_norm.lower())

    # Si es claramente audio de fondo (novelas, lives de ropa, TikTok), ignorar
    lex = cargar_lexico()
    if _contiene(t, lex.get("audio_fondo", [])):
        return {
            "es_consulta_habitacion": False,
            "tipo_solicitado": "",
            "tipos_lista": [],
            "requisito_indispensable": "",
            "estaba_ocupado": False,
            "hotel_lleno_total": False,
            "alternativa_ofrecida": "",
            "precio_solicitado_o_esperado": 0.0,
            "precio_ofrecido": 0.0,
        }

    tipos: List[str] = []
    if re.search(r"\b(matrimonial|matrimoniales|matrimoniana|patrimonial|para\s+pareja)\b", t):
        tipos.append("Matrimonial")
    if re.search(r"\b(simple|simples|personal|personales|una\s+persona|1\s+persona|mas\s+simple)\b", t):
        tipos.append("Simple / Personal")
    if re.search(r"\b(doble|dobles|2\s+camas|dos\s+camas)\b", t):
        tipos.append("Doble (2 camas)")
    if re.search(r"\b(familiar|familiares|triple|3\s+camas|tres\s+camas)\b", t):
        tipos.append("Familiar / Triple")
    if re.search(r"\b(con\s+tina|tina|jacuzzi|contina)\b", t):
        tipos.append("Con Tina")
    if re.search(r"\b(bano\s+privado|bania\s+privada|mania\s+privado|compania\s+privada|privado|privada)\b", t):
        tipos.append("Baño Privado")
    if re.search(r"\b(bano\s+compartido|compartido|compartida|conveyor\s+compartido|companio\s+compartido|mas\s+economico)\b", t):
        tipos.append("Baño Compartido (Económica)")
    if re.search(r"\b(cochera|estacionamiento|garaje|guardar\s+moto|guardar\s+carro)\b", t):
        tipos.append("Con Cochera")
    if re.search(r"\b(primer\s+piso|segundo\s+piso|1er\s+piso|2do\s+piso|piso\s+bajo|no\s+subir|viejito)\b", t):
        tipos.append("Piso Bajo (1er/2do Piso)")
    if re.search(r"\b(por\s+horas|unas\s+horas|3\s+horas|un\s+rato|solo\s+descansar|quiero\s+descansar)\b", t):
        tipos.append("Por Horas / Descanso")

    # Detectar si el tipo buscado estaba ocupado o agotado
    pat_ocupado = (
        r"\b(no\s+tengo\s+(?:dobles|doblez|simples|simple|matrimonial|familiar|familiares|cuarto|habitacion)|"
        r"esta\s+ocupad[oa]|estan\s+ocupad[oa]s|estan\s+todos\s+ocupados|todo\s+el\s+cuarto\s+piso\s+esta\s+ocupado|"
        r"no\s+me\s+queda|solo\s+me\s+queda|la\s+mas\s+que\s+me\s+queda|la\s+unica\s+que\s+queda|"
        r"no\s+hay\s+habitacion|papel\s+que\s+dice\s+no\s+hay\s+habitacion|esta\s+separad[oa]|"
        r"no\s+tendria\s+el\s+mas\s+simple|no\s+tenemos\s+simple|no\s+se\s+alquila\s+por\s+horas|"
        r"lo\s+mande\s+a\s+poquemari|hotel\s+mari|primavera)\b"
    )
    estaba_ocupado = bool(re.search(pat_ocupado, t))
    hotel_lleno_total = bool(
        re.search(
            r"\b(no\s+hay\s+habitacion|papel\s+que\s+dice\s+no\s+hay\s+habitacion|estan\s+todos\s+ocupados|"
            r"hacia\s+la\s+izquierda\s+esta\s+el\s+mari|al\s+lado\s+esta\s+el\s+primavera)\b",
            t,
        )
    )

    es_consulta = bool(
        tipos
        or estaba_ocupado
        or re.search(
            r"\b(tienes\s+habitacion|tiene\s+habitacion|cuanto\s+esta\s+la\s+habitacion|cuanto\s+esta\s+una\s+habitacion|"
            r"que\s+tipo\s+de\s+habitacion|cuartitos|cuartito|habitacion\s+por\s+favor|una\s+habitacion)\b",
            t,
        )
    )

    if not es_consulta:
        return {
            "es_consulta_habitacion": False,
            "tipo_solicitado": "",
            "tipos_lista": [],
            "requisito_indispensable": "",
            "estaba_ocupado": False,
            "hotel_lleno_total": False,
            "alternativa_ofrecida": "",
            "precio_solicitado_o_esperado": 0.0,
            "precio_ofrecido": 0.0,
        }

    # Determinar el tipo principal solicitado por el cliente (priorizando el primer tipo pedido antes del "me queda / tengo")
    tipo_principal = " + ".join(tipos[:2]) if tipos else "Habitación (General)"
    if hotel_lleno_total and not tipos:
        tipo_principal = "Cualquier Habitación (Hotel 100% Lleno)"

    # Determinar qué alternativa ofreció recepción cuando no había exactamente lo pedido o hubo negociación
    alternativas: List[str] = []
    if re.search(r"\b(colocar\s+en\s+1?\s*habitacion\s+doble|usar[ií]a\s+1\s+sola\s+cama|le\s+das\s+1?\s*doble\s+pero\s+que\s+te\s+use\s+1\s+sola|me\s+queda\s+1?\s*doble\s+pero\s+te\s+la\s+alquilaria\s+como\s+1)\b", t):
        alternativas.append("Doble usando 1 sola cama a tarifa Simple (S/ 55)")
        if "Simple / Personal" not in tipos:
            tipos.insert(0, "Simple / Personal")
            tipo_principal = "Simple / Personal (S/ 40–55)"
        estaba_ocupado = True
    if re.search(r"\b(matrimonial\s+con\s+(?:bano|bania|varios|conveyor)\s+compartid[oa]s?.*?(?:55|45)|55\s*,\s*esa\s+le\s+puedo\s+dar|matrimonial\s+con\s+bano\s+compartido\s+tengo\s+que\s+esta\s+a\s+55)\b", t):
        alternativas.append("Matrimonial con Baño Compartido (S/ 45–55)")
    if re.search(r"\b(como\s+tiene\s+tina.*?(?:85|80)|en\s+80\s*,\s*me\s+podrias\s+dejar\s+en\s+75)\b", t):
        alternativas.append("Matrimonial con Tina rebajada (S/ 85 → S/ 75–80)")
        estaba_ocupado = True
    if re.search(r"\b(algo\s+mas\s+economico\s+tengo\s+unas\s+familias|familiar.*?compartid)\b", t):
        alternativas.append("Familiar (Matrimonial + Simple) Baño Compartido (S/ 70)")
    if re.search(r"\b(no\s+tengo\s+simples\s*,\s*me\s+quedas?\s+son\s+simples\s+con\s+bano\s+privado\s*,\s*55)\b", t):
        alternativas.append("Simple con Baño Privado (S/ 55 en vez de S/ 40)")
        estaba_ocupado = True
    if re.search(r"\b(la\s+matrimonial\s+se\s+la\s+voy\s+a\s+dejar\s+en\s+70|1\s+matrimonial\s+y\s+1\s+doble|para\s+la\s+matrimonial\s+o\s+para\s+la\s+doble)\b", t):
        alternativas.append("Combinación 1 Doble + 1 Matrimonial con descuento (S/ 70)")
    if re.search(r"\b(la\s+unica\s+que\s+queda\s+vale.*?65|70\s+minimo)\b", t):
        alternativas.append("Descuento sobre tarifa (S/ 75 → S/ 65–70)")
    if re.search(r"\b(lo\s+mande\s+a\s+poquemari|hotel\s+mari|primavera)\b", t):
        alternativas.append("Derivado a Hotel Mari / Hotel Primavera (por falta de disponibilidad)")
        estaba_ocupado = True

    # Identificar cuál es el requisito indispensable para ese cliente
    if hotel_lleno_total:
        req_indisp = "Cualquier habitación disponible (Hotel al 100% de ocupación)"
    elif "Por Horas / Descanso" in tipos and re.search(r"\bno\s+se\s+alquila\s+por\s+horas\b", t):
        req_indisp = "Tarifa por Horas (Rechaza tarifa noche completa en fin de semana)"
    elif "Piso Bajo (1er/2do Piso)" in tipos or re.search(r"\b(viejito|cuarto\s+piso.*?no)\b", t):
        req_indisp = "Habitación en 1er/2do Piso (Adulto mayor / evita subir 4to piso)"
    elif re.search(r"\b(mas\s+simple|40\s+es\s+la\s+simple|no\s+tenia\s+mucha\s+plata|35|40)\b", t) and ("Simple / Personal" in tipos or "Baño Compartido (Económica)" in tipos):
        req_indisp = "Tarifa Económica Simple S/ 35–40 (Baño Compartido)"
    elif "Familiar / Triple" in tipos or "Doble (2 camas)" in tipos:
        if "Baño Privado" in tipos or re.search(r"\b(sin\s+bano|no\s+les\s+gusta\s+la\s+habitacion)\b", t):
            req_indisp = "Familiar / Doble (2–3 camas) con Baño Privado"
        else:
            req_indisp = "2 o 3 camas separadas (Doble o Familiar)"
    elif "Baño Privado" in tipos:
        req_indisp = f"{tipos[0]} con Baño Privado" if tipos else "Baño Privado"
    elif "Matrimonial" in tipos:
        req_indisp = "Habitación Matrimonial (S/ 55–75)"
    else:
        req_indisp = tipo_principal

    montos = extraer_montos(texto_norm)
    return {
        "es_consulta_habitacion": True,
        "tipo_solicitado": tipo_principal,
        "tipos_lista": tipos,
        "requisito_indispensable": req_indisp,
        "estaba_ocupado": estaba_ocupado,
        "hotel_lleno_total": hotel_lleno_total,
        "alternativa_ofrecida": " | ".join(alternativas) if alternativas else ("Misma habitación solicitada" if not estaba_ocupado else "Sin alternativa disponible"),
        "montos_hablados": montos,
    }


def clasificar_por_lexico(texto: str) -> Dict[str, Any]:
    """
    Clasifica una transcripción (Whisper o Gemini) usando el lenguaje real de recepción.
    Prioridad: audio de fondo > alerta yape > gasto > pase > relevo tienda > mantenimiento >
               liquidación > continuar > cobro real > cotización > charla interna.
    """
    lex = cargar_lexico()
    texto_norm = normalizar_numeros_es(texto or "")
    t = _sin_tildes(texto_norm.lower())
    hits = {k: _contiene(t, v) for k, v in lex.items()}
    montos = extraer_montos(texto_norm)
    mixto = parsear_pago_mixto(texto_norm)
    relevo = parsear_relevo_tienda(texto_norm)

    # En Perú los DNI de 8 dígitos inician en 0-8; los que inician en 9 son celulares dictados (ej. WhatsApp para enviar boleta)
    es_dictado_whatsapp = bool(re.search(r"\b(?:numero\s+de\s+whatsapp|tu\s+whatsapp|enviar\s+por\s+whatsapp)\b", t))
    m_dni = re.search(r"(?<!\d)([0-8]\d{7})(?!\d)", t) if not es_dictado_whatsapp else None
    dni = m_dni.group(1) if (m_dni and not re.search(r"000$|^000|(\d)\1{3}", m_dni.group(1))) else ""
    if es_dictado_whatsapp:
        montos = [m for m in montos if m < 90 or m > 99]
    m_hab = re.search(r"(?<!\d)([1-4]0[1-9])(?!\d)", re.sub(r"(?<!\d)\d{8,}(?!\d)", " ", t))
    hab = m_hab.group(1) if m_hab else ""
    if hab == "202" and hits.get("pase"):
        hab = "206"

    banderas: List[str] = []
    if hits.get("pago_pendiente"):
        banderas.append("PAGO_PENDIENTE")
    if (mixto and len({m for _, m in mixto}) > 1) or hits.get("pago_mixto"):
        banderas.append("PAGO_MIXTO")
    if hits.get("equipaje"):
        banderas.append("EQUIPAJE_O_SALIDA_TEMPORAL")
    if hits.get("saldo_pendiente"):
        banderas.append("SALDO_O_VUELTO_PENDIENTE")
    if hits.get("por_horas"):
        banderas.append("POR_HORAS")
    if hits.get("alerta_registro"):
        banderas.append("ALERTA_REGISTRO_DATOS_FALSOS")
    if hits.get("coordinacion_admin"):
        banderas.append("COORDINACION_ADMIN_CAJA")

    n_fondo = len(hits.get("audio_fondo") or [])
    checkin_fuerte = any(f in (hits.get("checkin") or []) for f in (
        "su dni", "tu dni", "me dicta su dni", "dni me puede dictar", "me puede dictar", "tu numero de dni",
        "su dni por favor", "el dni por favor", "va a pagar", "vas a pagar", "efectivo o yape", "efectivo o en yape",
        "aca esta el qr", "aca el qr",
    ))
    if n_fondo >= 1 and not dni and not checkin_fuerte and not hits.get("medio_pago"):
        tipo = "AUDIO_FONDO"
    elif hits.get("alerta_yape"):
        tipo = "COBRO_REAL"
        banderas.append("ALERTA_YAPE")
    elif hits.get("deuda_interna"):
        tipo = "DEUDA_INTERNA_PERSONAL"
    elif hits.get("coordinacion_admin") and not dni and not any(f in hits.get("checkin", []) for f in ["su dni", "tu dni", "me dicta su dni", "dni me puede dictar"]):
        tipo = "CHARLA_INTERNA"
    elif hits.get("cambio_sencillo") and not dni and not hab:
        tipo = "CHARLA_INTERNA"
        banderas.append("CAMBIO_SENCILLO")
    elif hits.get("gasto_caja") and not dni and not hits.get("checkin"):
        tipo = "GASTO_CAJA"
    elif hits.get("pase") and (
        hab in ("206", "208")
        or dni
        or re.search(r"\b(?:el\s+pase|un\s+pase|pases|pase\s+de\s+visita|pase\s+con)\b", t)
    ):
        tipo = "PASE_VISITA"
    elif hab in ("206", "208") and dni and not montos and not hits.get("medio_pago"):
        # DNI dictado para una habitación que trabaja con pases, sin cobro de habitación: probable visita
        tipo = "PASE_VISITA"
        banderas.append("PASE_PROBABLE_SIN_PALABRA_PASE")
    elif hits.get("faltante_sobrante") and (relevo["falta"] or relevo["sobra"]):
        tipo = "RELEVO_INVENTARIO_TIENDA"
    elif hits.get("venta_tienda") and not dni and not hab:
        tipo = "VENTA_TIENDA"
    elif hits.get("mantenimiento") and not montos and not dni:
        tipo = "MANTENIMIENTO_LIMPIEZA"
    elif hits.get("liquidacion"):
        tipo = "LIQUIDACION_CUENTA"
    elif hits.get("continuar"):
        tipo = "CONTINUAR_ESTANCIA"
    elif hits.get("saldo_pendiente") and not hits.get("cotizacion") and not dni:
        tipo = "LIQUIDACION_CUENTA"
    elif dni or hits.get("checkin") or (hits.get("medio_pago") and montos) or ("PAGO_PENDIENTE" in banderas and hab):
        tipo = "COBRO_REAL"
    elif hits.get("saldo_pendiente") and not hits.get("cotizacion"):
        tipo = "LIQUIDACION_CUENTA"
    elif hits.get("cotizacion") or (montos and not dni):
        tipo = "COTIZACION_CONSULTA"
    else:
        tipo = "CHARLA_INTERNA"

    # Monto: con medio de pago explícito, abono a cuenta o habitación, aceptar números de 2 dígitos o múltiplos de 50 (excluyendo 100/200/300/400)
    if not montos and tipo in ("COBRO_REAL", "GASTO_CAJA", "CONTINUAR_ESTANCIA") and (hits.get("medio_pago") or hits.get("pago_pendiente") or hab):
        for m in re.finditer(r"(?<![\d/])(\d{2,3})(?![\d/])", t):
            if m.group(1) != hab and not re.fullmatch(r"[1-4]0[0-9]", m.group(1)) and 10 <= float(m.group(1)) <= 500:
                montos.append(float(m.group(1)))
                break
    # Por horas: la tarifa rebajada suele decirse sola ("55... quiero descansar... 40")
    if "POR_HORAS" in banderas:
        for m in re.finditer(r"(?<![\d/:])(\d{2})(?![\d/:])", t):
            v = float(m.group(1))
            if 20 <= v <= 95 and v not in montos:
                montos.append(v)

    precio = 0.0
    if mixto and tipo == "COBRO_REAL":
        precio = round(sum(v for v, _ in mixto), 2)
    elif montos:
        if tipo == "COTIZACION_CONSULTA" and "POR_HORAS" in banderas:
            precio = min(montos)  # "55... quiero descansar... 40" -> lo acordado es el menor
        elif len(montos) >= 2 and montos[-1] >= 30 and len(re.findall(rf"(?<!\d){int(montos[-1])}(?!\d)", t)) >= 2:
            precio = montos[-1]  # tarifa confirmada/repetida al final tras comparar opciones ("con tina 80... 75 ¿verdad? sí, 75")
        elif tipo in ("COBRO_REAL", "COTIZACION_CONSULTA", "GASTO_CAJA", "CONTINUAR_ESTANCIA"):
            precio = max(montos)
        else:
            precio = montos[0]
    if tipo == "PASE_VISITA" and precio == 0.0:
        precio = 10.0
    if tipo == "AUDIO_FONDO" or "COORDINACION_ADMIN_CAJA" in banderas:
        precio = 0.0  # montos de una transmisión/TV o de revisión administrativa no son cobro nuevo en mostrador

    metodo = ""
    if mixto:
        metodo = " + ".join(f"{m} {v:g}" for v, m in mixto)
    elif re.search(r"\b(yape|plin|qr|maelen)\b", t):
        metodo = "Yape"
    elif re.search(r"\b(efectivo|vuelto|billete|sencillo)\b", t):
        metodo = "Efectivo"
    if "PAGO_PENDIENTE" in banderas:
        metodo = (metodo + " | " if metodo else "") + "Pendiente (paga después)"

    return {
        "texto_normalizado": texto_norm,
        "tipo_interaccion": tipo,
        "hab": hab,
        "dni": dni,
        "precio": precio,
        "montos": montos,
        "pago_mixto": mixto,
        "metodo": metodo,
        "banderas": banderas,
        "relevo_tienda": relevo,
        "frases_detectadas": {k: v for k, v in hits.items() if v},
    }


if __name__ == "__main__":
    pruebas = [
        "¿Habitación? La trescientos dos tengo disponible, setenta y cinco sería. ¿Su DNI me puede dictar? 78 37 64 19.",
        "75, ¿no? 25 en Yape y 50 en efectivo.",
        "¿Tienes con tarjetero? No. ¿Cuánto está la habitación? Matrimonial, setenta y cinco.",
        "Sácate ahorita 55 de tu pago, pago Rous turno noche.",
        "Falta la tienda. Sobra un Sublime y una Coca-Cola. Falta galleta General.",
        "¿206? ¿Su DNI? 45582328",
        "Más tarde le doy, voy a ir al desfile. 208 te di, ¿no?",
        "No llega nada de señal wifi a la 102, al fondo.",
        "Los envíos son diarios chicas, por Shalom, aprovechen.",
    ]
    for p in pruebas:
        r = clasificar_por_lexico(p)
        print(f"{r['tipo_interaccion']:<26} hab={r['hab'] or '-':<4} dni={r['dni'] or '-':<9} S/={r['precio']:<6} "
              f"met={r['metodo'] or '-'} band={r['banderas']} relevo={r['relevo_tienda'] if r['tipo_interaccion']=='RELEVO_INVENTARIO_TIENDA' else ''}")
