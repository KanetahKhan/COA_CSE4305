import random as _random
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class State(Enum):
    IDLE = "IDLE"
    COMPARE_TAG = "COMPARE_TAG"
    WRITE_BACK = "WRITE_BACK"
    ALLOCATE = "ALLOCATE"
    WRITE_THROUGH = "WRITE_THROUGH"   # waiting for write-buffer room


class RequestType(Enum):
    READ = 0
    WRITE = 1


class Policy(Enum):
    DIRECT = "Direct-Mapped"
    LRU    = "LRU"
    LFU    = "LFU"
    RANDOM = "Random"


class WritePolicy(Enum):
    WRITE_BACK    = "Write-Back"
    WRITE_THROUGH = "Write-Through"


class AllocatePolicy(Enum):
    WRITE_ALLOCATE    = "Write-Allocate"
    NO_WRITE_ALLOCATE = "No-Write-Allocate"


class VictimCache:
    """Small fully-associative FIFO buffer that catches recently evicted blocks."""

    def __init__(self, size, block_size):
        self.size = max(0, int(size))
        self.block_size = block_size
        self.entries = []          # FIFO list of dicts: tag, set, dirty, data
        self.accesses = 0          # lookups (only when enabled)
        self.hits = 0
        self.swaps = 0             # successful victim->L1 swaps
        self.installs = 0          # blocks pushed in from L1 evictions
        self.dirty_evictions = 0   # FIFO-evicted dirty blocks needing write-back

    @property
    def enabled(self):
        return self.size > 0

    def lookup(self, tag, set_idx):
        for i, entry in enumerate(self.entries):
            if entry["tag"] == tag and entry["set"] == set_idx:
                return i, entry
        return -1, None

    def remove(self, idx):
        return self.entries.pop(idx)

    def push(self, tag, set_idx, dirty, data):
        """Insert a block; FIFO-evict the oldest if full. Returns evicted entry or None."""
        evicted = None
        if len(self.entries) >= self.size:
            evicted = self.entries.pop(0)
            if evicted["dirty"]:
                self.dirty_evictions += 1
        self.entries.append({
            "tag": tag,
            "set": set_idx,
            "dirty": bool(dirty),
            "data": list(data),
        })
        self.installs += 1
        return evicted

    def snapshot(self):
        return [
            {
                "slot": i,
                "tag": f"0x{e['tag']:X}",
                "set": e["set"],
                "dirty": e["dirty"],
                "data": [f"0x{d:02X}" for d in e["data"]],
            }
            for i, e in enumerate(self.entries)
        ]


@dataclass
class CacheLine:
    valid: bool = False
    dirty: bool = False
    tag: int = 0
    data: list = field(default_factory=lambda: [0] * 4)
    lru_counter: int = 0   # timestamp of last access (higher = more recent)
    access_count: int = 0  # total accesses, used by LFU
    write_count: int = 0   # dirty writes to this line (reset on eviction)


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
                 associativity=1, policy=Policy.DIRECT,
                 base_cpi=1.0,
                 write_policy=WritePolicy.WRITE_BACK,
                 allocate_policy=AllocatePolicy.WRITE_ALLOCATE,
                 victim_cache_size=0):
        self.num_lines = num_lines
        self.block_size = block_size
        self.addr_bits = addr_bits
        self.associativity = max(1, associativity)
        # Force DIRECT policy for direct-mapped (1-way)
        self.policy = Policy.DIRECT if self.associativity == 1 else policy

        self.num_sets = num_lines // self.associativity
        self.base_cpi = float(base_cpi)
        self.write_policy = write_policy
        self.allocate_policy = allocate_policy
        self.victim_cache = VictimCache(victim_cache_size, block_size)

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
        self._write_buffer_has_room = True
        self._pending_writeback_enqueue = None
        # Write-through deferred enqueue: dict {address, value, offset} or None
        self._pending_partial_write = None

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

        # Miss classification
        self._seen_blocks     = set()   # blocks accessed at least once (for compulsory detection)
        self.compulsory_misses = 0      # cold / first-access misses
        self.conflict_misses   = 0      # replacement misses (block was evicted)

        # Stall / bus tracking
        self.stall_cycles      = 0      # cycles CPU is stalled
        self.bus_reads         = 0      # memory read transactions (allocations)
        self.bus_writes        = 0      # memory write transactions (write-backs)
        self.total_miss_penalty = 0     # cumulative cycles spent servicing misses
        self._miss_start_cycle  = 0     # cycle when current miss handling began
        self.write_buffer_depth = 0
        self.write_buffer_max_occupancy = 0
        self.write_buffer_size = 0
        self._hierarchy_stats = {}

        self.write_through_writes = 0    # write-through propagations enqueued
        self.no_allocate_bypass   = 0    # no-write-allocate writes that skipped L1
        # Pending request that's mid-write-through (waiting for buffer)
        self._pending_wt_complete = None

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

    def set_write_buffer_status(self, has_room: bool):
        self._write_buffer_has_room = bool(has_room)

    def consume_enqueued_writeback(self):
        wb = self._pending_writeback_enqueue
        self._pending_writeback_enqueue = None
        return wb

    def consume_enqueued_partial_write(self):
        pw = self._pending_partial_write
        self._pending_partial_write = None
        return pw

    def notify_writeback_completed(self, address, data):
        self.bus_writes += 1
        self._log_event(
            "WRITE_BUFFER_DRAIN_DONE",
            f"Wrote buffered block to mem addr=0x{address:04X} "
            f"data={[f'0x{d:02X}' for d in data]}"
        )

    def set_write_buffer_stats(self, depth, max_occupancy, size):
        self.write_buffer_depth = int(depth)
        self.write_buffer_max_occupancy = int(max_occupancy)
        self.write_buffer_size = int(size)

    def set_hierarchy_stats(self, stats):
        self._hierarchy_stats = dict(stats or {})

    # ------------------------------------------------------------------
    # FSM tick
    # ------------------------------------------------------------------

    def tick(self):
        self.cycle     += 1
        self.prev_state = self.state
        self.cpu.ready  = False
        self.mem.read   = False
        self.mem.write  = False
        self._pending_writeback_enqueue = None
        self._pending_partial_write = None

        if self.cpu.stall:
            self.stall_cycles += 1

        if self.state == State.IDLE:
            self._handle_idle()
        elif self.state == State.COMPARE_TAG:
            self._handle_compare_tag()
        elif self.state == State.WRITE_BACK:
            self._handle_write_back()
        elif self.state == State.ALLOCATE:
            self._handle_allocate()
        elif self.state == State.WRITE_THROUGH:
            self._handle_write_through()

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Write-through / victim cache helpers
    # ------------------------------------------------------------------

    def _begin_write_through(self, address, offset, value):
        """Try to enqueue a write-through; defer to WRITE_THROUGH state if buffer full."""
        if self._write_buffer_has_room:
            self._pending_partial_write = {
                "address": address,
                "offset":  offset,
                "value":   value,
            }
            self.write_through_writes += 1
            self._log_event(
                "WRITE_THROUGH_ENQUEUE",
                f"Enqueued write-through addr=0x{address:04X} "
                f"offset={offset} value=0x{value:02X}"
            )
            self.cpu.ready = True
            self.cpu.stall = False
            self.state     = State.IDLE
        else:
            self._pending_wt_complete = {
                "address": address,
                "offset":  offset,
                "value":   value,
            }
            self._log_event(
                "WRITE_BUFFER_FULL_WAIT",
                f"Write-through deferred (buffer full) addr=0x{address:04X}"
            )
            self.state = State.WRITE_THROUGH

    def _try_victim_swap(self, tag, set_idx, victim_way, offset):
        """If the requested block lives in the victim cache, swap it back into L1
        and complete the access. Returns True if handled."""
        if not self.victim_cache.enabled:
            return False
        self.victim_cache.accesses += 1
        idx, entry = self.victim_cache.lookup(tag, set_idx)
        if entry is None:
            return False

        # Need room in the write buffer if the swap displaces a dirty L1 line
        # AND the FIFO push would also evict a dirty victim entry.
        evict_l1 = self.cache[set_idx][victim_way]
        l1_dirty = evict_l1.valid and evict_l1.dirty
        wb_pressure = (l1_dirty and self.victim_cache.size <= len(self.victim_cache.entries) - 1
                       and any(e["dirty"] for e in self.victim_cache.entries[:1]))

        # Be conservative: if the FIFO eviction could spill a dirty block to
        # the write buffer, require buffer room for that one outgoing write.
        if wb_pressure and not self._write_buffer_has_room:
            self._log_event(
                "VICTIM_DEFER",
                "Victim swap deferred (write buffer full)"
            )
            return False

        self.victim_cache.hits += 1
        self.victim_cache.swaps += 1
        # Remove victim entry; install its block into L1 at the chosen way.
        self.victim_cache.remove(idx)
        # Push the current L1 line (if valid) into the victim cache
        evicted = None
        if evict_l1.valid:
            evicted = self.victim_cache.push(
                evict_l1.tag, set_idx, evict_l1.dirty, evict_l1.data,
            )
        if evicted is not None and evicted["dirty"]:
            ev_addr = self._block_address(evicted["tag"], evicted["set"])
            self._pending_writeback_enqueue = {
                "address": ev_addr,
                "data": list(evicted["data"]),
            }
            self._log_event(
                "VICTIM_FIFO_WRITEBACK",
                f"Victim FIFO evicted dirty addr=0x{ev_addr:04X} -> write buffer"
            )

        new_line = self.cache[set_idx][victim_way]
        new_line.valid       = True
        new_line.tag         = tag
        new_line.data        = list(entry["data"])
        new_line.dirty       = entry["dirty"]
        new_line.access_count = 0
        new_line.write_count  = 0
        self._touch(set_idx, victim_way)

        wb_addr = self._block_address(tag, set_idx)
        if self.saved_request == RequestType.READ:
            self.cpu.data_out = new_line.data[offset]
            self._log_event(
                "VICTIM_HIT_READ",
                f"Victim cache hit addr=0x{wb_addr:04X} "
                f"-> swapped into L1 set={set_idx} way={victim_way} "
                f"data=0x{new_line.data[offset]:02X}"
            )
            self.cpu.ready = True
            self.cpu.stall = False
            self.state     = State.IDLE
        else:
            new_line.data[offset] = self.saved_data
            new_line.write_count += 1
            if self.write_policy == WritePolicy.WRITE_THROUGH:
                new_line.dirty = False
                self._log_event(
                    "VICTIM_HIT_WRITE_THROUGH",
                    f"Victim cache hit addr=0x{wb_addr:04X} "
                    f"-> swapped into L1, wrote=0x{self.saved_data:02X} "
                    f"(forwarding to memory)"
                )
                self._begin_write_through(self.saved_address, offset, self.saved_data)
            else:
                new_line.dirty = True
                self._log_event(
                    "VICTIM_HIT_WRITE",
                    f"Victim cache hit addr=0x{wb_addr:04X} "
                    f"-> swapped into L1, wrote=0x{self.saved_data:02X} "
                    f"(dirty=True)"
                )
                self.cpu.ready = True
                self.cpu.stall = False
                self.state     = State.IDLE
        return True

    def _push_victim_clean(self, line, set_idx):
        """Push a clean L1 eviction into the victim cache. Dirty FIFO evictions
        go to the write buffer."""
        evicted = self.victim_cache.push(line.tag, set_idx, False, line.data)
        if evicted is not None and evicted["dirty"]:
            if self._write_buffer_has_room:
                ev_addr = self._block_address(evicted["tag"], evicted["set"])
                self._pending_writeback_enqueue = {
                    "address": ev_addr,
                    "data": list(evicted["data"]),
                }
                self._log_event(
                    "VICTIM_FIFO_WRITEBACK",
                    f"Victim FIFO evicted dirty addr=0x{ev_addr:04X} -> write buffer"
                )

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

        block_key = (tag, set_idx)

        if hit_way >= 0:
            # ---- HIT ----
            self.hits += 1
            self._seen_blocks.add(block_key)
            line = self.cache[set_idx][hit_way]
            self._touch(set_idx, hit_way)         # update LRU stamp + access count

            if self.saved_request == RequestType.READ:
                self.cpu.data_out = line.data[offset]
                self._log_event(
                    "CACHE_HIT_READ",
                    f"tag=0x{tag:X} set={set_idx} way={hit_way} offset={offset} "
                    f"data=0x{line.data[offset]:02X}"
                )
                self.cpu.ready = True
                self.cpu.stall = False
                self.state     = State.IDLE
            else:
                line.data[offset] = self.saved_data
                line.write_count += 1
                if self.write_policy == WritePolicy.WRITE_THROUGH:
                    # Update L1 but stay clean; propagate to memory.
                    line.dirty = False
                    self._log_event(
                        "CACHE_HIT_WRITE_THROUGH",
                        f"tag=0x{tag:X} set={set_idx} way={hit_way} offset={offset} "
                        f"wrote=0x{self.saved_data:02X} (forwarding to memory)"
                    )
                    self._begin_write_through(self.saved_address, offset, self.saved_data)
                else:
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
            if block_key not in self._seen_blocks:
                self.compulsory_misses += 1
            else:
                self.conflict_misses += 1
            self._seen_blocks.add(block_key)
            self._miss_start_cycle = self.cycle

            # Write-miss with no-write-allocate: bypass L1, send write to memory.
            if (self.saved_request == RequestType.WRITE
                    and self.allocate_policy == AllocatePolicy.NO_WRITE_ALLOCATE):
                self.no_allocate_bypass += 1
                self._log_event(
                    "CACHE_MISS_NO_ALLOCATE",
                    f"tag=0x{tag:X} set={set_idx} offset={offset} "
                    f"wrote=0x{self.saved_data:02X} (bypass — no L1 install)"
                )
                self._begin_write_through(self.saved_address, offset, self.saved_data)
                return

            victim_way     = self._find_victim_way(set_idx)
            self.saved_set = set_idx
            self.saved_way = victim_way

            # Victim cache: catch the requested block before going to L2/memory.
            if self._try_victim_swap(tag, set_idx, victim_way, offset):
                return

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
                # Push the (clean) evicted block into the victim cache too,
                # if enabled and the line was previously valid.
                if self.victim_cache.enabled and victim_line.valid:
                    self._push_victim_clean(victim_line, set_idx)
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

        if not self._write_buffer_has_room:
            self._log_event(
                "WRITE_BUFFER_FULL_WAIT",
                f"Buffer full; cannot enqueue dirty block addr=0x{wb_addr:04X}"
            )
            return

        # If victim cache is enabled, divert the dirty block there instead of
        # straight to the write buffer. The block keeps its dirty flag in the
        # victim cache; only a FIFO eviction triggers an actual write-back.
        if self.victim_cache.enabled:
            evicted = self.victim_cache.push(line.tag, set_idx, True, line.data)
            self.victim_cache.swaps  # noqa: B018 (no-op; just to expose attr existence)
            line.dirty = False
            self.alloc_counter = 0
            self._log_event(
                "VICTIM_INSTALL_DIRTY",
                f"Diverted dirty eviction to victim cache addr=0x{wb_addr:04X}"
            )
            if evicted is not None and evicted["dirty"]:
                ev_addr = self._block_address(evicted["tag"], evicted["set"])
                self._pending_writeback_enqueue = {
                    "address": ev_addr,
                    "data": list(evicted["data"]),
                }
                self._log_event(
                    "VICTIM_FIFO_WRITEBACK",
                    f"Victim FIFO evicted dirty addr=0x{ev_addr:04X} "
                    f"-> write buffer"
                )
            self.state = State.ALLOCATE
            return

        self._pending_writeback_enqueue = {
            "address": wb_addr,
            "data": list(line.data),
        }
        line.dirty = False
        self.alloc_counter = 0
        self.state = State.ALLOCATE
        self._log_event(
            "WRITE_BUFFER_ENQUEUE",
            f"Enqueued dirty block addr=0x{wb_addr:04X} "
            f"data={[f'0x{d:02X}' for d in line.data]}"
        )

    def _handle_write_through(self):
        """Retry a deferred write-through enqueue (write buffer was full)."""
        if not self._write_buffer_has_room:
            self._log_event(
                "WRITE_BUFFER_FULL_WAIT",
                "Buffer full; cannot enqueue write-through"
            )
            return

        info = self._pending_wt_complete
        if info is None:
            # Defensive: should not happen, but recover gracefully.
            self.state = State.IDLE
            return
        self._pending_partial_write = {
            "address": info["address"],
            "offset":  info["offset"],
            "value":   info["value"],
        }
        self.write_through_writes += 1
        self._log_event(
            "WRITE_THROUGH_ENQUEUE",
            f"Enqueued write-through addr=0x{info['address']:04X} "
            f"offset={info['offset']} value=0x{info['value']:02X}"
        )
        self._pending_wt_complete = None
        self.cpu.ready = True
        self.cpu.stall = False
        self.state     = State.IDLE

    def _handle_allocate(self):
        tag, _, offset = self._decompose_address(self.saved_address)
        set_idx    = self.saved_set
        way        = self.saved_way
        block_addr = self._block_address(tag, set_idx)

        self.mem.read    = True
        self.mem.address = block_addr

        self.alloc_counter += 1

        if self.mem.ready:
            self.bus_reads += 1
            self.total_miss_penalty += (self.cycle - self._miss_start_cycle)
            line              = self.cache[set_idx][way]
            line.valid        = True
            line.tag          = tag
            line.data         = list(self.mem.data_in)
            line.dirty        = False
            line.access_count = 0          # reset before _touch increments it
            line.write_count  = 0          # fresh occupant — reset dirty-write counter
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
                self.cpu.ready = True
                self.cpu.stall = False
                self.state     = State.IDLE
            else:
                line.data[offset] = self.saved_data
                line.write_count += 1
                if self.write_policy == WritePolicy.WRITE_THROUGH:
                    line.dirty = False
                    self._log_event(
                        "ALLOCATE_DONE_WRITE_THROUGH",
                        f"Fetched block from mem addr=0x{block_addr:04X} "
                        f"then wrote=0x{self.saved_data:02X} at offset={offset} "
                        f"(forwarding to memory, took {self.alloc_counter} cycles)"
                    )
                    self._begin_write_through(self.saved_address, offset, self.saved_data)
                else:
                    line.dirty = True
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
                    "lru":        line.lru_counter,
                    "freq":       line.access_count,
                    "write_count": line.write_count,
                })
        return snapshot

    def get_stats(self):
        total_requests = self.total_requests
        total = total_requests or 1
        miss_r = self.misses / total
        hit_r  = self.hits / total
        avg_miss_penalty = (self.total_miss_penalty / self.misses
                            if self.misses else 0)
        # AMAT = Hit Time + Miss Rate × Miss Penalty
        # Hit Time = 1 cycle (compare-tag takes 1 cycle on hit)
        amat = 1 + miss_r * avg_miss_penalty

        instructions = self.total_requests
        mem_stalls_per_instruction = (
            self.stall_cycles / instructions if instructions else 0
        )
        effective_cpi = self.base_cpi + mem_stalls_per_instruction
        # Throughput is inverse of CPI for a fixed clock.
        ideal_ipc = (1.0 / self.base_cpi) if self.base_cpi > 0 else 0
        achieved_ipc = (1.0 / effective_cpi) if effective_cpi > 0 else 0
        throughput_ratio = (achieved_ipc / ideal_ipc) if ideal_ipc > 0 else 0
        throughput_loss_pct = (1.0 - throughput_ratio) * 100 if ideal_ipc > 0 else 0
        hier = self._hierarchy_stats or {}
        l2_enabled = bool(hier.get("l2_enabled", False))
        l1_local_miss_rate = (self.misses / total_requests) if total_requests else 0
        l1_global_miss_rate = l1_local_miss_rate

        l2_accesses = int(hier.get("l2_accesses", 0))
        l2_hits = int(hier.get("l2_hits", 0))
        l2_misses = int(hier.get("l2_misses", 0))
        l2_hit_rate_value = (l2_hits / l2_accesses) if l2_accesses else 0
        l2_local_miss_rate_value = (l2_misses / l2_accesses) if l2_accesses else 0
        l2_global_miss_rate_value = (l2_misses / total_requests) if total_requests else 0

        def _pct(value, valid):
            return f"{value * 100:.1f}%" if valid else "N/A"

        stats = {
            "total_requests":    self.total_requests,
            "hits":              self.hits,
            "misses":            self.misses,
            "hit_rate":          f"{hit_r * 100:.1f}%"
                                 if self.total_requests else "N/A",
            "total_cycles":      self.cycle,
            "compulsory_misses": self.compulsory_misses,
            "conflict_misses":   self.conflict_misses,
            "stall_cycles":      self.stall_cycles,
            "bus_reads":         self.bus_reads,
            "bus_writes":        self.bus_writes,
            "avg_miss_penalty":  round(avg_miss_penalty, 1),
            "amat":              round(amat, 2),
            "base_cpi":          round(self.base_cpi, 3),
            "instructions":      instructions,
            "memory_stalls":     self.stall_cycles,
            "stalls_per_instruction": round(mem_stalls_per_instruction, 3),
            "effective_cpi":     round(effective_cpi, 3),
            "ideal_ipc":         round(ideal_ipc, 3),
            "achieved_ipc":      round(achieved_ipc, 3),
            "throughput_ratio":  round(throughput_ratio, 3),
            "throughput_loss_pct": round(throughput_loss_pct, 1),
            "write_buffer_depth": self.write_buffer_depth,
            "write_buffer_max_occupancy": self.write_buffer_max_occupancy,
            "write_buffer_size": self.write_buffer_size,
            "write_policy":       self.write_policy.value,
            "allocate_policy":    self.allocate_policy.value,
            "write_through_writes": self.write_through_writes,
            "no_allocate_bypass": self.no_allocate_bypass,
            "victim_cache_enabled": self.victim_cache.enabled,
            "victim_cache_size":  self.victim_cache.size,
            "victim_cache_depth": len(self.victim_cache.entries),
            "victim_accesses":    self.victim_cache.accesses,
            "victim_hits":        self.victim_cache.hits,
            "victim_swaps":       self.victim_cache.swaps,
            "victim_installs":    self.victim_cache.installs,
            "victim_dirty_evictions": self.victim_cache.dirty_evictions,
            "victim_hit_rate":    _pct(
                (self.victim_cache.hits / self.victim_cache.accesses)
                if self.victim_cache.accesses else 0,
                self.victim_cache.accesses > 0,
            ),
            "victim_hit_rate_value": (
                (self.victim_cache.hits / self.victim_cache.accesses)
                if self.victim_cache.accesses else 0
            ),
            "l1_hits":           self.hits,
            "l1_misses":         self.misses,
            "l1_hit_rate":       f"{hit_r * 100:.1f}%"
                                 if self.total_requests else "N/A",
            "l1_hit_rate_value": hit_r if self.total_requests else 0,
            "l1_local_miss_rate": _pct(l1_local_miss_rate, self.total_requests > 0),
            "l1_local_miss_rate_value": l1_local_miss_rate,
            "l1_global_miss_rate": _pct(l1_global_miss_rate, self.total_requests > 0),
            "l1_global_miss_rate_value": l1_global_miss_rate,
            "l2_enabled":        l2_enabled,
            "l2_accesses":       l2_accesses,
            "l2_hits":           l2_hits,
            "l2_misses":         l2_misses,
            "l2_hit_rate":       _pct(l2_hit_rate_value, l2_accesses > 0),
            "l2_hit_rate_value": l2_hit_rate_value,
            "l2_local_miss_rate": _pct(l2_local_miss_rate_value, l2_accesses > 0),
            "l2_local_miss_rate_value": l2_local_miss_rate_value,
            "l2_global_miss_rate": _pct(l2_global_miss_rate_value, self.total_requests > 0),
            "l2_global_miss_rate_value": l2_global_miss_rate_value,
        }
        stats.update(hier)
        return stats
