# Movris Vault

**Encrypted iPhone Backups for Linux**

Movris Vault makes full, encrypted iPhone backups on Linux with one button — the same kind of
backup Finder or iTunes makes, without a terminal. Plug the iPhone in, press *Back up now*,
type your passcode on the iPhone, done.

> **Status:** early (0.1). It makes real backups and has been tested on Linux Mint 22 with an
> iPhone on iOS 27. The interface is in Spanish for now; English is next.

## Features

- **One button.** Guides you through plugging in, unlocking and tapping *Trust*.
- **Real progress** with speed and time left, and a *Cancel* button that leaves the iPhone in a clean state.
- **Encrypted backups**, the only kind that include saved passwords, Health and WhatsApp data.
  The app suggests using your Linux login password and keeps it in your session keyring.
- **Incremental:** after the first backup only what changed is copied (minutes, not hours).
- **Safe switch to encryption:** the old unencrypted backup is kept until the new encrypted one
  finishes, and restored automatically if it fails.
- **Stays awake:** blocks suspend while backing up, and warns if the laptop is on battery.
- **Clear errors** instead of stack traces: cable unplugged, iPhone locked, disk full…
- Backups live in a normal folder (`~/iPhoneBackup` by default, or an external disk), in the
  standard format other tools understand.

## Install

On Linux Mint, Ubuntu or Debian:

```bash
git clone https://github.com/jaca101/movris-vault.git
cd movris-vault
packaging/install.sh --desktop-shortcut
```

It only asks for your password if system packages are missing (GTK 4, libadwaita, usbmuxd).
Everything else goes into your home folder. Then open **Movris Vault** from the menu.

To update: `git pull` and run `packaging/install.sh` again.
To uninstall: `packaging/uninstall.sh` (your backups are not touched).

## Command line

```bash
movris-vault info       # the connected iPhone
movris-vault list       # saved backups
movris-vault backup     # make a backup (Ctrl+C cancels cleanly)
movris-vault encrypt    # turn on encryption or change its password
```

## How it works

Movris Vault talks to the iPhone through [pymobiledevice3](https://github.com/doronz88/pymobiledevice3)
and `usbmuxd`, using Apple's backup protocol (`mobilebackup2`). Encryption is done by the iPhone
itself: the password is only needed to restore or change it, never to make a backup.
Nothing leaves your computer.

## Not yet

- Restoring a backup to an iPhone, or extracting photos and files from one.
- Automatic backups when the iPhone is plugged in.
- English interface, Flatpak.

## En español

Movris Vault hace copias de seguridad cifradas del iPhone en Linux con un botón. Instálala con
`packaging/install.sh --desktop-shortcut` y búscala en el menú. La interfaz está en español.
Si activas el cifrado, te sugiere usar la misma contraseña con la que entras en el ordenador y
la guarda en el llavero; apúntala igualmente, porque sin ella no se puede restaurar la copia.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
