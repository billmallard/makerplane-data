# AER-4636: IAS.Vgnd in Aircraft Parameters

Headless renders of `public/aircraft.html` in demo mode (`?demo=1`, no
account), served with a speeds-only schema that carries fix-gateway #117's
`aux_meta` for `Vgnd`. The live R2 schema has no `Vgnd` until #117 merges
and the schema is re-uploaded.

| File | State |
|---|---|
| `vgnd_kt_default_warning.png` | knots; Vgnd blank, Vs0 38 kt. The 40 kt default is not below Vs0: warning, Save still enabled. |
| `vgnd_mph_out_of_range.png` | mph entry; Vgnd 120 kt (138.09 mph) is outside 11.51-115.08 mph: hard error, Save disabled. |
| `vgnd_mph_ok.png` | mph entry; Vgnd 35 kt (40.28 mph), below Vs0: no message. |

Regenerate, from `configurator/`:

```sh
CHROME=~/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell \
  sh tools/renders/AER-4636/render.sh
```

The script injects the demo values by rewriting the page's initial
`profile.values` in a temp copy. The page in the repo is not modified.
