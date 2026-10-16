"""Read-only PvZ 1.2.0.1073 state decoder.

The caller supplies a memory reader and must establish a stopped simulation or
verify its own before/after consistency token. This module neither opens a
process nor writes memory. Offsets are supported only for the binary identified
in definitions.json and the Codex patches derived from it.
"""

import json
import math
from pathlib import Path
import struct


class StateError(ValueError):
    """Memory no longer matches the supported, bounded object layout."""


_DEFINITIONS = json.loads(Path(__file__).with_name("definitions.json").read_text(encoding="utf-8"))
_NAMES = {
    kind: {entry["type"]: entry for entry in _DEFINITIONS[kind]}
    for kind in ("plants", "zombies", "projectiles")
}
_POOLS = {
    # Board pool offset, slot size, complete handle offset, removed byte offset.
    "zombies": (0xA8, 0x168, 0x164, 0xEC),
    "plants": (0xC4, 0x14C, 0x148, 0x141),
    "projectiles": (0xE0, 0x94, 0x90, 0x50),
    "coins": (0xFC, 0xD8, 0xD0, 0x38),
    "mowers": (0x118, 0x48, 0x44, 0x30),
}
_SUN_VALUES = {4: 25, 5: 15, 6: 50}
_COIN_NAMES = {1: "SILVER_COIN", 2: "GOLD_COIN", 3: "DIAMOND", 4: "SUN", 5: "SMALL_SUN", 6: "LARGE_SUN"}


def _pointer(value, size=1, label="pointer"):
    if not isinstance(value, int) or value < 0x10000 or value > 0xFFFEFFFF or value % 4:
        raise StateError("Invalid {}: {!r}".format(label, value))
    if size < 0 or value + size > 0xFFFF0000:
        raise StateError("Invalid {} range".format(label))
    return value


def _read(mem, address, size, label):
    _pointer(address, size, label)
    try:
        data = mem.read(address, size)
    except Exception as exc:
        raise StateError("Cannot read {} at 0x{:08x}: {}".format(label, address, exc)) from exc
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != size:
        raise StateError("Short or invalid read for {}".format(label))
    return bytes(data)


def _u(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _i(data, offset):
    return struct.unpack_from("<i", data, offset)[0]


def _f(data, offset):
    value = struct.unpack_from("<f", data, offset)[0]
    # JSON must never contain the JavaScript/Python-only NaN or Infinity tokens.
    return round(value, 4) if math.isfinite(value) else None


def _name(kind, type_id):
    return _NAMES[kind].get(type_id, {}).get("name", "UNKNOWN_{}".format(type_id))


def definitions():
    """Return a fresh JSON-safe copy of supported types and their provenance."""
    return json.loads(json.dumps(_DEFINITIONS))


def _enumerate_pool(mem, board_data, kind, epoch):
    offset, stride, handle_offset, removed_offset = _POOLS[kind]
    head = _u(board_data, offset)
    high_water = _u(board_data, offset + 4)
    capacity = _u(board_data, offset + 8)
    allocated = _u(board_data, offset + 0x10)
    if not 0 <= high_water <= capacity <= 4096 or allocated > high_water:
        raise StateError("Invalid {} pool bounds: high_water={}, capacity={}, allocated={}".format(kind, high_water, capacity, allocated))
    if capacity:
        _pointer(head, capacity * stride, kind + " pool")
    elif head:
        _pointer(head, 0, kind + " pool")
    block = _read(mem, head, high_water * stride, kind + " slots") if high_water else b""
    result = []
    occupied = 0
    for slot in range(high_water):
        data = block[slot * stride:(slot + 1) * stride]
        handle = _u(data, handle_offset)
        if not handle & 0xFFFF0000:
            continue
        occupied += 1
        if handle & 0xFFFF != slot:
            raise StateError("{} slot {} has an inconsistent generation handle".format(kind, slot))
        if data[removed_offset]:
            continue
        item = {"id": "{}:{}:{:08x}".format(epoch, kind, handle), "handle": handle}
        if kind == "plants":
            type_id = _i(data, 0x24)
            item.update(type=type_id, name=_name(kind, type_id), row=_i(data, 0x1C) + 1,
                        col=_i(data, 0x28) + 1, x=_i(data, 8), y=_i(data, 0xC),
                        hp=_i(data, 0x40), max_hp=_i(data, 0x44),
                        state=_i(data, 0x3C), sleeping=bool(data[0x143]),
                        attack_countdown=_i(data, 0x58))
        elif kind == "zombies":
            type_id = _i(data, 0x24)
            item.update(type=type_id, name=_name(kind, type_id), row=_i(data, 0x1C) + 1,
                        x=_f(data, 0x2C), y=_f(data, 0x30), phase=_i(data, 0x28),
                        hp=_i(data, 0xC8), max_hp=_i(data, 0xCC),
                        helmet_type=_i(data, 0xC4), helmet_hp=_i(data, 0xD0),
                        helmet_max_hp=_i(data, 0xD4), shield_type=_i(data, 0xD8),
                        shield_hp=_i(data, 0xDC), shield_max_hp=_i(data, 0xE0),
                        extra_hp_raw=_i(data, 0xE4), extra_max_hp_raw=_i(data, 0xE8),
                        has_head=bool(data[0xBA]), eating=bool(data[0x51]),
                        chill_countdown=_i(data, 0xAC), butter_countdown=_i(data, 0xB0),
                        freeze_countdown=_i(data, 0xB4), age_updates=_i(data, 0x60))
        elif kind == "projectiles":
            type_id = _i(data, 0x5C)
            item.update(type=type_id, name=_name(kind, type_id), row=_i(data, 0x1C) + 1,
                        x=_f(data, 0x30), y=_f(data, 0x34), height=_f(data, 0x38),
                        vx=_f(data, 0x3C), vy=_f(data, 0x40), vertical_speed=_f(data, 0x44),
                        vertical_acceleration=_f(data, 0x48), motion=_i(data, 0x58),
                        age_updates=_i(data, 0x60), target_handle=_u(data, 0x88))
        elif kind == "coins":
            type_id = _i(data, 0x58)
            item.update(type=type_id, name=_COIN_NAMES.get(type_id, "COIN_{}".format(type_id)),
                        x=_f(data, 0x24), y=_f(data, 0x28), state=_i(data, 0x3C),
                        collecting=bool(data[0x50]), sun_value=_SUN_VALUES.get(type_id, 0))
        elif kind == "mowers":
            item.update(row=_i(data, 0x14) + 1, x=_f(data, 8), y=_f(data, 0xC),
                        state=_i(data, 0x2C), type=_i(data, 0x34))
        result.append(item)
    if occupied != allocated:
        raise StateError("{} pool changed or has inconsistent occupied count ({} != {})".format(kind, occupied, allocated))
    return result, {"high_water": high_water, "capacity": capacity,
                    "allocated_count": allocated, "active_count": len(result)}


def snapshot(mem, board, epoch):
    """Decode one supported Board. Public rows/columns/card slots start at one.

    Entity handles include the caller's board epoch and generation bits. A new
    board lifetime needs a new epoch even if the allocator reuses its address.
    Pointer/bounds/count failures abort rather than returning a partial state.
    """
    if not isinstance(epoch, (str, int)) or not str(epoch) or ":" in str(epoch):
        raise StateError("epoch must be a nonempty string/integer without ':'")
    epoch = str(epoch)
    b = _read(mem, board, 0x5800, "Board")
    app = _u(b, 0xA4)
    app_data = _read(mem, _pointer(app, 0x924, "App"), 0x924, "App")
    if _u(app_data, 0x868) != board:
        raise StateError("Board/App ownership changed during snapshot")
    scene, mode = _i(app_data, 0x920), _i(app_data, 0x91C)
    background = _i(b, 0x5564)
    playable_rows = 6 if background in (2, 3) else 5 if background in (0, 1, 4, 5) else None
    result = {"schema_version": 1, "board_epoch": epoch, "scene": scene,
              "scene_name": "playing" if scene == 3 else "other", "mode": mode,
              "level": _i(b, 0x5568), "board_state_raw": background,
              "background_type": background,
              "counters": {"board_updates": _u(b, 0x5584), "gameplay_updates": _u(b, 0x5580)},
              "game_pause_flag": bool(b[0x17C]), "time_freeze_flag": bool(b[0x5760]),
              "cards": [], "counts": {}, "pool_stats": {}}
    for kind in _POOLS:
        result[kind], result["pool_stats"][kind] = _enumerate_pool(mem, b, kind, epoch)
        result["counts"][kind] = len(result[kind])
    zombie_ids = {item["handle"]: item["id"] for item in result["zombies"]}
    for projectile in result["projectiles"]:
        projectile["target_id"] = zombie_ids.get(projectile["target_handle"])
    pending = sum(coin["sun_value"] for coin in result["coins"] if coin["collecting"])
    stored = _i(b, 0x5578)
    result["sun"] = {"stored": stored, "pending": pending, "available": stored + pending}
    seedbank = _u(b, 0x15C)
    if seedbank:
        bank = _read(mem, seedbank, 0x28, "seed bank")
        count = _i(bank, 0x24)
        if not 0 <= count <= 16:
            raise StateError("Invalid seed card count: {}".format(count))
        slots = _read(mem, seedbank + 0x28, count * 0x50, "seed cards") if count else b""
        for index in range(count):
            card = slots[index * 0x50:(index + 1) * 0x50]
            type_id, imitator = _i(card, 0x34), _i(card, 0x38)
            ready, refreshing = bool(card[0x48]), bool(card[0x49])
            progress, threshold = _i(card, 0x24), _i(card, 0x28)
            effective_type = imitator if type_id == 48 and imitator != -1 else type_id
            definition = _NAMES["plants"].get(effective_type, {})
            result["cards"].append({
                "slot": index + 1, "type": type_id, "name": _name("plants", type_id),
                "imitator_type": imitator, "effective_type": effective_type,
                "effective_name": _name("plants", effective_type), "ready": ready,
                "refreshing": refreshing, "base_cost": definition.get("base_cost"),
                "cooldown": {"progress": progress, "threshold": threshold,
                             "remaining_updates": max(0, threshold - progress + 1) if refreshing and not ready else 0}})
    result["counts"]["cards"] = len(result["cards"])
    wave_index, total = _i(b, 0x5594), _i(b, 0x557C)
    result["waves"] = {"next_index_native": wave_index, "spawned_count": wave_index,
                       "next_number": wave_index + 1 if 0 <= wave_index < total else None,
                       "total": total, "next_spawn_countdown": _i(b, 0x55B4),
                       "initial_spawn_countdown": _i(b, 0x55B8),
                       "health_threshold_raw": _i(b, 0x55AC)}
    result["terrain"] = {
        "rows": 6, "native_row_slots": 6, "playable_rows": playable_rows, "columns": 9,
        "row_types_raw": [_i(b, 0x5F0 + row * 4) for row in range(6)],
        "cell_types_raw": [[_i(b, 0x180 + col * 24 + row * 4) for col in range(9)] for row in range(6)]}
    result["coverage"] = {
        "coordinates": "Rows, columns and card slots are one-based; pixels are native game coordinates.",
        "consistency": "Caller must freeze simulation or validate a before/after consistency token; this decoder does not pause it.",
        "timing": "Countdowns are native update counts; no conversion to seconds is assumed.",
        "terrain": "Six native row slots are exposed, including unused rows. playable_rows is the nominal lawn height for normal backgrounds (pool/fog six, others five); tutorials and individual cell rules may restrict planting further. Unknown backgrounds return null. Terrain codes remain raw.",
        "cards": "ready is the card's native ready flag, not a guarantee of legal planting or sufficient sun. base_cost excludes mode-specific upgrade surcharges.",
        "health": "Body, helmet and shield health remain separate. extra_hp_raw semantics are only partially established.",
        "omitted": ["grid items/graves", "effects/animations", "full wave schedule", "authoritative win/loss reason", "replay"],
        "layout_source": _DEFINITIONS["provenance"]["target_sha256"]}
    return result
