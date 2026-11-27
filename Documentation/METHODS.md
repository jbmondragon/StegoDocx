# Steganographic Methods: Process and Function Reference

This document explains, step by step, how each method hides a secret in a .docx file and how it gets it back. Each step names the function that does the work and links to its definition, so you can click through to the code.

| Method | Class | Source |
|---|---|---|
| Proposed: block-based with Hamming(8,4) | `BlockSteganography` | [block_steganography.py](../algorithms/block_steganography.py#L11) |
| Proposed: block-based, synchronized | `BlockSteganography` | [block_steganography_with_synchronization.py](../algorithms/block_steganography_with_synchronization.py#L11) |
| Albano (2019): Huffman + colour | `HuffmanColorStego` | [huffman_color_stego.py](../algorithms/huffman_color_stego.py#L17) |
| Albano (2019): Huffman + colour, synchronized | `HuffmanColorStegoSync` | [huffman_color_with_synchronization.py](../algorithms/huffman_color_with_synchronization.py#L18) |
| Mustafa (2020): invisible characters | `InvisibleCharacterStego` | [invisible_character_stego.py](../algorithms/invisible_character_stego.py#L11) |
| Mustafa (2020): invisible characters, synchronized | `InvisibleCharacterStegoSync` | [invisible_character_with_synchronization.py](../algorithms/invisible_character_with_synchronization.py#L11) |
| Ablation: proposed without Hamming(8,4) | `BlockStegoNoHamming`, `BlockStegoSyncNoHamming` | [proposed_ablation.py](../algorithms/proposed_ablation.py#L6) |
| Huffman coder used by Albano (2019) | `HuffmanCoder` | [huffman_coder.py](../algorithms/huffman_coder.py#L13) |

---

## 1. Shared Document Helpers

All methods read and write the document through these helpers in [processing/docx_helpers.py](../processing/docx_helpers.py).

| Step | What it does | Function |
|---|---|---|
| Walk the document | Yields every paragraph and table in body order | [`iter_block_items`](../processing/docx_helpers.py#L11) |
| Include tables | Yields body paragraphs and paragraphs inside table cells, recursively | [`iter_paragraphs`](../processing/docx_helpers.py#L26) |
| Skip duplicates | Yields each paragraph once, even when merged table cells repeat it | [`iter_unique_paragraphs`](../processing/docx_helpers.py#L35) |
| Get the plain text | Joins all paragraph text with newlines | [`document_plain_text`](../processing/docx_helpers.py#L42) |
| One run per character | Splits each run into one run per character, copying its formatting, so each character can be changed on its own | [`split_text_runs`](../processing/docx_helpers.py#L53) |
| Clear explicit colours | Removes existing font colours before the colour-based Huffman embedding | [`normalize_runs_for_embedding`](../processing/docx_helpers.py#L77) |

---

## 2. Proposed Method: Block-Based Steganography

Source: [algorithms/block_steganography.py](../algorithms/block_steganography.py)

### Idea

The visible characters of the cover are the carriers. They are split into blocks of 96 characters (`BLOCK_SIZE`), and the blocks alternate between two channels:

- **Odd blocks, RGB-LSB:** 3 bits per character, stored in the least significant bit of the red, green and blue font colour values. That is 288 bits per block.
- **Even blocks, zero-width:** 1 bit per character. A U+200B zero-width space after the character means `1`; no marker means `0`. That is 96 bits per block.

### Embedding Process

| # | Step | Function |
|---|---|---|
| 1 | Convert the secret to a UTF-8 bit string | [`utf8_encode`](../algorithms/block_steganography.py#L25) |
| 2 | Put a 32-bit length header (message length in bytes) in front, then run steps 3 and 4 | [`preprocess_secret`](../algorithms/block_steganography.py#L130) |
| 3 | Split the bits into 4-bit groups, padding the last group with zeros | [`segment_into_4bits`](../algorithms/block_steganography.py#L46) |
| 4 | Turn each 4-bit group into an 8-bit Hamming(8,4) codeword `[p1, p2, d1, p4, d2, d3, d4, p8]` | [`hamming_84_encode`](../algorithms/block_steganography.py#L59), [`hamming_encode_all_4bits`](../algorithms/block_steganography.py#L106) |
| 5 | Open the cover, split runs per character, and list the visible characters (U+200B excluded) with their runs | [`_visible_runs`](../algorithms/block_steganography.py#L143) |
| 6 | Work out the capacity: one RGB block plus one zero-width block hold 384 bits | [`capacity_bits_for`](../algorithms/block_steganography.py#L154) |
| 7 | Map each payload bit to a character and a channel (R, G, B, or zero-width) | [`bit_location`](../algorithms/block_steganography.py#L159) |
| 8 | Write the bits: set colour LSBs for RGB bits, and insert a U+200B run after the character for zero-width `1` bits | [`_write_bits`](../algorithms/block_steganography.py#L186), [`_insert_zero_width_marker`](../algorithms/block_steganography.py#L168) |
| 9 | Check capacity, write, and save the stego document | [`embed_docx`](../algorithms/block_steganography.py#L217) |

### Extraction Process

| # | Step | Function |
|---|---|---|
| 1 | List the visible characters of the stego document again | [`_visible_runs`](../algorithms/block_steganography.py#L143) |
| 2 | Read the first 64 coded bits, which hold the 32-bit length header after Hamming encoding | [`_read_bits`](../algorithms/block_steganography.py#L204) |
| 3 | Read each bit from a colour LSB, or from whether a U+200B marker follows the character | [`bit_location`](../algorithms/block_steganography.py#L159), [`_marker_follows`](../algorithms/block_steganography.py#L179) |
| 4 | Hamming-decode the header to get the message length, then read exactly as many coded bits as that length needs | [`hamming_decode_all_8bits`](../algorithms/block_steganography.py#L113) |
| 5 | Correct single-bit errors in each codeword using the syndrome bits s1, s2, s4 and overall parity p8 | [`hamming_84_decode`](../algorithms/block_steganography.py#L76) |
| 6 | Remove the header and decode the UTF-8 bytes back into text | [`utf8_decode`](../algorithms/block_steganography.py#L34) |
| 7 | Run the whole procedure and return the secret with the number of corrections | [`extract_docx`](../algorithms/block_steganography.py#L240) |

---

## 3. Proposed Method, Synchronized

Source: [algorithms/block_steganography_with_synchronization.py](../algorithms/block_steganography_with_synchronization.py)

### Idea

This uses the same carriers and Hamming(8,4) coding as Section 2, with an extra framing layer so the message survives edits that shift the text. Each frame is 96 bits:

| Sync marker `0xA5` | Frame index | Interleaved Hamming data | CRC-16 |
|---|---|---|---|
| 8 bits | 8 bits | 64 bits | 16 bits |

### Embedding Process

| # | Step | Function |
|---|---|---|
| 1 | UTF-8 encode, add the 32-bit length header, split into 4 bits, Hamming encode (as in Section 2) | [`utf8_encode`](../algorithms/block_steganography_with_synchronization.py#L31), [`segment_into_4bits`](../algorithms/block_steganography_with_synchronization.py#L53), [`hamming_84_encode`](../algorithms/block_steganography_with_synchronization.py#L67), [`hamming_encode_all_4bits`](../algorithms/block_steganography_with_synchronization.py#L112) |
| 2 | Cut the Hamming stream into 64-bit frames, each with the 0xA5 marker, frame index and CRC-16 | [`sync_frame`](../algorithms/block_steganography_with_synchronization.py#L155) |
| 3 | Build the 16-bit frame header | [`_header_bits`](../algorithms/block_steganography_with_synchronization.py#L183) |
| 4 | Compute the CRC-16 over the header and data, before interleaving | [`crc16`](../algorithms/block_steganography_with_synchronization.py#L139) |
| 5 | Interleave the 64 data bits so neighbouring bits land in different codewords. A burst of errors then hits each codeword only once. | [`interleave`](../algorithms/block_steganography_with_synchronization.py#L188) |
| 6 | Combine all preprocessing steps | [`preprocess_secret`](../algorithms/block_steganography_with_synchronization.py#L541) |
| 7 | Map the framed bits onto the RGB and zero-width blocks and write them | [`capacity_bits_for`](../algorithms/block_steganography_with_synchronization.py#L569), [`bit_location`](../algorithms/block_steganography_with_synchronization.py#L575), [`_write_bits`](../algorithms/block_steganography_with_synchronization.py#L605), [`_insert_zero_width_marker`](../algorithms/block_steganography_with_synchronization.py#L585) |
| 8 | Check capacity and save | [`embed_docx`](../algorithms/block_steganography_with_synchronization.py#L624) |

### Extraction Process

| # | Step | Function |
|---|---|---|
| 1 | Turn every visible character into a carrier: its RGB colour, and whether a U+200B follows it | [`_carriers`](../algorithms/block_steganography_with_synchronization.py#L648) |
| 2 | Read one bit at a given position, with an optional shift that accounts for inserted or deleted text | [`_read_carrier_bit`](../algorithms/block_steganography_with_synchronization.py#L658) |
| 3 | **Lock** each frame: try shifts close to the last good one (up to ±128 characters) and accept a header with at most 1 wrong bit | [`_lock_frame`](../algorithms/block_steganography_with_synchronization.py#L341), [`_shift_candidates`](../algorithms/block_steganography_with_synchronization.py#L294), [`_read_frame_bits`](../algorithms/block_steganography_with_synchronization.py#L315) |
| 4 | **Validate** a frame: de-interleave, check the codewords and CRC-16, and fix one Hamming or CRC bit error when the CRC confirms it | [`validate_frame`](../algorithms/block_steganography_with_synchronization.py#L216), [`crc16_validate`](../algorithms/block_steganography_with_synchronization.py#L150), [`_all_valid_codewords`](../algorithms/block_steganography_with_synchronization.py#L195) |
| 5 | **Stitch** frames that cross an edit: read the start with the shift before the edit and the end with the shift after it, then try every split point | [`_stitch_frame`](../algorithms/block_steganography_with_synchronization.py#L404) |
| 6 | Repair a frame where up to 24 characters were deleted: treat the missing bits as erasures and fill them from valid codewords plus the CRC | [`_stitch_deletion`](../algorithms/block_steganography_with_synchronization.py#L378), [`_read_frame_with_gap`](../algorithms/block_steganography_with_synchronization.py#L361), [`_fill_erasures`](../algorithms/block_steganography_with_synchronization.py#L255) |
| 7 | Recover trailing frames when no later frame gives a shift to work from | [`_stitch_unknown_after`](../algorithms/block_steganography_with_synchronization.py#L518), [`_unknown_after_candidates`](../algorithms/block_steganography_with_synchronization.py#L527) |
| 8 | Combine steps 3 to 7 over all frame slots | [`sync_deframe`](../algorithms/block_steganography_with_synchronization.py#L446) |
| 9 | Use frame 0 to find the message length and the number of frames | [`_declared_bytes`](../algorithms/block_steganography_with_synchronization.py#L670), [`_frame_count`](../algorithms/block_steganography_with_synchronization.py#L676) |
| 10 | Join the frame data (zeros for lost frames), Hamming-decode, and convert to UTF-8 | [`hamming_decode_all_8bits`](../algorithms/block_steganography_with_synchronization.py#L120), [`hamming_84_decode`](../algorithms/block_steganography_with_synchronization.py#L85) |
| 11 | Return the secret, lost frames, stitched frames, corrections, and whether the recovery is complete | [`extract_from_carriers`](../algorithms/block_steganography_with_synchronization.py#L687), [`extract_docx`](../algorithms/block_steganography_with_synchronization.py#L682) |

---

## 4. Albano (2019): Huffman Coding + Font Colour

Sources: [algorithms/huffman_color_stego.py](../algorithms/huffman_color_stego.py), [algorithms/huffman_coder.py](../algorithms/huffman_coder.py)

### Idea

The secret is compressed with a Huffman code built from the cover's own character frequencies. The compressed bits are written 4 at a time as a grey font colour (N, N, N) with N from 0 to 15, one value per non-space character. The tree is rebuilt from the stego text, so it never has to be sent.

### Embedding Process

| # | Step | Function |
|---|---|---|
| 1 | Get the cover's plain text | [`document_plain_text`](../processing/docx_helpers.py#L42) |
| 2 | Build the Huffman tree from the cover's character frequencies, with an extra ESCAPE symbol | [`build_tree`](../algorithms/huffman_coder.py#L18), [`_tree_from_counts`](../algorithms/huffman_coder.py#L25) |
| 3 | Walk the tree to get each character's code | [`build_codes`](../algorithms/huffman_coder.py#L51), [`_codes_from_tree`](../algorithms/huffman_coder.py#L62) |
| 4 | Encode the secret. Characters missing from the cover are written as ESCAPE plus a 21-bit Unicode code point. | [`encode`](../algorithms/huffman_coder.py#L77), [`encode_with_codes`](../algorithms/huffman_coder.py#L84) |
| 5 | Pad to a multiple of 4 bits and add a 4-bit end marker: the padding count (1 to 3), or a random value from 4 to 15 when there is no padding | [`prepare_payload`](../algorithms/huffman_color_stego.py#L24) |
| 6 | Count usable carriers, meaning characters that aren't whitespace | [`_is_embedding_character`](../algorithms/huffman_color_stego.py#L47) |
| 7 | Split runs per character and clear existing colours | [`split_text_runs`](../processing/docx_helpers.py#L53), [`normalize_runs_for_embedding`](../processing/docx_helpers.py#L77) |
| 8 | Write each 4-bit value as the colour `RGB(v, v, v)` of the next usable character, then save | [`_character_runs`](../algorithms/huffman_color_stego.py#L51), [`embed_docx`](../algorithms/huffman_color_stego.py#L59) |

### Extraction Process

| # | Step | Function |
|---|---|---|
| 1 | Collect the grey colour values from 0 to 15 of every usable character | [`_read_embedded_values`](../algorithms/huffman_color_stego.py#L110) |
| 2 | Drop the trailing zeros (unchanged black text), then read the last 4-bit group as the end marker | [`extract_docx`](../algorithms/huffman_color_stego.py#L126) |
| 3 | When the marker is below 4, remove that many padding bits | [`extract_docx`](../algorithms/huffman_color_stego.py#L126) |
| 4 | Rebuild the Huffman code from the stego text (or from the original cover, if one is given) and decode | [`decode`](../algorithms/huffman_coder.py#L98), [`decode_with_codes`](../algorithms/huffman_coder.py#L104) |

---

## 5. Albano (2019), Synchronized

Source: [algorithms/huffman_color_with_synchronization.py](../algorithms/huffman_color_with_synchronization.py)

### Idea

This uses the same Huffman and colour channel as Section 4. The compressed stream gets a header and is cut into CRC-8 frames, so extraction can find the message again after edits. Each frame is 88 bits:

| Sync marker `0xA5` | Frame index | Data | CRC-8 |
|---|---|---|---|
| 8 bits | 8 bits | 64 bits | 8 bits |

### Embedding Process

| # | Step | Function |
|---|---|---|
| 1 | Build the Huffman code from the sorted character counts, so it can be rebuilt the same way later | [`_codes`](../algorithms/huffman_color_with_synchronization.py#L186), [`build_codes_from_counts`](../algorithms/huffman_coder.py#L55) |
| 2 | Compress the secret | [`encode_with_codes`](../algorithms/huffman_coder.py#L84) |
| 3 | Put a header in front: a 24-bit compressed length and a CRC-16 of the original UTF-8 message | [`prepare_payload`](../algorithms/huffman_color_with_synchronization.py#L190), [`crc16`](../algorithms/huffman_color_with_synchronization.py#L55) |
| 4 | Cut into 64-bit frames, each with marker, index and CRC-8 | [`sync_frame`](../algorithms/huffman_color_with_synchronization.py#L65), [`crc8`](../algorithms/huffman_color_with_synchronization.py#L40) |
| 5 | Add padding and the end marker, and split into 4-bit values (as in Section 4) | [`prepare_payload`](../algorithms/huffman_color_with_synchronization.py#L190) |
| 6 | Write the values as grey font colours and save | [`_character_runs`](../algorithms/huffman_color_with_synchronization.py#L228), [`embed_docx`](../algorithms/huffman_color_with_synchronization.py#L236) |

### Extraction Process

| # | Step | Function |
|---|---|---|
| 1 | Read the grey values, and count non-coloured characters inserted inside the coloured range (typed after embedding) | [`_read_document`](../algorithms/huffman_color_with_synchronization.py#L300), [`_payload_value`](../algorithms/huffman_color_with_synchronization.py#L292) |
| 2 | Scan the bit stream one bit at a time for frames whose marker and CRC-8 are valid | [`_scan_frames`](../algorithms/huffman_color_with_synchronization.py#L95), [`_frame_data`](../algorithms/huffman_color_with_synchronization.py#L85), [`crc8_validate`](../algorithms/huffman_color_with_synchronization.py#L51) |
| 3 | Stitch damaged frames by filling up to two missing 4-bit groups and keeping the fills that pass the CRC | [`_stitch`](../algorithms/huffman_color_with_synchronization.py#L118), [`_stitch_candidates`](../algorithms/huffman_color_with_synchronization.py#L168), [`_frame_start`](../algorithms/huffman_color_with_synchronization.py#L162) |
| 4 | Combine scanning and stitching | [`sync_deframe`](../algorithms/huffman_color_with_synchronization.py#L138) |
| 5 | Try candidate Huffman trees: the stego text minus inserted characters, then small frequency changes | [`_tree_candidates`](../algorithms/huffman_color_with_synchronization.py#L318) |
| 6 | Decode each candidate and accept the one whose CRC-16 matches the header | [`extract_from_values`](../algorithms/huffman_color_with_synchronization.py#L370) |
| 7 | If nothing verifies, decode as much as possible | [`_decode_best_effort`](../algorithms/huffman_color_with_synchronization.py#L336) |
| 8 | Entry point | [`extract_docx`](../algorithms/huffman_color_with_synchronization.py#L349) |

---

## 6. Mustafa (2020): Invisible Characters

Source: [algorithms/invisible_character_stego.py](../algorithms/invisible_character_stego.py)

### Idea

The visible text is split into equal blocks, one block per payload bit. Each block gets two U+200B markers: one at the block start, and one 1 character after it for a `0` or 2 characters after it for a `1`. Each block must be at least 3 characters long.

### Embedding Process

| # | Step | Function |
|---|---|---|
| 1 | Refuse a cover that already contains U+200B | [`_prepare_cover`](../algorithms/invisible_character_stego.py#L244) |
| 2 | List each visible character with its run | [`_cover_char_runs`](../algorithms/invisible_character_stego.py#L29), [`_docx_character_runs`](../algorithms/invisible_character_stego.py#L20) |
| 3 | Build the payload: the `MSF1` tag, a 32-bit length, and the UTF-8 secret | [`_payload_bytes`](../algorithms/invisible_character_stego.py#L69), [`_payload_bits`](../algorithms/invisible_character_stego.py#L80) |
| 4 | Work out the block length (cover length divided by bit count) and the two marker positions per bit | [`_marker_offsets_for_bits`](../algorithms/invisible_character_stego.py#L84) |
| 5 | Insert the markers from last to first so earlier positions don't move | [`_insert_at_visible_offset`](../algorithms/invisible_character_stego.py#L52), [`_insert_invisible_run`](../algorithms/invisible_character_stego.py#L36) |
| 6 | Save | [`embed_docx`](../algorithms/invisible_character_stego.py#L223) |

### Extraction Process

| # | Step | Function |
|---|---|---|
| 1 | Record the visible offset of every U+200B | [`_read_marker_offsets`](../algorithms/invisible_character_stego.py#L253) |
| 2 | Try every block length and read the header. Accept it when the `MSF1` tag matches and the length fits the cover. | [`_decode_payload`](../algorithms/invisible_character_stego.py#L103) |
| 3 | Read bits from the marker pairs: offset +1 is `0`, +2 is `1` | [`_read_payload_bits`](../algorithms/invisible_character_stego.py#L151) |
| 4 | Fall back to the legacy format, which needs the secret length | [`_decode_legacy_payload`](../algorithms/invisible_character_stego.py#L166) |
| 5 | Entry point | [`extract_docx`](../algorithms/invisible_character_stego.py#L269) |

---

## 7. Mustafa (2020), Synchronized

Source: [algorithms/invisible_character_with_synchronization.py](../algorithms/invisible_character_with_synchronization.py)

### Idea

This uses the same marker-pair channel as Section 6. The payload (tagged `MSS1`) is cut into 88-bit frames with a 0xA5 marker, 8-bit index, 64 data bits and CRC-8. The extractor estimates the block length from the marker spacing instead of trying every length.

### Embedding Process

| # | Step | Function |
|---|---|---|
| 1 | Remove any existing U+200B from the cover | [`_prepare_cover`](../algorithms/invisible_character_with_synchronization.py#L400) |
| 2 | Build the payload and cut it into CRC-8 frames | [`_payload_bits`](../algorithms/invisible_character_with_synchronization.py#L110), [`sync_frame`](../algorithms/invisible_character_with_synchronization.py#L38), [`crc8`](../algorithms/invisible_character_with_synchronization.py#L23) |
| 3 | Place and insert the markers, as in Section 6 | [`embed_docx`](../algorithms/invisible_character_stego.py#L223) (inherited) |

### Extraction Process

| # | Step | Function |
|---|---|---|
| 1 | Record the marker offsets | [`_read_marker_offsets`](../algorithms/invisible_character_stego.py#L253) (inherited) |
| 2 | Estimate the block length from the most common gap between marker pairs | [`_estimate_block_length`](../algorithms/invisible_character_with_synchronization.py#L132) |
| 3 | Pair the markers and turn them into bits, noting missing, stray and ambiguous slots | [`_recover_bits`](../algorithms/invisible_character_with_synchronization.py#L170), [`_recover_bits_by_offsets`](../algorithms/invisible_character_with_synchronization.py#L256), [`_recover_bits_by_grid`](../algorithms/invisible_character_with_synchronization.py#L280) |
| 4 | Build variants that flip ambiguous bits | [`_flip_variants`](../algorithms/invisible_character_with_synchronization.py#L114) |
| 5 | Fix ambiguous bits inside a frame by finding the fewest flips that make the CRC-8 valid | [`_repair_frames_from_crc`](../algorithms/invisible_character_with_synchronization.py#L351) |
| 6 | Scan for frames with a valid marker and CRC, re-locking one bit at a time after damage | [`sync_deframe`](../algorithms/invisible_character_with_synchronization.py#L58), [`crc8_validate`](../algorithms/invisible_character_with_synchronization.py#L34) |
| 7 | Try block lengths near the estimate and keep the best result | [`extract_from_offsets`](../algorithms/invisible_character_with_synchronization.py#L415) |
| 8 | Entry point | [`extract_docx`](../algorithms/invisible_character_with_synchronization.py#L406) |

---

## 8. Ablation: Proposed Method Without Hamming(8,4)

Source: [algorithms/proposed_ablation.py](../algorithms/proposed_ablation.py)

These classes inherit the whole process from Sections 2 and 3 and replace only the error-correction steps. That isolates what Hamming(8,4) contributes.

| Class | Replaced step | Function |
|---|---|---|
| `BlockStegoNoHamming` | Encoding writes the 4-bit groups as they are | [`hamming_encode_all_4bits`](../algorithms/proposed_ablation.py#L11) |
| | Decoding returns the bits unchanged | [`hamming_decode_all_8bits`](../algorithms/proposed_ablation.py#L15) |
| `BlockStegoSyncNoHamming` | Encoding writes the 4-bit groups as they are | [`hamming_encode_all_4bits`](../algorithms/proposed_ablation.py#L24) |
| | Decoding returns the bits unchanged | [`hamming_decode_all_8bits`](../algorithms/proposed_ablation.py#L28) |
| | No interleaving | [`interleave`](../algorithms/proposed_ablation.py#L32) |
| | The length is read straight from the first 32 bits | [`_declared_bytes`](../algorithms/proposed_ablation.py#L36) |
| | A frame is accepted only when the header matches exactly and the CRC-16 is valid, with no correction | [`validate_frame`](../algorithms/proposed_ablation.py#L40) |
| | No deletion repair | [`_stitch_deletion`](../algorithms/proposed_ablation.py#L52) |

---

## 9. How the App Calls the Methods

[bridge.py](../bridge.py) connects the user interface to the classes above.

| Step | Function |
|---|---|
| Register every method with its ID, name and class | [`Method`](../bridge.py#L33), [`_method`](../bridge.py#L75) |
| Count carriers for the selected method | [`_carrier_count`](../bridge.py#L82) |
| Capacity: exact for block-based, by binary search | [`_block_capacity_bytes`](../bridge.py#L97) |
| Capacity: invisible-character methods | [`_invisible_capacity_bytes`](../bridge.py#L111) |
| Capacity: estimate for Huffman, from the average code length | [`_huffman_capacity_bytes`](../bridge.py#L120) |
| Capacity entry point | [`method_capacity`](../bridge.py#L145) |
| Embed and collect statistics | [`method_embed`](../bridge.py#L271), [`_embed_stats`](../bridge.py#L261) |
| Block usage map shown in the UI | [`_block_map`](../bridge.py#L135) |
| Extract and report status | [`method_extract`](../bridge.py#L300), [`_status`](../bridge.py#L291) |
| Page preview, by converting to PDF with LibreOffice and rendering with PyMuPDF | [`method_pages`](../bridge.py#L321), [`docx_to_pdf`](../processing/psnr_utils.py#L32) |
