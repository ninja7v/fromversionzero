# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-12

First version.

- Renders a product-evolution video from a dataset (one CSV row per
  generation): an animated timeline where every metric is a curve, shown as
  a multiple of its first generation on a log scale, with units, value
  labels, fact cards and an optional speculated next generation.
- Landscape video and a portrait Short from the same dataset, encoded as
  H.264 (or H.265) MP4. `resolution` is landscape and
  `shorts_overrides.resolution` portrait, each side between 64 and 8192
  pixels; `--resolution` sets whichever format is being rendered.
- Optional music and sound effects, mixed into a 48 kHz stereo track that
  always lasts exactly as long as the video. A short soundtrack repeats
  until the end (`music.loop`, default `true`).
- Logos and product images linked in the dataset are downloaded once per
  render, redrawn as line art in their own colours (or one colour with
  `media.line_color`) and cached; generations without an image get
  a typographic mark. Media is only downloaded from public http(s) hosts
  (set `FVZ_ALLOW_PRIVATE_MEDIA=1` to allow private ones), and linked images
  over 64 megapixels are skipped.
- Commands: `render [--shorts] [--workers N]`, `preview` (one frame as a
  PNG), `validate [--strict] [--check-media]`, `list`, `schema` and `mark`.
  Dataset, configuration and FFmpeg errors print one readable message;
  `--verbose` adds the traceback.
- `validate` checks `data.csv` strictly: a `name` column, unique named
  columns, rows as long as the header, valid dates, true/false
  `speculation` values, speculation rows after all released rows, and
  `image` paths inside the dataset's `images/` folder are required. A
  single-digit month (`2018.1`) and a missing `sfx` file are warnings.
- Validated JSON configuration with a published JSON Schema, three themes
  (`signal`, `blueprint`, `paper`) and customizable on-screen text.
- Reads the datasets of
  [fromversionzero-data](https://github.com/ninja7v/fromversionzero-data);
  one example dataset is included.
- Runs on Linux, macOS and Windows with Python 3.12 or newer; FFmpeg and SVG
  rendering come with the Python packages, so no system library is needed.
