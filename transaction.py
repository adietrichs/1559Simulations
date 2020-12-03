class Transaction:
    def __init__(self, sender, nonce, gas_price):
        self.sender = sender
        self.nonce = nonce
        self.gas_price = float(gas_price)

        self.hash = abs(hash(sender + hash(str(nonce)) + hash(self.gas_price)))

    def __hash__(self):
        return self.hash

    def __str__(self):
        return f"Tx<0x{self.hash:016x}>(sender=0x{self.sender:016x}, nonce={self.nonce}, gas_price={self.gas_price:.2f})"


class TxSortedMap:
    def __init__(self):
        self.items = {}  # type: dict[int, Transaction]

    def __len__(self):
        len(self.items)

    def get(self, nonce) -> Transaction:
        return self.items.get(nonce)

    def put(self, tx: Transaction):
        self.items[tx.nonce] = tx

    def remove(self, nonce):
        try:
            self.items.pop(nonce)
            return True
        except KeyError:
            return False

    def filter(self, filter):
        nonces_to_remove = [nonce for nonce, tx in self.items.items() if filter(tx)]
        return [self.items.pop(nonce) for nonce in nonces_to_remove]


class TxList:
    def __init__(self, strict):
        self.strict = strict

        self.txs = TxSortedMap()

    def __len__(self):
        return len(self.txs)

    def add(self, tx: Transaction, price_bump):
        old = self.txs.get(tx.nonce)
        if old and tx.gas_price <= old.gas_price * (100 + price_bump) / 100:
            return False, None
        self.txs.put(tx)
        return True, old

    def remove(self, tx: Transaction):
        if not self.txs.remove(tx.nonce):
            return False, []
        if self.strict:
            return True, self.txs.filter(lambda other_tx: other_tx.nonce > tx.nonce)
        return True, []

    def overlaps(self, tx: Transaction):
        return self.txs.get(tx.nonce) is not None
