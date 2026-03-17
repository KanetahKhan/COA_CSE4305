from cache_controller import CacheController, State, RequestType
from memory import Memory
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
    State.IDLE:        COLORS["GREEN"],
    State.COMPARE_TAG: COLORS["CYAN"],
    State.WRITE_BACK:  COLORS["RED"],
    State.ALLOCATE:    COLORS["YELLOW"],
}


def colorize(text, color_key):
    return f"{COLORS[color_key]}{text}{COLORS['RESET']}"


def state_str(state):
    return f"{STATE_COLORS[state]}{COLORS['BOLD']}{state.value:>12}{COLORS['RESET']}"


class Simulator:
    def __init__(self, num_cache_lines=8, block_size=4, addr_bits=16,
                 mem_read_latency=3, mem_write_latency=2, verbose=True):
        self.cache_ctrl = CacheController(num_cache_lines, block_size, addr_bits)
        self.memory = Memory(
            size=2**addr_bits,
            block_size=block_size,
            read_latency=mem_read_latency,
            write_latency=mem_write_latency,
        )
        self.cpu = CPU()
        self.verbose = verbose
        self.max_cycles = 10000
        self._mem_op_started = False

    def init_memory(self, start_addr, values):
        self.memory.init_region(start_addr, values)

    def run(self, requests, label="Simulation"):
        self.cpu.load_requests(requests)
        self.cache_ctrl.cycle = 0
        self.cache_ctrl.log = []
        self._mem_op_started = False

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

            self.cache_ctrl.mem.ready = self.memory.ready
            if self.memory.ready:
                if self.memory.operation == "read":
                    self.cache_ctrl.mem.data_in = list(self.memory.buffer)
                self._mem_op_started = False

            prev_state = self.cache_ctrl.state
            self.cache_ctrl.tick()
            curr_state = self.cache_ctrl.state

            if prev_state != curr_state:
                self._mem_op_started = False

            state_stable = (prev_state == curr_state)

            if curr_state == State.WRITE_BACK and state_stable and not self._mem_op_started and not self.memory.busy:
                self.memory.start_write(self.cache_ctrl.mem.address,
                                        self.cache_ctrl.mem.data_out)
                self._mem_op_started = True

            elif curr_state == State.ALLOCATE and state_stable and not self._mem_op_started and not self.memory.busy:
                self.memory.start_read(self.cache_ctrl.mem.address)
                self._mem_op_started = True

            if self.verbose:
                self._print_cycle(cycle)

        if self.verbose:
            self._print_separator()
            self._print_cache_state()
            self._print_stats()
            self._print_results()

        return {
            "stats": self.cache_ctrl.get_stats(),
            "results": self.cpu.results,
            "log": self.cache_ctrl.log,
            "cache": self.cache_ctrl.get_cache_snapshot(),
        }

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
        print(f"\n  {colorize('Configuration:', 'BOLD')}")
        print(f"    Cache Lines : {ctrl.num_lines}    "
              f"Block Size : {ctrl.block_size} words    "
              f"Address Bits : {ctrl.addr_bits}")
        print(f"    Tag Bits    : {ctrl.tag_bits}    "
              f"Index Bits : {ctrl.index_bits}        "
              f"Offset Bits  : {ctrl.offset_bits}")
        print(f"    Memory Read Latency  : {self.memory.read_latency} cycles")
        print(f"    Memory Write Latency : {self.memory.write_latency} cycles")
        print(f"    Policy: Write-Back, Write-Allocate, Direct-Mapped")
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
            prev = entry["prev_state"]
            curr = entry["state"]
            event = entry["event"]
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
            if "HIT" in event:
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
        print(f"\n  {colorize('Final Cache State:', 'BOLD')}")
        print(f"    {'Idx':>3}  {'V':>1}  {'D':>1}  {'Tag':>6}  {'Data'}")
        print(f"    {'---':>3}  {'-':>1}  {'-':>1}  {'------':>6}  {'----'}")
        for line in self.cache_ctrl.get_cache_snapshot():
            v = colorize("1", "GREEN") if line["valid"] else colorize("0", "DIM")
            d = colorize("1", "RED") if line["dirty"] else colorize("0", "DIM")
            tag = colorize(line["tag"], "CYAN") if line["valid"] else colorize(line["tag"], "DIM")
            data_str = " ".join(line["data"])
            if line["valid"]:
                data_str = colorize(data_str, "WHITE")
            else:
                data_str = colorize(data_str, "DIM")
            print(f"    {line['index']:>3}  {v}  {d}  {tag:>6}  [{data_str}]")

    def _print_stats(self):
        stats = self.cache_ctrl.get_stats()
        print(f"\n  {colorize('Performance Statistics:', 'BOLD')}")
        print(f"    Total Requests : {stats['total_requests']}")
        print(f"    Cache Hits     : {colorize(str(stats['hits']), 'GREEN')}")
        print(f"    Cache Misses   : {colorize(str(stats['misses']), 'RED')}")
        print(f"    Hit Rate       : {colorize(stats['hit_rate'], 'CYAN')}")
        print(f"    Total Cycles   : {stats['total_cycles']}")

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