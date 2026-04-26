import json
import hashlib


class RUDPPacket:
    VALID_FLAGS = {'SYN', 'SYNACK', 'ACK', 'FIN', 'DATA'}

    def __init__(self, seq_num, ack_num, flags, data=""):

        if flags not in self.VALID_FLAGS:
            raise ValueError(f"[Packet] Invalid flag '{flags}'. Must be one of {self.VALID_FLAGS}")

        self.seq_num  = seq_num
        self.ack_num  = ack_num
        self.flags    = flags
        self.data     = data
        self.checksum = self._calculate_checksum()


    def _calculate_checksum(self):
        content = f"{self.seq_num}:{self.ack_num}:{self.flags}:{self.data}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def is_corrupt(self):
        return self.checksum != self._calculate_checksum()

    def simulate_corruption(self):
        self.checksum = "corrupted_checksum_0000"

    # Serialization

    def to_bytes(self):
        return json.dumps({
            "seq_num":  self.seq_num,
            "ack_num":  self.ack_num,
            "flags":    self.flags,
            "data":     self.data,
            "checksum": self.checksum,
        }).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw_bytes):

        try:
            d = json.loads(raw_bytes.decode("utf-8"))
            pkt = cls(
                seq_num=d["seq_num"],
                ack_num=d["ack_num"],
                flags=d["flags"],
                data=d.get("data", ""),
            )
            # Overwrite the freshly computed checksum with the one that arrived
            # so is_corrupt() compares received vs expected.
            pkt.checksum = d["checksum"]
            return pkt
        except (json.JSONDecodeError, KeyError, ValueError):
            return None


    def __str__(self):
        snippet = (self.data[:30] + "…") if len(self.data) > 30 else self.data
        return (
            f"[Packet] SEQ:{self.seq_num} | ACK:{self.ack_num} | "
            f"FLAGS:{self.flags:<6} | CHK:{self.checksum[:6]} | "
            f"DATA({len(self.data)}):'{snippet}'"
        )

    def __repr__(self):
        return self.__str__()