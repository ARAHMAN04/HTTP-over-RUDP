import json
import hashlib


class RUDPPacket:
    VALID_FLAGS = {'SYN', 'SYNACK', 'ACK', 'FIN', 'DATA'}

    def __init__(self, seq_num, ack_num, flags, data=""):
        """
        Initializes a Reliable UDP Packet.
        seq_num  : sender's sequence number
        ack_num  : next sequence number the sender expects to receive
        flags    : one of SYN | SYNACK | ACK | FIN | DATA
        data     : payload string (only meaningful for DATA packets)
        """
        if flags not in self.VALID_FLAGS:
            raise ValueError(f"[Packet] Invalid flag '{flags}'. Must be one of {self.VALID_FLAGS}")

        self.seq_num  = seq_num
        self.ack_num  = ack_num
        self.flags    = flags
        self.data     = data
        self.checksum = self._calculate_checksum()

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def _calculate_checksum(self):
        """MD5 over all header fields + payload for complete integrity."""
        content = f"{self.seq_num}:{self.ack_num}:{self.flags}:{self.data}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def is_corrupt(self):
        """Returns True when the stored checksum doesn't match a fresh computation."""
        return self.checksum != self._calculate_checksum()

    def simulate_corruption(self):
        """Intentionally corrupt the checksum to test the receiver's detection logic."""
        self.checksum = "corrupted_checksum_0000"

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_bytes(self):
        """Serialize the packet to a UTF-8 JSON byte string."""
        return json.dumps({
            "seq_num":  self.seq_num,
            "ack_num":  self.ack_num,
            "flags":    self.flags,
            "data":     self.data,
            "checksum": self.checksum,
        }).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw_bytes):
        """
        Deserialize raw bytes back into an RUDPPacket.
        Returns None if the bytes are not valid JSON or are missing required keys.
        The stored checksum is preserved so is_corrupt() can validate it later.
        """
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def __str__(self):
        snippet = (self.data[:30] + "…") if len(self.data) > 30 else self.data
        return (
            f"[Packet] SEQ:{self.seq_num} | ACK:{self.ack_num} | "
            f"FLAGS:{self.flags:<6} | CHK:{self.checksum[:6]} | "
            f"DATA({len(self.data)}):'{snippet}'"
        )

    def __repr__(self):
        return self.__str__()