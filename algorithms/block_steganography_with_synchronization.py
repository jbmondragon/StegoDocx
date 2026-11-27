from copy import deepcopy

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.run import Run

from processing.docx_helpers import iter_unique_paragraphs, split_text_runs


class BlockSteganography:

    BLOCK_SIZE = 96
    HAMMING_DATA_BITS = 4
    HAMMING_CODE_BITS = 8
    SYNC_MARKER = 0xA5
    SYNC_BLOCK_DATA_BITS = 64
    SYNC_CRC_BITS = 16
    SYNC_FRAME_SIZE = 8 + 8 + SYNC_BLOCK_DATA_BITS + SYNC_CRC_BITS
    RGB_BITS_PER_CHAR = 3
    RGB_BLOCK_BITS = 96 * RGB_BITS_PER_CHAR                  
    ZERO_WIDTH_BLOCK_BITS = 96                               
    PAIR_BITS = RGB_BLOCK_BITS + ZERO_WIDTH_BLOCK_BITS       
    LENGTH_HEADER_BITS = 32
    INVISIBLE = "\u200b"
    MAX_SHIFT = 128
    MAX_DELETION_SEARCH = 3 * HAMMING_CODE_BITS


    @staticmethod
    def utf8_encode(secret: str) -> str:
        binary_stream = ""
        for character in secret:
            utf8_bytes = character.encode('utf-8')
            for byte_val in utf8_bytes:
                binary_stream += format(byte_val, '08b')
        return binary_stream


    @staticmethod
    def utf8_decode(binary_stream: str) -> str:
        if len(binary_stream) % 8 != 0:
            raise ValueError("Binary stream length must be multiple of 8")
        byte_list = []
        for i in range(0, len(binary_stream), 8):
            byte_val = int(binary_stream[i:i+8], 2)
            byte_list.append(byte_val)

        return bytes(byte_list).decode('utf-8', errors='strict')


    @staticmethod
    def segment_into_4bits(binary_stream: str) -> list:
        remainder = len(binary_stream) % 4
        if remainder != 0:
            padding_bits = 4 - remainder
            binary_stream = binary_stream + ('0' * padding_bits)

        blocks = []

        for i in range(0, len(binary_stream), 4):
            blocks.append(binary_stream[i:i+4])
        return blocks


    @staticmethod
    def hamming_84_encode(data_4bits: str) -> str:
        if len(data_4bits) != 4:
            raise ValueError("Data must be exactly 4 bits")

        b1, b2, b3, b4 = [int(bit) for bit in data_4bits]


        p1 = b1 ^ b2 ^ b4
        p2 = b1 ^ b3 ^ b4
        p4 = b2 ^ b3 ^ b4
        p8 = p1 ^ p2 ^ p4 ^ b1 ^ b2 ^ b3 ^ b4


        codeword = [p1, p2, b1, p4, b2, b3, b4, p8]
        return ''.join(str(bit) for bit in codeword)


    @staticmethod
    def hamming_84_decode(codeword_8bits: str) -> tuple:
        if len(codeword_8bits) != 8:
            raise ValueError("Codeword must be exactly 8 bits")

        bits = [int(bit) for bit in codeword_8bits]

        s1 = bits[0] ^ bits[2] ^ bits[4] ^ bits[6]
        s2 = bits[1] ^ bits[2] ^ bits[5] ^ bits[6]
        s4 = bits[3] ^ bits[4] ^ bits[5] ^ bits[6]
        s8 = bits[7] ^ bits[0] ^ bits[1] ^ bits[2] ^ bits[3] ^ bits[4] ^ bits[5] ^ bits[6]

        error_pos = s1 * 1 + s2 * 2 + s4 * 4
        corrections = 0
        uncorrectable = 0

        if s8 == 1:
            if error_pos != 0:
                bits[error_pos - 1] ^= 1
            corrections = 1
        elif error_pos != 0:
            uncorrectable = 1

        data_bits = [bits[2], bits[4], bits[5], bits[6]]
        return ''.join(str(bit) for bit in data_bits), corrections, uncorrectable


    @classmethod
    def hamming_encode_all_4bits(cls, blocks_4bits: list) -> tuple:
        encoded = []
        for block in blocks_4bits:
            encoded.append(cls.hamming_84_encode(block))
        return ''.join(encoded), 0


    @classmethod
    def hamming_decode_all_8bits(cls, bitstream: str) -> tuple:
        if len(bitstream) % 8 != 0:
            raise ValueError("Bitstream length must be multiple of 8")

        decoded = []
        total_corrections = 0
        total_uncorrectable = 0

        for i in range(0, len(bitstream), 8):
            codeword = bitstream[i:i+8]
            data_bits, corrections, uncorrectable = cls.hamming_84_decode(codeword)
            decoded.append(data_bits)
            total_corrections += corrections
            total_uncorrectable += uncorrectable

        return ''.join(decoded), total_corrections, total_uncorrectable


    @staticmethod
    def crc16(bitstring: str) -> int:
        crc = 0xFFFF
        for bit_char in bitstring:
            msb = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if msb ^ int(bit_char):
                crc ^= 0x1021
        return crc


    @staticmethod
    def crc16_validate(bitstring: str, crc_received: int) -> bool:
        return BlockSteganography.crc16(bitstring) == crc_received


    @classmethod
    def sync_frame(cls, payload_bitstream: str, marker: int = SYNC_MARKER) -> str:
        framed = ""
        block_id = 0
        pos = 0

        while pos < len(payload_bitstream):

            data = payload_bitstream[pos:pos + cls.SYNC_BLOCK_DATA_BITS]
            if len(data) < cls.SYNC_BLOCK_DATA_BITS:
                data = data + '0' * (cls.SYNC_BLOCK_DATA_BITS - len(data))

            marker_bits = format(marker, '08b')
            block_id_bits = format(block_id % 256, '08b')
            header = marker_bits + block_id_bits

            crc_input = header + data
            crc_val = cls.crc16(crc_input)
            crc_bits = format(crc_val, f'0{cls.SYNC_CRC_BITS}b')

            frame = header + cls.interleave(data) + crc_bits
            framed += frame

            pos += cls.SYNC_BLOCK_DATA_BITS
            block_id = (block_id + 1) % 256
        return framed


    @classmethod
    def _header_bits(cls, slot: int, marker: int = SYNC_MARKER) -> str:
        return format(marker, '08b') + format(slot % 256, '08b')


    @classmethod
    def interleave(cls, bits: str) -> str:
        width = cls.HAMMING_CODE_BITS
        count = len(bits) // width
        return ''.join(bits[(t % count) * width + t // count] for t in range(len(bits)))


    @classmethod
    def _all_valid_codewords(cls, data: str) -> bool:
        valid = set(cls._all_codewords())
        width = cls.HAMMING_CODE_BITS
        return all(data[i:i + width] in valid for i in range(0, len(data), width))


    @classmethod
    def _codeword_values(cls) -> list:
        if not hasattr(cls, "_codeword_value_table"):
            cls._codeword_value_table = [int(word, 2) for word in cls._all_codewords()]
        return cls._codeword_value_table


    @classmethod
    def _all_codewords(cls) -> list:
        if not hasattr(cls, "_codeword_table"):
            cls._codeword_table = [cls.hamming_84_encode(format(value, "04b")) for value in range(16)]
        return cls._codeword_table


    @classmethod
    def validate_frame(cls, frame_bits: str, slot: int, erased=frozenset()) -> tuple:
        header = cls._header_bits(slot)
        if len(frame_bits) != cls.SYNC_FRAME_SIZE:
            return None
        header_errors = sum(frame_bits[i] != header[i] for i in range(16) if i not in erased)
        if header_errors > 1:
            return None

        data_end = 16 + cls.SYNC_BLOCK_DATA_BITS
        data = cls.interleave(frame_bits[16:data_end])
        if header_errors:
            if erased or not cls._all_valid_codewords(data):
                return None
            if cls.crc16_validate(header + data, int(frame_bits[data_end:], 2)):
                return data, 1
            return None
        if erased:
            return cls._fill_erasures(header, data, frame_bits[data_end:], erased)

        crc_received = int(frame_bits[data_end:], 2)
        if cls._all_valid_codewords(data):
            crc_errors = bin(cls.crc16(header + data) ^ crc_received).count("1")
            if crc_errors == 1:
                return data, 1
        if cls.crc16_validate(header + data, crc_received):
            if cls._all_valid_codewords(data):
                return data, 0
            return None

        decoded, corrections, uncorrectable = cls.hamming_decode_all_8bits(data)
        if corrections == 0 or uncorrectable:
            return None
        repaired, _ = cls.hamming_encode_all_4bits(cls.segment_into_4bits(decoded))
        if cls.crc16_validate(header + repaired, crc_received):
            return repaired, corrections
        return None


    @classmethod
    def _fill_erasures(cls, header: str, data: str, crc_bits: str, erased) -> tuple:
        width = cls.HAMMING_CODE_BITS
        count = cls.SYNC_BLOCK_DATA_BITS // width
        data_end = 16 + cls.SYNC_BLOCK_DATA_BITS
        missing = [set() for _ in range(count)]
        for position in erased:
            if 16 <= position < data_end:
                t = position - 16
                missing[t % count].add(t // count)

        codewords, filled, repaired_any = [], 0, False
        valid = cls._codeword_values()
        for index in range(count):
            if len(missing[index]) > 3:
                return None
            word = int(data[index * width:(index + 1) * width], 2)
            known = 0xFF
            for bit_index in missing[index]:
                known &= ~(1 << (width - 1 - bit_index))
            allowed_errors = 1 if len(missing[index]) <= 1 else 0
            completions = [value for value in valid
                           if bin((value ^ word) & known).count("1") <= allowed_errors]
            if len(completions) != 1:
                return None
            codewords.append(format(completions[0], "08b"))
            filled += len(missing[index])
            repaired_any = repaired_any or bool(missing[index]) or completions[0] != word

        repaired = "".join(codewords)
        known = [i for i in range(cls.SYNC_CRC_BITS) if data_end + i not in erased]
        if len(known) < cls.SYNC_CRC_BITS // 2 and repaired_any:
            return None
        expected = format(cls.crc16(header + repaired), f"0{cls.SYNC_CRC_BITS}b")
        if any(expected[i] != crc_bits[i] for i in known):
            return None
        return repaired, filled


    @classmethod
    def _shift_candidates(cls, around: int) -> list:
        candidates = [around]
        for distance in range(1, 2 * cls.MAX_SHIFT + 1):
            for shift in (around + distance, around - distance):
                if -cls.MAX_SHIFT <= shift <= cls.MAX_SHIFT:
                    candidates.append(shift)
        return candidates


    @classmethod
    def _frame_geometry(cls, slot: int) -> tuple:
        carrier, _ = cls.bit_location(slot * cls.SYNC_FRAME_SIZE)
        return carrier, 1


    @classmethod
    def _is_zero_width_frame(cls, slot: int) -> bool:
        return cls.bit_location(slot * cls.SYNC_FRAME_SIZE)[1] is None


    @classmethod
    def _read_frame_bits(cls, carriers: list, slot: int, shift_before: int,
                         shift_after: int = None, split: int = SYNC_FRAME_SIZE,
                         fill_before_start: bool = False):
        expected_header = cls._header_bits(slot)
        shifts = [shift_before if offset < split else shift_after
                  for offset in range(cls.SYNC_FRAME_SIZE)]
        bits = cls._frame_bits(carriers, slot, shifts)
        start = slot * cls.SYNC_FRAME_SIZE
        for offset, bit in enumerate(bits):
            if bit is None:
                carrier_index = cls.bit_location(start + offset)[0] + shifts[offset]
                if not (fill_before_start and carrier_index < 0):
                    return None
                bits[offset] = expected_header[offset] if offset < 16 else '0'
        return ''.join(bits)


    @classmethod
    def _frame_bits(cls, carriers: list, slot: int, shifts: list) -> list:

        start = slot * cls.SYNC_FRAME_SIZE
        return [cls._read_carrier_bit(carriers, start + offset, shift)
                for offset, shift in enumerate(shifts)]


    @classmethod
    def _lock_frame(cls, carriers: list, slot: int, around: int):
        header = cls._header_bits(slot)
        start = slot * cls.SYNC_FRAME_SIZE
        for shift in cls._shift_candidates(around):
            mismatches = 0
            for offset, expected in enumerate(header):
                if cls._read_carrier_bit(carriers, start + offset, shift) != expected:
                    mismatches += 1
                    if mismatches > 1:
                        break
            if mismatches > 1:
                continue
            frame_bits = cls._read_frame_bits(carriers, slot, shift)
            result = cls.validate_frame(frame_bits, slot) if frame_bits else None
            if result is not None:
                return result[0], shift, result[1]
        return None


    @classmethod
    def _read_frame_with_gap(cls, carriers: list, slot: int, shift_before: int, shift_after: int,
                             gap_start: int, gap_length: int):
        _, per_carrier = cls._frame_geometry(slot)
        gap_first = gap_start * per_carrier
        gap_end = (gap_start + gap_length) * per_carrier
        shifts = [shift_before if offset < gap_first else shift_after
                  for offset in range(cls.SYNC_FRAME_SIZE)]
        bits = cls._frame_bits(carriers, slot, shifts)
        erased = set()
        for offset in range(cls.SYNC_FRAME_SIZE):
            if gap_first <= offset < gap_end or bits[offset] is None:
                erased.add(offset)
                bits[offset] = '0'
        return ''.join(bits), frozenset(erased)


    @classmethod
    def _stitch_deletion(cls, carriers: list, slot: int, shift_before: int, shift_after: int):
        deleted = shift_before - shift_after
        width = cls.HAMMING_CODE_BITS
        if deleted > 3 * width:
            return None
        accepted = {}
        _, per_carrier = cls._frame_geometry(slot)
        frame_carriers = cls.SYNC_FRAME_SIZE // per_carrier
        zero_width = cls._is_zero_width_frame(slot)
        for gap_start in range(-deleted + 1, frame_carriers):
            first = max(gap_start, 0)
            length = min(gap_start + deleted, frame_carriers) - first
            frame_bits, erased = cls._read_frame_with_gap(
                carriers, slot, shift_before, shift_after, first, length)
            if zero_width and gap_start > 0:
                erased = erased | {gap_start - 1}
            result = cls.validate_frame(frame_bits, slot, erased)
            if result is not None:
                data, filled = result
                accepted[data] = min(filled, accepted.get(data, filled))
        if len(accepted) != 1:
            return None
        return next(iter(accepted.items()))


    @classmethod
    def _stitch_frame(cls, carriers: list, slot: int, shift_before: int, shift_after: int):
        if shift_before == shift_after:
            return None
        if shift_after < shift_before:
            result = cls._stitch_deletion(carriers, slot, shift_before, shift_after)
            if result is not None:
                return result
        accepted = {}
        seen = set()
        _, per_carrier = cls._frame_geometry(slot)
        size = cls.SYNC_FRAME_SIZE
        expected_header = cls._header_bits(slot)
        start = slot * size

        def read(shift):
            bits = cls._frame_bits(carriers, slot, [shift] * size)
            for offset, bit in enumerate(bits):
                if bit is None and cls.bit_location(start + offset)[0] + shift < 0:
                    bits[offset] = expected_header[offset] if offset < 16 else '0'
            return bits

        before, after = read(shift_before), read(shift_after)
        first_missing_before = next((i for i, bit in enumerate(before) if bit is None), size)
        last_missing_after = max((i for i, bit in enumerate(after) if bit is None), default=-1)
        for split in range(0, size + 1, per_carrier):
            if split > first_missing_before or split <= last_missing_after:
                continue
            frame_bits = ''.join(before[:split]) + ''.join(after[split:])
            if frame_bits in seen:
                continue
            seen.add(frame_bits)
            result = cls.validate_frame(frame_bits, slot)
            if result is not None:
                data, corrections = result
                accepted[data] = min(corrections, accepted.get(data, corrections))
        if len(accepted) != 1:
            return None
        data, corrections = next(iter(accepted.items()))
        return data, corrections


    @classmethod
    def sync_deframe(cls, carriers: list, slot_count: int) -> dict:
        frames = {}
        shift = 0
        slot = 0
        frame_count_known = False
        while slot < slot_count:
            locked = cls._lock_frame(carriers, slot, shift)
            if locked is not None:
                data, shift, corrections = locked
                frames[slot] = {"data": data, "shift": shift,
                                "corrections": corrections, "stitched": False}
                if slot == 0:
                    slot_count = min(slot_count, cls._frame_count(data))
                    frame_count_known = True
            slot += 1

        if frames:
            def first_carrier(s):
                return cls._frame_geometry(s)[0]

            def shift_before_of(slot):
                earlier = [s for s in frames
                           if first_carrier(s) < first_carrier(slot) and not frames[s]["stitched"]]
                return frames[max(earlier)]["shift"] if earlier else 0

            last = max(frames)
            for slot in range(last):
                if slot in frames:
                    continue
                later = [s for s in frames if first_carrier(s) > first_carrier(slot)]
                shift_before = shift_before_of(slot)
                if later:
                    shift_after = frames[min(later)]["shift"]
                    stitched = cls._stitch_frame(carriers, slot, shift_before, shift_after)
                    if stitched is not None:
                        stitched = (stitched[0], stitched[1], shift_after)
                elif frame_count_known:
                    stitched = cls._stitch_unknown_after(carriers, slot, shift_before)
                else:
                    stitched = None
                if stitched is not None:
                    frames[slot] = {"data": stitched[0], "shift": stitched[2],
                                    "corrections": stitched[1], "stitched": True}

            if frame_count_known:
                pending = [s for s in range(last + 1, slot_count) if s not in frames]
                options = {}

                def candidates(s):
                    if s not in options:
                        options[s] = cls._unknown_after_candidates(carriers, s, shift_before_of(s))
                    return options[s]

                def block_of(s):
                    return first_carrier(s) // cls.BLOCK_SIZE

                for slot in pending:
                    found = candidates(slot)
                    if len(found) > 1:
                        shared = {shift for other in pending
                                  if other != slot and block_of(other) == block_of(slot)
                                  for _, shift in candidates(other).values()}
                        found = {data: value for data, value in found.items() if value[1] in shared}
                    if len(found) != 1:
                        break
                    data, (corrections, shift_after) = next(iter(found.items()))
                    frames[slot] = {"data": data, "shift": shift_after,
                                    "corrections": corrections, "stitched": True}
        return frames


    @classmethod
    def _stitch_unknown_after(cls, carriers: list, slot: int, shift_before: int):
        found = cls._unknown_after_candidates(carriers, slot, shift_before)
        if len(found) != 1:
            return None
        data, (corrections, shift_after) = next(iter(found.items()))
        return data, corrections, shift_after


    @classmethod
    def _unknown_after_candidates(cls, carriers: list, slot: int, shift_before: int) -> dict:
        found = {}
        for delta in range(1, cls.MAX_SHIFT + 1):
            candidates = [shift_before + delta]
            if delta <= cls.MAX_DELETION_SEARCH:
                candidates.append(shift_before - delta)
            for shift_after in candidates:
                stitched = cls._stitch_frame(carriers, slot, shift_before, shift_after)
                if stitched is not None:
                    found.setdefault(stitched[0], (stitched[1], shift_after))
        return found


    @classmethod
    def preprocess_secret(cls, secret: str) -> dict:
        raw_utf8 = cls.utf8_encode(secret)
        length_bytes = len(raw_utf8) // 8
        length_header = format(length_bytes, f'0{cls.LENGTH_HEADER_BITS}b')
        binary_utf8 = length_header + raw_utf8
        blocks_4bit = cls.segment_into_4bits(binary_utf8)
        hamming_encoded, _ = cls.hamming_encode_all_4bits(blocks_4bit)
        sync_framed = cls.sync_frame(hamming_encoded)
        return {
            'utf8_binary': binary_utf8,
            'hamming_binary': hamming_encoded,
            'sync_binary': sync_framed,
        }


    @staticmethod
    def _visible_runs(document) -> list:
        visible = []
        for paragraph in iter_unique_paragraphs(document):
            split_text_runs(paragraph)
            for run in paragraph.runs:
                for character in run.text or "":
                    if character != BlockSteganography.INVISIBLE:
                        visible.append((character, run))
        return visible


    @classmethod
    def capacity_bits_for(cls, carrier_count: int) -> int:
        blocks = carrier_count // cls.BLOCK_SIZE
        return (blocks // 2) * cls.PAIR_BITS + (blocks % 2) * cls.RGB_BLOCK_BITS


    @classmethod
    def bit_location(cls, position: int) -> tuple:
        pair, offset = divmod(position, cls.PAIR_BITS)
        start = pair * 2 * cls.BLOCK_SIZE
        if offset < cls.RGB_BLOCK_BITS:
            channel, index = divmod(offset, cls.BLOCK_SIZE)
            return start + index, channel
        return start + cls.BLOCK_SIZE + offset - cls.RGB_BLOCK_BITS, None


    @classmethod
    def _insert_zero_width_marker(cls, run: Run) -> None:
        cloned = deepcopy(run._r)
        for child in list(cloned):
            if child.tag != qn("w:rPr"):
                cloned.remove(child)

        marker = Run(cloned, run._parent)
        marker.text = cls.INVISIBLE
        run._r.addnext(marker._r)


    @classmethod
    def _marker_follows(cls, run: Run) -> bool:
        next_element = run._r.getnext()
        if next_element is None or next_element.tag != qn("w:r"):
            return False
        return cls.INVISIBLE in (Run(next_element, run._parent).text or "")


    @classmethod
    def _write_bits(cls, visible: list, payload_bits: str) -> None:
        colors = {}
        for position, bit in enumerate(payload_bits):
            carrier, channel = cls.bit_location(position)
            run = visible[carrier][1]
            if channel is None:
                if bit == '1':
                    cls._insert_zero_width_marker(run)
                continue
            if carrier not in colors:
                color = run.font.color.rgb
                colors[carrier] = [0, 0, 0] if color is None else [int(c) for c in color]
            rgb = colors[carrier]
            rgb[channel] = (rgb[channel] & ~1) | int(bit)
        for carrier, rgb in colors.items():
            visible[carrier][1].font.color.rgb = RGBColor(*rgb)


    @classmethod
    def embed_docx(cls, cover_path: str, secret: str, output_path: str) -> dict:

        prep = cls.preprocess_secret(secret)
        payload_bits = prep['sync_binary']

        document = Document(cover_path)
        visible = cls._visible_runs(document)
        total_blocks = len(visible) // cls.BLOCK_SIZE

        total_capacity = cls.capacity_bits_for(len(visible))
        if len(payload_bits) > total_capacity:
            raise ValueError(
                f"Insufficient capacity: need {len(payload_bits)} bits, "
                f"but only {total_capacity} bits available "
                f"({total_blocks} blocks: RGB-LSB {cls.RGB_BLOCK_BITS} bits, "
                f"zero-width {cls.ZERO_WIDTH_BLOCK_BITS} bits)"
            )

        cls._write_bits(visible, payload_bits)
        document.save(output_path)
        return {"output_path": output_path}


    @classmethod
    def _carriers(cls, document) -> list:
        carriers = []
        for _, run in cls._visible_runs(document):
            color = run.font.color.rgb
            rgb = None if color is None else tuple(int(c) for c in color)
            carriers.append((rgb, cls._marker_follows(run)))
        return carriers


    @classmethod
    def _read_carrier_bit(cls, carriers: list, position: int, shift: int = 0):
        carrier, channel = cls.bit_location(position)
        carrier_index = carrier + shift
        if not 0 <= carrier_index < len(carriers):
            return None
        rgb, marker = carriers[carrier_index]
        if channel is None:
            return '1' if marker else '0'
        return '0' if rgb is None else str(rgb[channel] & 1)


    @classmethod
    def _declared_bytes(cls, header_data: str) -> int:
        length_bits, _, _ = cls.hamming_decode_all_8bits(header_data)
        return int(length_bits, 2)


    @classmethod
    def _frame_count(cls, header_data: str) -> int:
        coded_bits = (cls.LENGTH_HEADER_BITS + cls._declared_bytes(header_data) * 8 + 3) // 4 * cls.HAMMING_CODE_BITS
        return -(-coded_bits // cls.SYNC_BLOCK_DATA_BITS)


    @classmethod
    def extract_docx(cls, stego_path: str) -> dict:
        return cls.extract_from_carriers(cls._carriers(Document(stego_path)))


    @classmethod
    def extract_from_carriers(cls, carriers: list) -> dict:
        block_count = len(carriers) // cls.BLOCK_SIZE

        if block_count == 0:
            raise ValueError("No valid blocks found in stego document")

        slot_count = cls.capacity_bits_for(len(carriers)) // cls.SYNC_FRAME_SIZE
        frames = cls.sync_deframe(carriers, slot_count)

        if not frames:
            raise ValueError("No valid synchronization frames found in extracted data")

        header_recovered = 0 in frames
        declared_bytes = None
        if header_recovered:
            declared_bytes = cls._declared_bytes(frames[0]["data"])
            frame_count = cls._frame_count(frames[0]["data"])
            if frame_count > slot_count:
                raise ValueError(
                    "Declared secret length exceeds the capacity of the document; "
                    "the stego document may be corrupted or truncated."
                )
        else:
            frame_count = max(frames) + 1

        lost_frames = [slot for slot in range(frame_count) if slot not in frames]
        payload_bits = ''.join(
            frames[slot]["data"] if slot in frames else '0' * cls.SYNC_BLOCK_DATA_BITS
            for slot in range(frame_count)
        )
        binary_stream, corrections, uncorrectable = cls.hamming_decode_all_8bits(payload_bits)
        corrections += sum(frame["corrections"] for slot, frame in frames.items() if slot < frame_count)
        body = binary_stream[cls.LENGTH_HEADER_BITS:]

        if header_recovered:
            body = body[:declared_bytes * 8]
            secret_bytes = int(body, 2).to_bytes(len(body) // 8, "big") if body else b""
        else:
            usable = len(body) // 8 * 8
            secret_bytes = (int(body[:usable], 2).to_bytes(usable // 8, "big") if usable else b"").rstrip(b"\x00")

        secret = secret_bytes.decode("utf-8", errors="replace")
        in_range = [frame for slot, frame in frames.items() if slot < frame_count]

        return {
            "secret": secret,
            "frames_expected": frame_count,
            "lost_frames": lost_frames,
            "frames_stitched": sum(1 for frame in in_range if frame["stitched"]),
            "header_recovered": header_recovered,
            "hamming_corrections": corrections,
            "hamming_uncorrectable": uncorrectable,
            "complete": header_recovered and not lost_frames,
        }
