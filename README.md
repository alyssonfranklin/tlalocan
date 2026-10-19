# Tlalocan

Herramienta abierta para vigilar incendios forestales en zonas de montaña. Cada zona es una carpeta con su configuración; hoy vigila Ciénega de González (Nuevo León, México) y el Parque Nacional do Caparaó (Minas Gerais y Espírito Santo, Brasil).

El nombre viene del Tlalocan, el paraíso de Tláloc: un lugar verde y con agua. Es lo que queremos cuidar.

## Visión

Tlalocan existe para que las comunidades de la sierra y los equipos de rescate tengan la misma información que hoy solo ven unos pocos, a tiempo y en un lenguaje claro.

- **Preparar.** Conocer el terreno antes del fuego: el relieve, los senderos, por dónde avanzó un incendio en el pasado y cómo lo empujaron el viento y la pendiente. La simulación sirve para practicar escenarios, no para predecir.
- **Crear conciencia.** Mostrar a vecinos, caminantes y autoridades lo que pasa en la sierra con datos reales y abiertos: detecciones de satélite, imágenes antes y después, kilómetros de senderos afectados.
- **Apoyar a los equipos de rescate.** Avisar por Telegram en cuanto un satélite detecta fuego cerca del poblado o de los senderos, con la ubicación, el viento y qué rutas están en riesgo.

Tlalocan no sustituye avisos oficiales ni decisiones de Protección Civil. Emergencias: 911.

## Qué incluye

### `web/` · Mapa

Página que se abre en cualquier navegador, en 2D o 3D, sin instalar nada.

- Detecciones de fuego del incendio de marzo de 2021 (NASA FIRMS, VIIRS 375 m), hora por hora.
- Relieve real (Copernicus DEM) con capas de satélite Sentinel-2 antes y después; en infrarrojo lo quemado se ve magenta.
- Viento, humedad y temperatura por hora (ERA5, Open-Meteo).
- Simulación simple de propagación: tocas el mapa, enciendes un fuego y ves cómo lo mueven el viento y la pendiente.
- Senderos de OpenStreetMap y los tuyos (GPX, KML o GeoJSON), con los tramos que pasan cerca del fuego.

### `zonas/` · Una carpeta por lugar

```
zonas/
  cienega-de-gonzalez/   zona.toml, senderos.geojson
  caparao/               zona.toml, trilhas.geojson
```

`zona.toml` define el área, el idioma de los avisos (`es` o `pt-BR`), la zona horaria, los lugares vigilados con su radio, los archivos de senderos y el chat de Telegram de la zona.

### `alerta/` · Aviso por Telegram

Un solo proceso revisa cada 30 minutos todas las zonas activas con las detecciones de FIRMS (VIIRS y MODIS). Une las detecciones cercanas en focos y avisa cuando un foco está:

- dentro del radio de un lugar vigilado (por ejemplo, 8 km de Alto Caparaó),
- sobre un sendero (a 400 m o menos), o
- cerca de un sendero (a 2 km o menos).

Cada foco trae sus coordenadas, la distancia y dirección al lugar más cercano, los senderos afectados, la hora de la última detección y un enlace al mapa. El mensaje cierra con el viento actual y marca si empuja el fuego hacia un lugar vigilado. Los avisos salen en el idioma de la zona.

El dato de satélite llega con 1 a 3 horas de retraso, puede tener falsos positivos y no ve fuegos pequeños o bajo nubes.

## Instalación de la alerta (macOS)

Necesitas:

- Python 3.11 o más reciente (`python3 --version`). No usa librerías externas.
- Una clave gratuita de FIRMS: https://firms.modaps.eosdis.nasa.gov/api/map_key/
- Un bot de Telegram: habla con @BotFather, usa `/newbot` y guarda el token.
- Un chat por zona: agrega el bot al grupo de cada zona, escribe un mensaje y abre `https://api.telegram.org/bot<TOKEN>/getUpdates`. El número en `"chat":{"id": ...}` es el chat (en grupos empieza con `-`). Guárdalo en `.env` con el nombre que indica `telegram_chat` en `zona.toml`.

```zsh
cd ~/projects/tlalocan/alerta
cp .env.example .env
open -e .env    # llena las claves y guarda

# Prueba con datos reales del 25 de marzo de 2021 (no envía nada)
python3 alerta.py --zona cienega-de-gonzalez --archivo ../zonas/cienega-de-gonzalez/ejemplo-2021-03-25.csv --prueba

# Prueba contra FIRMS de hoy, todas las zonas (no envía nada)
python3 alerta.py --prueba

# Programar cada 30 minutos
mkdir -p logs
sed "s#__CARPETA__#$PWD#g" mx.tlalocan.alerta.plist > ~/Library/LaunchAgents/mx.tlalocan.alerta.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/mx.tlalocan.alerta.plist
```

Si `python3` no está en `/opt/homebrew/bin/python3`, cambia esa ruta dentro del `.plist` (usa `which python3`).

Para detenerla:

```zsh
launchctl bootout gui/$(id -u)/mx.tlalocan.alerta
```

Cada detección avisada se guarda en `alerta/estado/<zona>.json` por 5 días, así no se repite. Cada vuelta manda como máximo un mensaje por zona. Si una zona falla, las demás siguen.

## Agregar una zona

1. Copia una carpeta de `zonas/` con un nombre nuevo en minúsculas y con guiones (será la URL: `/nueva-zona`).
2. Edita `zona.toml`: nombre, área, idioma, zona horaria, lugares y radios.
3. Agrega los senderos en GeoJSON (líneas con la propiedad `name`). Los de OpenStreetMap se pueden exportar desde overpass turbo.
4. Agrega el chat de la zona en `alerta/.env` y prueba con `python3 alerta.py --zona nueva-zona --prueba`.

Para otro idioma, copia `alerta/textos/es.json` y tradúcelo.

## Próximos pasos

- Proceso que genere los datos del mapa de cada zona (relieve, senderos, FIRMS, Open-Meteo, Sentinel-2) y una página por zona en `/<zona>`; el incendio de 2021 pasa a ser un evento en `/cienega-de-gonzalez/marzo-2021`.
- Para Brasil, sumar las detecciones del INPE (BDQueimadas) a las de FIRMS.
- Vegetación real (ESA WorldCover o MapBiomas) en la simulación, en vez de suponerla por altitud.
- Viento medido por estaciones locales (SMN/CONAGUA); el reanálisis ERA5 no captó el viento del oeste que empujó el fuego en 2021.
- Más senderos, con permiso de Wikiloc o mejorando OpenStreetMap.

## Fuentes y licencias

El código está bajo licencia MIT (ver `LICENSE`). Los datos tienen sus propias licencias y deben citarse:

- Detecciones de fuego: NASA FIRMS (LANCE), https://firms.modaps.eosdis.nasa.gov
- Imágenes: contiene datos modificados de Copernicus Sentinel (2021), vía Earth Search (AWS).
- Relieve: Copernicus DEM GLO-90.
- Clima: Open-Meteo (CC BY 4.0) y reanálisis ERA5; uso no comercial sin clave.
- Senderos (`zonas/*/senderos.geojson`, `zonas/*/trilhas.geojson` y los del mapa): © colaboradores de OpenStreetMap, licencia ODbL. Si mejoras esos datos, la versión derivada debe seguir abierta bajo ODbL.
