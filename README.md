# Tlalocan

Herramienta abierta para vigilar incendios forestales en la sierra de Santiago, Nuevo León, empezando por Ciénega de González.

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

### `alerta/` · Aviso por Telegram

Revisa cada 30 minutos las detecciones de FIRMS (VIIRS y MODIS) y avisa cuando hay fuego:

- a menos de 10 km de Ciénega de González (u otro lugar que configures),
- sobre un sendero (a 400 m o menos), o
- cerca de un sendero (a 2 km o menos).

El mensaje incluye los senderos afectados, la detección más cercana con enlace al mapa y el viento actual. Si el viento empuja el fuego hacia el poblado, lo marca.

El dato de satélite llega con 1 a 3 horas de retraso, puede tener falsos positivos y no ve fuegos pequeños o bajo nubes.

## Instalación de la alerta (macOS)

Necesitas:

- Python 3.11 o más reciente (`python3 --version`). No usa librerías externas.
- Una clave gratuita de FIRMS: https://firms.modaps.eosdis.nasa.gov/api/map_key/
- Un bot de Telegram: habla con @BotFather, usa `/newbot` y guarda el token.
- El chat de los avisos: agrega el bot a un grupo, escribe un mensaje y abre `https://api.telegram.org/bot<TOKEN>/getUpdates`. El número en `"chat":{"id": ...}` es el `TELEGRAM_CHAT_ID` (en grupos empieza con `-`).

```zsh
cd ~/projects/tlalocan/alerta
cp .env.example .env
open -e .env    # llena las tres claves y guarda

# Prueba con datos reales del 25 de marzo de 2021 (no envía nada)
python3 alerta.py --archivo ejemplo-2021-03-25.csv --prueba

# Prueba contra FIRMS de hoy (no envía nada)
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

Cada detección avisada se guarda en `estado.json` por 5 días, así no se repite. Cada vuelta manda como máximo un mensaje.

## Usarlo en otra zona

Edita `alerta/config.toml`: área, lugares a vigilar y radios. Para senderos sirve cualquier GeoJSON con líneas y la propiedad `name`; agrégalo en `[senderos] archivos`.

## Próximos pasos

- Job en la Mac Mini que actualice el mapa con datos del día (FIRMS, Open-Meteo, Sentinel-2) en vez de datos fijos de 2021.
- Viento medido por estaciones locales (SMN/CONAGUA); el reanálisis ERA5 no captó el viento del oeste que empujó el fuego en 2021.
- Más senderos, con permiso de Wikiloc o mejorando OpenStreetMap.

## Fuentes y licencias

El código está bajo licencia MIT (ver `LICENSE`). Los datos tienen sus propias licencias y deben citarse:

- Detecciones de fuego: NASA FIRMS (LANCE), https://firms.modaps.eosdis.nasa.gov
- Imágenes: contiene datos modificados de Copernicus Sentinel (2021), vía Earth Search (AWS).
- Relieve: Copernicus DEM GLO-90.
- Clima: Open-Meteo (CC BY 4.0) y reanálisis ERA5; uso no comercial sin clave.
- Senderos (`alerta/senderos.geojson` y los del mapa): © colaboradores de OpenStreetMap, licencia ODbL. Si mejoras esos datos, la versión derivada debe seguir abierta bajo ODbL.
