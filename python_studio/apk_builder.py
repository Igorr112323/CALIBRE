import datetime
import hashlib
import json
import os
import struct
import threading
import zlib
from io import BytesIO
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'apk_template' / 'kolibri-shell.apk'
KEY_DIR = ROOT / 'data' / 'signing'
RUNTIME = b'assets/public/runtime-catalog.js'
MAGIC = b'APK Sig Block 42'
ALGORITHM = 0x0103
CHUNK = 1024 * 1024
KEY_LOCK = threading.Lock()


def u32(value):
    return struct.pack('<I', value)


def lp(value):
    return u32(len(value)) + value


def key_material():
    with KEY_LOCK:
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        key_path = KEY_DIR / 'debug-key.pem'
        cert_path = KEY_DIR / 'debug-cert.pem'
        if key_path.exists() != cert_path.exists():
            raise RuntimeError('Неполная пара ключа и сертификата подписи. Восстановите оба файла из резервной копии.')
        if not key_path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Kolibri Studio Local Debug')])
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(days=1))
                    .not_valid_after(now + datetime.timedelta(days=3650))
                    .sign(key, hashes.SHA256()))
            key_data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            cert_data = cert.public_bytes(serialization.Encoding.PEM)
            key_tmp = key_path.with_suffix('.tmp')
            cert_tmp = cert_path.with_suffix('.tmp')
            key_tmp.write_bytes(key_data)
            try:
                os.chmod(key_tmp, 0o600)
            except OSError:
                pass
            cert_tmp.write_bytes(cert_data)
            os.replace(key_tmp, key_path)
            os.replace(cert_tmp, cert_path)
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cert_spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if spki != cert_spki or not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
            raise RuntimeError('Ключ и сертификат не совпадают или не являются RSA-2048.')
        return key, cert.public_bytes(serialization.Encoding.DER), spki


def clean_extra(raw):
    output = bytearray()
    pos = 0
    while pos + 4 <= len(raw):
        kind, size = struct.unpack_from('<HH', raw, pos)
        end = pos + 4 + size
        if end > len(raw):
            raise ValueError('Повреждён ZIP extra field.')
        if kind != 0xD935:
            output.extend(raw[pos:end])
        pos = end
    if pos != len(raw):
        raise ValueError('Повреждён ZIP extra field.')
    return bytes(output)


def aligned_extra(raw, local_offset, name_length, stored, name):
    extra = clean_extra(raw)
    if not stored:
        return extra
    alignment = 16384 if name.endswith(b'.so') else 4
    padding_size = (-(local_offset + 30 + name_length + len(extra) + 4)) % alignment
    return extra + struct.pack('<HH', 0xD935, padding_size) + bytes(padding_size)


def is_signature(name):
    value = name.upper()
    return value.startswith(b'META-INF/') and (value.endswith((b'.SF', b'.RSA', b'.DSA', b'.EC')) or value == b'META-INF/MANIFEST.MF')


def parse_zip(data):
    end = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        raise ValueError('В шаблоне не найден конец ZIP.')
    disk, disk_cd, count_disk, count, cd_size, cd_offset, comment_size = struct.unpack_from('<HHHHIIH', data, end + 4)
    if disk or disk_cd or count != count_disk or end + 22 + comment_size != len(data) or cd_offset + cd_size != end:
        raise ValueError('Многотомный или некорректный ZIP не поддерживается.')
    records = []
    pos = cd_offset
    names = set()
    for _ in range(count):
        if data[pos:pos + 4] != b'PK\x01\x02':
            raise ValueError('Повреждён центральный каталог шаблона.')
        values = struct.unpack_from('<HHHHHHIIIHHHHHII', data, pos + 4)
        made, needed, flags, method, time, date, crc, compressed_size, raw_size, nlen, elen, clen, disk_start, internal, external, local = values
        name = data[pos + 46:pos + 46 + nlen]
        central_extra = data[pos + 46 + nlen:pos + 46 + nlen + elen]
        comment = data[pos + 46 + nlen + elen:pos + 46 + nlen + elen + clen]
        pos += 46 + nlen + elen + clen
        if not name or name in names or disk_start or method not in (0, 8) or flags & 1:
            raise ValueError('Неподдерживаемая или повторная ZIP-запись.')
        names.add(name)
        if data[local:local + 4] != b'PK\x03\x04':
            raise ValueError('Повреждён локальный заголовок ZIP.')
        local_values = struct.unpack_from('<IHHHHHIIIHH', data, local)
        local_name_length, local_extra_length = local_values[9], local_values[10]
        if data[local + 30:local + 30 + local_name_length] != name:
            raise ValueError('Имена ZIP-записи не совпадают.')
        start = local + 30 + local_name_length + local_extra_length
        compressed = data[start:start + compressed_size]
        if len(compressed) != compressed_size or start + compressed_size > cd_offset:
            raise ValueError('Повреждены данные ZIP-записи.')
        if is_signature(name):
            continue
        records.append({'name': name, 'made': made, 'needed': needed, 'flags': flags & ~8,
                        'method': method, 'time': time, 'date': date, 'crc': crc,
                        'csize': compressed_size, 'size': raw_size, 'central_extra': central_extra,
                        'comment': comment, 'disk': disk_start, 'internal': internal,
                        'external': external, 'offset': local,
                        'local_extra': data[local + 30 + local_name_length:start], 'data': compressed})
    if pos != cd_offset + cd_size:
        raise ValueError('Размер каталога ZIP не совпадает.')
    records.sort(key=lambda record: record['offset'])
    return records, data[end + 22:end + 22 + comment_size]


def chunked_digest(sections):
    blocks = []
    for section in sections:
        for pos in range(0, len(section), CHUNK):
            chunk = section[pos:pos + CHUNK]
            blocks.append(hashlib.sha256(b'\xa5' + u32(len(chunk)) + chunk).digest())
    return hashlib.sha256(b'\x5a' + u32(len(blocks)) + b''.join(blocks)).digest()


def signing_block(digest, key, cert, spki):
    digest_record = u32(ALGORITHM) + u32(len(digest)) + digest
    signed_data = lp(lp(digest_record)) + lp(lp(cert)) + lp(b'')
    signature = key.sign(signed_data, padding.PKCS1v15(), hashes.SHA256())
    signature_record = u32(ALGORITHM) + u32(len(signature)) + signature
    signer = lp(signed_data) + lp(lp(signature_record)) + lp(spki)
    v2 = lp(lp(signer))
    pair = struct.pack('<QI', len(v2) + 4, 0x7109871A) + v2
    need = (8 - len(pair) % 8) % 8
    pad_len = (need - 4) % 8
    pair += struct.pack('<QI', 4 + pad_len, 0x42726577) + bytes(pad_len)
    size = len(pair) + 24
    return struct.pack('<Q', size) + pair + struct.pack('<Q', size) + MAGIC


def build_apk(project, template=TEMPLATE):
    if not isinstance(project, dict) or not isinstance(project.get('machines'), list):
        raise ValueError('Каталог должен содержать список машин.')
    runtime = json.dumps(project, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    runtime = ('window.__KOLIBRI_CATALOG__=' + runtime + ';\n').encode('utf-8')
    if len(runtime) > 250 * 1024 * 1024:
        raise ValueError('Каталог с изображениями превышает 250 МБ.')
    records, comment = parse_zip(Path(template).read_bytes())
    runtime_count = 0
    for record in records:
        if record['name'] == RUNTIME:
            compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
            record['data'] = compressor.compress(runtime) + compressor.flush() if record['method'] == 8 else runtime
            record['csize'] = len(record['data'])
            record['size'] = len(runtime)
            record['crc'] = zlib.crc32(runtime) & 0xffffffff
            runtime_count += 1
    if runtime_count != 1:
        raise ValueError('В APK отсутствует или повторяется файл каталога.')
    output = BytesIO()
    central = BytesIO()
    for record in records:
        local = output.tell()
        name = record['name']
        extra = aligned_extra(record['local_extra'], local, len(name), record['method'] == 0, name)
        output.write(struct.pack('<IHHHHHIIIHH', 0x04034B50, record['needed'], record['flags'],
                                 record['method'], record['time'], record['date'], record['crc'],
                                 record['csize'], record['size'], len(name), len(extra)))
        output.write(name)
        output.write(extra)
        output.write(record['data'])
        cd_extra, cd_comment = record['central_extra'], record['comment']
        central.write(struct.pack('<IHHHHHHIIIHHHHHII', 0x02014B50, record['made'], record['needed'],
                                  record['flags'], record['method'], record['time'], record['date'],
                                  record['crc'], record['csize'], record['size'], len(name), len(cd_extra),
                                  len(cd_comment), record['disk'], record['internal'], record['external'], local))
        central.write(name)
        central.write(cd_extra)
        central.write(cd_comment)
    entries = output.getvalue()
    cd = central.getvalue()
    eocd = struct.pack('<IHHHHIIH', 0x06054B50, 0, 0, len(records), len(records), len(cd), len(entries), len(comment)) + comment
    digest = chunked_digest((entries, cd, eocd))
    key, cert, spki = key_material()
    block = signing_block(digest, key, cert, spki)
    patched = bytearray(eocd)
    struct.pack_into('<I', patched, 16, len(entries) + len(block))
    return entries + block + cd + bytes(patched)
