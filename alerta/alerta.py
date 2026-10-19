#!/usr/bin/env python3
"""Tlalocan · alerta de fuego por zona.

Recorre las zonas activas en ../zonas/<slug>/zona.toml, busca detecciones de
NASA FIRMS (VIIRS y MODIS, casi tiempo real), agrupa las cercanas en focos,
mide la distancia a lugares y senderos y avisa por Telegram con coordenadas.

Solo usa la biblioteca estándar de Python (3.11 o más reciente).

Uso:
  python3 alerta.py                                  # todas las zonas activas
  python3 alerta.py --zona caparao --prueba          # una zona, solo imprime
  python3 alerta.py --zona cienega-de-gonzalez --archivo ../zonas/cienega-de-gonzalez/ejemplo-2021-03-25.csv --prueba
"""
import argparse, csv, datetime as dt, io, json, math, os, sys, tomllib, urllib.parse, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

AQUI = Path(__file__).resolve().parent
ZONAS = AQUI.parent / "zonas"
FUENTES = ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT", "MODIS_NRT"]
RADIO_FOCO_KM = 1.0      # detecciones a menos de esto forman un mismo foco
MARGEN_KM = 10           # cuánto se amplía el área al consultar FIRMS
MAX_FOCOS = 5            # focos listados por mensaje (el resto se resume)


# ---------- utilidades ----------
def leer_env(ruta):
    if ruta.exists():
        for linea in ruta.read_text().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def km_a_tramo(p, a, b):
    kx, ky = 111.32 * math.cos(math.radians(p[0])), 110.95
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


def rumbo(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "tlalocan/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


class Textos:
    def __init__(self, idioma):
        self.t = json.loads((AQUI / "textos" / f"{idioma}.json").read_text())

    def __call__(self, clave, **kw):
        return self.t[clave].format(**kw)

    def num(self, x, dec=1):
        return f"{x:.{dec}f}".replace(".", self.t["decimal"])

    def cardinal(self, grados):
        return self.t["cardinales"][round(grados / 45) % 8]


# ---------- zona ----------
def cargar_zona(carpeta):
    z = tomllib.loads((carpeta / "zona.toml").read_text())
    z["carpeta"] = carpeta
    z["senderos_lista"] = []
    for nombre in z["senderos"]["archivos"]:
        gj = json.loads((carpeta / nombre).read_text())
        for f in gj.get("features", []):
            g = f.get("geometry") or {}
            partes = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
            if partes:
                z["senderos_lista"].append({"nombre": (f.get("properties") or {}).get("name") or "?",
                                            "partes": [[(c[1], c[0]) for c in parte] for parte in partes]})
    return z


def zonas_a_revisar(slugs):
    if slugs:
        return [cargar_zona(ZONAS / s) for s in slugs]
    return [z for z in (cargar_zona(c) for c in sorted(ZONAS.iterdir()) if (c / "zona.toml").exists()) if z.get("activa")]


# ---------- detecciones ----------
def detecciones_firms(z, clave):
    m = MARGEN_KM / 111.0
    area = f'{z["oeste"]-m:.4f},{z["sur"]-m:.4f},{z["este"]+m:.4f},{z["norte"]+m:.4f}'
    filas = []
    for fuente in FUENTES:
        try:
            texto = get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{clave}/{fuente}/{area}/1")
        except Exception as e:
            print(f"[{z['slug']}] aviso: no se pudo leer {fuente}: {e}", file=sys.stderr)
            continue
        if not texto.lstrip().startswith("latitude"):
            print(f"[{z['slug']}] aviso: respuesta inesperada de {fuente}: {texto[:120]!r}", file=sys.stderr)
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


def confianza_baja(fila):
    c = (fila.get("confidence") or "").strip().lower()
    return c in ("l", "low") or (c.isdigit() and int(c) < 30)


def clave_deteccion(fila):
    return f'{fila["_fuente"]}|{float(fila["latitude"]):.4f}|{float(fila["longitude"]):.4f}|{fila["acq_date"]}|{fila["acq_time"]}'


def hora_local(fila, zona_horaria):
    utc = dt.datetime.strptime(f'{fila["acq_date"]} {fila["acq_time"].zfill(4)}', "%Y-%m-%d %H%M").replace(tzinfo=dt.timezone.utc)
    return utc.astimezone(ZoneInfo(zona_horaria))


def viento(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,relative_humidity_2m", "wind_speed_unit": "kmh"})
    try:
        c = json.loads(get(url, timeout=20))["current"]
        return {"vel": c["wind_speed_10m"], "dir": c["wind_direction_10m"], "raf": c["wind_gusts_10m"], "hr": c["relative_humidity_2m"]}
    except Exception:
        return None


# ---------- focos ----------
def agrupar(filas):
    """Une detecciones a menos de RADIO_FOCO_KM entre sí (enlace simple)."""
    pts = [(float(f["latitude"]), float(f["longitude"])) for f in filas]
    padre = list(range(len(pts)))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(len(pts)):
        for j in range(i):
            if km(pts[i], pts[j]) <= RADIO_FOCO_KM:
                padre[raiz(i)] = raiz(j)
    grupos = {}
    for i, f in enumerate(filas):
        grupos.setdefault(raiz(i), []).append(f)
    focos = []
    for fs in grupos.values():
        lat = sum(float(f["latitude"]) for f in fs) / len(fs)
        lon = sum(float(f["longitude"]) for f in fs) / len(fs)
        focos.append({"filas": fs, "centro": (lat, lon)})
    return focos


def evaluar(foco, z):
    a = z["alerta"]
    pts = [(float(f["latitude"]), float(f["longitude"])) for f in foco["filas"]]
    lugares = []
    for p in z["puntos"]:
        d = min(km(q, (p["lat"], p["lon"])) for q in pts)
        lugares.append({"nombre": p["nombre"], "km": d, "pos": (p["lat"], p["lon"]), "dentro": d <= p["radio_km"]})
    lugares.sort(key=lambda x: x["km"])
    senderos = []
    for s in z["senderos_lista"]:
        d = min(km_a_tramo(q, parte[i - 1], parte[i]) for q in pts for parte in s["partes"] for i in range(1, len(parte)))
        if d <= a["sendero_cerca_km"]:
            senderos.append({"nombre": s["nombre"], "km": d})
    senderos.sort(key=lambda x: x["km"])
    foco["lugares"], foco["senderos"] = lugares, senderos
    foco["relevante"] = any(l["dentro"] for l in lugares) or bool(senderos)
    foco["dist_min"] = lugares[0]["km"] if lugares else 1e9
    return foco


def mensaje(focos, z, T, con_viento=True):
    a = z["alerta"]
    focos = sorted(focos, key=lambda f: f["dist_min"])
    n_det = sum(len(f["filas"]) for f in focos)
    lineas = [T("titulo", zona=z["nombre"]), T("resumen", n=len(focos), d=n_det), ""]
    for i, f in enumerate(focos[:MAX_FOCOS], 1):
        lat, lon = f["centro"]
        ref = f["lugares"][0]
        lineas.append(T("foco", i=i, lat=f"{lat:.5f}", lon=f"{lon:.5f}"))
        lineas.append("   " + T("referencia", km=T.num(ref["km"]), dir=T.cardinal(rumbo(ref["pos"], f["centro"])), lugar=ref["nombre"]))
        en = [s["nombre"] for s in f["senderos"] if s["km"] <= a["sendero_fuego_km"]]
        cerca = [f'{s["nombre"]} ({T.num(s["km"])} km)' for s in f["senderos"] if s["km"] > a["sendero_fuego_km"]][:4]
        if en:
            lineas.append("   " + T("senderos_en", m=int(a["sendero_fuego_km"] * 1000), lista=", ".join(en[:4])))
        if cerca:
            lineas.append("   " + T("senderos_cerca", km=T.num(a["sendero_cerca_km"], 0), lista=", ".join(cerca)))
        ultima = max(f["filas"], key=lambda r: (r["acq_date"], r["acq_time"].zfill(4)))
        frp = max(float(r.get("frp") or 0) for r in f["filas"])
        lineas.append("   " + T("detalle", n=len(f["filas"]), hora=hora_local(ultima, z["zona_horaria"]).strftime("%d/%m %H:%M"), frp=T.num(frp)))
        lineas.append("   " + T("mapa", url=f"https://www.google.com/maps?q={lat:.5f},{lon:.5f}"))
        lineas.append("")
    if len(focos) > MAX_FOCOS:
        lineas.append(T("mas", n=len(focos) - MAX_FOCOS))
        lineas.append("")
    v = viento(*focos[0]["centro"]) if con_viento else None
    if v:
        lineas.append(T("viento", vel=round(v["vel"]), dir=T.cardinal(v["dir"]), raf=round(v["raf"]), hr=round(v["hr"])))
        hacia = (v["dir"] + 180) % 360
        avisados = set()
        for f in focos:
            for l in f["lugares"]:
                if l["dentro"] and l["nombre"] not in avisados and v["vel"] >= 5:
                    dif = abs((hacia - rumbo(f["centro"], l["pos"]) + 180) % 360 - 180)
                    if dif <= 45:
                        lineas.append(T("viento_hacia", lugar=l["nombre"]))
                        avisados.add(l["nombre"])
    if a.get("url_mapa"):
        lineas.append(T("ver_mapa", url=a["url_mapa"]))
    if lineas[-1] != "":
        lineas.append("")
    lineas.append(T("aviso"))
    return "\n".join(lineas)


def enviar_telegram(texto, var_chat):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get(var_chat)
    if not token or not chat:
        raise RuntimeError(f"faltan TELEGRAM_BOT_TOKEN o {var_chat} en .env")
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto, "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=datos), timeout=30) as r:
        if json.loads(r.read()).get("ok") is not True:
            raise RuntimeError("Telegram no aceptó el mensaje")


# ---------- ciclo ----------
def revisar(z, args):
    T = Textos(z["idioma"])
    if args.archivo:
        filas = detecciones_csv(args.archivo)
    else:
        clave = os.environ.get("FIRMS_MAP_KEY")
        if not clave:
            raise SystemExit("Falta FIRMS_MAP_KEY en .env (gratis en https://firms.modaps.eosdis.nasa.gov/api/map_key/)")
        filas = detecciones_firms(z, clave)

    ruta_estado = AQUI / "estado" / f'{z["slug"]}.json'
    estado = json.loads(ruta_estado.read_text()) if ruta_estado.exists() else {}
    limite = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat()
    estado = {k: v for k, v in estado.items() if v >= limite}

    nuevas = []
    for fila in filas:
        if confianza_baja(fila):
            continue
        k = clave_deteccion(fila)
        if k in estado and not args.prueba:
            continue
        nuevas.append(fila)
        estado[k] = dt.datetime.now(dt.timezone.utc).isoformat()

    focos = [f for f in (evaluar(f, z) for f in agrupar(nuevas)) if f["relevante"]]
    if not focos:
        print(f'[{z["slug"]}] sin focos nuevos cerca ({len(filas)} detecciones revisadas)')
    else:
        texto = mensaje(focos, z, T, con_viento=not args.sin_viento)
        if args.prueba:
            print(texto)
        else:
            enviar_telegram(texto, z["alerta"]["telegram_chat"])
            print(f'[{z["slug"]}] alerta enviada: {len(focos)} foco(s)')

    if not args.prueba:
        ruta_estado.parent.mkdir(exist_ok=True)
        ruta_estado.write_text(json.dumps(estado, indent=0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zona", action="append", help="slug de la zona (se puede repetir); sin esto, todas las activas")
    ap.add_argument("--archivo", help="CSV de FIRMS local en vez de consultar la API (requiere una sola --zona)")
    ap.add_argument("--prueba", action="store_true", help="imprime el mensaje en vez de enviarlo y no guarda estado")
    ap.add_argument("--sin-viento", action="store_true", help="no consulta el viento (útil sin internet)")
    args = ap.parse_args()
    if args.archivo and (not args.zona or len(args.zona) != 1):
        ap.error("--archivo requiere exactamente una --zona")

    leer_env(AQUI / ".env")
    fallas = 0
    for z in zonas_a_revisar(args.zona):
        try:
            revisar(z, args)
        except Exception as e:  # una zona con problemas no detiene a las demás
            fallas += 1
            print(f'[{z["slug"]}] error: {e}', file=sys.stderr)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
