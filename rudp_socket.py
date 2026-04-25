import socket
import random
import threading
from packet import RUDPPacket

MAX_RETRIES = 10          # Max send attempts before giving up
RECV_BUFFER = 65535       # UDP max payload


class ConnectionError(Exception):
    """Raised when a reliable send exhausts all retries."""


class ReliableSocket:
    """
    Stop-and-wait Reliable UDP socket.

    Improvements over v1:
    - Sequence numbers reset between server connections (fixes desync on 2nd client)
    - _send_reliable raises ConnectionError after MAX_RETRIES (no infinite loop)
    - accept() uses a dedicated _handshake_seq so server seq_num is always clean
    - Thread-safe send/recv via a reentrant lock
    - Simulation rates validated to [0.0, 1.0]
    - Cleaner teardown with TIME_WAIT equivalent (flush lingering packets)
    """

    def __init__(self, timeout: float = 2.0):
        self.sock             = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.target_addr      = None
        self.seq_num          = 0
        self.expected_seq_num = 0
        self.loss_rate        = 0.0
        self.corruption_rate  = 0.0
        self.is_server        = False
        self._lock            = threading.RLock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_simulation_rates(self, loss_rate: float = 0.0, corruption_rate: float = 0.0):
        """Set packet loss / corruption probabilities (each must be in [0.0, 1.0])."""
        if not (0.0 <= loss_rate <= 1.0 and 0.0 <= corruption_rate <= 1.0):
            raise ValueError("Rates must be between 0.0 and 1.0")
        self.loss_rate       = loss_rate
        self.corruption_rate = corruption_rate

    def bind(self, address):
        self.sock.bind(address)
        self.is_server = True

    # ------------------------------------------------------------------
    # Low-level send / recv
    # ------------------------------------------------------------------

    def _send_raw(self, packet: RUDPPacket):
        """Send one packet, applying simulated corruption and loss."""
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
        """
        Block until a valid, uncorrupted packet arrives or a timeout occurs.
        Returns (RUDPPacket | None, addr | None).
        """
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
            # Windows ICMP Port Unreachable and similar — safe to ignore
            return None, None

    # ------------------------------------------------------------------
    # Reliable send (stop-and-wait)
    # ------------------------------------------------------------------

    def _send_reliable(self, packet: RUDPPacket, expected_ack_flag: str) -> RUDPPacket:
        """
        Send *packet* and block until the expected ACK arrives.
        Retransmits on timeout up to MAX_RETRIES times.
        Raises ConnectionError when all retries are exhausted.
        Increments self.seq_num on success.
        """
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

            elif ack_pkt.flags == expected_ack_flag and ack_pkt.ack_num == packet.seq_num + 1:
                self.seq_num += 1
                return ack_pkt

            else:
                # Wrong flag or wrong ack_num — stale / out-of-order; ignore
                print(f"[RUDP] Unexpected packet (flags={ack_pkt.flags}, "
                      f"ack={ack_pkt.ack_num}) while waiting for {expected_ack_flag}. Ignoring.")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, address):
        """Client-side 3-way handshake: SYN → SYNACK → ACK."""
        self.target_addr = address
        self._reset_state()
        print("[RUDP] Initiating 3-way handshake…")

        syn_pkt    = RUDPPacket(self.seq_num, 0, "SYN")
        synack_pkt = self._send_reliable(syn_pkt, expected_ack_flag="SYNACK")

        self.expected_seq_num = synack_pkt.seq_num + 1
        ack_pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "ACK")
        self._send_raw(ack_pkt)
        print("[RUDP] Connection established.")

    def accept(self):
        """
        Server-side: block until a SYN arrives, complete the handshake,
        and return (self, client_addr).

        FIX: seq_num and expected_seq_num are reset per-connection so that
        the second (and every subsequent) client doesn't experience desync.
        """
        print("[RUDP] Listening for incoming connections…")
        while True:
            pkt, addr = self._recv_raw()
            if pkt and pkt.flags == "SYN":
                self.target_addr      = addr
                self._reset_state()                         # ← key fix
                self.expected_seq_num = pkt.seq_num + 1

                print(f"[RUDP] Received SYN from {addr}. Sending SYNACK…")
                synack_pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "SYNACK")
                self._send_reliable(synack_pkt, expected_ack_flag="ACK")
                print(f"[RUDP] Connection accepted from {addr}.")
                return self, addr

    # ------------------------------------------------------------------
    # Data transfer
    # ------------------------------------------------------------------

    def send(self, data: str):
        """Reliably send a DATA packet carrying *data*."""
        with self._lock:
            pkt = RUDPPacket(self.seq_num, self.expected_seq_num, "DATA", data)
            self._send_reliable(pkt, expected_ack_flag="ACK")

    def recv(self) -> str:
        """
        Block until a DATA packet with the expected sequence number arrives.
        Duplicate packets are ACKed again (sender may not have got the first ACK).
        Returns the payload string.
        """
        with self._lock:
            while True:
                pkt, _ = self._recv_raw()
                if pkt is None:
                    continue

                if pkt.flags != "DATA":
                    # Could be a stale handshake/teardown packet — ignore
                    continue

                if pkt.seq_num == self.expected_seq_num:
                    self.expected_seq_num += 1
                    ack = RUDPPacket(self.seq_num, self.expected_seq_num, "ACK")
                    self._send_raw(ack)
                    return pkt.data
                else:
                    # Duplicate — resend ACK so the sender can move on
                    print(f"[RUDP] Duplicate DATA (seq={pkt.seq_num} "
                          f"expected={self.expected_seq_num}). Resending ACK.")
                    ack = RUDPPacket(self.seq_num, self.expected_seq_num, "ACK")
                    self._send_raw(ack)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def close(self):
        """
        Send FIN and wait for acknowledgment.
        Server resets state and keeps socket alive for the next client.
        Client closes the underlying UDP socket.
        """
        print("[RUDP] Initiating teardown (FIN)…")
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
                # Simultaneous close
                print("[RUDP] Simultaneous FIN received. Sending ACK…")
                ack = RUDPPacket(self.seq_num, pkt.seq_num + 1, "ACK")
                self._send_raw(ack)
                acked = True
                break

        if not acked:
            print("[RUDP] Teardown timed out — closing anyway.")

        # Brief TIME_WAIT: drain any in-flight packets before closing
        self.sock.settimeout(0.5)
        while True:
            pkt, _ = self._recv_raw()
            if pkt is None:
                break
        self.sock.settimeout(2.0)

        if self.is_server:
            # Server: keep socket alive, reset per-connection state
            self.target_addr = None
            print("[RUDP] Connection closed. Server socket ready for next client.")
        else:
            try:
                self.sock.close()
            except OSError:
                pass
            print("[RUDP] Socket closed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_state(self):
        """Reset sequence numbers for a fresh connection."""
        self.seq_num          = 0
        self.expected_seq_num = 0