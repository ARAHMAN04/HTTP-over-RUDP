import os
import threading
from rudp_socket import ReliableSocket, ConnectionError

HOST     = "127.0.0.1"
PORT     = 8080
WWW_ROOT = "."         


_MIME_MAP = {
    ".html": "text/html",
    ".htm":  "text/html",
    ".txt":  "text/plain",
    ".json": "application/json",
    ".css":  "text/css",
    ".js":   "application/javascript",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}

def _mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _MIME_MAP.get(ext, "application/octet-stream")

def _build_response(status: str, body: str, content_type: str = "text/html") -> str:
    return (
        f"HTTP/1.0 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
        f"{body}"
    )

def _safe_filepath(path: str) -> str | None:
    if path == "/":
        path = "/index.html"

    candidate = os.path.realpath(os.path.join(WWW_ROOT, path.lstrip("/")))
    root       = os.path.realpath(WWW_ROOT)
    if not candidate.startswith(root + os.sep) and candidate != root:
        return None
    return candidate


def _handle_client(conn: ReliableSocket, addr):
    print(f"\n[SERVER] Handling client {addr}")
    try:
        request_data = conn.recv()
    except ConnectionError as e:
        print(f"[SERVER] recv failed for {addr}: {e}")
        conn.close()
        return

    if not request_data:
        conn.close()
        return

    print(f"[SERVER] ← {addr}\n{request_data.splitlines()[0]}")  

    lines        = request_data.split("\r\n")
    request_line = lines[0].split(" ")

    if len(request_line) < 2:
        response = _build_response("400 Bad Request", "<h1>400 Bad Request</h1>")
        _send_and_close(conn, response)
        return

    method = request_line[0].upper()
    path   = request_line[1]

    # ---- GET ----
    if method == "GET":
        filepath = _safe_filepath(path)

        if filepath is None:
            response = _build_response("403 Forbidden", "<h1>403 Forbidden</h1>")

        elif os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    body = f.read()
                response = _build_response("200 OK", body, _mime(filepath))
            except (OSError, UnicodeDecodeError) as e:
                print(f"[SERVER] File read error: {e}")
                response = _build_response("500 Internal Server Error",
                                           "<h1>500 Internal Server Error</h1>")
        else:
            response = _build_response("404 Not Found", "<h1>404 Not Found</h1>")

    # ---- POST ----
    elif method == "POST":
        parts     = request_data.split("\r\n\r\n", 1)
        post_body = parts[1] if len(parts) > 1 else ""
        print(f"[SERVER] POST body: {post_body[:200]}")
        body     = f"POST received ({len(post_body)} bytes)."
        response = _build_response("200 OK", body, "text/plain")

    # ---- Anything else ----
    else:
        body     = f"<h1>405 Method Not Allowed</h1><p>{method} is not supported.</p>"
        response = _build_response("405 Method Not Allowed", body)

    _send_and_close(conn, response)


def _send_and_close(conn: ReliableSocket, response: str):
    try:
        conn.send(response)
    except ConnectionError as e:
        print(f"[SERVER] send failed: {e}")
    finally:
        conn.close()

# Main server loop
def run_server():
    server = ReliableSocket()
    server.bind((HOST, PORT))
    print(f"[SERVER] HTTP/1.0 over RUDP — listening on {HOST}:{PORT}")
    print(f"[SERVER] Serving files from '{os.path.realpath(WWW_ROOT)}'")

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down.")


# Entry point
if __name__ == "__main__":
    index = os.path.join(WWW_ROOT, "index.html")
    if not os.path.exists(index):
        with open(index, "w", encoding="utf-8") as f:
            f.write(
                "<html><head><title>RUDP Server</title></head>"
                "<body><h1>Hello from Reliable UDP Server!</h1>"
                "<p>The lab is working correctly.</p></body></html>"
            )
        print(f"[SERVER] Created demo {index}")

    run_server()