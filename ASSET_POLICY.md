# Asset policy

## This repository

Application code and project-owned FromVersionZero branding (bundled metric
icons, typographic marks, themes) are distributed under the repository
[MIT license](LICENSE.txt). The bundled Montserrat font files keep their SIL
Open Font License 1.1; see `src/fromversionzero/resources/OFL.txt`.

This repository ships no third-party logos or product images. The example in
`examples/` is media-free apart from a link to the brand's logo.

## Datasets and linked media

The datasets live in
[fromversionzero-data](https://github.com/ninja7v/fromversionzero-data) under
CC BY 4.0. Following its
[asset policy](https://github.com/ninja7v/fromversionzero-data/blob/main/ASSET_POLICY.md),
they **link** to logos and product images (`media.logo` in `config.json`, URLs
in the `image` column) instead of storing them.

At render time FromVersionZero downloads each linked file, redraws it as line
art and caches it locally (`~/.cache/fromversionzero/media`). The cached files
are not redistributed by this project. Product names, logos and designs belong
to their owners and are used only for identification; no affiliation or
endorsement is implied. The MIT license does not cover them.

Whoever renders and publishes a video is responsible for how the downloaded
media is used in it. Fair-use or platform-tolerance assumptions depend on the
video, the jurisdiction and the context.

## Local media

Users may put their own files in a dataset (`images/`, `logo.png`,
`soundtrack.mp3`). They are responsible for having permission to use them.

## Requests from rights holders

Rights holders can open an issue here, or in fromversionzero-data for a linked
file, to request a correction or removal.
