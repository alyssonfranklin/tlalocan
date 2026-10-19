#!/usr/bin/env python3
"""Tlalocan · alerta de fuego para Ciénega de González y sus senderos.

Busca detecciones de NASA FIRMS (VIIRS y MODIS, casi tiempo real), mide la
distancia a los puntos y senderos configurados y avisa por Telegram.

Solo usa la biblioteca estándar de Python (3.11 o más reciente).

Uso:
  python3 alerta.py                      # consulta FIRMS y avisa
  python3 alerta.py --prueba             # consulta FIRMS pero solo imprime
  python3 alerta.py --archivo x.csv --prueba   # usa un CSV local (sin FIRMS)
"""
import argparse, csv, datetime as dt, io, json, math, os, sys, tomllib, urllib.parse, urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RADIO_TIERRA = 6371.0


# ---------- utilidades ----------
def leer_env(ruta):
    if ruta.exists():
        for linea in ruta.read_text().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def km(a, b):
    """Distancia en km entre (lat, lon) a y b."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * RADIO_TIERRA * math.asin(math.sqrt(h))


def km_a_tramo(p, a, b):
    """Distancia en km del punto p al segmento a-b (aproximación plana, válida a escala local)."""
    kx = 111.32 * math.cos(math.radians(p[0]))
    ky = 110.95
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


def rumbo(a, b):
    """Rumbo en grados de a hacia b (0 = norte, 90 = este)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


PUNTOS_CARDINALES = ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]


def cardinal(grados):
    return PUNTOS_CARDINALES[round(grados / 45) % 8]


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "tlalocan/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


# ---------- datos ----------
def cargar_senderos(rutas):
    senderos = []
    for ruta in rutas:
        gj = json.loads((AQUI / ruta).read_text())
        for f in gj.get("features", []):
            g = f.get("geometry") or {}
            nombre = (f.get("properties") or {}).get("name") or "Sendero sin nombre"
            if g.get("type") == "LineString":
                partes = [g["coordinates"]]
            elif g.get("type") == "MultiLineString":
                partes = g["coordinates"]
            else:
                continue
            senderos.append({"nombre": nombre, "partes": [[(c[1], c[0]) for c in parte] for parte in partes]})
    return senderos


def detecciones_firms(cfg, clave):
    z = cfg["zona"]
    margen = cfg["alerta"]["margen_km"] / 111.0
    area = f'{z["oeste"]-margen:.4f},{z["sur"]-margen:.4f},{z["este"]+margen:.4f},{z["norte"]+margen:.4f}'
    filas = []
    for fuente in cfg["firms"]["fuentes"]:
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{clave}/{fuente}/{area}/{cfg['firms']['dias']}"
        try:
            texto = get(url)
        except Exception as e:  # una fuente caída no detiene a las demás
            print(f"Aviso: no se pudo leer {fuente}: {e}", file=sys.stderr)
            continue
        if not texto.lstrip().startswith("latitude"):
            print(f"Aviso: respuesta inesperada de {fuente}: {texto[:120]!r}", file=sys.stderr)
            continue
        for fila in csv.DictReader(io.StringIO(texto)):
            fila["_fuente"] = fuente
            filas.append(fila)
    return filas


def detecciones_csv(ruta):
    with open(ruta, newline="") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        fila["_fuente"] = Path(ruta).name
    return filas


def viento(lat, lon):
    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,relative_humidity_2m",
        "wind_speed_unit": "kmh", "timezone": "America/Monterrey"}))
    try:
        c = json.loads(get(url, timeout=20))["current"]
        return {"vel": c["wind_speed_10m"], "dir": c["wind_direction_10m"], "rafaga": c["wind_gusts_10m"], "hr": c["relative_humidity_2m"]}
    except Exception:
        return None


# ---------- lógica de alerta ----------
def confianza_baja(fila):
    c = (fila.get("confidence") or "").strip().lower()
    if c in ("l", "low"):
        return True
    return c.isdigit() and int(c) < 30  # MODIS usa 0-100


def clave_deteccion(fila):
    return f'{fila["_fuente"]}|{float(fila["latitude"]):.4f}|{float(fila["longitude"]):.4f}|{fila["acq_date"]}|{fila["acq_time"]}'


def hora_local(fila, zona_horaria):
    t = fila["acq_time"].zfill(4)
    utc = dt.datetime.strptime(f'{fila["acq_date"]} {t}', "%Y-%m-%d %H%M").replace(tzinfo=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo(zona_horaria))
    except Exception:
        return utc


def evaluar(fila, cfg, senderos):
    p = (float(fila["latitude"]), float(fila["longitude"]))
    a = cfg["alerta"]
    motivos = []
    for punto in cfg["puntos"]:
        d = km(p, (punto["lat"], punto["lon"]))
        if d <= punto["radio_km"]:
            motivos.append({"tipo": "punto", "nombre": punto["nombre"], "km": d, "lugar": (punto["lat"], punto["lon"])})
    mejor = None
    for s in senderos:
        dmin = min(km_a_tramo(p, parte[i - 1], parte[i]) for parte in s["partes"] for i in range(1, len(parte)))
        if dmin <= a["sendero_cerca_km"] and (mejor is None or dmin < mejor["km"]):
            mejor = {"tipo": "sendero", "nombre": s["nombre"], "km": dmin}
    if mejor:
        mejor["nivel"] = "en" if mejor["km"] <= a["sendero_fuego_km"] else "cerca"
        motivos.append(mejor)
    return p, motivos


def armar_mensaje(grupo, cfg):
    """Un mensaje por pasada: resume todas las detecciones nuevas relevantes."""
    zona_horaria = cfg["zona"]["zona_horaria"]
    lineas = [f'🔥 Tlalocan · Fuego detectado por satélite cerca de {cfg["zona"]["nombre"]}', ""]
    en_sendero = sorted({m["nombre"] for _, _, ms in grupo for m in ms if m["tipo"] == "sendero" and m["nivel"] == "en"})
    cerca_sendero = sorted({m["nombre"] for _, _, ms in grupo for m in ms if m["tipo"] == "sendero" and m["nivel"] == "cerca"} - set(en_sendero))
    puntos = {}
    for _, _, ms in grupo:
        for m in ms:
            if m["tipo"] == "punto":
                puntos[m["nombre"]] = min(puntos.get(m["nombre"], 99), m["km"])
    for nombre, d in sorted(puntos.items(), key=lambda x: x[1]):
        lineas.append(f"• A {d:.1f} km de {nombre}")
    if en_sendero:
        lineas.append(f'• Sobre o a menos de {int(cfg["alerta"]["sendero_fuego_km"]*1000)} m de: ' + ", ".join(en_sendero))
    if cerca_sendero:
        lineas.append(f'• Cerca (menos de {cfg["alerta"]["sendero_cerca_km"]:g} km) de: ' + ", ".join(cerca_sendero))

    # detección más cercana al primer punto como referencia
    ref = cfg["puntos"][0]
    fila, p, _ = min(grupo, key=lambda g: km(g[1], (ref["lat"], ref["lon"])))
    hl = hora_local(fila, zona_horaria)
    lineas += ["", f'{len(grupo)} detección(es). La más cercana a {ref["nombre"]}:',
               f'{p[0]:.4f}, {p[1]:.4f} · {hl:%d/%m %H:%M} h · {fila.get("instrument", "")} {fila.get("satellite", "")} · FRP {fila.get("frp", "?")} MW',
               f'Mapa: https://www.openstreetmap.org/?mlat={p[0]:.5f}&mlon={p[1]:.5f}#map=14/{p[0]:.5f}/{p[1]:.5f}']
    v = viento(*p) if cfg["alerta"].get("consultar_viento", True) else None
    if v:
        hacia = (v["dir"] + 180) % 360
        r = rumbo(p, (ref["lat"], ref["lon"]))
        dif = abs((hacia - r + 180) % 360 - 180)
        lineas.append(f'Viento ahora: {v["vel"]:.0f} km/h del {cardinal(v["dir"])}, ráfagas {v["rafaga"]:.0f} km/h, humedad {v["hr"]:.0f} %.')
        if dif <= 45 and v["vel"] >= 5:
            lineas.append(f'⚠️ El viento empuja el fuego hacia {ref["nombre"]}.')
    if cfg["zona"].get("url_herramienta"):
        lineas.append(f'Ver en el mapa: {cfg["zona"]["url_herramienta"]}')
    lineas += ["", "Dato satelital con 1 a 3 horas de retraso y posibles falsos positivos. No sustituye avisos oficiales. Emergencias: 911."]
    return "\n".join(lineas)


def enviar_telegram(texto):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto, "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=datos)
    with urllib.request.urlopen(req, timeout=30) as r:
        if json.loads(r.read()).get("ok") is not True:
            raise RuntimeError("Telegram no aceptó el mensaje")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--archivo", help="CSV de FIRMS local en vez de consultar la API")
    ap.add_argument("--prueba", action="store_true", help="imprime el mensaje en vez de enviarlo y no guarda estado")
    args = ap.parse_args()

    leer_env(AQUI / ".env")
    cfg = tomllib.loads((AQUI / args.config).read_text())
    senderos = cargar_senderos(cfg["senderos"]["archivos"])

    if args.archivo:
        filas = detecciones_csv(args.archivo)
    else:
        clave = os.environ.get("FIRMS_MAP_KEY")
        if not clave:
            raise SystemExit("Falta FIRMS_MAP_KEY en .env (pídela gratis en https://firms.modaps.eosdis.nasa.gov/api/map_key/)")
        filas = detecciones_firms(cfg, clave)

    ruta_estado = AQUI / cfg["alerta"]["archivo_estado"]
    estado = json.loads(ruta_estado.read_text()) if ruta_estado.exists() else {}
    limite = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()
    estado = {k: v for k, v in estado.items() if v >= limite}  # olvida lo viejo

    nuevas = []
    for fila in filas:
        if confianza_baja(fila):
            continue
        k = clave_deteccion(fila)
        if k in estado and not args.prueba:
            continue
        p, motivos = evaluar(fila, cfg, senderos)
        if motivos:
            nuevas.append((fila, p, motivos))
        estado[k] = dt.datetime.now(dt.timezone.utc).isoformat()

    if not nuevas:
        print(f"Sin detecciones nuevas cerca ({len(filas)} revisadas).")
    else:
        texto = armar_mensaje(nuevas, cfg)
        if args.prueba:
            print(texto)
        else:
            enviar_telegram(texto)
            print(f"Alerta enviada: {len(nuevas)} detección(es).")

    if not args.prueba:
        ruta_estado.write_text(json.dumps(estado, indent=0))


if __name__ == "__main__":
    main()
