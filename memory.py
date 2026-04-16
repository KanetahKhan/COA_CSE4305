import random as _random
from dataclasses import dataclass, field

from cache_controller import Policy


class Memory:
    def __init__(self, size=65536, block_size=4, read_latency=3, write_latency=2):
        self.data = [0] * size
        self.block_size = block_size
        self.read_latency = read_latency
        self.write_latency = write_latency

        self.busy = False
        self.ready = False
        self.counter = 0
        self.operation = None
        self.address = 0
        self.buffer = [0] * block_size

    def init_region(self, start_addr, values):
        for i, val in enumerate(values):
            if start_addr + i < len(self.data):
                self.data[start_addr + i] = val

    def start_read(self, address):
        if not self.busy:
            self.busy = True
            self.ready = False
            self.counter = 0
            self.operation = "read"
            self.address = address

    def start_write(self, address, block_data):
        if not self.busy:
            self.busy = True
            self.ready = False
            self.counter = 0
            self.operation = "write"
            self.address = address
            self.buffer = list(block_data)

    def start_write_partial(self, address, value):
        """Single-word write: only data[address] is updated. Used by
        write-through and no-write-allocate paths to avoid corrupting other
        words in the block."""
        if not self.busy:
            self.busy = True
            self.ready = False
            self.counter = 0
            self.operation = "write_partial"
            self.address = address
            self.buffer = [int(value) & 0xFF] * self.block_size

    def tick(self):
        self.ready = False

        if not self.busy:
            return

        self.counter += 1

        if self.operation == "read" and self.counter >= self.read_latency:
            base = self.address & ~(self.block_size - 1)
            self.buffer = [self.data[base + i] if base + i < len(self.data) else 0
                           for i in range(self.block_size)]
            self.ready = True
            self.busy = False

        elif self.operation == "write" and self.counter >= self.write_latency:
            base = self.address & ~(self.block_size - 1)
            for i in range(self.block_size):
                if base + i < len(self.data):
                    self.data[base + i] = self.buffer[i]
            self.ready = True
            self.busy = False

        elif self.operation == "write_partial" and self.counter >= self.write_latency:
            if self.address < len(self.data):
                self.data[self.address] = self.buffer[0]
            self.ready = True
            self.busy = False

    def read_word(self, address):
        if address < len(self.data):
            return self.data[address]
        return 0


@dataclass
class _HierarchyLine:
    valid: bool = False
    dirty: bool = False
    tag: int = 0
    data: list = field(default_factory=list)
    lru_counter: int = 0
    access_count: int = 0


class _CacheArray:
    def __init__(self, num_lines, block_size, addr_bits,
                 associativity=1, policy=Policy.DIRECT):
        self.num_lines = max(1, int(num_lines))
        self.block_size = block_size
        self.addr_bits = addr_bits
        self.associativity = max(1, int(associativity))
        if self.num_lines % self.associativity != 0:
            raise ValueError("Cache lines must be divisible by associativity")

        self.num_sets = self.num_lines // self.associativity
        self.policy = Policy.DIRECT if self.associativity == 1 else policy

        self.offset_bits = (block_size - 1).bit_length()
        self.index_bits = (self.num_sets - 1).bit_length() if self.num_sets > 1 else 0
        self.tag_bits = addr_bits - self.offset_bits - self.index_bits

        self.cache = [
            [_HierarchyLine(data=[0] * block_size) for _ in range(self.associativity)]
            for _ in range(self.num_sets)
        ]
        self._lru_time = 0

    def _decompose_address(self, address):
        offset = address & ((1 << self.offset_bits) - 1)
        index = ((address >> self.offset_bits) & ((1 << self.index_bits) - 1)
                 if self.index_bits > 0 else 0)
        tag = address >> (self.offset_bits + self.index_bits)
        tag &= (1 << self.tag_bits) - 1
        return tag, index, offset

    def _block_address(self, tag, set_idx):
        return ((tag << (self.offset_bits + self.index_bits))
                | (set_idx << self.offset_bits))

    def _find_hit_way(self, set_idx, tag):
        for way, line in enumerate(self.cache[set_idx]):
            if line.valid and line.tag == tag:
                return way
        return -1

    def _find_victim_way(self, set_idx):
        ways = self.cache[set_idx]
        for way, line in enumerate(ways):
            if not line.valid:
                return way

        if self.policy in (Policy.DIRECT, Policy.LRU):
            return min(range(self.associativity), key=lambda w: ways[w].lru_counter)
        if self.policy == Policy.LFU:
            return min(range(self.associativity),
                       key=lambda w: (ways[w].access_count, ways[w].lru_counter))
        if self.policy == Policy.RANDOM:
            return _random.randrange(self.associativity)
        return 0

    def _touch(self, set_idx, way):
        self._lru_time += 1
        line = self.cache[set_idx][way]
        line.lru_counter = self._lru_time
        line.access_count += 1

    def lookup_block(self, address, touch=True):
        tag, set_idx, _ = self._decompose_address(address)
        way = self._find_hit_way(set_idx, tag)
        if way < 0:
            return None
        line = self.cache[set_idx][way]
        if touch:
            self._touch(set_idx, way)
        return {
            "set": set_idx,
            "way": way,
            "tag": tag,
            "dirty": line.dirty,
            "block_address": self._block_address(tag, set_idx),
            "data": list(line.data),
        }

    def peek_word(self, address):
        tag, set_idx, offset = self._decompose_address(address)
        way = self._find_hit_way(set_idx, tag)
        if way < 0:
            return None
        return self.cache[set_idx][way].data[offset]

    def prepare_fill(self, address):
        tag, set_idx, _ = self._decompose_address(address)
        way = self._find_victim_way(set_idx)
        line = self.cache[set_idx][way]
        victim = None
        if line.valid and line.dirty:
            victim = {
                "address": self._block_address(line.tag, set_idx),
                "data": list(line.data),
            }
        return {
            "set": set_idx,
            "way": way,
            "tag": tag,
            "victim": victim,
        }

    def install_block(self, address, block_data, dirty=False, prepared=None):
        prepared = prepared or self.prepare_fill(address)
        set_idx = prepared["set"]
        way = prepared["way"]
        tag = prepared["tag"]
        line = self.cache[set_idx][way]
        line.valid = True
        line.dirty = bool(dirty)
        line.tag = tag
        line.data = list(block_data)
        line.access_count = 0
        self._touch(set_idx, way)
        return prepared["victim"]

    def write_hit(self, address, block_data, dirty=True):
        tag, set_idx, _ = self._decompose_address(address)
        way = self._find_hit_way(set_idx, tag)
        if way < 0:
            return False
        line = self.cache[set_idx][way]
        line.data = list(block_data)
        line.dirty = bool(dirty)
        self._touch(set_idx, way)
        return True

    def get_snapshot(self):
        snapshot = []
        for set_idx, ways in enumerate(self.cache):
            for way_idx, line in enumerate(ways):
                snapshot.append({
                    "index": set_idx * self.associativity + way_idx,
                    "set": set_idx,
                    "way": way_idx,
                    "valid": line.valid,
                    "dirty": line.dirty,
                    "tag": f"0x{line.tag:X}",
                    "data": [f"0x{d:02X}" for d in line.data],
                })
        return snapshot


class _HierarchyDataView:
    def __init__(self, hierarchy):
        self._hierarchy = hierarchy

    def __len__(self):
        return len(self._hierarchy.main_memory.data)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return [self[i] for i in range(start, stop, step)]
        return self._hierarchy.read_word(index)


class HierarchicalMemory:
    def __init__(self, size=65536, block_size=4, read_latency=3, write_latency=2,
                 addr_bits=16, l2_num_lines=16, l2_latency=2,
                 l2_associativity=1, l2_policy=Policy.DIRECT):
        self.block_size = block_size
        self.addr_bits = addr_bits

        self.main_memory = Memory(
            size=size,
            block_size=block_size,
            read_latency=read_latency,
            write_latency=write_latency,
        )
        self.l2 = _CacheArray(
            num_lines=l2_num_lines,
            block_size=block_size,
            addr_bits=addr_bits,
            associativity=l2_associativity,
            policy=l2_policy,
        )

        self.l2_latency = max(1, int(l2_latency))
        self.read_latency = self.l2_latency
        self.write_latency = self.l2_latency

        self.busy = False
        self.ready = False
        self.counter = 0
        self.operation = None
        self.address = 0
        self.buffer = [0] * block_size

        self._phase = None
        self._phase_counter = 0
        self._pending_block = None
        self._pending_victim = None
        self._prepared_fill = None

        self.l2_accesses = 0
        self.l2_hits = 0
        self.l2_misses = 0
        self.l2_write_accesses = 0
        self.l2_write_hits = 0
        self.l2_write_misses = 0
        self.l2_dirty_evictions = 0
        self.main_memory_reads = 0
        self.main_memory_writes = 0
        self.partial_writes = 0
        self._partial_word_offset = None

    @property
    def data(self):
        return _HierarchyDataView(self)

    def init_region(self, start_addr, values):
        self.main_memory.init_region(start_addr, values)

    def _block_base(self, address):
        return address & ~(self.block_size - 1)

    def _begin_operation(self, operation, address, block_data=None):
        self.busy = True
        self.ready = False
        self.counter = 0
        self.operation = operation
        self.address = self._block_base(address)
        self.buffer = list(block_data) if block_data is not None else [0] * self.block_size
        self._phase_counter = 0
        self._phase = None
        self._pending_block = None
        self._pending_victim = None
        self._prepared_fill = None

    def _clear_phase_state(self):
        self._phase = None
        self._phase_counter = 0
        self._pending_block = None
        self._pending_victim = None
        self._prepared_fill = None

    def _start_main_memory_read(self, address):
        self.main_memory.start_read(address)
        self.main_memory_reads += 1

    def _start_main_memory_write(self, address, block_data):
        self.main_memory.start_write(address, block_data)
        self.main_memory_writes += 1
        self.l2_dirty_evictions += 1

    def start_read(self, address):
        if self.busy:
            return

        self._begin_operation("read", address)
        self.l2_accesses += 1

        hit = self.l2.lookup_block(self.address, touch=True)
        if hit is not None:
            self.l2_hits += 1
            self._pending_block = list(hit["data"])
            self._phase = "read_hit"
            self.read_latency = self.l2_latency
            return

        self.l2_misses += 1
        self._prepared_fill = self.l2.prepare_fill(self.address)
        self._pending_victim = self._prepared_fill["victim"]
        self._phase = "read_lookup"
        self.read_latency = (self.l2_latency + self.main_memory.read_latency
                             + (self.main_memory.write_latency if self._pending_victim else 0))

    def start_write_partial(self, address, value):
        """Single-word write (write-through / no-write-allocate). Always
        propagates to main memory; if L2 happens to hold the containing
        block, patch the word in place too so L2 stays coherent with the
        write-through update."""
        if self.busy:
            return

        self.busy = True
        self.ready = False
        self.counter = 0
        self.operation = "write_partial"
        self.address = address
        self.buffer = [int(value) & 0xFF] * self.block_size
        self._phase_counter = 0
        self._phase = "write_partial"
        self._pending_block = None
        self._pending_victim = None
        self._prepared_fill = None
        self.partial_writes += 1
        self.write_latency = self.main_memory.write_latency

        # Patch L2 in place (no extra latency — coherence-only).
        l2_tag, l2_set, l2_offset = self.l2._decompose_address(address)
        l2_way = self.l2._find_hit_way(l2_set, l2_tag)
        if l2_way >= 0:
            self.l2.cache[l2_set][l2_way].data[l2_offset] = int(value) & 0xFF

        self.main_memory.start_write_partial(address, value)
        self.main_memory_writes += 1

    def start_write(self, address, block_data):
        if self.busy:
            return

        self._begin_operation("write", address, block_data)
        self.l2_write_accesses += 1

        hit = self.l2.lookup_block(self.address, touch=False)
        if hit is not None:
            self.l2_write_hits += 1
            self._phase = "write_hit"
            self._pending_block = list(block_data)
            self.write_latency = self.l2_latency
            return

        self.l2_write_misses += 1
        self._prepared_fill = self.l2.prepare_fill(self.address)
        self._pending_victim = self._prepared_fill["victim"]
        self._pending_block = list(block_data)
        self._phase = "write_lookup"
        self.write_latency = self.l2_latency + (
            self.main_memory.write_latency if self._pending_victim else 0
        )

    def tick(self):
        self.ready = False
        self.main_memory.tick()

        if not self.busy:
            return

        self.counter += 1
        self._phase_counter += 1

        if self._phase == "write_partial":
            if not self.main_memory.ready:
                return
            self.ready = True
            self.busy = False
            self._clear_phase_state()
            return

        if self._phase == "read_hit":
            if self._phase_counter >= self.l2_latency:
                self.buffer = list(self._pending_block)
                self.ready = True
                self.busy = False
                self._clear_phase_state()
            return

        if self._phase == "read_lookup":
            if self._phase_counter < self.l2_latency:
                return
            self._phase_counter = 0
            if self._pending_victim is not None:
                self._phase = "read_evict"
                self._start_main_memory_write(
                    self._pending_victim["address"],
                    self._pending_victim["data"],
                )
            else:
                self._phase = "read_mem"
                self._start_main_memory_read(self.address)
            return

        if self._phase == "read_evict":
            if not self.main_memory.ready:
                return
            self._phase = "read_mem"
            self._phase_counter = 0
            self._start_main_memory_read(self.address)
            return

        if self._phase == "read_mem":
            if not self.main_memory.ready:
                return
            fetched = list(self.main_memory.buffer)
            self.l2.install_block(
                self.address,
                fetched,
                dirty=False,
                prepared=self._prepared_fill,
            )
            self.buffer = fetched
            self.ready = True
            self.busy = False
            self._clear_phase_state()
            return

        if self._phase == "write_hit":
            if self._phase_counter >= self.l2_latency:
                self.l2.write_hit(self.address, self._pending_block, dirty=True)
                self.ready = True
                self.busy = False
                self._clear_phase_state()
            return

        if self._phase == "write_lookup":
            if self._phase_counter < self.l2_latency:
                return
            self._phase_counter = 0
            if self._pending_victim is not None:
                self._phase = "write_evict"
                self._start_main_memory_write(
                    self._pending_victim["address"],
                    self._pending_victim["data"],
                )
            else:
                self.l2.install_block(
                    self.address,
                    self._pending_block,
                    dirty=True,
                    prepared=self._prepared_fill,
                )
                self.ready = True
                self.busy = False
                self._clear_phase_state()
            return

        if self._phase == "write_evict":
            if not self.main_memory.ready:
                return
            self.l2.install_block(
                self.address,
                self._pending_block,
                dirty=True,
                prepared=self._prepared_fill,
            )
            self.ready = True
            self.busy = False
            self._clear_phase_state()

    def read_word(self, address):
        l2_val = self.l2.peek_word(address)
        if l2_val is not None:
            return l2_val
        return self.main_memory.read_word(address)

    def get_stats(self):
        return {
            "l2_enabled": True,
            "l2_accesses": self.l2_accesses,
            "l2_hits": self.l2_hits,
            "l2_misses": self.l2_misses,
            "l2_write_accesses": self.l2_write_accesses,
            "l2_write_hits": self.l2_write_hits,
            "l2_write_misses": self.l2_write_misses,
            "l2_dirty_evictions": self.l2_dirty_evictions,
            "main_memory_reads": self.main_memory_reads,
            "main_memory_writes": self.main_memory_writes,
            "l2_num_lines": self.l2.num_lines,
            "l2_associativity": self.l2.associativity,
            "l2_policy": self.l2.policy.value,
            "l2_latency": self.l2_latency,
            "main_memory_read_latency": self.main_memory.read_latency,
            "main_memory_write_latency": self.main_memory.write_latency,
        }

    def get_cache_snapshot(self):
        return self.l2.get_snapshot()
