import unittest
from io import BytesIO

import pypdf

from calibre.pdf_render import render_pdf_page


class PdfPageRenderTests(unittest.TestCase):
    def make_one_page_pdf(self):
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=360, height=240)
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def test_pdfium_renders_page_to_png_in_memory(self):
        image = render_pdf_page(self.make_one_page_pdf(), page_number=1, dpi=96)
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 100)

    def test_pdf_page_number_is_one_based_and_validated(self):
        pdf = self.make_one_page_pdf()
        with self.assertRaisesRegex(ValueError, "от 1"):
            render_pdf_page(pdf, page_number=0)
        with self.assertRaisesRegex(ValueError, "только 1 страниц"):
            render_pdf_page(pdf, page_number=2)

    def test_render_dpi_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "72 до 600"):
            render_pdf_page(self.make_one_page_pdf(), page_number=1, dpi=24)


if __name__ == "__main__":
    unittest.main()
