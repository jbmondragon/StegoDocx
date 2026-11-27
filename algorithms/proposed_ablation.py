
from algorithms.block_steganography import BlockSteganography as BlockStego
from algorithms.block_steganography_with_synchronization import BlockSteganography as BlockStegoSync


class BlockStegoNoHamming(BlockStego):

    HAMMING_CODE_BITS = 4

    @classmethod
    def hamming_encode_all_4bits(cls, blocks_4bits: list) -> tuple:
        return ''.join(blocks_4bits), 0

    @classmethod
    def hamming_decode_all_8bits(cls, bitstream: str) -> tuple:
        return bitstream, 0


class BlockStegoSyncNoHamming(BlockStegoSync):

    HAMMING_CODE_BITS = 4

    @classmethod
    def hamming_encode_all_4bits(cls, blocks_4bits: list) -> tuple:
        return ''.join(blocks_4bits), 0

    @classmethod
    def hamming_decode_all_8bits(cls, bitstream: str) -> tuple:
        return bitstream, 0, 0

    @classmethod
    def interleave(cls, bits: str) -> str:
        return bits

    @classmethod
    def _declared_bytes(cls, header_data: str) -> int:
        return int(header_data[:cls.LENGTH_HEADER_BITS], 2)

    @classmethod
    def validate_frame(cls, frame_bits: str, slot: int, erased=frozenset()) -> tuple:
        if len(frame_bits) != cls.SYNC_FRAME_SIZE or erased:
            return None
        if frame_bits[:16] != cls._header_bits(slot):
            return None
        data_end = 16 + cls.SYNC_BLOCK_DATA_BITS
        data = frame_bits[16:data_end]
        if cls.crc16_validate(frame_bits[:16] + data, int(frame_bits[data_end:], 2)):
            return data, 0
        return None

    @classmethod
    def _stitch_deletion(cls, carriers: list, slot: int, shift_before: int, shift_after: int):
        return None
