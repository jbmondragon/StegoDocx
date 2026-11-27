import base64
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from docx import Document
from docx.oxml.ns import qn

from algorithms.block_steganography import BlockSteganography as BlockStego
from algorithms.block_steganography_with_synchronization import BlockSteganography as BlockStegoSync
from algorithms.huffman_coder import HuffmanCoder
from algorithms.huffman_color_stego import HuffmanColorStego
from algorithms.huffman_color_with_synchronization import HuffmanColorStegoSync
from algorithms.invisible_character_stego import InvisibleCharacterStego
from algorithms.invisible_character_with_synchronization import InvisibleCharacterStegoSync
from algorithms.proposed_ablation import BlockStegoNoHamming, BlockStegoSyncNoHamming
from processing.docx_helpers import document_plain_text, iter_unique_paragraphs

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class Method:
    def __init__(self, id, name, description, cls, family, sync):
        self.id, self.name, self.description = id, name, description
        self.cls, self.family, self.sync = cls, family, sync

    def public(self):
        return {"id": self.id, "name": self.name, "description": self.description}


_METHOD_LIST = [
    Method("block_sync", "Proposed · Block-based, synchronized",
           "96-character blocks alternating RGB-LSB and zero-width channels; UTF-8 + 32-bit length header, "
           "Hamming(8,4), and CRC-16 frames that let extraction re-lock after edits.",
           BlockStegoSync, "block", True),
    Method("block", "Proposed · Block-based with Hamming(8,4)",
           "96-character blocks alternating RGB-LSB and zero-width channels; UTF-8 + 32-bit length header "
           "protected by Hamming(8,4). No synchronization frames.",
           BlockStego, "block", False),
    Method("huffman_sync", "Albano (2019) · Huffman + Color, synchronized",
           "Huffman code built from the cover text, written as 4-bit grayscale font colors, wrapped in "
           "CRC-8 frames with a length + CRC-16 header.",
           HuffmanColorStegoSync, "huffman", True),
    Method("huffman", "Albano (2019) · Huffman + Color",
           "Huffman code built from the cover text, written as 4-bit grayscale font colors (N, N, N).",
           HuffmanColorStego, "huffman", False),
    Method("invisible_sync", "Mustafa (2020) · Invisible Character, synchronized",
           "One bit per block of visible characters as a pair of U+200B markers, wrapped in CRC-8 frames.",
           InvisibleCharacterStegoSync, "invisible", True),
    Method("invisible", "Mustafa (2020) · Invisible Character",
           "One bit per block of visible characters as a pair of U+200B zero-width-space markers.",
           InvisibleCharacterStego, "invisible", False),
    Method("block_nohamming", "Ablation · Proposed without Hamming(8,4)",
           "The proposed block method with the Hamming(8,4) error correction removed.",
           BlockStegoNoHamming, "block", False),
    Method("block_sync_nohamming", "Ablation · Proposed without Hamming(8,4), synchronized",
           "The synchronized proposed method with the Hamming(8,4) error correction removed.",
           BlockStegoSyncNoHamming, "block", True),
]
METHODS = [m.public() for m in _METHOD_LIST]
_BY_ID = {m.id: m for m in _METHOD_LIST}


def _method(method_id):
    try:
        return _BY_ID[method_id]
    except KeyError:
        raise ValueError(f"Unknown method: {method_id!r}") from None


def _carrier_count(m, document):
    if m.family == "block":
        return len(m.cls._visible_runs(document))
    if m.family == "invisible":
        return sum(1 for ch, _ in m.cls._cover_char_runs(document) if ch != m.cls.INVISIBLE)
    text = (m.cls._plain_text(document) if m.sync
            else "\n".join(p.text for p in iter_unique_paragraphs(document)))
    return sum(1 for ch in text if m.cls._is_embedding_character(ch))


def _block_payload_bits(m, n_bytes):
    prep = m.cls.preprocess_secret("a" * n_bytes)
    return len(prep["sync_binary"] if m.sync else prep["hamming_binary"])


def _block_capacity_bytes(m, carriers):
    available = m.cls.capacity_bits_for(carriers)
    if available == 0:
        return 0
    lo, hi = 0, max(1, available // 8)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _block_payload_bits(m, mid) <= available:
            lo = mid
        else:
            hi = mid - 1
    return lo if _block_payload_bits(m, lo) <= available else 0


def _invisible_capacity_bytes(m, carriers):
    max_bits = carriers // m.cls.BIT_BLOCK_LENGTH
    header_bytes = len(m.cls.FORMAT) + 4
    if not m.sync:
        return max(0, max_bits // 8 - header_bytes)
    frames = max_bits // m.cls.SYNC_FRAME_SIZE
    return max(0, frames * m.cls.SYNC_BLOCK_DATA_BITS // 8 - header_bytes)


def _huffman_capacity_bytes(m, carriers, cover_text):
    counts = Counter(cover_text)
    if not counts or carriers == 0:
        return 0
    codes = HuffmanCoder.build_codes_from_counts(counts)
    total = sum(counts.values())
    avg_bits = sum(counts[ch] * len(codes[ch]) for ch in counts) / total
    groups_available = carriers
    bits_available = (groups_available - 1) * 4
    if m.sync:
        frames = bits_available // m.cls.SYNC_FRAME_SIZE
        bits_available = frames * m.cls.SYNC_BLOCK_DATA_BITS - m.cls.HEADER_BITS
    return max(0, int(bits_available / max(avg_bits, 1e-9)))


def _block_map(m, carriers, used_bits):
    blocks = []
    position = 0
    for index in range(carriers // m.cls.BLOCK_SIZE):
        size = m.cls.RGB_BLOCK_BITS if index % 2 == 0 else m.cls.ZERO_WIDTH_BLOCK_BITS
        blocks.append({"capacity": size, "used": max(0, min(size, used_bits - position))})
        position += size
    return blocks


def method_capacity(method_id, cover_path):
    m = _method(method_id)
    document = Document(cover_path)
    carriers = _carrier_count(m, document)
    if m.family == "block":
        return _block_capacity_bytes(m, carriers), carriers // m.cls.BLOCK_SIZE, False
    if m.family == "invisible":
        return _invisible_capacity_bytes(m, carriers), None, False
    cover_text = (m.cls._plain_text(document) if m.sync else document_plain_text(document))
    return _huffman_capacity_bytes(m, carriers, cover_text), None, True


_HIDDEN = {"secret", "values", "records", "cover_text", "cover_path", "output_path"}
_LABELS = {
    "utf8_bits": "UTF-8 + header bits",
    "hamming_bits": "Hamming(8,4) bits",
    "sync_bits": "Framed bits",
    "used_bits": "Bits written",
    "capacity_bits": "Capacity (bits)",
    "rgb_lsb_blocks": "RGB-LSB blocks",
    "hamming_corrections": "Hamming corrections",
    "hamming_uncorrectable": "Hamming uncorrectable",
    "hamming_decoded_bits": "Hamming-decoded bits",
}


def _label(key):
    return _LABELS.get(key) or key.replace("_", " ").capitalize()


def _value(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if not items:
            return "none"
        text = ", ".join(str(item) for item in items[:16])
        return text + (f" … (+{len(items) - 16})" if len(items) > 16 else "")
    if value is None:
        return "—"
    return str(value)


def _details(result):
    return {_label(k): _value(v) for k, v in result.items() if k not in _HIDDEN}


def _block_embed_stats(m, carriers, message):
    prep = m.cls.preprocess_secret(message)
    bits = prep["sync_binary"] if m.sync else prep["hamming_binary"]
    blocks_used = m.cls.bit_location(len(bits) - 1)[0] // m.cls.BLOCK_SIZE + 1
    stats = {
        "utf8_bits": len(prep["utf8_binary"]),
        "hamming_bits": len(prep["hamming_binary"]),
    }
    if m.sync:
        stats["sync_bits"] = len(bits)
        stats["frames_embedded"] = len(bits) // m.cls.SYNC_FRAME_SIZE
    stats.update({
        "blocks_used": blocks_used,
        "total_blocks_available": carriers // m.cls.BLOCK_SIZE,
        "rgb_lsb_blocks": (blocks_used + 1) // 2,
        "zero_width_blocks": blocks_used // 2,
        "capacity_bits": m.cls.capacity_bits_for(carriers),
        "used_bits": len(bits),
    })
    return stats


def _invisible_embed_stats(m, document, carriers, message):
    bits = m.cls._payload_bits(message)
    block_len = carriers // len(bits)
    stats = {
        "cover_characters": carriers,
        "payload_bits": len(m.cls._payload_bytes(message)) * 8,
    }
    if m.sync:
        stats["framed_bits"] = len(bits)
        stats["frames_embedded"] = len(bits) // m.cls.SYNC_FRAME_SIZE
        stats["preexisting_markers_removed"] = sum(
            (node.text or "").count(m.cls.INVISIBLE)
            for node in document.element.body.iter(qn("w:t")))
    stats.update({
        "capacity_bits": carriers // m.cls.BIT_BLOCK_LENGTH,
        "block_length": block_len,
        "capacity_characters": carriers // block_len,
        "invisible_markers": 2 * len(bits),
    })
    return stats


def _huffman_embed_stats(m, document, carriers, message):
    cover_text = m.cls._plain_text(document) if m.sync else document_plain_text(document)
    prepared = m.cls.prepare_payload(message, cover_text)
    compressed_bits, final_bits, values, padding = prepared[0], prepared[-3], prepared[-2], prepared[-1]
    stats = {
        "cover_characters": carriers,
        "compressed_bits": len(compressed_bits),
    }
    if m.sync:
        stats["framed_bits"] = len(prepared[1])
        stats["frames_embedded"] = len(prepared[1]) // m.cls.SYNC_FRAME_SIZE
    stats.update({
        "payload_bits": len(final_bits),
        "color_groups": len(values),
        "padding_bits": padding,
        "capacity_bits": carriers * m.cls.GROUP_BITS,
    })
    return stats


def _embed_stats(m, cover_path, message):
    document = Document(cover_path)
    carriers = _carrier_count(m, document)
    if m.family == "block":
        return _block_embed_stats(m, carriers, message)
    if m.family == "invisible":
        return _invisible_embed_stats(m, Document(cover_path), carriers, message)
    return _huffman_embed_stats(m, document, carriers, message)


def method_embed(method_id, cover_path, message, out_path):
    m = _method(method_id)
    if not message:
        raise ValueError("Type a secret message first.")
    m.cls.embed_docx(cover_path, message, out_path)
    stats = _embed_stats(m, cover_path, message)

    size = len(message.encode("utf-8"))
    details = {"Method": m.name, "Secret": f"{len(message):,} chars · {size:,} bytes"}
    details.update(_details(stats))

    blocks = []
    if m.family == "block":
        carriers = len(m.cls._visible_runs(Document(cover_path)))
        blocks = _block_map(m, carriers, stats["used_bits"])
        if stats["capacity_bits"]:
            details["Capacity used"] = f"{stats['used_bits'] / stats['capacity_bits'] * 100:.1f}%"
    return details, blocks


def _status(result):
    if "complete" in result:
        if result["complete"]:
            return "Complete — every frame recovered"
        lost = result.get("lost_frames") or []
        return f"Partial — {len(lost)} frame(s) lost" if lost else "Partial — message not fully verified"
    return "Recovered"


def method_extract(method_id, stego_path):
    m = _method(method_id)
    if m.family == "invisible" and not m.sync:
        result = m.cls.extract_docx(stego_path, None)
    else:
        result = m.cls.extract_docx(stego_path)
    secret = result.get("secret", "")
    details = {"Method": m.name, "Status": _status(result),
               "Recovered": f"{len(secret):,} chars · {len(secret.encode('utf-8')):,} bytes"}
    details.update(_details(result))
    if "frames_expected" in result:
        details["Frames valid"] = _value(result["frames_expected"] - len(result["lost_frames"]))
    return secret, details


MAX_PREVIEW_PAGES = 30
PREVIEW_DPI = 90
_RENDER_LOCK = threading.Lock()
_RENDER_PROFILE = os.path.join(tempfile.gettempdir(), "stegodocx_lo_profile")


def method_pages(docx_path):
    try:
        import pymupdf
        from processing.psnr_utils import docx_to_pdf
    except ImportError as exc:
        raise RuntimeError(f"Page preview needs PyMuPDF, numpy and Pillow ({exc.name} is not installed).") from None
    with tempfile.TemporaryDirectory() as d:
        with _RENDER_LOCK:
            pdf_path = docx_to_pdf(docx_path, d, profile_dir=_RENDER_PROFILE)
        pdf = pymupdf.open(pdf_path)
        try:
            total = pdf.page_count
            pages = [base64.b64encode(pdf[i].get_pixmap(dpi=PREVIEW_DPI, alpha=False).tobytes("png")).decode()
                     for i in range(min(total, MAX_PREVIEW_PAGES))]
        finally:
            pdf.close()
    return pages, total


def _safe_name(name, fallback="file.docx"):
    name = os.path.basename(str(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name or fallback


def _to_temp(payload, workdir):
    if not isinstance(payload, dict) or "data_b64" not in payload:
        raise ValueError("No file was sent.")
    path = os.path.join(workdir, _safe_name(payload.get("name")))
    with open(path, "wb") as f:
        f.write(base64.b64decode(payload["data_b64"]))
    return path


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": str(e) or e.__class__.__name__}
    return wrapper


class Api:

    @_safe
    def list_methods(self):
        return METHODS

    @_safe
    def pages(self, req):
        with tempfile.TemporaryDirectory() as d:
            images, total = method_pages(_to_temp(req["file"], d))
        return {"pages": images, "total": total}

    @_safe
    def capacity(self, req):
        with tempfile.TemporaryDirectory() as d:
            cap, n, approximate = method_capacity(req["method"], _to_temp(req["cover"], d))
        return {"capacity_bytes": cap, "blocks": n, "approximate": approximate}

    @_safe
    def embed(self, req):
        m = _method(req["method"])
        with tempfile.TemporaryDirectory() as d:
            cover = _to_temp(req["cover"], d)
            stem = os.path.splitext(_safe_name(req["cover"].get("name")))[0]
            name = f"{stem}_{m.id}.docx"
            out = os.path.join(d, "out_" + name)
            t0 = time.perf_counter()
            details, blocks = method_embed(m.id, cover, req.get("message", ""), out)
            details = {**details, "Output file": name,
                       "Elapsed": f"{(time.perf_counter() - t0) * 1000:.0f} ms"}
            with open(out, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        return {"file": {"name": name, "data_b64": data}, "details": details, "blocks": blocks}

    @_safe
    def extract(self, req):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.perf_counter()
            msg, details = method_extract(req["method"], _to_temp(req["stego"], d))
            details = {**details, "Elapsed": f"{(time.perf_counter() - t0) * 1000:.0f} ms"}
        return {"message": msg, "details": details}


def run_desktop():
    import webview
    webview.create_window("StegoDocx", os.path.join(HERE, "index.html"),
                          js_api=Api(), width=1320, height=880, min_size=(900, 640))
    webview.start()


class _Handler(BaseHTTPRequestHandler):
    api = Api()
    actions = ("capacity", "embed", "extract", "pages")
    server_version = "StegoDocx"

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _reply(self, result):
        self._send(400 if isinstance(result, dict) and "error" in result else 200, result)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/health":
            self._send(200, {"ok": True})
        elif path == "/api/methods":
            self._reply(self.api.list_methods())
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        action = path[len("/api/"):] if path.startswith("/api/") else None
        if action not in self.actions:
            return self._send(404, {"error": f"Unknown action {action or path}"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            return self._send(413, {"error": "Upload is larger than 50 MB."})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "Request body is not valid JSON."})
        self._reply(getattr(self.api, action)(body))

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def run_web(host="127.0.0.1", port=5000):
    server = ThreadingHTTPServer((host, port), _Handler)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"StegoDocx running at http://{shown}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _arg(name, default):
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return default


if __name__ == "__main__":
    if "--web" in sys.argv:
        run_web(_arg("--host", os.environ.get("HOST", "127.0.0.1")),
                int(_arg("--port", os.environ.get("PORT", 5000))))
    else:
        run_desktop()
