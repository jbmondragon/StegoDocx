# StegoDocx UI

A simple web front end for StegoDocx. `index.html` is self-contained, with no build step and no CDN.

## Files
| File | What it is |
|---|---|
| `index.html` | The whole UI: Embed and Extract tabs; follows the system light/dark setting |
| `bridge.py`  | Web server (standard-library `http.server`) and pywebview glue; connects the UI to `algorithms/` |
| `main.py`    | Starts the website |
| `algorithms/`, `processing/` | The steganography methods: preprocessing, embedding, extraction |

## Methods
Every method runs its own `embed_docx` / `extract_docx` from `algorithms/`:

| id | Method | Capacity shown |
|---|---|---|
| `block_sync` (default) | Proposed · block-based, synchronized | exact |
| `block` | Proposed · block-based with Hamming(8,4) | exact |
| `huffman_sync` | Albano (2019) · Huffman + Color, synchronized | estimate (≈) |
| `huffman` | Albano (2019) · Huffman + Color | estimate (≈) |
| `invisible_sync` | Mustafa (2020) · Invisible Character, synchronized | exact |
| `invisible` | Mustafa (2020) · Invisible Character | exact |
| `block_nohamming`, `block_sync_nohamming` | Ablations without Hamming(8,4) | exact |

Huffman capacity depends on how well the message compresses against the cover's code, so the UI shows it as `≈` and does not block the Embed button; the algorithm makes the final check.

Extraction only needs the stego file: the Huffman tree is rebuilt from the stego text and every length travels in the payload.

## Running
```bash
pip install -r requirements.txt     # only python-docx is required
python main.py                      # http://localhost:5000
python main.py --host 0.0.0.0 --port 8080   # reachable from other machines
python main.py --desktop            # desktop window (pip install pywebview)
```
`python bridge.py --web` does the same as `python main.py`. `HOST` and `PORT` environment variables are also read.

## Deploying to Render
The repo includes a `Dockerfile` (Python 3.12 + headless LibreOffice, so the Pages preview works) and a `render.yaml` blueprint.

1. Push the repo to GitHub or GitLab.
2. In Render, choose **New → Blueprint** and pick the repo. It reads `render.yaml` and creates the `stegodocx` web service.
   (Or choose **New → Web Service**, pick the repo and set the runtime to **Docker**.)
3. Render sets `PORT`. The image sets `HOST=0.0.0.0`, and the health check is `/api/health`.

On the free plan the service sleeps when idle, so the first request after a pause takes a while. The first page preview is also slow while LibreOffice sets up its profile.

To test the image locally:
```bash
docker build -t stegodocx .
docker run -p 10000:10000 stegodocx   # http://localhost:10000
```

The **Pages** panel shows the stego document's pages (after an embed, or when a stego file is chosen on Extract). It converts the .docx to PDF with LibreOffice and renders each page with PyMuPDF, so it needs LibreOffice plus numpy, Pillow and PyMuPDF; without them only that panel shows "Preview unavailable". The first render takes longer while LibreOffice sets up its profile; at most the first 30 pages are shown.
The page needs the server: opening `index.html` straight from disk only shows a reminder to start it.

To point the UI at another server, add this before the main script:
```html
<script>window.STEGO_CONFIG = { mode: 'http', apiBase: 'http://localhost:5000/api' };</script>
```
`mode` can be `auto` (the default), `pywebview` or `http`.

## API contract (HTTP)
All bodies are JSON. Files are sent as `{ name, size, data_b64 }`.

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/health` | | `{ ok: true }` |
| `GET /api/methods` | | `[{ id, name, description }]` |
| `POST /api/capacity` | `{ method, cover }` | `{ capacity_bytes, blocks, approximate }` |
| `POST /api/embed` | `{ method, cover, message }` | `{ file: {name, data_b64}, details, blocks }` |
| `POST /api/extract` | `{ method, stego }` | `{ message, details }` |
| `POST /api/pages` | `{ file }` | `{ pages: [png_b64, ...], total }` |

Errors come back as `{ error: "..." }` with HTTP 400.
