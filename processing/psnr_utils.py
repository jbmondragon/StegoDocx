


import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pymupdf as fitz
from PIL import Image
from processing.evaluation_helpers import psnr_from_arrays


class PSNRError(Exception):
    pass


def _find_soffice() -> str:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        return soffice

    candidate_paths = [ r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",]

    for candidate in candidate_paths:
        if Path(candidate).is_file():
            return candidate

    raise PSNRError( "LibreOffice was not found. It is required to render .docx files ")

def docx_to_pdf(docx_path: str, out_dir: str, profile_dir: str = None) -> str:
    docx_path = str(Path(docx_path).resolve())
    out_dir = str(Path(out_dir).resolve())
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    soffice = _find_soffice()

    cmd = [soffice]
    if profile_dir:
        cmd.append(f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}")
    cmd += [
        "--headless",
        "--norestore",
        "--convert-to", "pdf",
        "--outdir", out_dir,
        docx_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise PSNRError(f"LibreOffice timed out converting {docx_path}") from exc

    if result.returncode != 0:
        raise PSNRError(f"LibreOffice conversion failed for {docx_path}:\n{result.stderr}")

    pdf_path = Path(out_dir) / (Path(docx_path).stem + ".pdf")
    if not pdf_path.exists():
        raise PSNRError(f"Expected PDF not found at {pdf_path}")
    return str(pdf_path)

def pdf_to_arrays(pdf_path: str, dpi: int = 150) -> list:
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    pages = []
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=mat, alpha=False)
            rows = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
            pages.append(rows[:, :pix.width * pix.n].reshape(pix.height, pix.width, pix.n)[..., :3].copy())
    finally:
        doc.close()
    return pages

def render_pages(docx_path: str, dpi: int = 150, profile_dir: str = None) -> list:
    work_dir = tempfile.mkdtemp(prefix="psnr_render_")
    try:
        return pdf_to_arrays(docx_to_pdf(docx_path, work_dir, profile_dir), dpi=dpi)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

def _pixels(page: np.ndarray, grayscale: bool) -> np.ndarray:
    if grayscale:
        return np.asarray(Image.fromarray(page).convert("L"), dtype=np.float64)
    return page.astype(np.float64)

def save_diff_image(cover_page: np.ndarray, stego_page: np.ndarray, out_path: str, amplify: float = 20.0) -> str:
    a = cover_page.astype(np.float64)
    b = stego_page.astype(np.float64)
    height = min(a.shape[0], b.shape[0])
    width = min(a.shape[1], b.shape[1])
    a = a[:height, :width, ...]
    b = b[:height, :width, ...]

    diff = np.abs(a - b) * amplify
    diff = np.clip(diff, 0, 255).astype(np.uint8)

    Image.fromarray(diff, mode="RGB").save(out_path)
    return out_path


def evaluate_imperceptibility(cover_docx: str, stego_docx: str, dpi: int = 150, grayscale: bool = False, save_diff_images_to: str = None,
                              cover_pages: list = None, profile_dir: str = None) -> dict:
    if cover_pages is None:
        cover_pages = render_pages(cover_docx, dpi=dpi, profile_dir=profile_dir)
    stego_pages = render_pages(stego_docx, dpi=dpi, profile_dir=profile_dir)

    if not cover_pages or not stego_pages:
        raise PSNRError("Rendering produced no pages for one of the documents.")

    n_pages = min(len(cover_pages), len(stego_pages))
    mismatch = len(cover_pages) != len(stego_pages)

    if save_diff_images_to:
        Path(save_diff_images_to).mkdir(parents=True, exist_ok=True)

    per_page = []
    for i in range(n_pages):
        result = psnr_from_arrays(_pixels(cover_pages[i], grayscale), _pixels(stego_pages[i], grayscale))

        diff_image_path = None
        if save_diff_images_to:
            diff_image_path = str(Path(save_diff_images_to) / f"diff_page{i + 1:03d}.png")
            save_diff_image(cover_pages[i], stego_pages[i], diff_image_path)

        per_page.append({"page": i + 1, "mse": result["mse"], "psnr_db": result["psnr_db"], "note": result["note"], "differing_pixels": result["differing_pixels"], "total_pixels": result["total_pixels"], "differing_pct": result["differing_pct"], "max_abs_diff": result["max_abs_diff"], "diff_image_path": diff_image_path,})

    finite_psnrs = [p["psnr_db"] for p in per_page if np.isfinite(p["psnr_db"])]
    avg_psnr = float(np.mean(finite_psnrs)) if finite_psnrs else float("inf")
    all_identical = all(p["differing_pixels"] == 0 for p in per_page)

    return {"per_page": per_page, "average_psnr_db": avg_psnr, "num_pages_compared": n_pages, "page_count_mismatch": mismatch, "cover_pages": len(cover_pages), "stego_pages": len(stego_pages), "all_pages_pixel_identical": all_identical,}