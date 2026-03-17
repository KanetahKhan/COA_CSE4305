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

    def read_word(self, address):
        if address < len(self.data):
            return self.data[address]
        return 0