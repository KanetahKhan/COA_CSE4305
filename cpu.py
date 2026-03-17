from cache_controller import RequestType


class CPU:
    def __init__(self):
        self.request_queue = []
        self.current_request = None
        self.waiting_for_result = False
        self.results = []
        self.idle_cycles = 0
        self.busy_cycles = 0

    def load_requests(self, requests):
        self.request_queue = list(requests)

    def add_request(self, req_type, address, data=0):
        self.request_queue.append((req_type, address, data))

    def tick(self, cache_ready, cache_data_out):
        if self.waiting_for_result:
            self.busy_cycles += 1
            if cache_ready:
                req_type, addr, _ = self.current_request
                self.results.append({
                    "type": req_type,
                    "address": addr,
                    "data_returned": cache_data_out if req_type == RequestType.READ else None,
                })
                self.waiting_for_result = False
                self.current_request = None
                return None

            return None

        if self.request_queue:
            self.current_request = self.request_queue.pop(0)
            self.waiting_for_result = True
            return self.current_request
        else:
            self.idle_cycles += 1
            return None

    def is_done(self):
        return not self.request_queue and not self.waiting_for_result