
import heapq
from collections import Counter
class HuffmanNode:
    __slots__ = ("frequency", "order", "character", "left", "right")
    def __init__(self, frequency, order, character=None, left=None, right=None, ):
        self.frequency = frequency
        self.order = order
        self.character = character
        self.left = left
        self.right = right

class HuffmanCoder:
    ESCAPE = object()
    UNICODE_BITS = 21

    @staticmethod
    def build_tree(cover_text: str):
        if not cover_text:
            raise ValueError("The cover text is empty.")

        return HuffmanCoder._tree_from_counts(Counter(cover_text), list(dict.fromkeys(cover_text)))

    @staticmethod
    def _tree_from_counts(frequencies, symbols):
        heap = []
        order = 0

        escape_node = HuffmanNode(frequency=1, order=order, character=HuffmanCoder.ESCAPE,)
        heapq.heappush(heap, (escape_node.frequency, escape_node.order,escape_node))
        order += 1

        for character in symbols:
            node = HuffmanNode(frequency=frequencies[character], order=order, character=character,)
            heapq.heappush(heap, (node.frequency, node.order, node))
            order += 1

        if len(heap) == 1:
            return heap[0][2]

        while len(heap) > 1:
            freq1, order1, left = heapq.heappop(heap)
            freq2, order2, right = heapq.heappop(heap)

            parent = HuffmanNode(frequency=freq1 + freq2, order=order, left=left, right=right,)
            order += 1
            heapq.heappush(heap, (parent.frequency, parent.order, parent))
        return heap[0][2]

    @classmethod
    def build_codes(cls, cover_text: str):
        return cls._codes_from_tree(cls.build_tree(cover_text))

    @classmethod
    def build_codes_from_counts(cls, frequencies):
        symbols = sorted((char for char, count in frequencies.items() if count > 0), key=ord)
        if not symbols:
            raise ValueError("The cover text is empty.")
        return cls._codes_from_tree(cls._tree_from_counts(frequencies, symbols))

    @classmethod
    def _codes_from_tree(cls, root):
        codes = {}

        def walk(node, prefix):
            if node.character is not None:
                codes[node.character] = prefix or "0"
                return

            walk(node.left, prefix + "0")
            walk(node.right, prefix + "1")

        walk(root, "")
        return codes

    @classmethod
    def encode(cls, secret: str, cover_text: str) -> str:
        if not secret:
            raise ValueError("Secret message must not be empty.")

        return cls.encode_with_codes(secret, cls.build_codes(cover_text))

    @classmethod
    def encode_with_codes(cls, secret: str, codes: dict) -> str:
        escape_code = codes[cls.ESCAPE]
        encoded = []
        for character in secret:
            if character in codes:
                encoded.append(codes[character])
            else:
                encoded.append(
                    escape_code
                    + f"{ord(character):0{cls.UNICODE_BITS}b}"
                )
        return "".join(encoded)

    @classmethod
    def decode(cls, bitstream: str, cover_text: str) -> str:
        if not bitstream:
            return ""
        return cls.decode_with_codes(bitstream, cls.build_codes(cover_text))

    @classmethod
    def decode_with_codes(cls, bitstream: str, codes: dict) -> str:
        if not bitstream:
            return ""

        reverse_codes = {code: character for character, code in codes.items()}
        current = []
        recovered = []

        index = 0
        while index < len(bitstream):
            bit = bitstream[index]
            if bit not in ("0", "1"):
                raise ValueError("Invalid bit in Huffman stream.")
            current.append(bit)
            code = "".join(current)

            if code in reverse_codes:
                character = reverse_codes[code]
                current.clear()

                if character is cls.ESCAPE:
                    codepoint_end = index + 1 + cls.UNICODE_BITS

                    if codepoint_end > len(bitstream):
                        raise ValueError("The extracted escape sequence is incomplete.")
                    codepoint_bits = bitstream[index + 1:codepoint_end]
                    codepoint = int(codepoint_bits, 2)

                    if codepoint > 0x10FFFF:
                        raise ValueError("Invalid Unicode code point.")
                    recovered.append(chr(codepoint))
                    index = codepoint_end - 1

                else:
                    recovered.append(character)
            index += 1

        if current:
            raise ValueError("The extracted Huffman stream ends in an incomplete code. " "The cover document may not be the original cover.")
        return "".join(recovered)
