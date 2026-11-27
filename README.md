# StegoDocx Steganographic Methods

StegoDocx hides a secret text message inside a Word document (.docx) without visibly changing it. Each method has its own embedding and extraction procedure, and extraction needs only the stego document.

## Proposed Method: Block-Based Steganography

The cover text's visible characters are split into blocks of 96 characters. The blocks alternate between two hiding channels:

| Block | Channel | Bits per block |
|---|---|---|
| Odd (1st, 3rd, …) | RGB-LSB: the least significant bit of each character's red, green and blue font colour values | 288 (3 per character) |
| Even (2nd, 4th, …) | Zero-width: a U+200B zero-width space after a character means `1`, no marker means `0` | 96 (1 per character) |

The colour changes are at most one step per channel, so they can't be seen. The zero-width characters take up no space on the page.

### Preprocessing

1. **UTF-8 encoding.** The secret is converted to UTF-8 bits, so any language or symbol can be hidden.
2. **Length header.** A 32-bit header with the message length in bytes is placed in front.
3. **Hamming(8,4).** Every 4 bits become an 8-bit codeword. This corrects single-bit errors and detects double-bit errors in each codeword.

### Synchronized Variant

The synchronized variant adds a framing layer after Hamming encoding, so extraction can recover from edits made to the stego document:

| Field | Size |
|---|---|
| Sync marker (`0xA5`) | 8 bits |
| Frame index | 8 bits |
| Hamming-coded data, interleaved | 64 bits |
| CRC-16 checksum | 16 bits |

Interleaving spreads neighbouring bits across different codewords, so a burst of errors hits each codeword only once and Hamming can still correct it. When text has been inserted or deleted, extraction looks for the sync marker at nearby offsets and accepts a frame only when its CRC-16 matches. That lets it lock back on to the frame grid. Frames that can't be recovered are reported as lost, and the rest of the message is still returned.

## Albano (2019): Huffman Coding + Font Colour

1. A Huffman code is built from the character frequencies of the cover text.
2. The secret is compressed with that code. Characters missing from the cover are escaped and written as raw 21-bit Unicode values.
3. The compressed bits are split into 4-bit groups. Each group is written as the font colour of one cover character, using a near-black grey (N, N, N).

The extractor rebuilds the same Huffman tree from the stego text, so the tree never has to be sent. Capacity depends on how well the message compresses against the cover's code, so it can only be estimated before embedding.

**Synchronized variant:** the payload starts with a 24-bit length and a 16-bit CRC of the whole message, and is cut into frames of 0xA5 marker + 8-bit index + 64 data bits + CRC-8.

## Mustafa (2020): Invisible Characters

The cover's visible characters are split into equal blocks, one block per payload bit. Each block starts with a U+200B marker, and a second marker encodes the bit:

| Second marker position | Bit |
|---|---|
| 1 character after the block start | `0` |
| 2 characters after the block start | `1` |

The payload is a 4-byte format tag (`MSF1`), a 32-bit length and the UTF-8 secret. The extractor finds the block length by testing every candidate and keeping the one whose header decodes correctly. Each bit needs at least 3 visible characters.

**Synchronized variant:** the payload uses the tag `MSS1` and is cut into frames of 0xA5 marker + 8-bit index + 64 data bits + CRC-8.

## Ablation: Proposed Method Without Hamming(8,4)

These are the block-based method and its synchronized variant with Hamming(8,4) removed: the 4-bit groups are written as they are. They measure how much the error correction adds in robustness, and what it costs in capacity.

## Summary

| Method | Channel | Error handling | Capacity |
|---|---|---|---|
| Proposed, synchronized | RGB-LSB + zero-width blocks | Hamming(8,4), interleaving, CRC-16 frames | Exact |
| Proposed | RGB-LSB + zero-width blocks | Hamming(8,4) | Exact |
| Albano (2019), synchronized | 4-bit grey font colour | CRC-8 frames, message CRC-16 | Estimate |
| Albano (2019) | 4-bit grey font colour | None | Estimate |
| Mustafa (2020), synchronized | U+200B marker pairs | CRC-8 frames | Exact |
| Mustafa (2020) | U+200B marker pairs | None | Exact |
| Ablation, with or without sync | RGB-LSB + zero-width blocks | None, or CRC-16 frames | Exact |
