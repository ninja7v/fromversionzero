# Security policy

## Reporting a vulnerability

Please report security problems privately through
[GitHub security advisories](https://github.com/ninja7v/fromversionzero/security/advisories/new)
rather than a public issue. Include the version, the command you ran and, if
possible, a minimal dataset that reproduces the problem.

Only the latest release is supported.

## What the renderer does with a dataset

Datasets can come from other people, so the renderer treats them as untrusted:

- `config.json` is validated against a strict schema; unknown keys are errors.
- Linked media (`media.logo`, `image` URLs) is downloaded only from public
  `http(s)` hosts, with a 20 MB and 64 megapixel limit. Redirects are checked
  the same way. The host check resolves the name before the download does, so
  it does not stop a DNS server that deliberately changes its answer.
- `image` files must be inside the dataset's `images/` folder.
- FFmpeg is started without a shell, and audio files can only be opened from
  the local disk.

Known limits: `sfx`, `music.file`, `media.logo` and `fonts` may name any local
file path, and an SVG is rasterized without a limit on its height. Render
datasets from sources you do not trust in a container or a throwaway account.
