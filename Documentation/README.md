# StegoDocx Documentation

How to run StegoDocx, use it, deploy it, and call its API. For how each steganographic method works, see the [main README](../README.md). For a step-by-step walkthrough with a link to every function, see [METHODS.md](METHODS.md).

## Project Layout

| Path | Purpose |
|---|---|
| `index.html` | The whole user interface, with Embed and Extract tabs. Self-contained: no build step or CDN. |
| `bridge.py` | Web server (standard-library `http.server`) and desktop-window glue that connect the UI to the algorithms |
| `main.py` | Entry point that starts the app |
| `algorithms/` | The steganographic methods: preprocessing, embedding and extraction |
| `processing/` | Helpers for reading .docx files, evaluation, and PDF rendering for previews |
| `Dockerfile`, `render.yaml` | Container image and Render blueprint for deployment |

## Requirements

- Python 3.10 or newer
- `python-docx`, which is required for embedding and extraction
- For the optional Pages preview: `numpy`, `Pillow`, `PyMuPDF`, and LibreOffice installed with `soffice` on the PATH
- For the optional desktop window: `pywebview`

## Running Locally

Install the dependencies:

```bash
pip install -r requirements.txt
```

Start the web app, then open http://localhost:5000:

```bash
python main.py
```

Make it reachable from other machines on the network, or change the port:

```bash
python main.py --host 0.0.0.0 --port 8080
```

The `HOST` and `PORT` environment variables work too. `python bridge.py --web` does the same as `python main.py`.

Run it as a desktop window instead of in the browser:

```bash
pip install pywebview
python main.py --desktop
```

The page needs the server running. If you open `index.html` straight from disk, it only shows a reminder to start the server.

## Using the App

### Embedding a Secret

1. Open the **Embed** tab.
2. Choose a cover document (.docx).
3. Pick a method. The default is the proposed block-based, synchronized method.
4. Type the secret message. The app shows the cover's capacity and how much of it the message uses.
5. Click **Embed** and download the stego document. It is saved as `<cover name>_<method id>.docx`.

### Extracting a Secret

1. Open the **Extract** tab.
2. Choose the stego document.
3. Pick the method that was used to embed it.
4. Click **Extract**. The recovered message is shown with details. For synchronized methods, the details show whether every frame was recovered.

### Method IDs

| ID | Method |
|---|---|
| `block_sync` | Proposed: block-based, synchronized (default) |
| `block` | Proposed: block-based with Hamming(8,4) |
| `huffman_sync` | Albano (2019): Huffman + colour, synchronized |
| `huffman` | Albano (2019): Huffman + colour |
| `invisible_sync` | Mustafa (2020): invisible characters, synchronized |
| `invisible` | Mustafa (2020): invisible characters |
| `block_nohamming` | Ablation: proposed without Hamming(8,4) |
| `block_sync_nohamming` | Ablation: proposed without Hamming(8,4), synchronized |

Huffman capacity depends on how well the message compresses against the cover, so it is shown as an estimate (≈) and doesn't block the Embed button. The algorithm makes the final capacity check.

### Pages Preview

The **Pages** panel shows the stego document's pages after an embed, or when a stego file is chosen on the Extract tab. It converts the .docx to PDF with LibreOffice and renders the pages with PyMuPDF, showing at most the first 30.

Without LibreOffice, numpy, Pillow and PyMuPDF, only this panel shows "Preview unavailable". The rest of the app still works. The first render is slower while LibreOffice sets up its profile.

## Deploying to Render

The repository includes a `Dockerfile` and a `render.yaml` blueprint. The image is Python 3.12 with headless LibreOffice and fonts, so the Pages preview works on Render too.

1. Push the repository to GitHub or GitLab.
2. In the Render dashboard, choose **New → Blueprint** and select the repository. Render reads `render.yaml` and creates a web service named `stegodocx`.
3. Wait for the build to finish, then open the service URL.

Instead of a blueprint, you can choose **New → Web Service**, select the repository, and set the runtime to **Docker**.

| Setting | Value |
|---|---|
| Runtime | Docker |
| Plan | Free (set in `render.yaml`) |
| Port | Set by Render through `PORT`. The image defaults to 10000. |
| Host | `0.0.0.0` (set in the image) |
| Health check | `/api/health` |
| Auto-deploy | On every push |

On the free plan, the service sleeps when idle, so the first request after a pause takes a while. The free plan also has 512 MB of RAM. Previews of very large documents may need a paid instance.

### Testing the Image Locally

```bash
docker build -t stegodocx .
docker run -p 10000:10000 stegodocx
```

Then open http://localhost:10000.

## Pointing the UI at Another Server

By default the UI calls the API on the same server, at `/api`. To use a different server, add this before the main script in `index.html`:

```html
<script>window.STEGO_CONFIG = { mode: 'http', apiBase: 'http://localhost:5000/api' };</script>
```

| `mode` | Behaviour |
|---|---|
| `auto` | Uses the desktop bridge when it is available, otherwise HTTP (default) |
| `pywebview` | Desktop window only |
| `http` | Web server only |

## HTTP API

All request and response bodies are JSON. Files are sent as `{ name, size, data_b64 }`, with the file contents base64-encoded. Uploads are limited to 50 MB.

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/health` | | `{ ok: true }` |
| `GET /api/methods` | | `[{ id, name, description }]` |
| `POST /api/capacity` | `{ method, cover }` | `{ capacity_bytes, blocks, approximate }` |
| `POST /api/embed` | `{ method, cover, message }` | `{ file: { name, data_b64 }, details, blocks }` |
| `POST /api/extract` | `{ method, stego }` | `{ message, details }` |
| `POST /api/pages` | `{ file }` | `{ pages: [png_b64, ...], total }` |

Errors come back as `{ error: "..." }` with HTTP 400. An upload over the size limit returns HTTP 413.

## Troubleshooting

| Problem | Fix |
|---|---|
| The page only says to start the server | Run `python main.py` and open the page through http://localhost:5000, not from disk |
| "Preview unavailable" | Install LibreOffice and run `pip install numpy Pillow PyMuPDF` |
| The cover is too small for the message | Use a longer cover document or a shorter message |
| Extraction fails or returns garbage | Make sure the same method is selected as the one used for embedding |
| The Render service is slow on first load | The free plan sleeps when idle, so wait for it to wake up |
