
import os
import random
import string
from collections import Counter
from itertools import combinations_with_replacement, product

from docx import Document
from docx.shared import RGBColor
from processing.docx_helpers import (
    iter_unique_paragraphs,
    normalize_runs_for_embedding,
    split_text_runs,
)
from algorithms.huffman_coder import HuffmanCoder


class HuffmanColorStegoSync:
    name = "Huffman + 4-bit Grayscale Color with Synchronization"

    GROUP_BITS = 4
    MIN_MARKER = 4
    MAX_MARKER = 15

    SYNC_MARKER = 0xA5
    SYNC_BLOCK_DATA_BITS = 64
    SYNC_CRC_BITS = 8
    SYNC_FRAME_SIZE = 8 + 8 + SYNC_BLOCK_DATA_BITS + SYNC_CRC_BITS

    LENGTH_HEADER_BITS = 24
    MESSAGE_CRC_BITS = 16
    HEADER_BITS = LENGTH_HEADER_BITS + MESSAGE_CRC_BITS

    MAX_STITCH_GROUPS = 2
    MAX_ALTERNATIVES = 16
    MAX_TREE_DELTA = 10
    MAX_DECODES = 20000

    @staticmethod
    def crc8(bitstring: str) -> int:
        crc = 0
        for bit_char in bitstring:
            bit = int(bit_char)
            msb = (crc >> 7) & 1
            crc = ((crc << 1) & 0xFF) ^ bit
            if msb == 1:
                crc = crc ^ 0x07
        return crc

    @classmethod
    def crc8_validate(cls, bitstring: str, crc_received: int) -> bool:
        return cls.crc8(bitstring) == crc_received

    @staticmethod
    def crc16(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
                crc &= 0xFFFF
        return crc

    @classmethod
    def sync_frame(cls, payload_bitstream: str, marker: int = SYNC_MARKER) -> str:
        framed = ""
        block_id = 0
        position = 0

        while position < len(payload_bitstream):
            data = payload_bitstream[position:position + cls.SYNC_BLOCK_DATA_BITS]
            if len(data) < cls.SYNC_BLOCK_DATA_BITS:
                data = data + "0" * (cls.SYNC_BLOCK_DATA_BITS - len(data))

            header = format(marker, "08b") + format(block_id % 256, "08b")
            crc_bits = format(cls.crc8(header + data), "08b")

            framed += header + data + crc_bits

            position += cls.SYNC_BLOCK_DATA_BITS
            block_id = (block_id + 1) % 256
        return framed

    @classmethod
    def _frame_data(cls, frame: str, expected_id=None, marker: int = SYNC_MARKER):
        if len(frame) != cls.SYNC_FRAME_SIZE or int(frame[0:8], 2) != marker:
            return None
        if expected_id is not None and int(frame[8:16], 2) != expected_id % 256:
            return None
        if not cls.crc8_validate(frame[:16 + cls.SYNC_BLOCK_DATA_BITS], int(frame[-cls.SYNC_CRC_BITS:], 2)):
            return None
        return frame[16:16 + cls.SYNC_BLOCK_DATA_BITS]

    @classmethod
    def _scan_frames(cls, bits: str) -> dict:
        found = {}
        previous, index = None, 0
        while index + cls.SYNC_FRAME_SIZE <= len(bits):
            data = cls._frame_data(bits[index:index + cls.SYNC_FRAME_SIZE])
            if data is None:
                index += 1
                continue
            block_id = int(bits[index + 8:index + 16], 2)
            plausible = [number for number in (index // cls.SYNC_FRAME_SIZE, index // cls.SYNC_FRAME_SIZE + 1)
                         if number % 256 == block_id]
            if previous is not None and (previous + 1) % 256 == block_id:
                plausible.insert(0, previous + 1)
            plausible = [number for number in plausible if previous is None or number > previous]
            if not plausible:
                index += 1
                continue
            previous = plausible[0]
            found[previous] = {"start": index, "data": [data], "stitched": False}
            index += cls.SYNC_FRAME_SIZE - cls.MAX_STITCH_GROUPS * cls.GROUP_BITS
        return found

    @classmethod
    def _stitch(cls, region: str, region_start: int, frame_id: int) -> list:
        deficit = cls.SYNC_FRAME_SIZE - len(region)
        if deficit <= 0 or deficit % cls.GROUP_BITS or deficit // cls.GROUP_BITS > cls.MAX_STITCH_GROUPS:
            return []
        groups = deficit // cls.GROUP_BITS
        positions = [p for p in range(len(region) + 1) if (region_start + p) % cls.GROUP_BITS == 0]
        candidates = []
        for gaps in combinations_with_replacement(positions, groups):
            for fills in product(range(16), repeat=groups):
                frame = region
                for gap, fill in sorted(zip(gaps, fills), reverse=True):
                    frame = frame[:gap] + format(fill, "04b") + frame[gap:]
                data = cls._frame_data(frame, frame_id)
                if data is not None and data not in candidates:
                    candidates.append(data)
                    if len(candidates) > cls.MAX_ALTERNATIVES:
                        return []
        return candidates

    @classmethod
    def sync_deframe(cls, bits: str, frame_count: int | None = None) -> dict:
        frames = cls._scan_frames(bits)
        if not frames:
            return frames
        for frame_id in sorted(frames):
            following = frames.get(frame_id + 1)
            if following is None:
                continue
            region = bits[frames[frame_id]["start"]:following["start"]]
            if len(region) < cls.SYNC_FRAME_SIZE:
                for data in cls._stitch(region, frames[frame_id]["start"], frame_id):
                    if data not in frames[frame_id]["data"]:
                        frames[frame_id]["data"].append(data)
        last = max(frames) if frame_count is None else frame_count - 1
        for frame_id in range(last + 1):
            if frame_id in frames:
                continue
            candidates = cls._stitch_candidates(bits, frames, frame_id, last)
            if candidates:
                start = cls._frame_start(frames, frame_id)
                frames[frame_id] = {"start": start, "data": candidates, "stitched": True}
        return frames

    @classmethod
    def _frame_start(cls, frames: dict, frame_id: int) -> int:
        earlier = [f for f in frames if f < frame_id]
        return (frames[max(earlier)]["start"] + cls.SYNC_FRAME_SIZE * (frame_id - max(earlier))
                if earlier else 0)

    @classmethod
    def _stitch_candidates(cls, bits: str, frames: dict, frame_id: int, last: int) -> list:
        start = cls._frame_start(frames, frame_id)
        later = [f for f in frames if f > frame_id]
        if later and min(later) == frame_id + 1:
            regions = [bits[start:frames[min(later)]["start"]]]
        elif not later and frame_id == last:
            regions = [bits[start:start + cls.SYNC_FRAME_SIZE - groups * cls.GROUP_BITS]
                       for groups in range(1, cls.MAX_STITCH_GROUPS + 1)]
        else:
            return []
        candidates = []
        for region in regions:
            for data in cls._stitch(region, start, frame_id):
                if data not in candidates:
                    candidates.append(data)
        return candidates

    @classmethod
    def _codes(cls, counts):
        return HuffmanCoder.build_codes_from_counts(counts)

    @classmethod
    def prepare_payload(cls, secret: str, cover_text: str):
        compressed_bits = HuffmanCoder.encode_with_codes(secret, cls._codes(Counter(cover_text)))

        max_length = (1 << cls.LENGTH_HEADER_BITS) - 1
        if len(compressed_bits) > max_length:
            raise ValueError(
                f"Compressed message is too long for the {cls.LENGTH_HEADER_BITS}-bit "
                f"length header ({len(compressed_bits)} > {max_length} bits)."
            )

        header = (format(len(compressed_bits), f"0{cls.LENGTH_HEADER_BITS}b")
                  + format(cls.crc16(secret.encode("utf-8")), f"0{cls.MESSAGE_CRC_BITS}b"))
        framed_bits = cls.sync_frame(header + compressed_bits)

        remainder = len(framed_bits) % cls.GROUP_BITS
        if remainder != 0:
            padding_count = cls.GROUP_BITS - remainder
            final_bits = framed_bits + ("0" * padding_count) + f"{padding_count:04b}"
        else:
            padding_count = 0
            marker = random.randint(cls.MIN_MARKER, cls.MAX_MARKER)
            final_bits = framed_bits + f"{marker:04b}"

        values = [
            int(final_bits[index:index + cls.GROUP_BITS], 2)
            for index in range(0, len(final_bits), cls.GROUP_BITS)
        ]
        return compressed_bits, framed_bits, final_bits, values, padding_count

    @staticmethod
    def _is_embedding_character(character: str) -> bool:
        return character != " " and not character.isspace()

    @classmethod
    def _plain_text(cls, document) -> str:
        return "\n".join(paragraph.text for paragraph in iter_unique_paragraphs(document))

    @classmethod
    def _character_runs(cls, document):
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
        cover_text = cls._plain_text(document)
        (
            compressed_bits,
            framed_bits,
            final_bits,
            values,
            padding_count,
        ) = cls.prepare_payload(secret, cover_text)

        available_positions = sum(
            1
            for character in cover_text
            if cls._is_embedding_character(character)
        )

        if len(values) > available_positions:
            raise ValueError(
                "Insufficient embedding capacity.\n\n"
                f"Required: {len(values)} characters "
                f"({len(final_bits)} payload bits including framing and marker)\n"
                f"Available: {available_positions} non-space characters\n\n"
                "Synchronization adds "
                f"{cls.SYNC_FRAME_SIZE - cls.SYNC_BLOCK_DATA_BITS} bits of overhead "
                f"per {cls.SYNC_BLOCK_DATA_BITS}-bit frame.\n"
                "Use a longer cover document or a shorter secret message."
            )

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

    @staticmethod
    def _payload_value(run):
        rgb = run.font.color.rgb
        if rgb is None:
            return None
        red, green, blue = rgb[0], rgb[1], rgb[2]
        return red if red == green == blue and 0 <= red <= 15 else None

    @classmethod
    def _read_document(cls, document):
        values, cells = [], []
        for character, run in cls._character_runs(document):
            if not character:
                continue
            value = cls._payload_value(run)
            if value is not None:
                values.append(value)
            cells.append((character, value is not None))
        colored = [index for index, (_, is_payload) in enumerate(cells) if is_payload]
        inserted = Counter()
        if colored:
            for character, is_payload in cells[colored[0]:colored[-1]]:
                if not is_payload and cls._is_embedding_character(character):
                    inserted[character] += 1
        return values, inserted

    @classmethod
    def _tree_candidates(cls, counts: Counter, inserted: Counter, exact: bool):
        base = counts - inserted
        yield base
        if exact:
            return
        if inserted:
            yield counts
        symbols = sorted(set(base) | set(string.printable), key=ord)
        for delta in range(1, cls.MAX_TREE_DELTA + 1):
            for symbol in symbols:
                for sign in (1, -1):
                    adjusted = base.copy()
                    adjusted[symbol] += sign * delta
                    if adjusted[symbol] < 0:
                        continue
                    yield +adjusted

    @classmethod
    def _decode_best_effort(cls, data_bits: str, codes: dict):
        for trimmed in range(0, min(len(data_bits), 64) + 1):
            candidate = data_bits[:len(data_bits) - trimmed] if trimmed else data_bits
            try:
                return HuffmanCoder.decode_with_codes(candidate, codes)
            except ValueError:
                continue
        raise ValueError(
            "The extracted Huffman stream could not be decoded. "
            "The cover document may not be the original cover."
        )

    @classmethod
    def extract_docx(cls, stego_path: str, cover_path: str | None = None):
        if not os.path.isfile(stego_path):
            raise FileNotFoundError(f"Stego document not found: {stego_path}")

        stego_document = Document(stego_path)
        stego_visible_text = cls._plain_text(stego_document)
        if cover_path:
            if not os.path.isfile(cover_path):
                raise FileNotFoundError(
                    f"Original cover document not found: {cover_path}"
                )
            cover_text = cls._plain_text(Document(cover_path))
        else:
            cover_text = stego_visible_text

        values, inserted = cls._read_document(stego_document)
        if cover_path:
            inserted = Counter()
        return cls.extract_from_values(values, Counter(cover_text), inserted, exact_tree=bool(cover_path))

    @classmethod
    def extract_from_values(cls, values: list, counts: Counter, inserted: Counter,
                            exact_tree: bool = False) -> dict:
        if not values:
            raise ValueError(
                "No embedded grayscale font colors in the range (0..15) "
                "were found in the document."
            )

        framed_bits = "".join(f"{value:04b}" for value in values)

        frames = cls.sync_deframe(framed_bits)
        if not frames:
            raise ValueError(
                "No valid synchronization frames were found in the extracted data."
            )

        trees = lambda: cls._tree_candidates(counts, inserted, exact=exact_tree)
        max_frames = len(framed_bits) // cls.SYNC_FRAME_SIZE + 1
        placeholder = "0" * cls.SYNC_BLOCK_DATA_BITS

        attempts = []
        for header in (frames[0]["data"] if 0 in frames else []):
            declared = int(header[:cls.LENGTH_HEADER_BITS], 2)
            needed = -(-(cls.HEADER_BITS + declared) // cls.SYNC_BLOCK_DATA_BITS)
            if needed > max_frames:
                continue
            header_frames = cls.sync_deframe(framed_bits, needed)
            header_frames[0] = {**frames[0], "data": [header]}
            attempts.append((header, declared, needed, header_frames))

        budget = {"left": cls.MAX_DECODES}

        def decode(options, declared, expected_crc):
            combinations = 1
            for choices in options:
                combinations *= len(choices)
            if combinations > 256:
                return None
            payloads = ["".join(choice)[cls.HEADER_BITS:cls.HEADER_BITS + declared]
                        for choice in product(*options)]
            seen = set()
            for counts_candidate in trees():
                if budget["left"] <= 0:
                    break
                codes = cls._codes(counts_candidate)
                key = frozenset((str(char), code) for char, code in codes.items())
                if key in seen:
                    continue
                seen.add(key)
                for data_bits in payloads:
                    budget["left"] -= 1
                    try:
                        found = HuffmanCoder.decode_with_codes(data_bits, codes)
                    except ValueError:
                        continue
                    if cls.crc16(found.encode("utf-8")) == expected_crc:
                        return found
            return None

        secret, verified = None, False
        chosen = attempts[0] if attempts else None
        for header, declared, needed, header_frames in attempts:
            if any(i not in header_frames for i in range(needed)):
                continue
            expected_crc = int(header[cls.LENGTH_HEADER_BITS:cls.HEADER_BITS], 2)
            options = [header_frames[i]["data"] for i in range(needed)]
            secret = decode(options, declared, expected_crc)
            if secret is None:
                for suspect in range(1, needed):
                    others = {i: f for i, f in header_frames.items() if i != suspect}
                    rebuilt = cls._stitch_candidates(framed_bits, others, suspect, needed - 1)
                    if not rebuilt:
                        continue
                    trial = list(options)
                    trial[suspect] = rebuilt + [d for d in options[suspect] if d not in rebuilt]
                    secret = decode(trial, declared, expected_crc)
                    if secret is not None:
                        header_frames = {**header_frames, suspect: {**header_frames[suspect], "stitched": True,
                                                                    "data": trial[suspect]}}
                        break
            if secret is not None:
                chosen, verified = (header, declared, needed, header_frames), True
                break

        if chosen is not None:
            header, declared_bits, frame_count, frames = chosen
        else:
            declared_bits, frame_count = None, max(frames) + 1
        lost_frames = [i for i in range(frame_count) if i not in frames]
        payload = "".join(frames[i]["data"][0] if i in frames else placeholder for i in range(frame_count))
        data_bits = payload[cls.HEADER_BITS:]
        truncated = False
        if declared_bits is not None:
            truncated = declared_bits > len(data_bits)
            data_bits = data_bits[:declared_bits]
        if secret is None:
            secret = cls._decode_best_effort(data_bits, cls._codes(next(trees())))

        return {
            "secret": secret,
            "frames_expected": frame_count,
            "lost_frames": lost_frames,
            "frames_stitched": sum(1 for frame in frames.values() if frame["stitched"]),
            "verified": verified,
            "truncated": truncated,
            "complete": verified and not truncated,
        }
