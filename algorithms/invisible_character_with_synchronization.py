
import os
from collections import Counter

from docx import Document
from docx.oxml.ns import qn

from algorithms.invisible_character_stego import InvisibleCharacterStego


class InvisibleCharacterStegoSync(InvisibleCharacterStego):

    name = "Invisible Character with Synchronization"

    FORMAT = b"MSS1"

    SYNC_MARKER = 0xA5
    SYNC_BLOCK_DATA_BITS = 64
    SYNC_CRC_BITS = 8
    SYNC_FRAME_SIZE = 8 + 8 + SYNC_BLOCK_DATA_BITS + SYNC_CRC_BITS

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
    def sync_deframe(cls, extracted_bitstream: str, marker: int = SYNC_MARKER,
                     expected_frames: int | None = None) -> tuple:
        frames = {}
        wrap_base = 0
        previous_id = None

        num_valid = 0
        num_rejected = 0
        num_resyncs = 0
        index = 0

        while index + cls.SYNC_FRAME_SIZE <= len(extracted_bitstream):
            frame = extracted_bitstream[index:index + cls.SYNC_FRAME_SIZE]

            marker_bits = frame[0:8]
            block_id_bits = frame[8:16]
            data = frame[16:16 + cls.SYNC_BLOCK_DATA_BITS]
            crc_received_bits = frame[16 + cls.SYNC_BLOCK_DATA_BITS:cls.SYNC_FRAME_SIZE]

            if int(marker_bits, 2) != marker:
                num_resyncs += 1
                index += 1
                continue

            if not cls.crc8_validate(
                marker_bits + block_id_bits + data, int(crc_received_bits, 2)
            ):
                num_rejected += 1
                index += cls.SYNC_FRAME_SIZE
                continue

            block_id = int(block_id_bits, 2)
            if previous_id is not None and block_id < previous_id:
                wrap_base += 256
            previous_id = block_id

            frames[wrap_base + block_id] = data
            num_valid += 1
            index += cls.SYNC_FRAME_SIZE

        if not frames:
            return "", num_valid, num_rejected, num_resyncs, []

        highest = max(max(frames), expected_frames - 1) if expected_frames else max(frames)
        lost = [i for i in range(highest + 1) if i not in frames]
        payload = "".join(
            frames.get(i, "0" * cls.SYNC_BLOCK_DATA_BITS)
            for i in range(highest + 1)
        )
        return payload, num_valid, num_rejected, num_resyncs, lost

    @classmethod
    def _payload_bits(cls, secret):
        return cls.sync_frame(super()._payload_bits(secret))

    @staticmethod
    def _flip_variants(bits, ambiguous_slots):
        variants = [bits]
        if len(ambiguous_slots) <= 2:
            for mask in range(1, 1 << len(ambiguous_slots)):
                variant = list(bits)
                for bit, slot in enumerate(ambiguous_slots):
                    if mask & (1 << bit) and slot < len(variant):
                        variant[slot] = "1" if variant[slot] == "0" else "0"
                variants.append("".join(variant))
        elif len(ambiguous_slots) <= 6:
            for slot in ambiguous_slots:
                if slot < len(bits):
                    variant = list(bits)
                    variant[slot] = "1" if variant[slot] == "0" else "0"
                    variants.append("".join(variant))
        return variants

    @classmethod
    def _estimate_block_length(cls, marker_offsets, cover_length):
        if len(marker_offsets) < 2:
            raise ValueError("Too few invisible markers to recover the block length.")

        anchors = []
        index = 0
        offsets = sorted(marker_offsets)
        while index < len(offsets):
            if index + 1 < len(offsets) and offsets[index + 1] - offsets[index] in (1, 2):
                anchors.append(offsets[index])
                index += 2
            else:
                index += 1

        gaps = [
            anchors[i + 1] - anchors[i]
            for i in range(len(anchors) - 1)
            if anchors[i + 1] - anchors[i] > 0
        ]
        if not gaps:
            raise ValueError("Could not recover the block length from marker spacing.")

        counted = Counter(gaps)
        top = max(counted.values())
        block_len = min(gap for gap, count in counted.items() if count == top)

        for candidate in sorted(counted):
            if candidate >= cls.BIT_BLOCK_LENGTH and all(
                gap % candidate == 0 for gap in gaps
            ):
                block_len = candidate
                break

        if block_len < cls.BIT_BLOCK_LENGTH:
            raise ValueError("Recovered block length is below the protocol minimum.")
        return block_len

    @classmethod
    def _recover_bits(cls, marker_offsets, block_len):
        offsets = sorted(marker_offsets)
        pairs = []
        index = 0
        while index < len(offsets):
            if index + 1 < len(offsets) and offsets[index + 1] - offsets[index] in (0, 1, 2, 3):
                pairs.append((offsets[index], offsets[index + 1]))
                index += 2
            else:
                index += 1

        if not pairs:
            raise ValueError("No complete invisible marker pairs were found.")

        first_slot = max(0, round(pairs[0][0] / block_len))
        slots = {first_slot: None}
        ambiguous_slots = set()
        strays = len(offsets) - 2 * len(pairs)
        previous_anchor = pairs[0][0]
        previous_slot = first_slot

        for pair_index, (anchor, value_offset) in enumerate(pairs):
            observed_delta = value_offset - anchor
            if pair_index + 1 < len(pairs):
                next_anchor = pairs[pair_index + 1][0]
                steps_to_next = max(1, round((next_anchor - anchor) / block_len))
                inferred_delta = (
                    steps_to_next * block_len - (next_anchor - value_offset)
                )
            else:
                inferred_delta = observed_delta
            delta = inferred_delta if inferred_delta in (1, 2) else observed_delta
            bit = "0" if delta in (0, 1) else "1"

            if pair_index == 0:
                slots[first_slot] = bit
                if pair_index + 1 < len(pairs):
                    next_gap = pairs[pair_index + 1][0] - anchor
                    if (round(next_gap / block_len) == 1
                            and next_gap != block_len
                            and abs(next_gap - block_len) <= max(2, block_len * 0.35)):
                        ambiguous_slots.add(first_slot)
                continue

            gap = anchor - previous_anchor
            steps = round(gap / block_len)
            if steps < 1 or abs(gap - steps * block_len) > max(2, block_len * 0.35):
                strays += 2
                continue
            slot = previous_slot + steps
            if slot in slots:
                if slots[slot] != bit:
                    slots[slot] = None
            else:
                slots[slot] = bit
            previous_anchor = anchor
            previous_slot = slot

            if pair_index + 1 < len(pairs):
                next_gap = pairs[pair_index + 1][0] - anchor
                if (round(next_gap / block_len) == 1
                        and next_gap != block_len
                        and abs(next_gap - block_len) <= max(2, block_len * 0.35)):
                    ambiguous_slots.add(slot)
            else:
                ambiguous_slots.add(slot)

        highest = max(slots)
        bits = []
        missing = []
        for slot in range(highest + 1):
            bit = slots.get(slot)
            if bit not in ("0", "1"):
                bits.append("0")
                missing.append(slot)
            else:
                bits.append(bit)

        ambiguous_slots.update(missing)
        sequential_bits = "".join(bits)
        absolute_bits, _, _ = cls._recover_bits_by_offsets(
            marker_offsets, block_len
        )
        return sequential_bits, missing, strays, sorted(ambiguous_slots), absolute_bits

    @classmethod
    def _recover_bits_by_offsets(cls, marker_offsets, block_len):
        slots = {}
        strays = 0
        for offset in marker_offsets:
            slot, remainder = divmod(offset, block_len)
            if remainder in (1, 2):
                slots.setdefault(slot, set()).add("1" if remainder == 2 else "0")
            elif remainder != 0:
                strays += 1
        if not slots:
            return "", [], strays

        bits = []
        missing = []
        for slot in range(max(slots) + 1):
            values = slots.get(slot, set())
            if len(values) != 1:
                bits.append("0")
                missing.append(slot)
            else:
                bits.append(next(iter(values)))
        return "".join(bits), missing, strays

    @classmethod
    def _recover_bits_by_grid(cls, marker_offsets, block_len, cover_length):
        marker_set = set(marker_offsets)
        marker_counts = Counter(marker_offsets)
        pairs = {}
        for offset in marker_set:
            deltas = set()
            if marker_counts[offset] > 1:
                deltas.add(0)
            for delta in (1, 2, 3):
                if offset + delta in marker_set:
                    deltas.add(delta)
            if deltas:
                pairs[offset] = deltas

        max_slots = cover_length // block_len
        search_radius = max(1, min(16, block_len // 3))
        bits_by_slot = {}
        missing = []
        ambiguous = set()
        phase = 0
        previous_phase = 0
        previous_slot = None

        for slot in range(max_slots):
            expected = slot * block_len + phase
            anchor = expected if expected in pairs else None
            if anchor is None:
                nearby = [
                    expected + shift
                    for shift in range(-search_radius, search_radius + 1)
                    if shift and expected + shift in pairs
                ]
                if nearby:
                    distance = min(abs(position - expected) for position in nearby)
                    closest = [p for p in nearby if abs(p - expected) == distance]
                    if len(closest) == 1:
                        anchor = closest[0]
                        previous_phase, phase = phase, anchor - slot * block_len
                        if previous_slot is not None and phase != previous_phase:
                            ambiguous.add(previous_slot)

            if anchor is None:
                missing.append(slot)
                continue

            deltas = pairs[anchor]
            delta = min(deltas, key=lambda value: (value not in (1, 2), value))
            bits_by_slot[slot] = "0" if delta in (0, 1) else "1"
            if len({"0" if value in (0, 1) else "1" for value in deltas}) > 1:
                ambiguous.add(slot)
            elif delta in (0, 3):
                ambiguous.add(slot)
            previous_slot = slot

        if not bits_by_slot:
            raise ValueError("No marker pairs matched the candidate carrier grid.")
        highest = max(bits_by_slot)
        bits = []
        for slot in range(highest + 1):
            bit = bits_by_slot.get(slot)
            if bit is None:
                if slot not in missing:
                    missing.append(slot)
                bits.append("0")
            else:
                bits.append(bit)
        used_markers = sum(2 for _ in bits_by_slot)
        strays = max(0, len(marker_offsets) - used_markers)
        return "".join(bits), missing, strays, sorted(ambiguous)

    @classmethod
    def _repair_frames_from_crc(cls, bits, ambiguous_slots):
        recovered = list(bits)
        ambiguous = set(ambiguous_slots)
        corrected = []
        frame_size = cls.SYNC_FRAME_SIZE
        protected_size = frame_size - cls.SYNC_CRC_BITS

        for start in range(0, len(recovered) - frame_size + 1, frame_size):
            frame = "".join(recovered[start:start + frame_size])
            if (int(frame[:8], 2) == cls.SYNC_MARKER
                    and cls.crc8_validate(frame[:protected_size],
                                          int(frame[protected_size:], 2))):
                continue

            variables = sorted(
                slot for slot in ambiguous if start <= slot < start + frame_size
            )
            if not variables or len(variables) > 16:
                continue

            valid_frames = []
            for mask in range(1 << len(variables)):
                candidate = list(frame)
                changes = 0
                for bit, slot in enumerate(variables):
                    if mask & (1 << bit):
                        index = slot - start
                        candidate[index] = "1" if candidate[index] == "0" else "0"
                        changes += 1
                candidate = "".join(candidate)
                if (int(candidate[:8], 2) == cls.SYNC_MARKER
                        and cls.crc8_validate(candidate[:protected_size],
                                              int(candidate[protected_size:], 2))):
                    valid_frames.append((changes, candidate))

            if valid_frames:
                fewest_changes = min(item[0] for item in valid_frames)
                best = [frame for changes, frame in valid_frames
                        if changes == fewest_changes]
                repaired = best[0]
                recovered[start:start + frame_size] = repaired
                corrected.extend(
                    slot for slot in variables
                    if recovered[slot] != bits[slot]
                )

        return "".join(recovered), corrected

    @classmethod
    def _prepare_cover(cls, document):
        for text_node in document.element.body.iter(qn("w:t")):
            if cls.INVISIBLE in (text_node.text or ""):
                text_node.text = text_node.text.replace(cls.INVISIBLE, "")

    @classmethod
    def extract_docx(cls, stego_path, secret_length=None):
        if not os.path.isfile(stego_path):
            raise FileNotFoundError(f"Stego document not found: {stego_path}")

        document = Document(stego_path)
        marker_offsets, cover_length = cls._read_marker_offsets(document)
        return cls.extract_from_offsets(marker_offsets, cover_length)

    @classmethod
    def extract_from_offsets(cls, marker_offsets, cover_length):
        if not marker_offsets:
            raise ValueError("No U+200B invisible markers were found in the DOCX.")

        header_bits = len(cls.FORMAT) * 8 + cls.LENGTH_HEADER_BITS
        candidates = []

        max_block_len = cover_length // cls.SYNC_FRAME_SIZE
        try:
            estimated_len = cls._estimate_block_length(marker_offsets, cover_length)
        except ValueError:
            estimated_len = None
        prioritized = []
        if estimated_len is not None:
            search_radius = max(16, min(64, estimated_len // 3))
            for distance in range(0, search_radius + 1):
                for candidate_len in (
                    estimated_len - distance,
                    estimated_len + distance,
                ):
                    if (cls.BIT_BLOCK_LENGTH <= candidate_len <= max_block_len
                            and candidate_len not in prioritized):
                        prioritized.append(candidate_len)
        candidate_lengths = prioritized
        if estimated_len is None:
            if max_block_len <= 128:
                candidate_lengths = list(
                    range(cls.BIT_BLOCK_LENGTH, max_block_len + 1)
                )
            else:
                raise ValueError(
                    "Could not estimate marker block spacing. Re-embed this "
                    "document after removing damaged or unrelated markers."
                )
        for block_len in candidate_lengths:
            (
                bits,
                missing_slots,
                stray_markers,
                ambiguous_slots,
                absolute_bits,
            ) = cls._recover_bits(marker_offsets, block_len)
            variants = cls._flip_variants(bits, ambiguous_slots)
            if absolute_bits and absolute_bits not in variants:
                variants.append(absolute_bits)
            variant_records = [(variant, missing_slots, stray_markers)
                               for variant in dict.fromkeys(variants)]

            try:
                grid_bits, grid_missing, grid_strays, grid_ambiguous = (
                    cls._recover_bits_by_grid(marker_offsets, block_len, cover_length)
                )
                expected_marker = format(cls.SYNC_MARKER, "08b")
                expected_format = "".join(f"{byte:08b}" for byte in cls.FORMAT)
                plausible_header = (
                    len(grid_bits) >= 48
                    and sum(a != b for a, b in zip(grid_bits[:8], expected_marker)) <= 3
                    and sum(a != b for a, b in zip(grid_bits[16:48], expected_format)) <= 8
                )
                if plausible_header:
                    repaired_grid_bits, crc_corrected = cls._repair_frames_from_crc(
                        grid_bits, grid_ambiguous
                    )
                else:
                    repaired_grid_bits, crc_corrected = grid_bits, []
                grid_missing = [slot for slot in grid_missing
                                if slot not in crc_corrected]
                grid_variants = [grid_bits, repaired_grid_bits]
                grid_variants += cls._flip_variants(grid_bits, grid_ambiguous)[1:]
                variant_records.extend(
                    (variant, grid_missing, grid_strays)
                    for variant in dict.fromkeys(grid_variants)
                )
            except ValueError:
                pass

            for variant_bits, variant_missing, variant_strays in variant_records:
                (
                    payload_bits,
                    frames_valid,
                    frames_rejected,
                    _,
                    lost_frames,
                ) = cls.sync_deframe(variant_bits)
                if (frames_valid == 0 or 0 in lost_frames
                        or len(payload_bits) < header_bits):
                    continue

                data = bytes(
                    int(payload_bits[index:index + 8].ljust(8, "0"), 2)
                    for index in range(0, len(payload_bits) - 7, 8)
                )
                if data[:len(cls.FORMAT)] != cls.FORMAT:
                    continue

                size_end = len(cls.FORMAT) + cls.LENGTH_HEADER_BITS // 8
                declared_size = int.from_bytes(data[len(cls.FORMAT):size_end], "big")
                expected_frames = (
                    header_bits + declared_size * 8 + cls.SYNC_BLOCK_DATA_BITS - 1
                ) // cls.SYNC_BLOCK_DATA_BITS
                if expected_frames > max(
                    1, (len(variant_bits) + cls.SYNC_FRAME_SIZE - 1)
                    // cls.SYNC_FRAME_SIZE
                ):
                    continue
                (
                    payload_bits,
                    frames_valid,
                    frames_rejected,
                    _,
                    lost_frames,
                ) = cls.sync_deframe(variant_bits, expected_frames=expected_frames)
                data = bytes(
                    int(payload_bits[index:index + 8].ljust(8, "0"), 2)
                    for index in range(0, len(payload_bits) - 7, 8)
                )
                payload = data[size_end:size_end + declared_size]
                if len(payload) < declared_size:
                    continue

                try:
                    secret = payload.decode("utf-8")
                    clean = True
                except UnicodeDecodeError:
                    secret = payload.decode("utf-8", errors="replace")
                    clean = False

                result = {
                    "secret": secret,
                    "frames_valid": frames_valid,
                    "frames_rejected": frames_rejected,
                    "lost_frames": lost_frames,
                    "missing_bit_slots": variant_missing,
                    "stray_markers": variant_strays,
                    "complete": (
                        not lost_frames and not frames_rejected
                        and not variant_missing and clean
                    ),
                }
                candidates.append(result)
                if (
                    clean
                    and frames_valid == expected_frames
                    and not frames_rejected
                    and not lost_frames
                    and not variant_missing
                ):
                    return result

        if not candidates:
            raise ValueError(
                "No valid synchronization frames were found. The document may "
                "not have been embedded with this method, or the invisible "
                "markers may be too badly damaged to recover."
            )

        def score(result):
            return (
                result["frames_valid"],
                -result["frames_rejected"],
                -len(result["lost_frames"]),
                -len(result["missing_bit_slots"]),
                -result["stray_markers"],
            )

        candidates.sort(key=score, reverse=True)
        best = candidates[0]
        if any(score(candidate) == score(best)
               and candidate["secret"] != best["secret"]
               for candidate in candidates[1:]):
            raise ValueError(
                "Several marker geometries produced different valid payloads; "
                "the synchronized message is ambiguous."
            )
        return best
