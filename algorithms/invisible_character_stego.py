

import os
from copy import deepcopy
from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run
from processing.docx_helpers import iter_unique_paragraphs, split_text_runs


class InvisibleCharacterStego:

    name = "Invisible Character (Mustafa 2020)"
    INVISIBLE = "\u200b"
    FORMAT = b"MSF1"
    LENGTH_HEADER_BITS = 32
    BIT_BLOCK_LENGTH = 3

    @staticmethod
    def _docx_character_runs(document):
        for paragraph in iter_unique_paragraphs(document):
            split_text_runs(paragraph)
            for run in paragraph.runs:
                if run.text and run.text != InvisibleCharacterStego.INVISIBLE:
                    for character in run.text:
                        yield character, run

    @classmethod
    def _cover_char_runs(cls, document):
        runs = []
        for character, run in cls._docx_character_runs(document):
            runs.append((character, run))
        return runs

    @classmethod
    def _insert_invisible_run(cls, paragraph, reference_run, before=True):
        cloned = deepcopy(reference_run._r)

        for child in list(cloned):
            if child.tag != qn("w:rPr"):
                cloned.remove(child)

        marker_run = Run(cloned, paragraph)
        marker_run.text = cls.INVISIBLE

        if before:
            reference_run._r.addprevious(marker_run._r)
        else:
            reference_run._r.addnext(marker_run._r)

    @classmethod
    def _insert_at_visible_offset(cls, char_runs, offset):
        if not char_runs:
            raise ValueError("Cannot insert into an empty DOCX.")

        if offset < 0 or offset > len(char_runs):
            raise ValueError(f"Invalid marker offset: {offset}")

        if offset == len(char_runs):
            _, reference_run = char_runs[-1]
            paragraph = reference_run._parent
            cls._insert_invisible_run(paragraph, reference_run, before=False)
        else:
            _, reference_run = char_runs[offset]
            paragraph = reference_run._parent
            cls._insert_invisible_run(paragraph, reference_run, before=True)

    @classmethod
    def _payload_bytes(cls, secret):
        secret_bytes = secret.encode("utf-8")
        if len(secret_bytes) > (1 << cls.LENGTH_HEADER_BITS) - 1:
            raise ValueError(
                f"Secret is too long for the {cls.LENGTH_HEADER_BITS}-bit length header."
            )
        return cls.FORMAT + len(secret_bytes).to_bytes(
            cls.LENGTH_HEADER_BITS // 8, "big"
        ) + secret_bytes

    @classmethod
    def _payload_bits(cls, secret):
        return "".join(f"{byte:08b}" for byte in cls._payload_bytes(secret))

    @classmethod
    def _marker_offsets_for_bits(cls, cover_length, bits):
        block_count = len(bits)
        block_len = cover_length // block_count
        if block_len < cls.BIT_BLOCK_LENGTH:
            raise ValueError(
                "The cover does not have enough visible characters for this "
                f"secret. {block_count} payload bits need at least "
                f"{block_count * cls.BIT_BLOCK_LENGTH} visible characters, but "
                f"the cover has {cover_length}.\n\n"
                "Use a longer cover or a shorter message."
            )

        offsets = []
        for bit_number, bit in enumerate(bits):
            block_start = bit_number * block_len
            offsets.extend((block_start, block_start + (2 if bit == "1" else 1)))
        return block_len, offsets

    @classmethod
    def _decode_payload(cls, marker_offsets, cover_length):
        marker_set = set(marker_offsets)
        header_bits = (len(cls.FORMAT) + 4) * 8

        max_block_len = cover_length // header_bits
        matches = []
        for block_len in range(cls.BIT_BLOCK_LENGTH, max_block_len + 1):
            header = cls._read_payload_bits(marker_set, block_len, header_bits)
            if header is None:
                continue

            header_bytes = bytes(
                int(header[index:index + 8], 2)
                for index in range(0, header_bits, 8)
            )
            if header_bytes[:len(cls.FORMAT)] != cls.FORMAT:
                continue

            payload_size = int.from_bytes(header_bytes[len(cls.FORMAT):], "big")
            bit_count = (len(cls.FORMAT) + 4 + payload_size) * 8
            if bit_count > cover_length // cls.BIT_BLOCK_LENGTH:
                continue
            if cover_length // bit_count != block_len:
                continue

            bits = cls._read_payload_bits(marker_set, block_len, bit_count)
            if bits is None:
                continue
            data = bytes(
                int(bits[index:index + 8], 2)
                for index in range(0, bit_count, 8)
            )
            payload = data[header_bits // 8:]
            try:
                secret = payload.decode("utf-8")
            except UnicodeDecodeError:
                continue
            matches.append((secret, block_len))

        unique_matches = list(dict.fromkeys(matches))
        if len(unique_matches) != 1:
            raise ValueError(
                "Could not identify one complete MSF1 payload from the marker "
                "positions. The DOCX may contain damaged or ambiguous markers."
            )
        return unique_matches[0]

    @staticmethod
    def _read_payload_bits(marker_set, block_len, bit_count):
        bits = []
        for bit_number in range(bit_count):
            block_start = bit_number * block_len
            if block_start not in marker_set:
                return None
            if block_start + 1 in marker_set:
                bits.append("0")
            elif block_start + 2 in marker_set:
                bits.append("1")
            else:
                return None
        return "".join(bits)

    @classmethod
    def _decode_legacy_payload(cls, marker_offsets, cover_text, secret_length):
        block_len = len(cover_text) // secret_length
        if block_len < 5:
            raise ValueError("The legacy Mustafa cover is too short to decode.")

        remaining = list(marker_offsets)
        secret = []
        for block_number in range(secret_length):
            start = block_number * block_len
            end = start + block_len
            candidates = [
                index for index, offset in enumerate(remaining)
                if start + 3 <= offset <= end - 2
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"Legacy Mustafa block {block_number + 1} has "
                    f"{len(candidates)} candidate markers."
                )
            candidate_offset = remaining.pop(candidates[0])
            candidate_index = candidate_offset - start - 1

            protocol = {
                0: 1, 1: 3, 2: 5,
                block_len - 1: -3,
                block_len - 3: -5,
                block_len: 0,
            }
            protocol_candidates = [
                (index, protocol[offset - start])
                for index, offset in enumerate(remaining)
                if start <= offset <= end and offset - start in protocol
            ]
            if len(protocol_candidates) != 1:
                raise ValueError(f"Legacy Mustafa block {block_number + 1} has no protocol marker.")
            protocol_index, delta = protocol_candidates[0]
            remaining.pop(protocol_index)
            value = ord(cover_text[start + candidate_index]) - delta
            if not 0 <= value <= 0x10FFFF:
                raise ValueError(f"Legacy Mustafa block {block_number + 1} is invalid.")
            secret.append(chr(value))
        return "".join(secret), block_len

    @classmethod
    def _visible_text(cls, document):
        chars = []
        for paragraph in iter_unique_paragraphs(document):
            split_text_runs(paragraph)
            for run in paragraph.runs:
                if not run.text:
                    continue
                for character in run.text:
                    if character != cls.INVISIBLE:
                        chars.append(character)
        return "".join(chars)

    @classmethod
    def embed_docx(cls, cover_path, secret, output_path):
        if not os.path.isfile(cover_path):
            raise FileNotFoundError(f"Cover document not found: {cover_path}")
        if not secret:
            raise ValueError("Secret message must not be empty.")

        document = Document(cover_path)
        cls._prepare_cover(document)
        char_runs = cls._cover_char_runs(document)
        cover_text = "".join(character for character, _ in char_runs)

        bits = cls._payload_bits(secret)
        _, insert_offsets = cls._marker_offsets_for_bits(len(cover_text), bits)

        for offset in sorted(insert_offsets, reverse=True):
            cls._insert_at_visible_offset(char_runs, offset)

        document.save(output_path)
        return {"output_path": output_path}

    @classmethod
    def _prepare_cover(cls, document):
        for text_node in document.element.body.iter(qn("w:t")):
            if cls.INVISIBLE in (text_node.text or ""):
                raise ValueError(
                    "The cover DOCX already contains U+200B zero-width spaces. "
                    "Remove existing invisible markers before embedding."
                )

    @classmethod
    def _read_marker_offsets(cls, document):
        visible_offset = 0
        marker_offsets = []

        for paragraph in iter_unique_paragraphs(document):
            for run in paragraph.runs:
                text = run.text or ""
                for character in text:
                    if character == cls.INVISIBLE:
                        marker_offsets.append(visible_offset)
                    else:
                        visible_offset += 1

        return marker_offsets, visible_offset

    @classmethod
    def extract_docx(cls, stego_path, secret_length):
        if not os.path.isfile(stego_path):
            raise FileNotFoundError(f"Stego document not found: {stego_path}")

        document = Document(stego_path)
        marker_offsets, cover_length = cls._read_marker_offsets(document)

        if not marker_offsets:
            raise ValueError(
                "No U+200B invisible markers were found in the DOCX."
            )

        secret = None
        try:
            secret, block_len = cls._decode_payload(marker_offsets, cover_length)
        except ValueError:
            pass

        if secret is None:
            cover_text = cls._visible_text(document)
            inferred_legacy_length = len(marker_offsets) // 2
            if secret_length is None or len(marker_offsets) not in (
                secret_length * 2, secret_length * 8,
                secret_length * 2 + 1,
            ):
                secret_length = inferred_legacy_length
            if len(marker_offsets) == secret_length * 8:
                block_len = len(cover_text) // (secret_length * 4)
                pairs = marker_offsets[::2]
                values = [
                    marker_offsets[index + 1] - pairs[index] - 3
                    for index in range(len(pairs))
                ]
                try:
                    secret = bytes(values).decode("utf-32-be")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError("The legacy Unicode Mustafa payload is invalid.") from exc
            elif len(marker_offsets) == secret_length * 2:
                secret, block_len = cls._decode_legacy_payload(
                    marker_offsets, cover_text, secret_length
                )
            elif len(marker_offsets) == secret_length * 2 + 1:
                recoveries = []
                for stray_index in range(len(marker_offsets)):
                    candidate_offsets = (
                        marker_offsets[:stray_index]
                        + marker_offsets[stray_index + 1:]
                    )
                    try:
                        recovered, candidate_block_len = cls._decode_legacy_payload(
                            candidate_offsets, cover_text, secret_length
                        )
                    except (ValueError, IndexError):
                        continue
                    recoveries.append((recovered, candidate_block_len))

                unique_recoveries = list(dict.fromkeys(recoveries))
                if len(unique_recoveries) == 1:
                    secret, block_len = unique_recoveries[0]
                else:
                    raise ValueError(
                        f"Found {len(marker_offsets)} markers for a {secret_length}-character "
                        "legacy payload, but could not identify one unambiguous extra marker."
                    )
            else:
                raise ValueError(
                    f"The DOCX contains {len(marker_offsets)} markers, but they "
                    "do not form a valid Mustafa payload. Re-embed the secret "
                    "with the current method."
                )

        return {"secret": secret}
