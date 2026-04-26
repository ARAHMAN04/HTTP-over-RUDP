import sys
from rudp_socket import ReliableSocket, ConnectionError

SERVER_HOST      = "127.0.0.1"
SERVER_PORT      = 8080
SIM_LOSS_RATE    = 0.0   
SIM_CORRUPT_RATE = 0.0   


def _build_get(path: str) -> str:
    return (
        f"GET {path} HTTP/1.0\r\n"
        f"Host: {SERVER_HOST}\r\n"
        "User-Agent: RUDP-Client/2.0\r\n"
        "Connection: close\r\n\r\n"
    )


def _build_post(path: str, body: str) -> str:
    return (
        f"POST {path} HTTP/1.0\r\n"
        f"Host: {SERVER_HOST}\r\n"
        "User-Agent: RUDP-Client/2.0\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
        f"{body}"
    )


def _parse_response(raw: str):
    parts   = raw.split("\r\n\r\n", 1)
    header_block = parts[0]
    body    = parts[1] if len(parts) > 1 else ""
    lines   = header_block.split("\r\n")
    status  = lines[0] if lines else "(no status)"
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k] = v
    return status, headers, body


def run_client(method: str = "GET", path: str = "/index.html", post_body: str = ""):
    client = ReliableSocket()

    if SIM_LOSS_RATE or SIM_CORRUPT_RATE:
        client.set_simulation_rates(SIM_LOSS_RATE, SIM_CORRUPT_RATE)
        print(f"[CLIENT] Simulation — loss={SIM_LOSS_RATE:.0%}  corrupt={SIM_CORRUPT_RATE:.0%}")

    # ---- Handshake ----
    print(f"[CLIENT] Connecting to {SERVER_HOST}:{SERVER_PORT}…")
    try:
        client.connect((SERVER_HOST, SERVER_PORT))
    except ConnectionError as e:
        print(f"[CLIENT] Connection failed: {e}")
        return

    # ---- Build request ----
    method = method.upper()
    if method == "GET":
        request = _build_get(path)
    elif method == "POST":
        request = _build_post(path, post_body)
    else:
        print(f"[CLIENT] Unsupported method '{method}'. Use GET or POST.")
        client.close()
        return

    print(f"[CLIENT] → {method} {path}")

    # ---- Send ----
    try:
        client.send(request)
    except ConnectionError as e:
        print(f"[CLIENT] Send failed: {e}")
        client.close()
        return

    # ---- Receive ----
    print("[CLIENT] Waiting for response…")
    try:
        raw = client.recv()
    except ConnectionError as e:
        print(f"[CLIENT] Recv failed: {e}")
        client.close()
        return

    status, headers, body = _parse_response(raw)

    print("\n[CLIENT] ─── Response ────────────────────────────────")
    print(f"  Status : {status}")
    for k, v in headers.items():
        print(f"  {k}: {v}")
    print(f"\n{body}")
    print("[CLIENT] ─────────────────────────────────────────────")

    # ---- Teardown ----
    client.close()


# Entry point
if __name__ == "__main__":
    method    = sys.argv[1] if len(sys.argv) > 1 else "GET"
    path      = sys.argv[2] if len(sys.argv) > 2 else "/index.html"
    post_body = sys.argv[3] if len(sys.argv) > 3 else ""

    run_client(method, path, post_body)