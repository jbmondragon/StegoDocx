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
    RGB_BITS_PER_CHAR = 3
    RGB_BLOCK_BITS = BLOCK_SIZE * RGB_BITS_PER_CHAR
    ZERO_WIDTH_BLOCK_BITS = BLOCK_SIZE
    PAIR_BITS = RGB_BLOCK_BITS + ZERO_WIDTH_BLOCK_BITS
    LENGTH_HEADER_BITS = 32
    INVISIBLE = "\u200b"


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

        if s8 == 1:
            if error_pos != 0:
                bits[error_pos - 1] ^= 1
            corrections = 1


        data_bits = [bits[2], bits[4], bits[5], bits[6]]
        return ''.join(str(bit) for bit in data_bits), corrections


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

        for i in range(0, len(bitstream), 8):
            codeword = bitstream[i:i+8]
            data_bits, corrections = cls.hamming_84_decode(codeword)
            decoded.append(data_bits)
            total_corrections += corrections

        return ''.join(decoded), total_corrections


    @classmethod
    def preprocess_secret(cls, secret: str) -> dict:
        raw_utf8 = cls.utf8_encode(secret)
        length_bytes = len(raw_utf8) // 8
        length_header = format(length_bytes, f'0{cls.LENGTH_HEADER_BITS}b')
        binary_utf8 = length_header + raw_utf8
        blocks_4bit = cls.segment_into_4bits(binary_utf8)
        hamming_encoded, _ = cls.hamming_encode_all_4bits(blocks_4bit)
        return {
            'utf8_binary': binary_utf8,
            'hamming_binary': hamming_encoded,
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
    def _read_bits(cls, visible: list, start: int, count: int) -> str:
        bits = []
        for position in range(start, start + count):
            carrier, channel = cls.bit_location(position)
            run = visible[carrier][1]
            if channel is None:
                bits.append('1' if cls._marker_follows(run) else '0')
            else:
                color = run.font.color.rgb
                bits.append('0' if color is None else str(int(color[channel]) & 1))
        return ''.join(bits)

    @classmethod
    def embed_docx(cls, cover_path: str, secret: str, output_path: str) -> dict:

        prep = cls.preprocess_secret(secret)
        payload_bits = prep['hamming_binary']

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
    def extract_docx(cls, stego_path: str) -> dict:
        document = Document(stego_path)
        visible = cls._visible_runs(document)
        capacity = cls.capacity_bits_for(len(visible))

        if capacity == 0:
            raise ValueError("No valid blocks found in stego document")

        header_coded = cls.HAMMING_CODE_BITS * cls.LENGTH_HEADER_BITS // cls.HAMMING_DATA_BITS
        if capacity < header_coded:
            raise ValueError("Extracted data is too short to contain the length header.")
        length_bits, corrections = cls.hamming_decode_all_8bits(cls._read_bits(visible, 0, header_coded))
        declared_bytes = int(length_bits, 2)
        data_groups = -(-(cls.LENGTH_HEADER_BITS + declared_bytes * 8) // cls.HAMMING_DATA_BITS)
        coded_bits = data_groups * cls.HAMMING_CODE_BITS

        if coded_bits > capacity:
            raise ValueError(
                "Declared secret length exceeds the amount of extracted data; "
                "the stego document may be corrupted or truncated."
            )

        extracted_bits = cls._read_bits(visible, 0, coded_bits)
        hamming_decoded, corrections = cls.hamming_decode_all_8bits(extracted_bits)
        binary_stream = hamming_decoded[cls.LENGTH_HEADER_BITS:cls.LENGTH_HEADER_BITS + declared_bytes * 8]

        try:
            secret = cls.utf8_decode(binary_stream)
        except Exception as e:
            raise ValueError(f"Cannot decode extracted data: {e}")

        return {"secret": secret, "hamming_corrections": corrections}
