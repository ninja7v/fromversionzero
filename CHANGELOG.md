# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-08

First version.

- Renders a product-evolution video from a dataset (one CSV row per
  generation): an animated timeline where every metric is a curve, shown as
  a multiple of its first generation on a log scale, with units, value
  labels, fact cards and an optional speculated next generation.
- Landscape video and a portrait Short from the same dataset, encoded as
  H.264 (or H.265) MP4, with optional music and sound effects.
- Logos and product images linked in the dataset are downloaded, redrawn as
  line art and cached; generations without an image get a typographic mark.
- Commands: `render`, `validate [--strict] [--check-media]`, `list`,
  `schema` and `mark`.
- Validated JSON configuration with a published JSON Schema, three themes
  (`signal`, `blueprint`, `paper`) and customizable on-screen text.
- Reads the datasets of
  [fromversionzero-data](https://github.com/ninja7v/fromversionzero-data);
  one example dataset is included.
- Runs on Linux, macOS and Windows with Python 3.12 or newer; FFmpeg and SVG
  rendering come with the Python packages, so no system library is needed.
