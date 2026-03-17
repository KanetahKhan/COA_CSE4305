from enum import Enum, auto
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


@dataclass
class CacheLine:
    valid: bool = False
    dirty: bool = False
    tag: int = 0
    data: list = field(default_factory=lambda: [0] * 4)


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
    def __init__(self, num_lines=8, block_size=4, addr_bits=16):
        self.num_lines = num_lines
        self.block_size = block_size
        self.addr_bits = addr_bits

        self.offset_bits = (block_size - 1).bit_length()
        self.index_bits = (num_lines - 1).bit_length()
        self.tag_bits = addr_bits - self.offset_bits - self.index_bits

        self.cache = [CacheLine(data=[0] * block_size) for _ in range(num_lines)]
        self.state = State.IDLE
        self.prev_state = State.IDLE

        self.cpu = CPUInterface()
        self.mem = MemoryInterface()

        self.saved_address = 0
        self.saved_request = None
        self.saved_data = 0
        self.wb_counter = 0
        self.alloc_counter = 0

        self.hits = 0
        self.misses = 0
        self.total_requests = 0
        self.cycle = 0

        self.log = []

    def _decompose_address(self, address):
        offset = address & ((1 << self.offset_bits) - 1)
        index = (address >> self.offset_bits) & ((1 << self.index_bits) - 1)
        tag = (address >> (self.offset_bits + self.index_bits)) & ((1 << self.tag_bits) - 1)
        return tag, index, offset

    def _block_address(self, tag, index):
        return (tag << (self.offset_bits + self.index_bits)) | (index << self.offset_bits)

    def _log_event(self, event, details=""):
        entry = {
            "cycle": self.cycle,
            "state": self.state.value,
            "prev_state": self.prev_state.value,
            "event": event,
            "details": details,
            "cpu_ready": self.cpu.ready,
            "cpu_stall": self.cpu.stall,
            "mem_read": self.mem.read,
            "mem_write": self.mem.write,
        }
        self.log.append(entry)

    def submit_request(self, req_type: RequestType, address: int, data: int = 0):
        self.cpu.valid = True
        self.cpu.read_write = req_type
        self.cpu.address = address
        self.cpu.data_in = data
        self.cpu.ready = False
        self.cpu.stall = False

    def clear_request(self):
        self.cpu.valid = False
        self.cpu.read_write = None
        self.cpu.address = 0
        self.cpu.data_in = 0

    def tick(self):
        self.cycle += 1
        self.prev_state = self.state
        self.cpu.ready = False

        self.mem.read = False
        self.mem.write = False

        if self.state == State.IDLE:
            self._handle_idle()
        elif self.state == State.COMPARE_TAG:
            self._handle_compare_tag()
        elif self.state == State.WRITE_BACK:
            self._handle_write_back()
        elif self.state == State.ALLOCATE:
            self._handle_allocate()

    def _handle_idle(self):
        if self.cpu.valid:
            self.saved_address = self.cpu.address
            self.saved_request = self.cpu.read_write
            self.saved_data = self.cpu.data_in
            self.cpu.stall = True
            self.state = State.COMPARE_TAG
            self._log_event(
                "REQUEST_RECEIVED",
                f"{'READ' if self.saved_request == RequestType.READ else 'WRITE'} "
                f"addr=0x{self.saved_address:04X} data=0x{self.saved_data:02X}"
            )
        else:
            self._log_event("IDLE", "Waiting for CPU request")

    def _handle_compare_tag(self):
        tag, index, offset = self._decompose_address(self.saved_address)
        line = self.cache[index]

        self.total_requests += 1

        if line.valid and line.tag == tag:
            self.hits += 1
            if self.saved_request == RequestType.READ:
                self.cpu.data_out = line.data[offset]
                self._log_event(
                    "CACHE_HIT_READ",
                    f"tag=0x{tag:X} index={index} offset={offset} "
                    f"data=0x{line.data[offset]:02X}"
                )
            else:
                line.data[offset] = self.saved_data
                line.dirty = True
                self._log_event(
                    "CACHE_HIT_WRITE",
                    f"tag=0x{tag:X} index={index} offset={offset} "
                    f"wrote=0x{self.saved_data:02X} (dirty=True)"
                )
            self.cpu.ready = True
            self.cpu.stall = False
            self.state = State.IDLE
        else:
            self.misses += 1
            if line.valid and line.dirty:
                self.wb_counter = 0
                wb_addr = self._block_address(line.tag, index)
                self._log_event(
                    "CACHE_MISS_DIRTY",
                    f"tag=0x{tag:X} index={index} "
                    f"evicting dirty block (old_tag=0x{line.tag:X}) "
                    f"wb_addr=0x{wb_addr:04X}"
                )
                self.state = State.WRITE_BACK
            else:
                self.alloc_counter = 0
                self._log_event(
                    "CACHE_MISS_CLEAN",
                    f"tag=0x{tag:X} index={index} "
                    f"{'(invalid line)' if not line.valid else '(clean eviction)'}"
                )
                self.state = State.ALLOCATE

    def _handle_write_back(self):
        tag, index, offset = self._decompose_address(self.saved_address)
        line = self.cache[index]
        wb_addr = self._block_address(line.tag, index)

        self.mem.write = True
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
            line.dirty = False
            self.alloc_counter = 0
            self.state = State.ALLOCATE
        else:
            self._log_event(
                "WRITE_BACK_WAIT",
                f"Writing to mem addr=0x{wb_addr:04X} "
                f"cycle {self.wb_counter} of transfer"
            )

    def _handle_allocate(self):
        tag, index, offset = self._decompose_address(self.saved_address)
        block_addr = self._block_address(tag, index)

        self.mem.read = True
        self.mem.address = block_addr

        self.alloc_counter += 1

        if self.mem.ready:
            line = self.cache[index]
            line.valid = True
            line.tag = tag
            line.data = list(self.mem.data_in)
            line.dirty = False

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
                line.dirty = True
                self._log_event(
                    "ALLOCATE_DONE_WRITE",
                    f"Fetched block from mem addr=0x{block_addr:04X} "
                    f"then wrote=0x{self.saved_data:02X} at offset={offset} "
                    f"(dirty=True, took {self.alloc_counter} cycles)"
                )

            self.cpu.ready = True
            self.cpu.stall = False
            self.state = State.IDLE
        else:
            self._log_event(
                "ALLOCATE_WAIT",
                f"Reading from mem addr=0x{block_addr:04X} "
                f"cycle {self.alloc_counter} of transfer"
            )

    def get_cache_snapshot(self):
        snapshot = []
        for i, line in enumerate(self.cache):
            snapshot.append({
                "index": i,
                "valid": line.valid,
                "dirty": line.dirty,
                "tag": f"0x{line.tag:X}",
                "data": [f"0x{d:02X}" for d in line.data],
            })
        return snapshot

    def get_stats(self):
        return {
            "total_requests": self.total_requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hits / self.total_requests * 100:.1f}%" if self.total_requests else "N/A",
            "total_cycles": self.cycle,
        }