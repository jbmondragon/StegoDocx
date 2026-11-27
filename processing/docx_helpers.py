# Utilities for walking a python-docx Document in reading order and for splitting multi-character runs into one run per character. 
# implementations of different text steganographic methods depend on these functions

from copy import deepcopy

from docx.document import Document as DocumentType
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.text.run import Run

# ------------------------------------------------------------------------------------------

def iter_block_items(parent):
    if isinstance(parent, DocumentType):
        parent_element = parent.element.body
    elif isinstance(parent, _Cell):
        parent_element = parent._tc
    else:
        raise ValueError(f"Unsupported parent type: {type(parent)!r}")

    for child in parent_element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            table = Table(child, parent)
            yield table

# ------------------------------------------------------------------------------------------
# Yield all paragraphs in document reading order, including paragraphs inside tables. 
# This is useful for text steganography, where we want to process all visible text in the document.
def iter_paragraphs(parent):
    for block in iter_block_items(parent):
        if isinstance(block, Paragraph):
            yield block
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    yield from iter_paragraphs(cell)

# ------------------------------------------------------------------------------------------
def iter_unique_paragraphs(parent):
    seen = set()
    for paragraph in iter_paragraphs(parent):
        if paragraph._p not in seen:
            seen.add(paragraph._p)
            yield paragraph

# ------------------------------------------------------------------------------------------
def document_plain_text(document) -> str:
    texts = {}
    parts = []
    for paragraph in iter_paragraphs(document):
        text = texts.get(paragraph._p)
        if text is None:
            text = texts[paragraph._p] = paragraph.text
        parts.append(text)
    return "\n".join(parts)

# ------------------------------------------------------------------------------------------
def split_text_runs(paragraph: Paragraph):
    for run in list(paragraph.runs):
        text = run.text

        if not text or len(text) <= 1:
            continue

        original = run._r

        for character in text:
            cloned = deepcopy(original)

            for child in list(cloned):
                if child.tag != qn("w:rPr"):
                    cloned.remove(child)

            new_run = Run(cloned, paragraph)
            new_run.text = character

            original.addprevious(cloned)

        original.getparent().remove(original)

# ------------------------------------------------------------------------------------------
def normalize_runs_for_embedding(document):
    for paragraph in iter_paragraphs(document):
        for run in paragraph.runs:
            color = run.font.color

            if color.type is None:
                continue

            color_element = run._r.rPr.find(qn("w:color"))
            if color_element is not None:
                run._r.rPr.remove(color_element)

# ------------------------------------------------------------------------------------------
def character_runs(document):
    for paragraph in iter_paragraphs(document):
        split_text_runs(paragraph)
        for run in paragraph.runs:
            if run.text:
                yield run.text, run
