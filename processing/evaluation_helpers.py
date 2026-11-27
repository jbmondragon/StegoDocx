
from collections import Counter

import numpy as np
from docx import Document
from PIL import Image

from processing.docx_helpers import iter_paragraphs


MUSTAFA_BIT_BLOCK_LENGTH = 3
HUFFMAN_COLOR_GROUP_BITS = 4


def _character_distribution(text: str) -> dict[str, float]:
    if not text:
        return {}
    counts = Counter(text)
    total = sum(counts.values())
    return {character: count / total for character, count in counts.items()}


def compare_character_distributions(cover_text: str, stego_text: str) -> dict:
    cover_dist = _character_distribution(cover_text)
    stego_dist = _character_distribution(stego_text)
    alphabet = set(cover_dist) | set(stego_dist)
    tvd = 0.5 * sum(
        abs(cover_dist.get(character, 0.0) - stego_dist.get(character, 0.0))
        for character in alphabet
    )
    added_characters = sum(1 for character in stego_dist if character not in cover_dist)

    return {
        "cover_chars": len(cover_text),
        "stego_chars": len(stego_text),
        "chars_added": len(stego_text) - len(cover_text),
        "alphabet_size": len(alphabet),
        "new_characters_in_alphabet": added_characters,
        "distribution_tvd": tvd,
        "distribution_similarity_pct": (1.0 - tvd) * 100.0,
    }


def calculate_embedding_capacity(cover_docx: str) -> dict:
    document = Document(cover_docx)
    cover_characters = sum(
        len(character)
        for paragraph in iter_paragraphs(document)
        for character in paragraph.text
    )
    capacity_bits = cover_characters // MUSTAFA_BIT_BLOCK_LENGTH

    return {
        "cover_characters": cover_characters,
        "capacity_bits": capacity_bits,
        "capacity_bytes": capacity_bits // 8,
    }


def calculate_huffman_color_capacity(cover_docx: str) -> dict:
    document = Document(cover_docx)
    embedding_positions = sum(
        1
        for paragraph in iter_paragraphs(document)
        for character in paragraph.text
        if not character.isspace()
    )
    capacity_bits = embedding_positions * HUFFMAN_COLOR_GROUP_BITS

    return {
        "embedding_positions": embedding_positions,
        "capacity_bits": capacity_bits,
        "capacity_bytes": capacity_bits // 8,
    }


def embed_and_evaluate_mustafa(cover_docx: str, secret: str,stego_docx: str, dpi: int = 150, grayscale: bool = False,save_diff_images_to: str = None) -> dict:

    from algorithms.invisible_character_stego import InvisibleCharacterStego
    from processing.psnr_utils import evaluate_imperceptibility

    capacity = calculate_embedding_capacity(cover_docx)
    embedding = InvisibleCharacterStego.embed_docx(cover_docx, secret, stego_docx,)
    evaluation = evaluate_imperceptibility(cover_docx, stego_docx, dpi=dpi, grayscale=grayscale, save_diff_images_to=save_diff_images_to,)

    return {
        "capacity": capacity,
        "embedding": embedding,
        "evaluation": evaluation,
    }


def embed_and_evaluate_huffman_color(cover_docx: str, secret: str, stego_docx: str, dpi: int = 150, grayscale: bool = False, save_diff_images_to: str = None) -> dict:
    from algorithms.huffman_color_stego import HuffmanColorStego
    from processing.psnr_utils import evaluate_imperceptibility

    capacity = calculate_huffman_color_capacity(cover_docx)
    embedding = HuffmanColorStego.embed_docx(cover_docx, secret, stego_docx)
    evaluation = evaluate_imperceptibility(
        cover_docx,
        stego_docx,
        dpi=dpi,
        grayscale=grayscale,
        save_diff_images_to=save_diff_images_to,
    )

    return {
        "capacity": capacity,
        "embedding": embedding,
        "evaluation": evaluation,
    }


def _load_image_as_array(path: str, grayscale: bool = False) -> np.ndarray:
    img = Image.open(path)
    img = img.convert("L") if grayscale else img.convert("RGB")
    return np.asarray(img, dtype=np.float64)

def _align_dimensions(a: np.ndarray, b: np.ndarray) -> tuple:
    height = min(a.shape[0], b.shape[0])
    width = min(a.shape[1], b.shape[1])
    return a[:height, :width, ...], b[:height, :width, ...]

def compute_psnr(cover_img_path: str, stego_img_path: str,grayscale: bool = False) -> dict:
    cover = _load_image_as_array(cover_img_path, grayscale=grayscale)
    stego = _load_image_as_array(stego_img_path, grayscale=grayscale)
    return psnr_from_arrays(cover, stego)


def psnr_from_arrays(cover: np.ndarray, stego: np.ndarray) -> dict:
    cover, stego = _align_dimensions(cover, stego)

    difference = np.abs(cover - stego)
    per_pixel_difference = (difference if difference.ndim == 2 else difference.max(axis=-1))
    differing_mask = per_pixel_difference > 0.5
    differing_pixels = int(np.count_nonzero(differing_mask))
    total_pixels = int(per_pixel_difference.size)
    max_abs_diff = float(difference.max()) if difference.size else 0.0
    mse = float(np.mean(difference ** 2))

    result = {
        "differing_pixels": differing_pixels,
        "total_pixels": total_pixels,
        "differing_pct": (differing_pixels / total_pixels * 100.0 if total_pixels else 0.0),
        "max_abs_diff": max_abs_diff,
        "mse": mse,
    }

    if mse == 0:
        result.update({
            "psnr_db": float("inf"),
            "note": "Images are pixel-identical",
        })
        return result

    result.update({
        "psnr_db": float(10 * np.log10((255.0 ** 2) / mse)),
        "note": "",
    })
    return result