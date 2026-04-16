#!/usr/bin/env python3
"""Automated correctness tests for the cache FSM simulator."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cache_controller import RequestType, WritePolicy, AllocatePolicy
from simulator import Simulator

R = RequestType.READ
W = RequestType.WRITE

passed = 0
failed = 0


def check(name, actual, expected):
    global passed, failed
    if actual == expected:
        print(f"  \033[92mPASS\033[0m  {name}")
        passed += 1
    else:
        print(f"  \033[91mFAIL\033[0m  {name}: expected {expected}, got {actual}")
        failed += 1


def check_close(name, actual, expected, tol=1e-9):
    global passed, failed
    if abs(actual - expected) <= tol:
        print(f"  \033[92mPASS\033[0m  {name}")
        passed += 1
    else:
        print(f"  \033[91mFAIL\033[0m  {name}: expected {expected}, got {actual}")
        failed += 1


def test_read_miss_then_hit():
    print("\n[Test 1] Read miss then read hit")
    sim = Simulator(verbose=False)
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
    result = sim.run([(R, 0x0100, 0), (R, 0x0101, 0)])
    check("First read returns 0xAA", result["results"][0]["data_returned"], 0xAA)
    check("Second read returns 0xBB", result["results"][1]["data_returned"], 0xBB)
    check("1 hit, 1 miss", (result["stats"]["hits"], result["stats"]["misses"]), (1, 1))


def test_write_allocate():
    print("\n[Test 2] Write-allocate policy")
    sim = Simulator(verbose=False)
    sim.init_memory(0x0200, [0x10, 0x20, 0x30, 0x40])
    result = sim.run([(W, 0x0202, 0xFF), (R, 0x0202, 0), (R, 0x0200, 0)])
    check("Read back written value", result["results"][1]["data_returned"], 0xFF)
    check("Read original value at offset 0", result["results"][2]["data_returned"], 0x10)


def test_dirty_writeback():
    print("\n[Test 3] Dirty block write-back on conflict")
    sim = Simulator(verbose=False)
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
    result = sim.run([(W, 0x0001, 0xEE), (R, 0x0100, 0)])
    check("Read from new block", result["results"][1]["data_returned"], 0xAA)
    check("Written data persisted to memory",
          sim.memory.read_word(0x0001), 0xEE)
    check("Original data preserved in memory",
          sim.memory.read_word(0x0000), 0x11)


def test_clean_eviction():
    print("\n[Test 4] Clean eviction skips write-back")
    sim = Simulator(verbose=False)
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
    result = sim.run([(R, 0x0000, 0), (R, 0x0100, 0)])
    check("First read correct", result["results"][0]["data_returned"], 0x11)
    check("Second read correct", result["results"][1]["data_returned"], 0xAA)
    t3 = Simulator(verbose=False)
    t3.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    t3.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
    r_dirty = t3.run([(W, 0x0001, 0xEE), (R, 0x0100, 0)])
    check("Clean eviction fewer cycles than dirty",
          result["stats"]["total_cycles"] < r_dirty["stats"]["total_cycles"], True)


def test_sequential_writes():
    print("\n[Test 5] Sequential writes + readback")
    sim = Simulator(verbose=False)
    result = sim.run([
        (W, 0x0000, 0xA1), (W, 0x0004, 0xB2),
        (W, 0x0008, 0xC3), (W, 0x000C, 0xD4),
        (R, 0x0000, 0), (R, 0x0004, 0),
        (R, 0x0008, 0), (R, 0x000C, 0),
    ])
    check("Readback 0x0000", result["results"][4]["data_returned"], 0xA1)
    check("Readback 0x0004", result["results"][5]["data_returned"], 0xB2)
    check("Readback 0x0008", result["results"][6]["data_returned"], 0xC3)
    check("Readback 0x000C", result["results"][7]["data_returned"], 0xD4)
    check("4 hits on readback", result["stats"]["hits"], 4)


def test_cache_state_validity():
    print("\n[Test 6] Cache line valid/dirty flags")
    sim = Simulator(num_cache_lines=4, verbose=False)
    sim.run([(W, 0x0000, 0xFF), (R, 0x0004, 0)])
    snap = sim.cache_ctrl.get_cache_snapshot()
    check("Line 0 is valid+dirty (written)", snap[0]["valid"] and snap[0]["dirty"], True)
    check("Line 1 is valid+clean (read only)", snap[1]["valid"] and not snap[1]["dirty"], True)
    check("Line 2 is invalid", snap[2]["valid"], False)


def test_address_decomposition():
    print("\n[Test 7] Address decomposition correctness")
    from cache_controller import CacheController
    ctrl = CacheController(num_lines=8, block_size=4, addr_bits=16)
    tag, index, offset = ctrl._decompose_address(0x1234)
    reconstructed = (tag << 5) | (index << 2) | offset
    check("Address round-trip 0x1234", reconstructed, 0x1234)
    tag2, index2, offset2 = ctrl._decompose_address(0xFFFF)
    reconstructed2 = (tag2 << 5) | (index2 << 2) | offset2
    check("Address round-trip 0xFFFF", reconstructed2, 0xFFFF)


def test_cpi_impact_calculator():
    print("\n[Test 8] CPI impact calculator")
    sim = Simulator(verbose=False)
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
    result = sim.run([(R, 0x0100, 0), (R, 0x0101, 0)])
    stats = result["stats"]

    expected_cpi = round(
        stats["base_cpi"] + (stats["memory_stalls"] / stats["instructions"]),
        3,
    )
    check("Effective CPI formula", stats["effective_cpi"], expected_cpi)
    check("Throughput ratio <= 1", stats["throughput_ratio"] <= 1.0, True)
    check("Achieved IPC <= ideal IPC", stats["achieved_ipc"] <= stats["ideal_ipc"], True)


def test_write_buffer_dirty_miss_path():
    print("\n[Test 9] Write buffer dirty miss path")
    sim = Simulator(mem_read_latency=3, mem_write_latency=5, verbose=False)
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])

    result = sim.run([(W, 0x0001, 0xEE), (R, 0x0100, 0)])
    events = [e["event"] for e in result["log"]]

    check("Dirty miss enqueued", "WRITE_BUFFER_ENQUEUE" in events, True)
    check("Buffered write drained", "WRITE_BUFFER_DRAIN_DONE" in events, True)
    check("No write-back wait state", "WRITE_BACK_WAIT" in events, False)
    check("Dirty data persisted", sim.memory.read_word(0x0001), 0xEE)


def test_l2_hit_after_l1_eviction():
    print("\n[Test 10] L2 hit after L1 eviction")
    sim = Simulator(
        num_cache_lines=1,
        mem_read_latency=5,
        mem_write_latency=4,
        l2_num_lines=4,
        l2_latency=1,
        verbose=False,
    )
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0004, [0xAA, 0xBB, 0xCC, 0xDD])

    result = sim.run([(R, 0x0000, 0), (R, 0x0004, 0), (R, 0x0000, 0)])
    stats = result["stats"]

    check("Third read served correctly", result["results"][2]["data_returned"], 0x11)
    check("All three requests miss in L1", stats["l1_misses"], 3)
    check("L2 records one hit", stats["l2_hits"], 1)
    check("L2 records two misses", stats["l2_misses"], 2)
    check("Only two main-memory reads required", stats["main_memory_reads"], 2)


def test_l2_writeback_retains_dirty_block():
    print("\n[Test 11] Dirty block survives in L2")
    sim = Simulator(
        num_cache_lines=1,
        mem_read_latency=5,
        mem_write_latency=4,
        l2_num_lines=4,
        l2_latency=1,
        verbose=False,
    )
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0004, [0xAA, 0xBB, 0xCC, 0xDD])

    result = sim.run([(W, 0x0001, 0xEE), (R, 0x0004, 0), (R, 0x0001, 0)])

    check("Read after L1 eviction gets dirty value back", result["results"][2]["data_returned"], 0xEE)
    check("Hierarchy view sees dirty value", sim.memory.read_word(0x0001), 0xEE)
    check("Backing main memory not updated yet", sim.memory.main_memory.read_word(0x0001), 0x22)


def test_local_vs_global_miss_rates():
    print("\n[Test 12] Local vs global miss rates")
    sim = Simulator(
        num_cache_lines=1,
        mem_read_latency=5,
        mem_write_latency=4,
        l2_num_lines=4,
        l2_latency=1,
        verbose=False,
    )
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0004, [0xAA, 0xBB, 0xCC, 0xDD])

    result = sim.run([
        (R, 0x0000, 0),
        (R, 0x0001, 0),
        (R, 0x0004, 0),
        (R, 0x0005, 0),
        (R, 0x0000, 0),
    ])
    stats = result["stats"]

    check_close("L1 local miss rate", stats["l1_local_miss_rate_value"], 3 / 5)
    check_close("L1 global miss rate", stats["l1_global_miss_rate_value"], 3 / 5)
    check_close("L2 local miss rate", stats["l2_local_miss_rate_value"], 2 / 3)
    check_close("L2 global miss rate", stats["l2_global_miss_rate_value"], 2 / 5)


def test_write_through_keeps_line_clean():
    print("\n[Test 13] Write-through hit keeps line clean")
    sim = Simulator(
        write_policy=WritePolicy.WRITE_THROUGH,
        allocate_policy=AllocatePolicy.WRITE_ALLOCATE,
        verbose=False,
    )
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
    # First read brings the block in; second access writes to it.
    result = sim.run([(R, 0x0100, 0), (W, 0x0102, 0xEE)])

    snap = sim.cache_ctrl.get_cache_snapshot()
    line0 = snap[0]
    check("L1 line valid",        line0["valid"], True)
    check("L1 line stays clean",  line0["dirty"], False)
    check("Write reached memory", sim.memory.read_word(0x0102), 0xEE)
    check("Write-through count",  result["stats"]["write_through_writes"], 1)
    events = [e["event"] for e in result["log"]]
    check("Write-through event logged",
          any("WRITE_THROUGH_ENQUEUE" in e or "WRITE_THROUGH" in e for e in events),
          True)


def test_no_write_allocate_bypass():
    print("\n[Test 14] No-write-allocate bypasses L1 on write miss")
    sim = Simulator(
        write_policy=WritePolicy.WRITE_THROUGH,
        allocate_policy=AllocatePolicy.NO_WRITE_ALLOCATE,
        verbose=False,
    )
    sim.init_memory(0x0200, [0x10, 0x20, 0x30, 0x40])
    # Pure write miss; no L1 line should be installed for this block.
    result = sim.run([(W, 0x0202, 0xFF)])
    stats = result["stats"]

    snap = sim.cache_ctrl.get_cache_snapshot()
    valid_count = sum(1 for s in snap if s["valid"])
    check("No L1 line installed",          valid_count, 0)
    check("Write reached memory",          sim.memory.read_word(0x0202), 0xFF)
    check("Other words preserved",         sim.memory.read_word(0x0200), 0x10)
    check("No-allocate bypass counter",    stats["no_allocate_bypass"], 1)
    check("Cache marks the access a miss", stats["misses"], 1)


def test_victim_cache_catches_conflict():
    print("\n[Test 15] Victim cache turns conflict miss into a hit")
    # Direct-mapped, single line — every distinct block conflicts.
    sim_no_vc = Simulator(num_cache_lines=1, victim_cache_size=0, verbose=False)
    sim_vc    = Simulator(num_cache_lines=1, victim_cache_size=2, verbose=False)
    for sim in (sim_no_vc, sim_vc):
        sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
        sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])

    # Pattern that thrashes a 1-line cache: A, B, A again.
    pattern = [(R, 0x0000, 0), (R, 0x0100, 0), (R, 0x0000, 0)]
    no_vc = sim_no_vc.run(list(pattern))
    vc    = sim_vc.run(list(pattern))

    check("Without victim cache: 3 L1 misses",   no_vc["stats"]["misses"], 3)
    check("Without victim cache: zero VC hits",   no_vc["stats"]["victim_hits"], 0)
    check("Victim cache records a hit",           vc["stats"]["victim_hits"], 1)
    check("Third read still returns 0x11",        vc["results"][2]["data_returned"], 0x11)
    # Victim hits still count as L1 misses (L1 array didn't hold the block),
    # but they short-circuit the trip to L2/memory, so the cycle count drops.
    check("Victim path saves cycles",
          vc["stats"]["total_cycles"] < no_vc["stats"]["total_cycles"], True)


def test_victim_cache_preserves_dirty():
    print("\n[Test 16] Dirty block survives an L1 eviction via victim cache")
    sim = Simulator(num_cache_lines=1, victim_cache_size=2, verbose=False)
    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])

    # Write to A (dirties L1); access B (evicts A into victim, dirty);
    # access A again (victim swap reinstalls dirty A).
    result = sim.run([(W, 0x0001, 0xEE), (R, 0x0100, 0), (R, 0x0001, 0)])

    check("Re-read sees the dirty value",   result["results"][2]["data_returned"], 0xEE)
    # Dirty data lives in L1 after the swap-back (no L2 here, and main memory
    # was never updated because we never drained), so check L1 directly.
    snap = sim.cache_ctrl.get_cache_snapshot()
    line0 = snap[0]
    check("L1 line valid+dirty after swap",
          line0["valid"] and line0["dirty"], True)
    check("L1 line holds the dirty byte",   line0["data"][1], "0xEE")
    check("Main memory not yet updated",    sim.memory.read_word(0x0001), 0x22)
    check("Victim cache fielded the swap",  result["stats"]["victim_swaps"], 1)


if __name__ == "__main__":
    print("\033[1m=== Cache FSM Simulator - Automated Tests ===\033[0m")

    test_read_miss_then_hit()
    test_write_allocate()
    test_dirty_writeback()
    test_clean_eviction()
    test_sequential_writes()
    test_cache_state_validity()
    test_address_decomposition()
    test_cpi_impact_calculator()
    test_write_buffer_dirty_miss_path()
    test_l2_hit_after_l1_eviction()
    test_l2_writeback_retains_dirty_block()
    test_local_vs_global_miss_rates()
    test_write_through_keeps_line_clean()
    test_no_write_allocate_bypass()
    test_victim_cache_catches_conflict()
    test_victim_cache_preserves_dirty()

    print(f"\n\033[1mResults: {passed} passed, {failed} failed\033[0m")
    if failed:
        print("\033[91mSome tests FAILED!\033[0m")
        sys.exit(1)
    else:
        print("\033[92mAll tests PASSED!\033[0m")
