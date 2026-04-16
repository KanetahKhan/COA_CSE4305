#!/usr/bin/env python3
"""Automated correctness tests for the cache FSM simulator."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cache_controller import RequestType
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

    print(f"\n\033[1mResults: {passed} passed, {failed} failed\033[0m")
    if failed:
        print("\033[91mSome tests FAILED!\033[0m")
        sys.exit(1)
    else:
        print("\033[92mAll tests PASSED!\033[0m")