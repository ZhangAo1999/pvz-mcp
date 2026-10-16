"""Fixture tests for lifetime-safe IDs, bounded reads and game-facing semantics."""

import json
import struct
import unittest

import state


def put(buf, offset, value, fmt="I"):
    struct.pack_into("<" + fmt, buf, offset, value)


class Memory:
    def __init__(self):
        self.blocks = {}
        self.reads = []

    def read(self, address, size):
        self.reads.append((address, size))
        for base, data in self.blocks.items():
            offset = address - base
            if 0 <= offset and offset + size <= len(data):
                return bytes(data[offset:offset + size])
        raise OSError("Fixture has no mapped range")

    def u32(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def i32(self, address):
        return struct.unpack("<i", self.read(address, 4))[0]


class Fixture:
    BOARD = 0x100000
    APP = 0x200000
    BANK = 0x300000
    POOLS = {"zombies": (0xA8, 0x168, 0x164, 0xEC),
             "plants": (0xC4, 0x14C, 0x148, 0x141),
             "projectiles": (0xE0, 0x94, 0x90, 0x50),
             "coins": (0xFC, 0xD8, 0xD0, 0x38),
             "mowers": (0x118, 0x48, 0x44, 0x30)}

    def __init__(self):
        self.mem = Memory()
        self.b = bytearray(0x5800)
        self.app = bytearray(0x924)
        self.bank = bytearray(0x28 + 2 * 0x50)
        self.mem.blocks[self.BOARD] = self.b
        self.mem.blocks[self.APP] = self.app
        self.mem.blocks[self.BANK] = self.bank
        put(self.b, 0xA4, self.APP)
        put(self.app, 0x868, self.BOARD)
        put(self.app, 0x920, 3)
        put(self.b, 0x15C, self.BANK)
        put(self.b, 0x5568, 7)
        put(self.b, 0x5580, 875)
        put(self.b, 0x5584, 1400)
        put(self.b, 0x5578, -10, "i")  # Spending can debit sun still flying to the bank.
        put(self.b, 0x557C, 10)
        put(self.b, 0x5594, 2)
        put(self.b, 0x55B4, 33)
        for row in range(6):
            put(self.b, 0x5F0 + 4 * row, 1 if row < 5 else 0)
            for col in range(9):
                put(self.b, 0x180 + 24 * col + 4 * row, 1 if row < 5 else 0)
        put(self.bank, 0x24, 2)
        put(self.bank, 0x28 + 0x34, 0)
        put(self.bank, 0x28 + 0x38, -1, "i")
        self.bank[0x28 + 0x48] = 1
        c = 0x28 + 0x50
        put(self.bank, c + 0x34, 48)
        put(self.bank, c + 0x38, 1)
        put(self.bank, c + 0x24, 7)
        put(self.bank, c + 0x28, 10)
        self.bank[c + 0x49] = 1
        self.pool_data = {}
        for number, (kind, size) in enumerate((('zombies', 1), ('plants', 3), ('projectiles', 2), ('coins', 5), ('mowers', 1))):
            offset, stride, handle_at, removed_at = self.POOLS[kind]
            address = 0x400000 + number * 0x10000
            block = bytearray(size * stride)
            self.pool_data[kind] = block
            self.mem.blocks[address] = block
            put(self.b, offset, address)
            put(self.b, offset + 4, size)
            put(self.b, offset + 8, size)
            put(self.b, offset + 0x10, size)
            for slot in range(size):
                put(block, slot * stride + handle_at, (slot + 0x12) << 16 | slot)
        plants = self.pool_data['plants']
        put(plants, 0x24, 0)
        put(plants, 0x1C, 2)
        put(plants, 0x28, 4)
        put(plants, 0x40, 289)
        put(plants, 0x44, 300)
        put(plants, 0x14C + 0x148, 1)  # A free slot keeps low index but has no generation.
        plants[2 * 0x14C + 0x141] = 1  # Removed yet not released from DataArray.
        put(self.b, 0xC4 + 0x10, 2)
        zombies = self.pool_data['zombies']
        put(zombies, 0x24, 2)
        put(zombies, 0xC8, 270)
        put(zombies, 0xCC, 270)
        put(zombies, 0xD0, 355)
        put(zombies, 0xD4, 370)
        put(zombies, 0x2C, 713.25, 'f')
        projectiles = self.pool_data['projectiles']
        put(projectiles, 0x88, 0x120000)
        put(projectiles, 0x94 + 0x88, 0x110000)  # Stale handle with the same slot number.
        coins = self.pool_data['coins']
        for slot, type_id in enumerate([4, 5, 6, 6, 4]):
            put(coins, slot * 0xD8 + 0x58, type_id)
        for slot in [0, 1, 3, 4]:
            coins[slot * 0xD8 + 0x50] = 1
        coins[3 * 0xD8 + 0x38] = 1
        put(coins, 4 * 0xD8 + 0xD0, 4)
        put(self.b, 0xFC + 0x10, 4)
        put(self.pool_data['mowers'], 0x14, 4)

    def decode(self, epoch='board-7'):
        return state.snapshot(self.mem, self.BOARD, epoch)


class StateTests(unittest.TestCase):
    def test_current_objects_coordinates_and_armor_are_separate(self):
        result = Fixture().decode()
        self.assertEqual(result['counts']['plants'], 1)
        self.assertEqual(result['pool_stats']['plants']['allocated_count'], 2)
        self.assertEqual((result['plants'][0]['row'], result['plants'][0]['col']), (3, 5))
        self.assertEqual(result['plants'][0]['hp'], 289)
        self.assertEqual(result['zombies'][0]['hp'], 270)
        self.assertEqual(result['zombies'][0]['helmet_hp'], 355)
        self.assertEqual(result['mowers'][0]['row'], 5)
        self.assertEqual(result['terrain']['cell_types_raw'][5], [0] * 9)
        self.assertEqual(result['counters'], {'board_updates': 1400, 'gameplay_updates': 875})

    def test_generation_and_board_epoch_prevent_stale_target_aliases(self):
        fixture = Fixture()
        first = fixture.decode()
        old_id = first['zombies'][0]['id']
        self.assertEqual(first['projectiles'][0]['target_id'], old_id)
        self.assertIsNone(first['projectiles'][1]['target_id'])
        put(fixture.pool_data['zombies'], 0x164, 0x130000)
        reused = fixture.decode()
        self.assertNotEqual(reused['zombies'][0]['id'], old_id)
        self.assertIsNone(reused['projectiles'][0]['target_id'])
        self.assertNotEqual(reused['zombies'][0]['id'], fixture.decode('board-8')['zombies'][0]['id'])

    def test_pending_sun_excludes_uncollected_removed_and_free_slots(self):
        result = Fixture().decode()
        self.assertEqual(result['sun'], {'stored': -10, 'pending': 40, 'available': 30})
        self.assertEqual(len(result['coins']), 3)

    def test_card_cooldown_strict_threshold_and_imitator_cost(self):
        cards = Fixture().decode()['cards']
        self.assertEqual(cards[0]['slot'], 1)
        self.assertTrue(cards[0]['ready'])
        self.assertEqual(cards[0]['base_cost'], 100)
        self.assertEqual(cards[1]['effective_name'], 'SUNFLOWER')
        self.assertEqual(cards[1]['base_cost'], 50)
        self.assertEqual(cards[1]['cooldown']['remaining_updates'], 4)

    def test_bad_pool_bounds_abort_before_following_pool_pointer(self):
        fixture = Fixture()
        put(fixture.b, 0xA8 + 4, 4097)
        with self.assertRaisesRegex(state.StateError, 'pool bounds'):
            fixture.decode()
        self.assertFalse(any(address >= 0x400000 for address, _ in fixture.mem.reads))

    def test_corrupt_lifetime_or_occupancy_never_returns_partial_state(self):
        fixture = Fixture()
        put(fixture.pool_data['zombies'], 0x164, 0x120001)
        with self.assertRaisesRegex(state.StateError, 'generation handle'):
            fixture.decode()
        fixture = Fixture()
        put(fixture.b, 0xA8 + 0x10, 0)
        with self.assertRaisesRegex(state.StateError, 'occupied count'):
            fixture.decode()

    def test_ownership_short_reads_and_nonfinite_values(self):
        fixture = Fixture()
        put(fixture.app, 0x868, fixture.BOARD + 4)
        with self.assertRaisesRegex(state.StateError, 'ownership'):
            fixture.decode()
        fixture = Fixture()
        fixture.mem.blocks[fixture.BOARD] = fixture.b[:0x5700]
        with self.assertRaises(state.StateError):
            fixture.decode()
        fixture = Fixture()
        put(fixture.pool_data['zombies'], 0x2C, float('nan'), 'f')
        result = fixture.decode()
        self.assertIsNone(result['zombies'][0]['x'])
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
