# Dataset Guide

This guide explains how to create a product dataset that FromVersionZero can render.

## Folder structure

Each product lives under a category folder of a *data root* (`--data-root`,
`$FVZ_DATA_ROOT`, or `./data`). Published datasets live in the
[fromversionzero-data](https://github.com/ninja7v/fromversionzero-data)
repository, which defines the data format in its
[DATA_STANDARDS.md](https://github.com/ninja7v/fromversionzero-data/blob/main/DATA_STANDARDS.md):
contributions go there. `examples/` here holds one dataset copied from it.
This guide describes what the generator reads; the data standards take
precedence when the two differ.

```
<data root>/
└── <category>/            # e.g. Vehicle, Tech, AI, Shoes, Software
    ├── units.json         # optional — display units for the category
    └── <product>/         # e.g. Porsche_911
        ├── data.csv        # required — one row per generation
        ├── config.json     # required — per-product config overrides
        ├── images/         # optional — local product cutout images
        ├── logo.png        # optional — product logo overlay
        ├── soundtrack.mp3  # optional — background music
        ├── description.md  # sources and methodology (required for contributions)
        ├── units.json      # optional — product-specific unit overrides
        ├── icons/          # optional — metric icons overriding the bundled ones
        └── fonts/          # optional — extra .ttf files for config "fonts"
```

`--product` accepts a bare name (`Porsche_911`), `Category/Name`, or a directory path. A bare name is searched in every category; ambiguous names are rejected.

## Required files

### `data.csv`

One row per product generation. The first row is the header. The following columns have special meaning; all other numeric columns are plotted as curves:

| Column | Required | Type | Description |
| --- | --- | --- | --- |
| `name` | yes | str | Display name of the generation. The `name`, `image`, `fact`, `sfx` and `speculation` headers are matched ignoring case. |
| `date` | yes | `YYYY.MM` | X axis value (e.g. `2018.11`), converted to a fractional year. Write the month with two digits: `2018.1` is read as January (with a warning), not October. A `year` column with `YYYY` values is also accepted. |
| `<any numeric metric>` | one or more | number | Plotted as its own curve, as a multiple of its first value. Leading cells may be blank (the curve starts at the first generation that has a value). A blank between two values is interpolated linearly along the X axis and labelled `≈` in the video. The last row must have a value. Commas and `$` are stripped. Select and order curves with `metrics` in `config.json`. |
| `image` | optional | str | Filename inside `images/`, or an `http(s)` URL that is downloaded and redrawn as line art at render time (see *Linked media*). |
| `fact` | optional | str | Short milestone text shown in the fact card; `;` splits it into bullets. A leading `Speculation:` becomes the card's label. |
| `sfx` | optional | str | Sound effect file (relative to the dataset folder) played at this node's reveal. A missing file is a warning and is skipped. |
| `speculation` | optional | `true`/`false` | If `true`, the row is treated as a speculated next generation and rendered in the speculation phase. Speculation rows must be dated after every released row. |

### `config.json`

Per-product overrides. See [`CONFIGURATION.md`](CONFIGURATION.md) for the full field reference.

The loader validates every row before rendering. It requires at least one real
(non-speculation) row and one numeric metric with a value on a released row
and on the last row. Columns
that contain only text are treated as metadata and are not plotted. A column
that mixes numeric and nonnumeric values is rejected. So are duplicate or
unnamed columns and rows with more or fewer cells than the header.

### What a value means

Which product line a dataset follows, the comparison basis (entry-level model
at launch), units, sources and speculation rows are defined in the data
standards linked above, not here.

## Optional files

- `description.md` — scope, lineage, metrics, sources and release cycle (template in the data standards). Not read by the renderer.
- `logo.png` — product logo drawn over the dashboard when `show_logo` is on. A `media.logo` URL in `config.json` takes precedence.
- `soundtrack.mp3` — background music (filename referenced by `music.file`).
- `images/` — local media referenced by the `image` column. Nonempty values must exist. A generation without an image is shown as a typographic mark: the code in trailing parentheses of its name (`911 Carrera (992)` → **992**), otherwise its year.
- `units.json` — metric column → display unit, e.g. `{"power": "hp", "price": "USD", "acceleration_0_100": "s"}`. Keys are matched ignoring case, spaces and underscores, but must otherwise equal the column name. `USD`/`EUR` render as a currency prefix, `%` is appended directly, anything else after a space. Large values are abbreviated (`190k units`, `$1.2M`). Category defaults live in `<category>/units.json`; product files override them.

### Linked media

A dataset can link its logo and generation images instead of shipping them:
`media.logo` in `config.json` and URLs in the `image` column of `data.csv`.
At render time each link is downloaded once, redrawn as FromVersionZero line
art (tone contours in the media's own colours and a halftone fill, see
`fromversionzero.marks`) and cached
in `~/.cache/fromversionzero/media` (override with `$FVZ_MEDIA_CACHE`), so later
renders work offline. A link that cannot be downloaded or read falls back to the
typographic placeholder with a warning.

- Links must point to public `http(s)` hosts. Private, loopback and link-local
  addresses are refused, because a dataset written by someone else could
  otherwise make the renderer query your local network. Set
  `FVZ_ALLOW_PRIVATE_MEDIA=1` to allow them (for a media server of your own).
- Preview a link: `fromversionzero mark <url-or-file> preview.png [--levels 2] [--line-color #RRGGBB]`.
- Check every link: `fromversionzero validate --check-media`.
- SVG, PNG, JPEG and WebP are supported; files over 20 MB or 64 megapixels are skipped.

### Zeros and the log axis

The Y axis shows each metric as a multiple of its first generation on a log
scale (so ×2 and ×0.5 are symmetric). A log axis cannot show zero or negative
values: with `y_scale: "auto"` (default) such a dataset falls back to a linear
axis. Do not use `0` for *unknown*: leave the cell empty instead (see the
`<any numeric metric>` row above for how blanks are drawn).

## Complete minimal example

`<data root>/Tech/MyProduct/data.csv`:

```csv
name,date,power,price,image,fact,speculation
V1,2018.03,100,1500,,First launch.,false
V2,2020.03,150,2500,,Bigger battery.,false
V3,2022.03,200,3500,,Redesigned chassis.,false
V4,2024.03,260,4500,,Hybrid assist.,false
V5,2026.03,320,5500,,Speculation: next generation; hybrid assist.,true
```

`<data root>/Tech/MyProduct/config.json`:

```json
{
  "title": "Evolution of MyProduct",
  "motto": "Always evolving.",
  "x_metric": "date",
  "theme": "signal",
  "speculation_text": "What will V5 bring?",
  "music": {
    "file": "soundtrack.mp3",
    "volume": 0.6,
    "fade_in": 2.0,
    "fade_out": 2.5
  }
}
```

If image names are present, `<data root>/Tech/MyProduct/images/` must contain the
referenced files. Leave image cells empty to use typographic placeholders.

Only add media you have permission to redistribute; datasets published in
fromversionzero-data link third-party logos and images instead of storing them.
See [`ASSET_POLICY.md`](../ASSET_POLICY.md).

Check it, then render:

```bash
fromversionzero validate --product MyProduct --strict   # errors + data warnings
fromversionzero preview --product MyProduct -o look.png   # one frame, in a second
fromversionzero render --product MyProduct --resolution 480p
```

`validate --strict` also fails on warnings such as metrics without a unit or
zero values forcing a linear axis.
