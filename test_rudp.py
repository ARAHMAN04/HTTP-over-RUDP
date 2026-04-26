import json
import socket
import threading
import time
import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from Packet import RUDPPacket
from rudp_socket import ReliableSocket, ConnectionError, MAX_RETRIES

# Shared helpers
def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port, handler):
    srv = ReliableSocket(timeout=3.0)
    srv.bind(("127.0.0.1", port))
    done = threading.Event()

    def _loop():
        try:
            conn, _ = srv.accept()
            handler(conn)
        except Exception:
            pass
        done.set()

    threading.Thread(target=_loop, daemon=True).start()
    time.sleep(0.05)
    return done


def _http_handler(conn, www_root):
    def _resp(status, body, ct="text/html"):
        return (f"HTTP/1.0 {status}\r\nContent-Type: {ct}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n{body}")
    raw    = conn.recv()
    tokens = raw.split("\r\n")[0].split(" ")
    method, path = tokens[0].upper(), tokens[1]
    if method == "GET":
        if path == "/":
            path = "/index.html"
        fp = os.path.realpath(os.path.join(www_root, path.lstrip("/")))
        if os.path.exists(fp):
            conn.send(_resp("200 OK", open(fp).read(), "text/html"))
        else:
            conn.send(_resp("404 Not Found", "<h1>404 Not Found</h1>"))
    elif method == "POST":
        body = raw.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw else ""
        conn.send(_resp("200 OK", f"POST received ({len(body)} bytes).", "text/plain"))
    else:
        conn.send(_resp("405 Method Not Allowed", "<h1>405</h1>"))
    conn.close()


def _http_request(port, method, path, body=""):
    if method == "GET":
        req = f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    else:
        req = (f"POST {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n{body}")
    cli = ReliableSocket(timeout=3.0)
    cli.connect(("127.0.0.1", port))
    cli.send(req)
    raw = cli.recv()
    cli.close()
    return raw

class TestChecksum(unittest.TestCase):
    """calculate checksum, include in packet, drop if incorrect on receive."""

    def test_checksum_detects_tampered_packet(self):
        pkt = RUDPPacket(seq_num=1, ack_num=2, flags="DATA", data="hello")
        self.assertFalse(pkt.is_corrupt())
        pkt.data = "tampered"           # mutate without recalculating checksum
        self.assertTrue(pkt.is_corrupt())


class TestSimulateCorruption(unittest.TestCase):
    """implement a method to simulate a false checksum."""

    def test_simulate_corruption_makes_packet_corrupt(self):
        pkt = RUDPPacket(0, 0, "DATA", "test")
        pkt.simulate_corruption()
        self.assertTrue(pkt.is_corrupt())


class TestPacketLoss(unittest.TestCase):
    """implement packet loss simulation method."""

    def test_loss_rate_1_never_sends(self):
        from unittest.mock import MagicMock
        sock = ReliableSocket()
        sock.set_simulation_rates(loss_rate=1.0)
        sock.target_addr = ("127.0.0.1", 9999)
        sock.sock = MagicMock()
        sock._send_raw(RUDPPacket(0, 0, "DATA", "x"))
        sock.sock.sendto.assert_not_called()


class TestSequenceNumber(unittest.TestCase):
    """sequence numbers — duplicate packet must be ACKed but not re-delivered."""

    def test_duplicate_packet_not_delivered_twice(self):
        port        = _free_port()
        server      = ReliableSocket(timeout=2.0)
        server.bind(("127.0.0.1", port))
        client_port = _free_port()
        udp         = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", client_port))

        server.target_addr      = ("127.0.0.1", client_port)
        server.expected_seq_num = 0

        received = []
        done = threading.Event()

        def _recv():
            received.append(server.recv())
            done.set()

        threading.Thread(target=_recv, daemon=True).start()
        time.sleep(0.05)

        raw = RUDPPacket(seq_num=0, ack_num=0, flags="DATA", data="dup").to_bytes()
        udp.sendto(raw, ("127.0.0.1", port))
        time.sleep(0.05)
        udp.sendto(raw, ("127.0.0.1", port)) 

        done.wait(timeout=4)
        udp.close()
        self.assertEqual(len(received), 1)


class TestHandshake(unittest.TestCase):
    """3-way handshake with SYN, SYNACK, ACK flags."""

    def test_three_way_handshake_completes(self):
        port = _free_port()
        _start_server(port, lambda conn: conn.close())
        client = ReliableSocket(timeout=3.0)
        client.connect(("127.0.0.1", port))   # raises ConnectionError on failure
        client.close()


class TestRetransmission(unittest.TestCase):
    """timeout -> retransmit; give up after MAX_RETRIES with ConnectionError."""

    def test_retransmits_then_raises_on_total_loss(self):
        client = ReliableSocket(timeout=0.1)
        client.target_addr = ("127.0.0.1", _free_port())
        client.set_simulation_rates(loss_rate=1.0)
        with self.assertRaises(ConnectionError):
            client._send_reliable(RUDPPacket(0, 0, "DATA", "gone"), "ACK")
        client.sock.close()

    def test_data_delivered_after_one_dropped_packet(self):
        port    = _free_port()
        results = {}
        done    = threading.Event()

        def _handler(conn):
            results["data"] = conn.recv()
            conn.send("ok")
            conn.close()
            done.set()

        _start_server(port, _handler)

        client = ReliableSocket(timeout=1.0)
        client.connect(("127.0.0.1", port))

        orig    = client._send_raw
        dropped = {"once": False}

        def _drop_once(pkt):
            if pkt.flags == "DATA" and not dropped["once"]:
                dropped["once"] = True
                return         
            orig(pkt)

        client._send_raw = _drop_once
        client.send("retransmit me")
        client._send_raw = orig
        client.recv()
        client.close()

        done.wait(timeout=8)
        self.assertEqual(results.get("data"), "retransmit me")


class TestFINTeardown(unittest.TestCase):
    """FIN flag — connection teardown."""

    def test_fin_closes_connection_cleanly(self):
        port = _free_port()
        done = threading.Event()

        def _handler(conn):
            conn.recv()
            conn.send("bye")
            conn.close()
            done.set()

        _start_server(port, _handler)

        client = ReliableSocket(timeout=3.0)
        client.connect(("127.0.0.1", port))
        client.send("hi")
        client.recv()
        client.close()   # must not raise

        done.wait(timeout=6)


class TestHTTPGet200(unittest.TestCase):
    """GET method, 200 OK status."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()
        with open(os.path.join(self.root, "index.html"), "w") as f:
            f.write("<h1>Hello</h1>")
        self.port = _free_port()
        _start_server(self.port, lambda c: _http_handler(c, self.root))

    def test_get_returns_200_ok(self):
        raw = _http_request(self.port, "GET", "/index.html")
        self.assertIn("200 OK", raw)
        self.assertIn("Hello", raw)


class TestHTTPGet404(unittest.TestCase):
    """404 Not Found status."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()
        self.port = _free_port()
        _start_server(self.port, lambda c: _http_handler(c, self.root))

    def test_get_missing_file_returns_404(self):
        raw = _http_request(self.port, "GET", "/no_such_file.html")
        self.assertIn("404", raw)


class TestHTTPPost(unittest.TestCase):
    """POST method."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()
        self.port = _free_port()
        _start_server(self.port, lambda c: _http_handler(c, self.root))

    def test_post_returns_200(self):
        raw = _http_request(self.port, "POST", "/submit", "name=Abdullah&project=RUDP")
        self.assertIn("200 OK", raw)


class TestCorruptionEndToEnd(unittest.TestCase):
    """corrupted packet dropped by receiver; sender retransmits successfully."""

    def test_corrupted_packet_retransmitted_and_received(self):
        port    = _free_port()
        results = {}
        done    = threading.Event()

        def _handler(conn):
            results["data"] = conn.recv()
            conn.send("ok")
            conn.close()
            done.set()

        _start_server(port, _handler)

        client = ReliableSocket(timeout=1.0)
        client.connect(("127.0.0.1", port))

        orig      = client._send_raw
        corrupted = {"once": False}

        def _corrupt_once(pkt):
            if pkt.flags == "DATA" and not corrupted["once"]:
                corrupted["once"] = True
                d = json.loads(pkt.to_bytes())
                d["checksum"] = "corrupted_checksum_0000"
                client.sock.sendto(json.dumps(d).encode(), client.target_addr)
                return
            orig(pkt)

        client._send_raw = _corrupt_once
        client.send("survive corruption")
        client._send_raw = orig
        client.recv()
        client.close()

        done.wait(timeout=10)
        self.assertEqual(results.get("data"), "survive corruption")


# Entry point
if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(
        __import__("__main__")))
    sys.exit(0 if result.wasSuccessful() else 1)