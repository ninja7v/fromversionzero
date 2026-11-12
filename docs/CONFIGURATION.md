# Configuration Reference

Settings are layered; each layer overrides the previous one, and nested
objects (`camera`, `music`, `encoding`, `labels`, `shorts_overrides`,
`colors`, `fonts`) are **deep-merged**, so a layer can change one nested key
without repeating the others:

1. Built-in defaults (below).
2. Project config: `--config FILE`, else `$FVZ_CONFIG`, else `./video.json`.
3. Product config: `<dataset>/config.json`.
4. Command-line flags (`--shorts`, `--resolution`, `--output-folder`, `--no-speculation`).
   `--resolution` sets the size of the format being rendered: `resolution` for
   landscape, `shorts_overrides.resolution` with `--shorts`.

Every file is validated before anything renders. Unknown keys (typos such as
`show_lgo`) and wrong types are errors that name the file and the key.

The machine-readable contract is [`schema/config.schema.json`](../schema/config.schema.json)
(regenerate with `fromversionzero schema -o schema/config.schema.json`).
Reference it from a config file for editor autocompletion:

```json
{ "$schema": "../../../schema/config.schema.json", "title": "Evolution of X" }
```

## Content

| Key | Default | Description |
| --- | --- | --- |
| `title` | `"Product Evolution"` | Intro title. |
| `motto` | `""` | Quoted line under the graph. |
| `x_metric` | `"date"` | X-axis column; `date` and `year` are interchangeable. |
| `metrics` | all | Ordered list of columns to plot (fuzzy-matched, e.g. `"power"` → `power_hp`). Use it to keep a video to its 4–5 most telling metrics. |
| `y_scale` | `"auto"` | `log`, `linear` or `auto` (log when every plotted value is positive). Values are always shown as multiples of the first generation (×1, ×2, ×10). |
| `speculation_text` | `"What will the next generation be?"` | Question on the speculation card. |
| `labels` | see below | On-screen copy, for localization or rebranding. |
| `schema_version` | `1` | Contract version the file was written for. |

`labels` keys: `intro_kicker`, `facts`, `speculation_kicker`,
`speculation_prompt`, `speculation_footer`, `outro_kicker`, `outro_question`,
`outro_prompt`. An empty string hides the optional ones (prompts, footer).

## Video

| Key | Default | Description |
| --- | --- | --- |
| `resolution` | `"1080p"` | `240p` `360p` `480p` `720p` `1080p` `1440p` `4k` `8k`, or `WIDTHxHEIGHT` (even numbers, 64–8192 per side, not taller than wide). |
| `fps` | `30` | 1–240. |
| `ssaa_factor` | `2` | Minimum supersampling (1–4). Layouts are resolution-independent: the drawing canvas is a whole-number multiple of a 480-unit-tall design (1080 wide for Shorts) that is at least `ssaa_factor` × the output. |
| `downsample_filter` | `"bicubic"` | `bicubic` `lanczos` `bilinear` `nearest`. |
| `image_transition` | `"fade"` | Shorts product swap: `fade`, `small_to_big_to_medium`, `none`. |
| `encoding.codec` | `"h264"` | `h264` (universal) or `h265` (smaller files). |
| `encoding.crf` | `18` | Quality, 0–51; lower is better and larger. 16–20 is visually lossless for graphics. |
| `encoding.preset` | `"medium"` | x264/x265 speed preset, `ultrafast` … `veryslow`. |

Output is BT.709 `yuv420p` with `+faststart`, ready for YouTube.

## Style

| Key | Default | Description |
| --- | --- | --- |
| `theme` | `"signal"` | `signal` (dark, lime accent), `blueprint` (navy, amber) or `paper` (light editorial). |
| `colors.<role>` | theme | Hex override for `background`, `grid`, `grid_dots`, `line`, `line_glow`, `text_primary`, `text_secondary`, `accent`, `accent_glow`. |
| `colors.line_colors` | theme (8 colors) | Curve palette, assigned in metric order. |
| `fonts.<role>` | theme | `display`, `heading`, `body`, `label`. Use `Family-Weight` names (`Montserrat-Light`, `Montserrat-SemiBold`, ...) served by the bundled variable font, a `.ttf` in `<dataset>/fonts/`, or an absolute path. |

## Timing (seconds)

| Key | Default | Description |
| --- | --- | --- |
| `intro_duration` | `3.0` | Title card. |
| `reveal_duration` | `4.0` | Animation of each new generation (> 0). |
| `steady_duration` | `2.0` | Pause after each reveal. |
| `hold_duration` | `5.0` | Pause on the full timeline. |
| `speculation_duration` | `9.0` | Question card plus speculated generations (only if the CSV has any). |
| `outro_duration` | `3.0` | Recap card. |
| `fact_card_duration` | `4.0` | Time each insight stays on screen. |
| `camera.speed` | `2.5` | Camera damping (> 0); higher follows faster. |
| `shorts_overrides.*` | intro 0, outro 0, reveal 2, steady 1, hold 3, speculation 5, resolution `1080x1920` | Replace the timings above in Shorts mode. The resolution must not be wider than tall. Shorts have no title or recap card: an intro duration shows the empty chart and an outro duration extends the final hold. |

## Audio

| Key | Default | Description |
| --- | --- | --- |
| `music.file` | `"soundtrack.mp3"` | Looked up in the dataset folder, then `music/`. Missing file = no music. |
| `music.volume` | `0.7` | 0–1. |
| `music.fade_in` / `music.fade_out` | `2.0` / `3.0` | Seconds. |
| `music.loop` | `true` | Repeat a soundtrack shorter than the video. `false` leaves silence after it ends. |
| `mix_sfx` | `true` | Play each row's `sfx` file at its reveal. |

The audio track always lasts as long as the video (48 kHz stereo AAC): a
longer soundtrack is cut with the fade-out, a shorter one loops or is followed
by silence. If mixing fails, the complete silent video is kept at the output
path.

## Media

Linked logo and generation images (see `docs/DATASET_GUIDE.md`, *Linked media*).

| Key | Default | Description |
| --- | --- | --- |
| `media.logo` | none | `http(s)` URL of the logo, or a path relative to the dataset folder. |
| `media.logo_levels` | `3` | Tone bands the logo is reduced to (2–5). |
| `media.logo_invert` | `false` | Invert tones before banding (for light-on-dark logos). |
| `media.image_levels` | `2` | Tone bands for image URLs in `data.csv` (photos usually look best with 2). |
| `media.image_invert` | `false` | Invert tones of image URLs before banding. |
| `media.stylize` | `true` | Redraw linked media as line art; `false` shows it unchanged. |
| `media.line_color` | `"source"` | Line colour of the line art: `"source"` keeps the media's own colours, a `#RRGGBB` value draws every line in that colour (useful for a black logo on a dark theme). |

## Output and visibility

| Key | Default | Description |
| --- | --- | --- |
| `product_name` | dataset folder name | Output file prefix; also the default product for `render` in `video.json`. |
| `output_folder` | dataset folder | Where the MP4 is written. |
| `shorts` | `false` | Portrait Shorts layout. |
| `speculation` | `true` | Include speculated rows. |
| `show_motto`, `show_logo`, `show_legend`, `show_facts`, `show_progress_bar` | `true` | Toggle HUD elements. |

Output names: `<product>_landscape_<resolution>.mp4` and
`<product>_portrait_<W>x<H>.mp4`.
