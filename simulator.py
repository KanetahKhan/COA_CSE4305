from cache_controller import (
    CacheController, State, RequestType, Policy,
    WritePolicy, AllocatePolicy,
)
from memory import Memory, HierarchicalMemory
from cpu import CPU


COLORS = {
    "RESET":   "\033[0m",
    "BOLD":    "\033[1m",
    "DIM":     "\033[2m",
    "RED":     "\033[91m",
    "GREEN":   "\033[92m",
    "YELLOW":  "\033[93m",
    "BLUE":    "\033[94m",
    "MAGENTA": "\033[95m",
    "CYAN":    "\033[96m",
    "WHITE":   "\033[97m",
}

STATE_COLORS = {
    State.IDLE:          COLORS["GREEN"],
    State.COMPARE_TAG:   COLORS["CYAN"],
    State.WRITE_BACK:    COLORS["RED"],
    State.ALLOCATE:      COLORS["YELLOW"],
    State.WRITE_THROUGH: COLORS["MAGENTA"],
}


def colorize(text, color_key):
    return f"{COLORS[color_key]}{text}{COLORS['RESET']}"


def state_str(state):
    return f"{STATE_COLORS[state]}{COLORS['BOLD']}{state.value:>12}{COLORS['RESET']}"


class Simulator:
    def __init__(self, num_cache_lines=8, block_size=4, addr_bits=16,
                 mem_read_latency=3, mem_write_latency=2,
                 associativity=1, policy=Policy.DIRECT,
                 base_cpi=1.0, write_buffer_size=4, verbose=True,
                 enable_l2=True, l2_num_lines=None, l2_latency=2,
                 l2_associativity=1, l2_policy=Policy.DIRECT,
                 write_policy=WritePolicy.WRITE_BACK,
                 allocate_policy=AllocatePolicy.WRITE_ALLOCATE,
                 victim_cache_size=0):
        self.cache_ctrl = CacheController(
            num_cache_lines, block_size, addr_bits,
            associativity=associativity, policy=policy,
            base_cpi=base_cpi,
            write_policy=write_policy,
            allocate_policy=allocate_policy,
            victim_cache_size=victim_cache_size,
        )
        self.enable_l2 = bool(enable_l2)
        self.l2_num_lines = (max(16, num_cache_lines * 2)
                             if l2_num_lines is None else int(l2_num_lines))
        self.l2_latency = max(1, int(l2_latency))
        self.l2_associativity = max(1, int(l2_associativity))
        self.l2_policy = Policy.DIRECT if self.l2_associativity == 1 else l2_policy

        if self.enable_l2:
            self.memory = HierarchicalMemory(
                size=2**addr_bits,
                block_size=block_size,
                read_latency=mem_read_latency,
                write_latency=mem_write_latency,
                addr_bits=addr_bits,
                l2_num_lines=self.l2_num_lines,
                l2_latency=self.l2_latency,
                l2_associativity=self.l2_associativity,
                l2_policy=self.l2_policy,
            )
        else:
            self.memory = Memory(
                size=2**addr_bits,
                block_size=block_size,
                read_latency=mem_read_latency,
                write_latency=mem_write_latency,
            )
        self.cpu = CPU()
        self.verbose = verbose
        self.max_cycles = 10000
        self.write_buffer_size = max(1, int(write_buffer_size))
        self.write_buffer = []
        self._max_wb_occupancy = 0
        self._active_wb = None
        self._mem_op_started = False
        self._sync_hierarchy_stats()

    def _sync_hierarchy_stats(self):
        if hasattr(self.memory, "get_stats"):
            self.cache_ctrl.set_hierarchy_stats(self.memory.get_stats())
        else:
            self.cache_ctrl.set_hierarchy_stats({})

    def init_memory(self, start_addr, values):
        self.memory.init_region(start_addr, values)

    def run(self, requests, label="Simulation"):
        self.cpu.load_requests(requests)
        self.cache_ctrl.cycle = 0
        self.cache_ctrl.log   = []
        self._mem_op_started  = False
        self.write_buffer     = []
        self._max_wb_occupancy = 0
        self._active_wb        = None

        if self.verbose:
            self._print_header(label)
            self._print_config()

        cycle = 0
        while not self.cpu.is_done() and cycle < self.max_cycles:
            cycle += 1

            new_req = self.cpu.tick(
                self.cache_ctrl.cpu.ready,
                self.cache_ctrl.cpu.data_out
            )

            if new_req is not None:
                req_type, addr, data = new_req
                self.cache_ctrl.submit_request(req_type, addr, data)
            elif not self.cpu.waiting_for_result:
                self.cache_ctrl.clear_request()

            self.memory.tick()

            completed_write = (self.memory.ready
                               and self.memory.operation in ("write", "write_partial"))
            completed_write_addr = self.memory.address
            completed_write_data = list(self.memory.buffer)

            self.cache_ctrl.mem.ready = self.memory.ready
            if self.memory.ready:
                if self.memory.operation == "read":
                    self.cache_ctrl.mem.data_in = list(self.memory.buffer)
                elif completed_write:
                    self._active_wb = None
                self._mem_op_started = False
            self._sync_hierarchy_stats()

            wb_has_room = len(self.write_buffer) < self.write_buffer_size
            self.cache_ctrl.set_write_buffer_status(wb_has_room)

            prev_state = self.cache_ctrl.state
            self.cache_ctrl.tick()
            curr_state = self.cache_ctrl.state

            if completed_write:
                self.cache_ctrl.notify_writeback_completed(
                    completed_write_addr,
                    completed_write_data,
                )

            wb_entry = self.cache_ctrl.consume_enqueued_writeback()
            if wb_entry is not None and len(self.write_buffer) < self.write_buffer_size:
                self.write_buffer.append(wb_entry)
                self._max_wb_occupancy = max(self._max_wb_occupancy, len(self.write_buffer))

            pw_entry = self.cache_ctrl.consume_enqueued_partial_write()
            if pw_entry is not None and len(self.write_buffer) < self.write_buffer_size:
                self.write_buffer.append({
                    "address":  pw_entry["address"],
                    "value":    pw_entry["value"],
                    "partial":  True,
                })
                self._max_wb_occupancy = max(self._max_wb_occupancy, len(self.write_buffer))

            if prev_state != curr_state:
                self._mem_op_started = False

            if (curr_state == State.ALLOCATE
                  and not self._mem_op_started and not self.memory.busy):
                alloc_addr = self.cache_ctrl.mem.address
                if prev_state != curr_state:
                    tag, _, _ = self.cache_ctrl._decompose_address(
                        self.cache_ctrl.saved_address
                    )
                    alloc_addr = self.cache_ctrl._block_address(
                        tag,
                        self.cache_ctrl.saved_set,
                    )
                self.memory.start_read(alloc_addr)
                self._mem_op_started = True

            elif (not self.memory.busy and self.write_buffer):
                self._active_wb = self.write_buffer.pop(0)
                if self._active_wb.get("partial"):
                    self.memory.start_write_partial(
                        self._active_wb["address"],
                        self._active_wb["value"],
                    )
                else:
                    self.memory.start_write(self._active_wb["address"],
                                            self._active_wb["data"])
                self._mem_op_started = True

            if self.verbose:
                self._print_cycle(cycle)

        # CPU completion does not wait on write-back. Drain buffered writes in background
        # so memory state is eventually consistent for post-run verification.
        self._drain_write_buffer_background()

        self.cache_ctrl.set_write_buffer_stats(
            len(self.write_buffer),
            self._max_wb_occupancy,
            self.write_buffer_size,
        )
        self._sync_hierarchy_stats()

        if self.verbose:
            self._print_separator()
            self._print_cache_state()
            self._print_stats()
            self._print_results()

        return {
            "stats":   self.cache_ctrl.get_stats(),
            "results": self.cpu.results,
            "log":     self.cache_ctrl.log,
            "cache":   self.cache_ctrl.get_cache_snapshot(),
            "l2_cache": self.memory.get_cache_snapshot() if hasattr(self.memory, "get_cache_snapshot") else [],
        }

    def _drain_write_buffer_background(self):
        while self.memory.busy or self.write_buffer:
            if not self.memory.busy and self.write_buffer:
                self._active_wb = self.write_buffer.pop(0)
                if self._active_wb.get("partial"):
                    self.memory.start_write_partial(
                        self._active_wb["address"],
                        self._active_wb["value"],
                    )
                else:
                    self.memory.start_write(self._active_wb["address"],
                                            self._active_wb["data"])

            self.memory.tick()
            self._sync_hierarchy_stats()
            if self.memory.ready and self.memory.operation in ("write", "write_partial"):
                self.cache_ctrl.notify_writeback_completed(
                    self.memory.address,
                    list(self.memory.buffer),
                )
                self._active_wb = None
        self._sync_hierarchy_stats()

    def _print_header(self, label):
        width = 96
        print()
        print(colorize("=" * width, "BOLD"))
        title = f"  CACHE FSM SIMULATOR : {label}  "
        pad = (width - len(title)) // 2
        print(colorize(" " * pad + title, "BOLD"))
        print(colorize("=" * width, "BOLD"))

    def _print_config(self):
        ctrl = self.cache_ctrl
        assoc_str = (f"{ctrl.associativity}-way Set-Associative"
                     if ctrl.associativity > 1 else "Direct-Mapped")
        policy_str = ctrl.policy.value
        main_mem = self.memory.main_memory if hasattr(self.memory, "main_memory") else self.memory
        stats = self.cache_ctrl.get_stats()

        print(f"\n  {colorize('Configuration:', 'BOLD')}")
        print(f"    Cache Lines : {ctrl.num_lines}    "
              f"Block Size : {ctrl.block_size} words    "
              f"Address Bits : {ctrl.addr_bits}")
        print(f"    Associativity: {assoc_str}    "
              f"Sets: {ctrl.num_sets}    "
              f"Replacement: {policy_str}")
        print(f"    Tag Bits    : {ctrl.tag_bits}    "
              f"Index Bits : {ctrl.index_bits}        "
              f"Offset Bits  : {ctrl.offset_bits}")
        if stats.get("l2_enabled"):
            l2_assoc = stats.get("l2_associativity", 1)
            l2_assoc_str = (f"{l2_assoc}-way Set-Associative"
                            if l2_assoc > 1 else "Direct-Mapped")
            print(f"    L2 Cache     : {stats.get('l2_num_lines', 0)} lines    "
                  f"Latency : {stats.get('l2_latency', 0)} cycles    "
                  f"Assoc : {l2_assoc_str}")
            print(f"    L2 Policy    : {stats.get('l2_policy', 'Direct-Mapped')}")
        else:
            print(f"    L2 Cache     : Disabled")
        print(f"    Main Mem Read Latency  : {main_mem.read_latency} cycles")
        print(f"    Main Mem Write Latency : {main_mem.write_latency} cycles")
        print(f"    Write Policy: Write-Back, Write-Allocate")
        self._print_separator()
        print(f"  {'Cycle':>5}  {'State Transition':^32}  "
              f"{'Event':<22}  {'Signals & Details'}")
        self._print_separator()

    def _print_separator(self):
        print(colorize("  " + "-" * 92, "DIM"))

    def _print_cycle(self, cycle):
        log_entries = [e for e in self.cache_ctrl.log if e["cycle"] == cycle]
        if not log_entries:
            return

        for entry in log_entries:
            prev   = entry["prev_state"]
            curr   = entry["state"]
            event  = entry["event"]
            details = entry["details"]

            prev_s = State(prev)
            curr_s = State(curr)

            if prev_s == curr_s:
                transition = f"{state_str(curr_s)}"
                arrow = "       "
            else:
                transition = f"{state_str(prev_s)}"
                arrow = f" {colorize('->', 'BOLD')} {state_str(curr_s)}"

            sig_parts = []
            if entry["cpu_ready"]:
                sig_parts.append(colorize("CPU_RDY", "GREEN"))
            if entry.get("cpu_stall"):
                sig_parts.append(colorize("STALL", "RED"))
            if entry["mem_read"]:
                sig_parts.append(colorize("MEM_RD", "YELLOW"))
            if entry["mem_write"]:
                sig_parts.append(colorize("MEM_WR", "RED"))

            signals = " ".join(sig_parts)

            event_color = "WHITE"
            if "HIT"  in event:
                event_color = "GREEN"
            elif "MISS" in event:
                event_color = "RED"
            elif "DONE" in event:
                event_color = "GREEN"
            elif "WAIT" in event:
                event_color = "YELLOW"

            print(f"  {colorize(f'{cycle:>5}', 'BOLD')}  "
                  f"{transition}{arrow}  "
                  f"{colorize(event, event_color):<22}")

            if details:
                print(f"         {colorize(details, 'DIM')}")
            if signals:
                print(f"         Signals: [{signals}]")

    def _print_cache_state(self):
        ctrl = self.cache_ctrl
        print(f"\n  {colorize('Final Cache State:', 'BOLD')}")
        if ctrl.associativity > 1:
            print(f"    {'Set':>3}  {'Way':>3}  {'V':>1}  {'D':>1}  "
                  f"{'Tag':>6}  {'Data'}")
            print(f"    {'---':>3}  {'---':>3}  {'-':>1}  {'-':>1}  "
                  f"{'------':>6}  {'----'}")
        else:
            print(f"    {'Idx':>3}  {'V':>1}  {'D':>1}  {'Tag':>6}  {'Data'}")
            print(f"    {'---':>3}  {'-':>1}  {'-':>1}  {'------':>6}  {'----'}")

        for line in ctrl.get_cache_snapshot():
            v = colorize("1", "GREEN") if line["valid"] else colorize("0", "DIM")
            d = colorize("1", "RED")   if line["dirty"] else colorize("0", "DIM")
            tag = (colorize(line["tag"], "CYAN") if line["valid"]
                   else colorize(line["tag"], "DIM"))
            data_str = " ".join(line["data"])
            data_str = (colorize(data_str, "WHITE") if line["valid"]
                        else colorize(data_str, "DIM"))

            if ctrl.associativity > 1:
                print(f"    {line['set']:>3}  {line['way']:>3}  {v}  {d}  "
                      f"{tag:>6}  [{data_str}]")
            else:
                print(f"    {line['set']:>3}  {v}  {d}  {tag:>6}  [{data_str}]")

    def _print_stats(self):
        stats = self.cache_ctrl.get_stats()
        print(f"\n  {colorize('Performance Statistics:', 'BOLD')}")
        print(f"    Total Requests : {stats['total_requests']}")
        print(f"    L1 Hits        : {colorize(str(stats['hits']), 'GREEN')}")
        print(f"    L1 Misses      : {colorize(str(stats['misses']), 'RED')}"
              f"  (compulsory: {stats['compulsory_misses']}"
              f"  conflict: {stats['conflict_misses']})")
        print(f"    L1 Hit Rate    : {colorize(stats['hit_rate'], 'CYAN')}")
        print(f"    L1 Miss Rate   : local={stats['l1_local_miss_rate']}  "
              f"global={stats['l1_global_miss_rate']}")
        if stats.get("l2_enabled"):
            print(f"    L2 Accesses    : {stats['l2_accesses']}")
            print(f"    L2 Hits/Misses : {stats['l2_hits']} / {stats['l2_misses']}")
            print(f"    L2 Miss Rate   : local={stats['l2_local_miss_rate']}  "
                  f"global={stats['l2_global_miss_rate']}")
        print(f"    Total Cycles   : {stats['total_cycles']}")
        print(f"    Stall Cycles   : {stats['stall_cycles']}")
        print(f"    L1 Bus Reads   : {stats['bus_reads']}  (allocations)")
        print(f"    L1 Bus Writes  : {stats['bus_writes']}  (write-backs)")
        if stats.get("l2_enabled"):
            print(f"    Main Mem Reads : {stats.get('main_memory_reads', 0)}")
            print(f"    Main Mem Writes: {stats.get('main_memory_writes', 0)}"
                  f"  (dirty L2 evictions: {stats.get('l2_dirty_evictions', 0)})")
        print(f"    Write Buffer   : depth={stats['write_buffer_depth']}/"
              f"{stats['write_buffer_size']}  max={stats['write_buffer_max_occupancy']}")
        print(f"    Avg Miss Pen.  : {colorize(str(stats['avg_miss_penalty']), 'YELLOW')} cycles")
        print(f"    AMAT           : {colorize(str(stats['amat']), 'CYAN')} cycles")
        print(f"    Base CPI       : {stats['base_cpi']}")
        print(f"    Instructions   : {stats['instructions']}")
        print(f"    Memory Stalls  : {stats['memory_stalls']}")
        print(f"    Effective CPI  : {colorize(str(stats['effective_cpi']), 'MAGENTA')}"
              f"  (= Base CPI + Memory Stalls / Instructions)")
        print(f"    Throughput IPC : {colorize(str(stats['achieved_ipc']), 'GREEN')}"
              f"  (ideal: {stats['ideal_ipc']}, "
              f"{stats['throughput_ratio'] * 100:.1f}% of ideal)")

    def _print_results(self):
        results = self.cpu.results
        if not results:
            return
        print(f"\n  {colorize('CPU Results:', 'BOLD')}")
        for i, r in enumerate(results):
            if r["type"] == RequestType.READ:
                val = r["data_returned"]
                val_str = colorize(f"0x{val:02X}", "GREEN")
                print(f"    [{i}] READ  addr=0x{r['address']:04X}  => data={val_str}")
            else:
                print(f"    [{i}] WRITE addr=0x{r['address']:04X}  => {colorize('OK', 'GREEN')}")
        print()
