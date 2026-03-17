import random as _random
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class State(Enum):
    IDLE = "IDLE"
    COMPARE_TAG = "COMPARE_TAG"
    WRITE_BACK = "WRITE_BACK"
    ALLOCATE = "ALLOCATE"


class RequestType(Enum):
    READ = 0
    WRITE = 1


class Policy(Enum):
    DIRECT = "Direct-Mapped"
    LRU    = "LRU"
    LFU    = "LFU"
    RANDOM = "Random"


@dataclass
class CacheLine:
    valid: bool = False
    dirty: bool = False
    tag: int = 0
    data: list = field(default_factory=lambda: [0] * 4)
    lru_counter: int = 0   # timestamp of last access (higher = more recent)
    access_count: int = 0  # total accesses, used by LFU


@dataclass
class CPUInterface:
    valid: bool = False
    read_write: Optional[RequestType] = None
    address: int = 0
    data_in: int = 0
    data_out: int = 0
    ready: bool = False
    stall: bool = False


@dataclass
class MemoryInterface:
    read: bool = False
    write: bool = False
    address: int = 0
    data_in: list = field(default_factory=lambda: [0] * 4)
    data_out: list = field(default_factory=lambda: [0] * 4)
    ready: bool = False


class CacheController:
    def __init__(self, num_lines=8, block_size=4, addr_bits=16,
                 associativity=1, policy=Policy.DIRECT):
        self.num_lines = num_lines
        self.block_size = block_size
        self.addr_bits = addr_bits
        self.associativity = max(1, associativity)
        # Force DIRECT policy for direct-mapped (1-way)
        self.policy = Policy.DIRECT if self.associativity == 1 else policy

        self.num_sets = num_lines // self.associativity

        self.offset_bits = (block_size - 1).bit_length()
        self.index_bits  = (self.num_sets - 1).bit_length()   # 0 when num_sets==1
        self.tag_bits    = addr_bits - self.offset_bits - self.index_bits

        # 2-D structure: cache[set_idx][way] = CacheLine
        self.cache = [
            [CacheLine(data=[0] * block_size) for _ in range(self.associativity)]
            for _ in range(self.num_sets)
        ]

        self.state      = State.IDLE
        self.prev_state = State.IDLE

        self.cpu = CPUInterface()
        self.mem = MemoryInterface()

        self.saved_address = 0
        self.saved_request = None
        self.saved_data    = 0
        self.saved_set     = 0   # set index of the current miss
        self.saved_way     = 0   # victim way index of the current miss
        self.wb_counter    = 0
        self.alloc_counter = 0
        self._lru_time     = 0   # global tick used for LRU stamping

        self.hits            = 0
        self.misses          = 0
        self.total_requests  = 0
        self.cycle           = 0

        self.log = []

    # ------------------------------------------------------------------
    # Address helpers
    # ------------------------------------------------------------------

    def _decompose_address(self, address):
        offset  = address & ((1 << self.offset_bits) - 1)
        index   = (address >> self.offset_bits) & ((1 << self.index_bits) - 1) \
                  if self.index_bits > 0 else 0
        tag     = address >> (self.offset_bits + self.index_bits)
        tag    &= (1 << self.tag_bits) - 1
        return tag, index, offset

    def _block_address(self, tag, set_idx):
        return (tag << (self.offset_bits + self.index_bits)) | (set_idx << self.offset_bits)

    # ------------------------------------------------------------------
    # Replacement-policy helpers
    # ------------------------------------------------------------------

    def _find_hit_way(self, set_idx, tag):
        """Return the way index of a matching valid line, or -1 on miss."""
        for way, line in enumerate(self.cache[set_idx]):
            if line.valid and line.tag == tag:
                return way
        return -1

    def _find_victim_way(self, set_idx):
        """
        Select which way to evict.
        Always prefer an invalid (empty) slot before consulting the policy.
        """
        ways = self.cache[set_idx]

        # Cold-start: fill invalid slots first
        for w, line in enumerate(ways):
            if not line.valid:
                return w

        if self.policy in (Policy.DIRECT, Policy.LRU):
            # LRU: evict the way with the oldest (smallest) access timestamp
            return min(range(self.associativity), key=lambda w: ways[w].lru_counter)

        if self.policy == Policy.LFU:
            # LFU: evict least-frequently-used; break ties with LRU order
            return min(range(self.associativity),
                       key=lambda w: (ways[w].access_count, ways[w].lru_counter))

        if self.policy == Policy.RANDOM:
            return _random.randrange(self.associativity)

        return 0  # fallback

    def _touch(self, set_idx, way):
        """Update LRU timestamp and increment access count for a way."""
        self._lru_time += 1
        self.cache[set_idx][way].lru_counter = self._lru_time
        self.cache[set_idx][way].access_count += 1

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_event(self, event, details=""):
        entry = {
            "cycle":      self.cycle,
            "state":      self.state.value,
            "prev_state": self.prev_state.value,
            "event":      event,
            "details":    details,
            "cpu_ready":  self.cpu.ready,
            "cpu_stall":  self.cpu.stall,
            "mem_read":   self.mem.read,
            "mem_write":  self.mem.write,
        }
        self.log.append(entry)

    # ------------------------------------------------------------------
    # CPU interface
    # ------------------------------------------------------------------

    def submit_request(self, req_type: RequestType, address: int, data: int = 0):
        self.cpu.valid      = True
        self.cpu.read_write = req_type
        self.cpu.address    = address
        self.cpu.data_in    = data
        self.cpu.ready      = False
        self.cpu.stall      = False

    def clear_request(self):
        self.cpu.valid      = False
        self.cpu.read_write = None
        self.cpu.address    = 0
        self.cpu.data_in    = 0

    # ------------------------------------------------------------------
    # FSM tick
    # ------------------------------------------------------------------

    def tick(self):
        self.cycle     += 1
        self.prev_state = self.state
        self.cpu.ready  = False
        self.mem.read   = False
        self.mem.write  = False

        if self.state == State.IDLE:
            self._handle_idle()
        elif self.state == State.COMPARE_TAG:
            self._handle_compare_tag()
        elif self.state == State.WRITE_BACK:
            self._handle_write_back()
        elif self.state == State.ALLOCATE:
            self._handle_allocate()

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_idle(self):
        if self.cpu.valid:
            self.saved_address  = self.cpu.address
            self.saved_request  = self.cpu.read_write
            self.saved_data     = self.cpu.data_in
            self.cpu.stall      = True
            self.state          = State.COMPARE_TAG
            self._log_event(
                "REQUEST_RECEIVED",
                f"{'READ' if self.saved_request == RequestType.READ else 'WRITE'} "
                f"addr=0x{self.saved_address:04X} data=0x{self.saved_data:02X}"
            )
        else:
            self._log_event("IDLE", "Waiting for CPU request")

    def _handle_compare_tag(self):
        tag, set_idx, offset = self._decompose_address(self.saved_address)

        self.total_requests += 1
        hit_way = self._find_hit_way(set_idx, tag)

        if hit_way >= 0:
            # ---- HIT ----
            self.hits += 1
            line = self.cache[set_idx][hit_way]
            self._touch(set_idx, hit_way)         # update LRU stamp + access count

            if self.saved_request == RequestType.READ:
                self.cpu.data_out = line.data[offset]
                self._log_event(
                    "CACHE_HIT_READ",
                    f"tag=0x{tag:X} set={set_idx} way={hit_way} offset={offset} "
                    f"data=0x{line.data[offset]:02X}"
                )
            else:
                line.data[offset] = self.saved_data
                line.dirty = True
                self._log_event(
                    "CACHE_HIT_WRITE",
                    f"tag=0x{tag:X} set={set_idx} way={hit_way} offset={offset} "
                    f"wrote=0x{self.saved_data:02X} (dirty=True)"
                )
            self.cpu.ready = True
            self.cpu.stall = False
            self.state     = State.IDLE

        else:
            # ---- MISS ----
            self.misses   += 1
            victim_way     = self._find_victim_way(set_idx)
            self.saved_set = set_idx
            self.saved_way = victim_way
            victim_line    = self.cache[set_idx][victim_way]

            if victim_line.valid and victim_line.dirty:
                self.wb_counter = 0
                wb_addr = self._block_address(victim_line.tag, set_idx)
                self._log_event(
                    "CACHE_MISS_DIRTY",
                    f"tag=0x{tag:X} set={set_idx} way={victim_way} "
                    f"evicting dirty block (old_tag=0x{victim_line.tag:X}) "
                    f"wb_addr=0x{wb_addr:04X}"
                )
                self.state = State.WRITE_BACK
            else:
                self.alloc_counter = 0
                self._log_event(
                    "CACHE_MISS_CLEAN",
                    f"tag=0x{tag:X} set={set_idx} way={victim_way} "
                    f"{'(invalid line)' if not victim_line.valid else '(clean eviction)'}"
                )
                self.state = State.ALLOCATE

    def _handle_write_back(self):
        set_idx = self.saved_set
        way     = self.saved_way
        line    = self.cache[set_idx][way]
        wb_addr = self._block_address(line.tag, set_idx)

        self.mem.write   = True
        self.mem.address = wb_addr
        self.mem.data_out = list(line.data)

        self.wb_counter += 1

        if self.mem.ready:
            self._log_event(
                "WRITE_BACK_DONE",
                f"Wrote block to mem addr=0x{wb_addr:04X} "
                f"data={[f'0x{d:02X}' for d in line.data]} "
                f"(took {self.wb_counter} cycles)"
            )
            line.dirty         = False
            self.alloc_counter = 0
            self.state         = State.ALLOCATE
        else:
            self._log_event(
                "WRITE_BACK_WAIT",
                f"Writing to mem addr=0x{wb_addr:04X} "
                f"cycle {self.wb_counter} of transfer"
            )

    def _handle_allocate(self):
        tag, _, offset = self._decompose_address(self.saved_address)
        set_idx    = self.saved_set
        way        = self.saved_way
        block_addr = self._block_address(tag, set_idx)

        self.mem.read    = True
        self.mem.address = block_addr

        self.alloc_counter += 1

        if self.mem.ready:
            line              = self.cache[set_idx][way]
            line.valid        = True
            line.tag          = tag
            line.data         = list(self.mem.data_in)
            line.dirty        = False
            line.access_count = 0          # reset before _touch increments it
            self._touch(set_idx, way)      # stamp LRU + access_count = 1

            if self.saved_request == RequestType.READ:
                self.cpu.data_out = line.data[offset]
                self._log_event(
                    "ALLOCATE_DONE_READ",
                    f"Fetched block from mem addr=0x{block_addr:04X} "
                    f"data={[f'0x{d:02X}' for d in line.data]} "
                    f"returned=0x{line.data[offset]:02X} "
                    f"(took {self.alloc_counter} cycles)"
                )
            else:
                line.data[offset] = self.saved_data
                line.dirty        = True
                self._log_event(
                    "ALLOCATE_DONE_WRITE",
                    f"Fetched block from mem addr=0x{block_addr:04X} "
                    f"then wrote=0x{self.saved_data:02X} at offset={offset} "
                    f"(dirty=True, took {self.alloc_counter} cycles)"
                )

            self.cpu.ready = True
            self.cpu.stall = False
            self.state     = State.IDLE
        else:
            self._log_event(
                "ALLOCATE_WAIT",
                f"Reading from mem addr=0x{block_addr:04X} "
                f"cycle {self.alloc_counter} of transfer"
            )

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_cache_snapshot(self):
        """
        Returns a flat list of dicts, one per (set, way) pair.
        Flat ordering: set 0 way 0, set 0 way 1, ..., set N way W.
        """
        snapshot = []
        for set_idx, ways in enumerate(self.cache):
            for way_idx, line in enumerate(ways):
                snapshot.append({
                    "index": set_idx * self.associativity + way_idx,  # legacy key
                    "set":   set_idx,
                    "way":   way_idx,
                    "valid": line.valid,
                    "dirty": line.dirty,
                    "tag":   f"0x{line.tag:X}",
                    "data":  [f"0x{d:02X}" for d in line.data],
                    "lru":   line.lru_counter,
                    "freq":  line.access_count,
                })
        return snapshot

    def get_stats(self):
        return {
            "total_requests": self.total_requests,
            "hits":           self.hits,
            "misses":         self.misses,
            "hit_rate":       f"{self.hits / self.total_requests * 100:.1f}%"
                              if self.total_requests else "N/A",
            "total_cycles":   self.cycle,
        }
