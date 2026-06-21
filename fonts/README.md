# Fonts for pyEfis displays

The configuration-manager editor offers a curated set of clean, highly-readable
sans-serifs — **B612** (the font Airbus designed for cockpit displays),
**Inter**, **Roboto**, and **Open Sans** — alongside the DejaVu family that
ships on every Pi. pyEfis renders text with whatever fonts are installed **on
the device**, so these must be present on the Pi for a design to render in them;
otherwise Qt silently falls back to DejaVu.

The editor loads the same families as **web fonts** in the browser, so the
text-bearing instruments preview live in the chosen font.

## Interim: `install-fonts.sh`

Installs the set into the user font dir (`~/.local/share/fonts`) with **no
sudo**, by `apt-get download`-ing the Debian font packages and extracting the
TTFs. Run it once on the Pi:

```bash
bash install-fonts.sh
```

All four are in the Debian / Raspberry Pi OS repos (trixie): `fonts-b612`,
`fonts-inter`, `fonts-roboto-unhinted` (the plain `fonts-roboto` is an empty
transitional package), and `fonts-open-sans`.

## Proper: a signed `fonts` pack (TODO)

The durable solution is a **`fonts` pack kind** in `packtools`: the TTFs (plus
**WOFF2** for the editor to self-host instead of a CDN) bundled, signed into the
manifest, and installed by the `pyefis-data` on-Pi updater (staging →
`fc-cache`), exactly like the navdata / config packs. It rides the same trust
chain and the same Update screen.

**Licensing** (all redistributable; ship each licence with the pack): B612,
Inter, Open Sans — SIL Open Font License 1.1; Roboto — Apache License 2.0.
