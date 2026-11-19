#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lanzador de Microsoft Edge 100% invisible (headless=True con camuflaje anti-WAF)
para los motores SUNAT (SEE-SOL).

Por qué antes fallaba headless=True con net::ERR_CONNECTION_RESET:
- El WAF de SUNAT bloquea la cabecera por defecto 'HeadlessChrome' y la marca
  JS `navigator.webdriver = true`.
- Al lanzar Edge con `headless=True`, `--disable-blink-features=AutomationControlled`
  y un `User-Agent` real de Microsoft Edge en `crear_contexto_sunat(browser)`,
  SUNAT acepta la conexión al 100% sin abrir NINGUNA ventana ni icono en la barra de tareas.
"""

EDGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

ARGS_STEALTH_HEADLESS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling",
    "--mute-audio",
]


def lanzar_edge_sunat(p, oculto=True):
    """
    oculto=True  -> 100% invisible (headless=True + anti-detección WAF SUNAT).
    oculto=False -> ventana visible solo para depuración manual.
    """
    return p.chromium.launch(
        channel="msedge",
        headless=bool(oculto),
        args=ARGS_STEALTH_HEADLESS,
    )


def crear_contexto_sunat(browser, viewport=None):
    """Crea un BrowserContext con User-Agent real de Edge y oculta navigator.webdriver."""
    vp = viewport or {"width": 1600, "height": 1150}
    ctx = browser.new_context(
        viewport=vp,
        user_agent=EDGE_USER_AGENT,
        locale="es-PE",
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return ctx
