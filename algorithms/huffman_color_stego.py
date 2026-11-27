
import os
import random

from docx import Document
from docx.shared import RGBColor
from processing.docx_helpers import (
    document_plain_text,
    iter_paragraphs,
    iter_unique_paragraphs,
    normalize_runs_for_embedding,
    split_text_runs,
)
from algorithms.huffman_coder import HuffmanCoder


class HuffmanColorStego:
    name = "Huffman + 4-bit Grayscale Color (Paper Method)"
    GROUP_BITS = 4
    MIN_MARKER = 4
    MAX_MARKER = 15

    @classmethod
    def prepare_payload(cls, secret: str, cover_text: str):
        compressed_bits = HuffmanCoder.encode(secret, cover_text)
        remainder = len(compressed_bits) % cls.GROUP_BITS

        if remainder != 0:
            padding_count = cls.GROUP_BITS - remainder
            final_bits = (
                compressed_bits
                + ("0" * padding_count)
                + f"{padding_count:04b}"
            )
        else:
            padding_count = 0
            marker = random.randint(cls.MIN_MARKER, cls.MAX_MARKER)
            final_bits = compressed_bits + f"{marker:04b}"

        values = [
            int(final_bits[index:index + cls.GROUP_BITS], 2)
            for index in range(0, len(final_bits), cls.GROUP_BITS)
        ]
        return compressed_bits, final_bits, values, padding_count

    @staticmethod
    def _is_embedding_character(character: str) -> bool:
        return character != " " and not character.isspace()

    @staticmethod
    def _character_runs(document):
        for paragraph in iter_unique_paragraphs(document):
            split_text_runs(paragraph)
            for run in paragraph.runs:
                if run.text:
                    yield run.text, run

    @classmethod
    def embed_docx(cls, cover_path: str, secret: str, output_path: str):
        if not os.path.isfile(cover_path):
            raise FileNotFoundError(f"Cover document not found: {cover_path}")
        if not secret:
            raise ValueError("Secret message must not be empty.")

        document = Document(cover_path)
        cover_text = document_plain_text(document)
        compressed_bits, final_bits, values, padding_count = cls.prepare_payload(
            secret, cover_text
        )
        available_positions = sum(
            1
            for paragraph in iter_unique_paragraphs(document)
            for character in paragraph.text
            if cls._is_embedding_character(character)
        )

        if len(values) > available_positions:
            raise ValueError(
                "Insufficient embedding capacity.\n\n"
                f"Required: {len(values)} characters "
                f"({len(final_bits)} payload bits including the marker)\n"
                f"Available: {available_positions} non-space characters\n\n"
                "Use a longer cover document or a shorter secret message."
            )

        for paragraph in iter_paragraphs(document):
            split_text_runs(paragraph)
        normalize_runs_for_embedding(document)

        embedded = 0
        for character, run in cls._character_runs(document):
            if embedded >= len(values):
                break
            if not cls._is_embedding_character(character):
                continue
            value = values[embedded]
            run.font.color.rgb = RGBColor(value, value, value)
            embedded += 1

        if embedded != len(values):
            raise ValueError(
                "The document changed while preparing the embedding and no "
                "longer has enough usable characters."
            )

        document.save(output_path)
        return {"output_path": output_path}

    @classmethod
    def _read_embedded_values(cls, document):
        values = []
        for paragraph in iter_unique_paragraphs(document):
            for run in paragraph.runs:
                text = run.text
                if not text:
                    continue
                rgb = run.font.color.rgb
                if rgb is None:
                    continue
                red, green, blue = rgb[0], rgb[1], rgb[2]
                if red == green == blue and 0 <= red <= 15:
                    values += [red] * sum(1 for character in text if cls._is_embedding_character(character))
        return values

    @classmethod
    def extract_docx(cls, stego_path: str, cover_path: str | None = None):
        if not os.path.isfile(stego_path):
            raise FileNotFoundError(f"Stego document not found: {stego_path}")

        stego_document = Document(stego_path)
        stego_visible_text = document_plain_text(stego_document)
        if cover_path:
            if not os.path.isfile(cover_path):
                raise FileNotFoundError(
                    f"Original cover document not found: {cover_path}"
                )
            cover_text = document_plain_text(Document(cover_path))
        else:
            cover_text = stego_visible_text

        values = cls._read_embedded_values(stego_document)
        if not values:
            raise ValueError(
                "No embedded grayscale font colors in the range (0..15) "
                "were found in the document."
            )

        reversed_values = list(reversed(values))
        while reversed_values and reversed_values[0] == 0:
            reversed_values.pop(0)
        if not reversed_values:
            raise ValueError("No non-black final marker was found.")

        values = list(reversed(reversed_values))
        bitstream_with_marker = "".join(f"{value:04b}" for value in values)
        if len(bitstream_with_marker) < 8:
            raise ValueError("The extracted payload is too short to contain the marker.")

        marker = int(bitstream_with_marker[-4:], 2)
        if marker == 0:
            raise ValueError("Invalid marker 0000.")

        data_bits = bitstream_with_marker[:-4]
        if marker < 4:
            if len(data_bits) < marker:
                raise ValueError("Invalid padding marker.")
            data_bits = data_bits[:-marker]

        secret = HuffmanCoder.decode(data_bits, cover_text)
        return {"secret": secret}
