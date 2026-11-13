import json
import sys
import tempfile
import unittest
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from document_pipeline import crop_page, parse_document, recognize_image
from store import validate_project


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = ROOT / 'examples' / 'РуководствоПоГраблям.pdf'
        cls.temp = tempfile.TemporaryDirectory()
        cls.result = parse_document(cls.pdf, cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_reference_document(self):
        result = self.result
        self.assertEqual(result['pages'], 56)
        groups = result['machine']['partGroups']
        self.assertEqual([len(group['items']) for group in groups], [36, 11, 12, 13, 10, 7, 8, 27])
        self.assertEqual(sum(len(group['items']) for group in groups), 124)
        self.assertEqual(len(result['machine']['sections']), 7)
        self.assertEqual(result['ocr_errors'], [])
        validate_project({'machines': [result['machine']]})
        pages = json.loads((Path(self.temp.name) / 'raw-pages.json').read_text(encoding='utf-8'))
        self.assertEqual(len(pages), 56)
        self.assertTrue(any(page['tables'] for page in pages))
        self.assertTrue(any(page['onnx_lines'] for page in pages))
        self.assertEqual(len(result['gallery']), 56)

    def test_russian_onnx_and_tesseract_on_rendered_page(self):
        pdf = pdfium.PdfDocument(str(self.pdf))
        image = pdf[0].render(scale=2).to_pil()
        result = recognize_image(image)
        self.assertGreaterEqual(result['detected_lines'], 3)
        self.assertIn('ONNX', result['engine'])
        self.assertTrue(any('Руководств' in line for line in result['onnx_text'].splitlines()))
        self.assertIn('ГРН-471', result['text'])
        self.assertIn('ГРАБЛИ', result['text'])
        self.assertFalse(result['errors'])

    def test_crop(self):
        image = crop_page(self.pdf, 1, [.05, .05, .6, .5])
        self.assertTrue(image.startswith('data:image/jpeg;base64,'))
        with self.assertRaises(ValueError):
            crop_page(self.pdf, 1, [.6, .5, .1, .2])


if __name__ == '__main__':
    unittest.main()
