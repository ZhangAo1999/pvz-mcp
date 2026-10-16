"""Read-only cross-checks against the original PE and extracted localization."""
import hashlib
import os
import json
from pathlib import Path
import re
import struct
import unittest


class ModeCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((Path(__file__).parents[1]/'app'/'modes.json').read_text(encoding='utf-8'))
        game=os.environ.get('PVZ_TEST_GAME')
        if not game:raise unittest.SkipTest('Set PVZ_TEST_GAME to your own supported original EXE')
        cls.binary=Path(game).read_bytes()
        pe = struct.unpack_from('<I', cls.binary, 0x3c)[0]
        section_table = pe + 24 + struct.unpack_from('<H', cls.binary, pe + 20)[0]
        cls.sections = [struct.unpack_from('<IIII', cls.binary, section_table + i * 40 + 8)
                        for i in range(struct.unpack_from('<H', cls.binary, pe + 6)[0])]

    def offset(self, va):
        for _, rva, raw_size, raw in self.sections:
            if rva <= va - 0x400000 < rva + raw_size:
                return raw + va - 0x400000 - rva
        self.fail('Catalog points outside original image')

    def test_pinned_original_and_complete_native_boundary(self):
        self.assertEqual(hashlib.sha256(self.binary).hexdigest(), self.catalog['provenance']['target_sha256'])
        self.assertEqual([mode['id'] for mode in self.catalog['modes']], list(range(73)))
        self.assertEqual(struct.unpack_from('<I', self.binary, self.offset(0x74eb50))[0], 0xffffffff)

    def test_every_catalog_id_and_key_matches_its_actual_pe_record(self):
        for mode in self.catalog['modes'][1:]:
            va = 0x74e490 + (mode['id'] - 1) * 24
            values = list(struct.unpack_from('<6I', self.binary, self.offset(va)))
            self.assertEqual(mode['native_record']['dwords'], values)
            self.assertEqual(values[0], mode['id'])
            at = self.offset(values[5])
            key = self.binary[at:self.binary.index(0, at)].decode('ascii')
            self.assertEqual('[' + mode['key'] + ']', key)

    def test_all_chinese_names_are_actual_matching_localization(self):
        location=os.environ.get('PVZ_TEST_LOCALIZATION')
        if not location:raise unittest.SkipTest('Set PVZ_TEST_LOCALIZATION to extracted localization for this optional cross-check')
        source=Path(location).read_text(encoding='utf-8')
        localized = {m.group(1): m.group(2).strip()
                     for m in re.finditer(r'^\[([^\]\r\n]+)\]\s*\n(.*?)(?=^\[|\Z)', source, re.M | re.S)}
        for mode in self.catalog['modes'][1:]:
            self.assertEqual(mode['name'], localized[mode['key']])
        by_key = {mode['key']: mode['id'] for mode in self.catalog['modes']}
        self.assertEqual(by_key['ZEN_GARDEN'], 43)
        self.assertEqual(by_key['SCARY_POTTER_ENDLESS'], 60)
        self.assertEqual(by_key['I_ZOMBIE_ENDLESS'], 70)

    def test_static_catalog_does_not_claim_unlocked_or_successful_start(self):
        for mode in self.catalog['modes']:
            self.assertIsNone(mode['availability'])
            self.assertFalse(mode['start_validated'])
            if mode['id']:
                self.assertIsNone(mode['level_range'])

    def test_native_scene_assignment_evidence_is_present(self):
        # Exact native stores; labels are separately qualified in scene records.
        for va, register_modrm, value in [(0x41f82c, 0x81, 4), (0x45c4ee, 0x87, 1),
                                           (0x45c5ed, 0x86, 5), (0x45c75b, 0x86, 6),
                                           (0x45c85b, 0x86, 7)]:
            expected = bytes([0xc7, register_modrm]) + struct.pack('<II', 0x920, value)
            at = self.offset(va)
            self.assertEqual(self.binary[at:at + len(expected)], expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
