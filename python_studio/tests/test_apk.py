import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import apk_builder


def take(buffer, offset=0):
    size = struct.unpack_from('<I', buffer, offset)[0]
    end = offset + 4 + size
    if end > len(buffer):
        raise AssertionError('Invalid length prefix')
    return buffer[offset + 4:end], end


def records(buffer):
    result = []
    offset = 0
    while offset < len(buffer):
        value, offset = take(buffer, offset)
        result.append(value)
    return result


def verify_v2(data):
    eocd_offset = data.rfind(b'PK\x05\x06')
    if eocd_offset < 0:
        raise AssertionError('EOCD missing')
    central_offset = struct.unpack_from('<I', data, eocd_offset + 16)[0]
    if data[central_offset - 16:central_offset] != b'APK Sig Block 42':
        raise AssertionError('APK signing block missing')
    block_size = struct.unpack_from('<Q', data, central_offset - 24)[0]
    start = central_offset - block_size - 8
    if start < 0 or struct.unpack_from('<Q', data, start)[0] != block_size or block_size % 8:
        raise AssertionError('Invalid signing block length')
    offset = start + 8
    v2 = None
    while offset < central_offset - 24:
        size, identifier = struct.unpack_from('<QI', data, offset)
        if size < 4 or offset + 8 + size > central_offset - 24:
            raise AssertionError('Invalid signing block pair')
        if identifier == 0x7109871a:
            v2 = data[offset + 12:offset + 8 + size]
        offset += 8 + size
    if v2 is None or offset != central_offset - 24:
        raise AssertionError('APK v2 signer missing')
    sequence, _ = take(v2)
    signers = records(sequence)
    if len(signers) != 1:
        raise AssertionError('Unexpected signer count')
    signed, pos = take(signers[0])
    signatures, pos = take(signers[0], pos)
    public, pos = take(signers[0], pos)
    if pos != len(signers[0]):
        raise AssertionError('Trailing signer bytes')
    digests, pos = take(signed)
    certificates, pos = take(signed, pos)
    _, pos = take(signed, pos)
    if pos != len(signed):
        raise AssertionError('Trailing signed bytes')
    digest_record = records(digests)[0]
    signature_record = records(signatures)[0]
    algorithm, digest_size = struct.unpack_from('<II', digest_record)
    signature_algorithm, signature_size = struct.unpack_from('<II', signature_record)
    if algorithm != 0x0103 or signature_algorithm != algorithm:
        raise AssertionError('Unexpected algorithm')
    if digest_size != len(digest_record) - 8 or signature_size != len(signature_record) - 8:
        raise AssertionError('Invalid digest or signature size')
    certificate = x509.load_der_x509_certificate(records(certificates)[0])
    key = certificate.public_key()
    actual_public = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if actual_public != public:
        raise AssertionError('Certificate public key mismatch')
    key.verify(signature_record[8:], signed, padding.PKCS1v15(), hashes.SHA256())
    eocd = bytearray(data[eocd_offset:])
    struct.pack_into('<I', eocd, 16, start)
    pieces = []
    for section in (data[:start], data[central_offset:eocd_offset], bytes(eocd)):
        for index in range(0, len(section), 1024 * 1024):
            chunk = section[index:index + 1024 * 1024]
            pieces.append(hashlib.sha256(b'\xa5' + struct.pack('<I', len(chunk)) + chunk).digest())
    digest = hashlib.sha256(b'\x5a' + struct.pack('<I', len(pieces)) + b''.join(pieces)).digest()
    if digest != digest_record[8:]:
        raise AssertionError('APK digest mismatch')
    return digest.hex()


class ApkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_key_dir = apk_builder.KEY_DIR
        apk_builder.KEY_DIR = Path(cls.temp.name) / 'signing'
        project = json.loads((ROOT / 'examples' / 'sample-catalog.json').read_text(encoding='utf-8'))
        project['machines'][0]['model'] = 'KOLIBRI_LOCAL_BUILD_TEST'
        cls.data = apk_builder.build_apk(project)
        cls.path = Path(cls.temp.name) / 'test.apk'
        cls.path.write_bytes(cls.data)

    @classmethod
    def tearDownClass(cls):
        apk_builder.KEY_DIR = cls.old_key_dir
        cls.temp.cleanup()

    def test_signature_and_tampering(self):
        self.assertEqual(len(verify_v2(self.data)), 64)
        modified = bytearray(self.data)
        modified[40] ^= 1
        with self.assertRaises(AssertionError):
            verify_v2(bytes(modified))

    def test_catalog_images_and_alignment(self):
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            catalog = archive.read('assets/public/runtime-catalog.js').decode('utf-8')
            self.assertIn('KOLIBRI_LOCAL_BUILD_TEST', catalog)
            self.assertIn('data:image/', catalog)
            self.assertEqual(archive.getinfo('resources.arsc').compress_type, zipfile.ZIP_STORED)
            self.assertFalse([name for name in archive.namelist() if name.upper().startswith('META-INF/') and name.upper().endswith(('.SF', '.RSA', '.DSA', '.EC', 'MANIFEST.MF'))])
            stored = 0
            for info in archive.infolist():
                if info.compress_type == zipfile.ZIP_STORED:
                    local = struct.unpack_from('<IHHHHHIIIHH', self.data, info.header_offset)
                    offset = info.header_offset + 30 + local[9] + local[10]
                    self.assertEqual(offset % (16384 if info.filename.endswith('.so') else 4), 0, info.filename)
                    stored += 1
            self.assertGreater(stored, 200)
            self.assertIsNone(archive.testzip())

    def test_manifest(self):
        try:
            from pyaxmlparser import APK
        except ImportError:
            self.skipTest('Установите requirements-test.txt для проверки манифеста')
        apk = APK(str(self.path))
        self.assertEqual(str(apk.get_min_sdk_version()), '24')
        self.assertEqual(str(apk.get_target_sdk_version()), '36')
        self.assertFalse([permission for permission in apk.get_permissions() if 'INTERNET' in permission or 'NETWORK' in permission])


if __name__ == '__main__':
    unittest.main()
