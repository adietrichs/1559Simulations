import asyncio
import heapq
import itertools

from transaction import Transaction, TxList


class TxPoolConfig:
    def __init__(self, price_bump=10, global_slots=4096, global_queue=1024):
        self.price_bump = price_bump
        self.global_slots = global_slots
        self.global_queue = global_queue


class TxPool:
    def __init__(self, config=TxPoolConfig()):
        self.config = config

        self.pending_nonces = TxNoncer()

        self.pending = {}  # type: dict[int, TxList]
        self.queue = {}  # type: dict[int, TxList]
        self.priced = PricedHeap(self)
        self.all = set()

        self.loop_queue = asyncio.Queue()
        self.chain_head_ch = Channel(self.loop_queue)
        self.shutdown_ch = Channel(self.loop_queue)

        self.reorg_queue = asyncio.Queue()
        self.req_reset_ch = Channel(self.reorg_queue)
        self.req_promote_ch = Channel(self.reorg_queue)
        self.queue_tx_event_ch = Channel(self.reorg_queue)

        event_loop = asyncio.get_event_loop()
        self.reorg_task = event_loop.create_task(self.schedule_reorg_loop())
        self.loop_task = event_loop.create_task(self.loop())

    def add_txs(self, txs: list[Transaction]):
        dirty = set()
        for tx in txs:
            replaced, err = self.add(tx)
            if not err and not replaced:
                dirty.add(tx.sender)
        if dirty:
            self.req_promote_ch.send(dirty)

    def add(self, tx: Transaction):
        if tx in self.all:
            return False, True

        total_slots = self.config.global_slots + self.config.global_queue
        if len(self.all) >= total_slots:
            if self.priced.underpriced(tx):
                return False, True
            drop = self.priced.discard(len(self.all) - total_slots + 1)
            for drop_tx in drop:
                self.remove_tx(drop_tx, outofbound=False)

        tx_list = self.pending.get(tx.sender)
        if tx_list and tx_list.overlaps(tx):
            inserted, old = tx_list.add(tx, self.config.price_bump)
            if not inserted:
                return False, True
            if old:
                self.all.remove(tx)
                self.priced.removed(1)
            self.all.add(tx)
            self.priced.put(tx)
            self.queue_tx_event_ch.send(tx)
            return old is not None, False

        return self.enqueue_tx(tx)

    def remove_tx(self, tx: Transaction, outofbound):
        self.all.remove(tx)
        if outofbound:
            self.priced.removed(1)

        try:
            pending = self.pending[tx.sender]
            removed, invalids = pending.remove(tx)
            if removed:
                if not len(pending):
                    del self.pending[tx.sender]
                for tx in invalids:
                    self.enqueue_tx(tx)
                self.pending_nonces.set_if_lower(tx.sender, tx.nonce)
                return
        except KeyError:
            pass

        try:
            future = self.queue[tx.sender]
            if future.remove(tx)[0]:
                if not len(future):
                    del self.queue[tx.sender]
        except KeyError:
            pass

    def enqueue_tx(self, tx):
        try:
            tx_list = self.queue[tx.sender]
        except KeyError:
            tx_list = TxList(False)
            self.queue[tx.sender] = tx_list
        inserted, old = tx_list.add(tx, self.config.price_bump)
        if not inserted:
            return False, True
        if old:
            self.all.remove(old)
            self.priced.removed(1)

        if tx not in self.all:
            self.all.add(tx)
            self.priced.put(tx)

        return old is not None, False

    async def schedule_reorg_loop(self):
        while True:
            print("reorg loop start")
            channel, value = await self.reorg_queue.get()
            if channel is self.req_reset_ch:
                print("request reset:", value)
            elif channel is self.req_promote_ch:
                print("request promote:", *[f"0x{account:016x}" for account in value])
            elif channel is self.queue_tx_event_ch:
                print("tx event:", value)
            else:
                raise NotImplementedError
            self.reorg_queue.task_done()

    async def loop(self):
        try:
            while True:
                print("loop start")
                channel, value = await self.loop_queue.get()
                if channel is self.chain_head_ch:
                    print("chain head event:", value)
                elif channel is self.shutdown_ch:
                    print("loop shutdown")
                    break
                else:
                    raise NotImplementedError
                self.loop_queue.task_done()
        except asyncio.CancelledError:
            print("loop interrupt")
        finally:
            self.reorg_task.cancel()
            print("loop exit")


class Channel:
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    def send(self, value=None):
        self.queue.put_nowait((self, value))


class PricedHeap:
    def __init__(self, tx_pool):
        self.tx_pool = tx_pool  # type: TxPool

        self.heap = []
        self.stales = 0
        self.counter = itertools.count()

    def __len__(self):
        return len(self.heap)

    def underpriced(self, tx: Transaction):
        return self.heap and self.heap[0][0] >= tx.gas_price

    def discard(self, slots):
        drop = []
        while self.heap and slots:
            tx = heapq.heappop(self.heap)[2]
            if tx not in self.tx_pool.all:
                self.stales -= 1
                continue
            drop.append(tx)
            slots -= 1
        return drop

    def removed(self, count):
        self.stales += count

    def put(self, tx: Transaction):
        heapq.heappush(self.heap, (tx.gas_price, next(self.counter), tx))


class TxNoncer:
    def __init__(self):
        pass

    def set_if_lower(self, addr, nonce):
        pass


if __name__ == "__main__":
    mempool = TxPool()
    mempool.add_txs([Transaction(abs(hash("a0")), 0, 10.0)])

    async def shutdown(delay=10):
        await asyncio.sleep(delay)
        mempool.shutdown_ch.send()
        await mempool.loop_task

    asyncio.get_event_loop().run_until_complete(shutdown(10))
