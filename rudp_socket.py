import socket
import random
import threading
from Packet import RUDPPacket

MAX_RETRIES = 10          
RECV_BUFFER = 65535   # UDP max payload


class ConnectionError(Exception):
    """Raised when a reliable send exhausts all retries"""


class ReliableSocket:

    def __init__(self, timeout: float = 2.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.target_addr = None
        self.seq_num = 0
        self.expected_seq_num = 0
        self.loss_rate = 0.0
        self.corruption_rate = 0.0
        self.is_server = False
        self._lock = threading.RLock()

    def set_simulation_rates(self, loss_rate: float = 0.0, corruption_rate: float = 0.0):
        if not (0.0 <= loss_rate <= 1.0 and 0.0 <= corruption_rate <= 1.0):
            raise ValueError("Rates must be between 0.0 and 1.0")
        self.loss_rate = loss_rate
        self.corruption_rate = corruption_rate

    def bind(self, address):
        self.sock.bind(address)
        self.is_server = True

    def _send_raw(self, packet: RUDPPacket):
        if random.random() < self.corruption_rate:
            print("[SIM] Corrupting packet before send…")
            packet.simulate_corruption()

        if random.random() < self.loss_rate:
            print("[SIM] Dropping packet (simulated loss).")
            return

        if self.target_addr:
            try:
                self.sock.sendto(packet.to_bytes(), self.target_addr)
            except OSError as e:
                print(f"[RUDP] sendto error: {e}")

    def _recv_raw(self):
        try:
            data, addr = self.sock.recvfrom(RECV_BUFFER)
            pkt = RUDPPacket.from_bytes(data)
            if pkt is None:
                print("[RUDP] Received unreadable packet — discarding.")
                return None, addr
            if pkt.is_corrupt():
                print(f"[RUDP] Dropped corrupted packet from {addr}.")
                return None, addr
            return pkt, addr
        except socket.timeout:
            return None, None
        except (ConnectionResetError, OSError):
            return None, None

    # Reliable send (stop-and-wait)
    def _send_reliable(self, packet: RUDPPacket, expected_ack_flag: str) -> RUDPPacket:

        self._send_raw(packet)
        attempts = 1

        while True:
            ack_pkt, _ = self._recv_raw()
            if ack_pkt is None:
                if attempts >= MAX_RETRIES:
                    raise ConnectionError(
                        f"[RUDP] No {expected_ack_flag} after {MAX_RETRIES} attempts. Giving up."
                    )
                print(f"[RUDP] Timeout ({attempts}/{MAX_RETRIES}) waiting for "
                      f"{expected_ack_flag}. Retransmitting…")
                self._send_raw(packet)
                attempts += 1
                continue

            if ack_pkt.flags != expected_ack_flag:

                if ack_pkt.flags == "FIN":
                    print("[RUDP] Received FIN while waiting for ACK "
                          "— peer already closed. Sending ACK and finishing.")
                    ack = RUDPPacket(self.seq_num, ack_pkt.seq_num + 1, "ACK")
                    self._send_raw(ack)
                    self.seq_num += 1
                    return ack_pkt  
                else:
                    print(f"[RUDP] Unexpected packet (flags={ack_pkt.flags}, "
                          f"ack={ack_pkt.ack_num}) while waiting for "
                          f"{expected_ack_flag}. Ignoring.")
                continue

            self.seq_num += 1
            return ack_pkt

    def connect(self, address):
        self.target_addr = address
        self._reset_state()
        print("[RUDP] Initiating 3-way handshake…")

        syn_pkt = RUDPPacket(self.seq_num, 0, "SYN")
        synack_pkt = self._send_reliable(syn_pkt, expected_ack_flag="SYNACK")

        self.expected_seq_num = synack_pkt.seq_num + 1
        ack_pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "ACK")
        self._send_raw(ack_pkt)
        print("[RUDP] Connection established.")

    def accept(self):
        print("[RUDP] Listening for incoming connections…")
        while True:
            pkt, addr = self._recv_raw()
            if pkt and pkt.flags == "SYN":
                self.target_addr = addr
                self._reset_state()
                self.expected_seq_num = pkt.seq_num + 1

                print(f"[RUDP] Received SYN from {addr}. Sending SYNACK…")
                synack_pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "SYNACK")
                self._send_reliable(synack_pkt, expected_ack_flag="ACK")
                print(f"[RUDP] Connection accepted from {addr}.")
                return self, addr

    def send(self, data: str):
        with self._lock:
            pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "DATA", data)
            self._send_reliable(pkt, expected_ack_flag="ACK")

    def recv(self) -> str:
        with self._lock:
            while True:
                pkt, _ = self._recv_raw()
                if pkt is None:
                    continue

                if pkt.flags == "FIN":
                    print("[RUDP] Received FIN inside recv() — sending ACK.")
                    ack = RUDPPacket(self.seq_num, pkt.seq_num + 1, "ACK")
                    self._send_raw(ack)
                    continue   

                if pkt.flags != "DATA":
                    continue

                if pkt.seq_num == self.expected_seq_num:
                    self.expected_seq_num += 1
                    ack = RUDPPacket(self.seq_num, self.expected_seq_num, "ACK")
                    self._send_raw(ack)
                    return pkt.data
                else:
                    print(f"[RUDP] Duplicate DATA (seq={pkt.seq_num} "
                          f"expected={self.expected_seq_num}). Resending ACK.")
                    ack = RUDPPacket(self.seq_num, self.expected_seq_num, "ACK")
                    self._send_raw(ack)

    def close(self):

        if self.is_server:
            print("[RUDP] Waiting for client FIN...")
            for _ in range(MAX_RETRIES):
                pkt, _ = self._recv_raw()
                if pkt is None:
                    continue
                if pkt.flags == "FIN":
                    ack = RUDPPacket(self.seq_num, pkt.seq_num + 1, "ACK")
                    self._send_raw(ack)
                    print("[RUDP] Client FIN acknowledged.")
                    break
            self.target_addr = None
            print("[RUDP] Connection closed. Server socket ready for next client.")
            return
 
        # Client path
        print("[RUDP] Initiating teardown (FIN)...")
        fin_pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "FIN")
 
        acked = False
        for attempt in range(1, MAX_RETRIES + 1):
            self._send_raw(fin_pkt)
            pkt, _ = self._recv_raw()
 
            if pkt is None:
                print(f"[RUDP] FIN attempt {attempt}/{MAX_RETRIES}: no response.")
                continue
 
            if pkt.flags == "ACK":
                print("[RUDP] Teardown acknowledged.")
                acked = True
                break
 
            if pkt.flags == "FIN":
                # Simultaneous close edge case
                print("[RUDP] Simultaneous FIN. Sending ACK...")
                ack = RUDPPacket(self.seq_num, pkt.seq_num + 1, "ACK")
                self._send_raw(ack)
                acked = True
                break
 
        if not acked:
            print("[RUDP] Teardown timed out - closing anyway.")
 
        try:
            self.sock.close()
        except OSError:
            pass
        print("[RUDP] Socket closed.")

    def _reset_state(self):
        self.seq_num  = 0
        self.expected_seq_num = 0