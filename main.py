#!/usr/bin/env python3
"""
Cache Controller FSM Simulator
===============================
Simulates a simple finite state machine based cache controller with:
  - Direct-mapped cache (write-back, write-allocate)
  - 4 FSM states: IDLE, COMPARE_TAG, WRITE_BACK, ALLOCATE
  - Simple CPU issuing read/write from a request queue
  - Simple memory with configurable multi-cycle latency

Usage:
    python main.py              # Run all test scenarios
    python main.py --test N     # Run specific test (1-6)
    python main.py --quiet      # Minimal output
"""

import sys
from cache_controller import RequestType
from simulator import Simulator

R = RequestType.READ
W = RequestType.WRITE


def test_1_read_miss_then_hit():
    """Test 1: Read miss (cold) followed by read hit to same block."""
    sim = Simulator(num_cache_lines=8, block_size=4, addr_bits=16,
                    mem_read_latency=3, mem_write_latency=2)

    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])

    requests = [
        (R, 0x0100, 0),
        (R, 0x0101, 0),
    ]
    return sim.run(requests, label="Test 1: Read Miss → Read Hit (same block)")


def test_2_write_miss_then_read_hit():
    """Test 2: Write miss (allocate + write) then read back the written data."""
    sim = Simulator(num_cache_lines=8, block_size=4, addr_bits=16,
                    mem_read_latency=3, mem_write_latency=2)

    sim.init_memory(0x0200, [0x10, 0x20, 0x30, 0x40])

    requests = [
        (W, 0x0202, 0xFF),
        (R, 0x0202, 0),
        (R, 0x0200, 0),
    ]
    return sim.run(requests, label="Test 2: Write Miss → Read Hit (write-allocate)")


def test_3_write_back_on_conflict():
    """Test 3: Two addresses map to same index, forcing write-back of dirty block."""
    sim = Simulator(num_cache_lines=8, block_size=4, addr_bits=16,
                    mem_read_latency=3, mem_write_latency=2)

    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])

    requests = [
        (W, 0x0001, 0xEE),
        (R, 0x0100, 0),
    ]
    return sim.run(requests,
                   label="Test 3: Write-Back on Conflict (dirty eviction)")


def test_4_clean_eviction():
    """Test 4: Read miss evicts a clean (non-dirty) block — no write-back needed."""
    sim = Simulator(num_cache_lines=8, block_size=4, addr_bits=16,
                    mem_read_latency=3, mem_write_latency=2)

    sim.init_memory(0x0000, [0x11, 0x22, 0x33, 0x44])
    sim.init_memory(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])

    requests = [
        (R, 0x0000, 0),
        (R, 0x0100, 0),
    ]
    return sim.run(requests, label="Test 4: Clean Eviction (no write-back)")


def test_5_sequential_writes():
    """Test 5: Multiple writes to different blocks, then reads to verify."""
    sim = Simulator(num_cache_lines=8, block_size=4, addr_bits=16,
                    mem_read_latency=3, mem_write_latency=2)

    requests = [
        (W, 0x0000, 0xA1),
        (W, 0x0004, 0xB2),
        (W, 0x0008, 0xC3),
        (W, 0x000C, 0xD4),
        (R, 0x0000, 0),
        (R, 0x0004, 0),
        (R, 0x0008, 0),
        (R, 0x000C, 0),
    ]
    return sim.run(requests,
                   label="Test 5: Sequential Writes + Readback Verification")


def test_6_full_stress():
    """Test 6: Stress test — fills cache, causes multiple evictions and write-backs."""
    sim = Simulator(num_cache_lines=4, block_size=4, addr_bits=16,
                    mem_read_latency=4, mem_write_latency=3)

    for i in range(8):
        sim.init_memory(i * 0x40, [(i * 16 + j) & 0xFF for j in range(4)])

    requests = [
        (W, 0x0000, 0xF0),
        (W, 0x0040, 0xF1),
        (W, 0x0080, 0xF2),
        (W, 0x00C0, 0xF3),
        (R, 0x0100, 0),
        (R, 0x0140, 0),
        (R, 0x0000, 0),
        (W, 0x0000, 0xAA),
        (R, 0x0001, 0),
        (R, 0x0100, 0),
    ]
    return sim.run(requests,
                   label="Test 6: Stress Test (4-line cache, heavy eviction)")


ALL_TESTS = [
    ("1", "Read Miss → Read Hit",            test_1_read_miss_then_hit),
    ("2", "Write Miss → Read Hit",           test_2_write_miss_then_read_hit),
    ("3", "Dirty Eviction (Write-Back)",      test_3_write_back_on_conflict),
    ("4", "Clean Eviction",                   test_4_clean_eviction),
    ("5", "Sequential Writes + Readback",     test_5_sequential_writes),
    ("6", "Stress Test (small cache)",        test_6_full_stress),
]


def main():
    args = sys.argv[1:]

    test_num = None
    if "--test" in args:
        idx = args.index("--test")
        if idx + 1 < len(args):
            test_num = args[idx + 1]

    if test_num:
        for num, desc, func in ALL_TESTS:
            if num == test_num:
                func()
                return
        print(f"Unknown test number: {test_num}")
        print(f"Available: {', '.join(t[0] for t in ALL_TESTS)}")
        sys.exit(1)
    else:
        for num, desc, func in ALL_TESTS:
            func()
            print("\n")


if __name__ == "__main__":
    main()