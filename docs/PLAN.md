# Plan de Movris Vault

## Tecnología

- **Python 3.12 + GTK4/libadwaita** (PyGObject).
- **pymobiledevice3** para hablar con el iPhone (progreso real, cifrado, restauración); `idevicebackup2` como alternativa si falla.
- **libsecret** para guardar la contraseña de cifrado en el llavero.
- Licencia **GPL-3.0** (obligatoria por pymobiledevice3).

## Fases

### Fase 0 — pruebas técnicas (hecha, 25-10-2026)
- [x] pymobiledevice3 11.44 lee los datos del iPhone (iOS 27.0.1) y reutiliza el emparejamiento existente.
- [x] Una copia con pymobiledevice3 se puede cancelar al instante sin dejar el iPhone en mal estado (2,3 GB en 2 min).
- [x] Motor elegido: **pymobiledevice3**.

Lo aprendido:
- iOS 27 pide el código en el iPhone al empezar cada copia. Llegan los avisos
  `LocalAuthentication.ui.presented` / `dismissed` → pantalla «Escribe tu código en el iPhone».
- El porcentaje que manda el iPhone va a saltos (0,3 % → 24,9 % de golpe). La barra tiene que
  combinarlo con los bytes recibidos frente al espacio usado en el iPhone.
- Cancelar mientras el iPhone espera el código lo deja colgado: el siguiente intento no avanza.
  Hay que esperar a que se meta el código o desconectar y reconectar.
- Si el portátil se suspende, la copia se cuelga sin error y `usbmuxd` pierde el iPhone hasta
  reconectar el cable. Hace falta: inhibir suspensión (gestor de sesión; `systemd-inhibit` no
  tiene permiso desde SSH), avisar si va con batería y detectar «iPhone en USB pero no en usbmuxd».
- Mint 22 no trae `python3-venv`; instalar dependencias sin `sudo` necesita otro camino (pip desde wheel o Flatpak).

### Fase 1 — lo básico

Hecho y probado con el iPhone real (25-10-2026): núcleo, línea de órdenes y ventana. Primera copia
incremental desde la ventana sobre la copia de idevicebackup2: 761 MB en ~3 min, completa.
Instalador (`packaging/install.sh`) y lanzador en el menú y el escritorio: hechos.
Copia cifrada: activar/cambiar contraseña con la de Linux sugerida y guardada en el llavero; la copia
sin cifrar se aparta y vuelve a su sitio si la nueva no termina. Probado: copia completa cifrada de
245 GB en 1 h 41 min (~41 MB/s), y la contraseña del llavero abre la copia (109.981 ficheros).

- Pantalla principal con un botón «Hacer copia ahora» y la fecha de la última copia.
- Asistente: conectar, desbloquear, «Confiar».
- Progreso real y botón Cancelar.
- Activar copia cifrada con contraseña guardada en el llavero.
- Inhibir suspensión durante la copia; notificación al terminar.
- Comprobar espacio libre; errores en lenguaje normal.
- Lista de copias (fecha, tamaño, versión de iOS); elegir carpeta destino.
- Reutilizar las copias existentes en `~/iPhoneBackup` (copia incremental).

### Fase 2 — comodidad
- Copia automática al conectar el iPhone (opcional).
- Recordatorio si pasan N días sin copia.
- Guardar varias versiones sin duplicar espacio.

### Fase 3 — más adelante
- Restaurar una copia en el iPhone.
- Extraer fotos o archivos de una copia.
- Flatpak.

## Estructura

```
src/movris_vault/
  cli.py           línea de órdenes (info, pair, list, backup, encrypt)
  ui/              ventana GTK 4 / libadwaita
  core/            device, backup, encryption, storage, power, settings
data/              lanzador .desktop e icono
packaging/         install.sh / uninstall.sh
tests/
```

## Entorno de pruebas

MacBook Pro Intel con Linux Mint 22.3 (Cinnamon) e iPhone 13 mini con iOS 27.0.1.
