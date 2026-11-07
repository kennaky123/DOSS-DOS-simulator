"""Server Web Bán Hàng E-Commerce thực tế (Sử dụng SQLite và Thread Pool).

Hệ thống được thiết kế chân thực để chứng minh tấn công DoS/DDoS:
- Số lượng Worker Threads giới hạn (mặc định 8 luồng, tương tự Apache/Gunicorn).
- Khi bị DDoS và TẮT phòng thủ: Worker threads bị nghẽn -> Trình duyệt Chrome/Edge thực tế
  sẽ bị quay tròn vô tận (Native Loading Spinner) và không thể tải trang!
- Khi BẬT phòng thủ (WAF Rate Limiter): Tự động phát hiện botnet và chặn ngay lập tức,
  giúp người dùng thật trên trình duyệt tải trang vèo vèo (<20ms).
"""

from __future__ import annotations

import collections
import hashlib
import http.cookies
import json
import os
import queue
import re
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from db import get_connection, hash_password, init_db

# Thư mục gốc
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Cấu hình Web Server
HOST = "127.0.0.1"
PORT = 5000
WORKER_THREADS = 8           # 8 luồng xử lý đồng thời
RATE_LIMIT_PER_SEC = 10      # Ngưỡng chặn DDoS: tối đa 10 request/giây/IP
RATE_WINDOW_SEC = 2.0

# Trạng thái phòng thủ và giám sát (Thread-Safe)
state_lock = threading.Lock()
defense_enabled = False      # Mặc định tắt phòng thủ để kiểm tra hiệu ứng lag/xoay vòng
total_requests = 0
blocked_requests = 0
active_workers = 0

# Rate limiter tracking: IP -> deque([timestamp])
ip_request_history: dict[str, collections.deque] = collections.defaultdict(collections.deque)
sessions: dict[str, dict] = {}  # session_token -> user_data
ram_stress_buffers: collections.deque[bytearray] = collections.deque(maxlen=400)


def apply_dos_workload(current_defense: bool):
    """Khi tắt phòng thủ WAF: Đốt CPU (băm SHA-256) + Đốt RAM (cấp phát buffer) + Nghẽn Worker Pool."""
    if not current_defense:
        # 1. ĐỐT CPU (CPU Stress via Cryptographic Hash Workload):
        # Ép CPU máy tính lên 80% - 100% thấy rõ trên Task Manager của Windows!
        dummy = b"stress_dos_cpu_" + str(time.monotonic()).encode()
        for _ in range(45_000):
            dummy = hashlib.sha256(dummy).digest()

        # 2. ĐỐT RAM (RAM Exhaustion via Socket Buffer Accumulation):
        # Tích lũy buffer 512KB cho mỗi kết nối DoS để đẩy RAM tiến trình lên
        with state_lock:
            ram_stress_buffers.append(bytearray(512 * 1024))
            current_active = active_workers

        # 3. GÂY NGHẼN HÀNG ĐỢI (Queue Latency):
        time.sleep(0.3 + min(0.9, current_active * 0.1))
    else:
        with state_lock:
            if ram_stress_buffers:
                ram_stress_buffers.clear()


def check_rate_limit(client_ip: str) -> bool:
    """Kiểm tra xem IP có đang gửi request quá tần suất cho phép hay không."""
    now = time.monotonic()
    history = ip_request_history[client_ip]
    while history and history[0] < now - RATE_WINDOW_SEC:
        history.popleft()
    history.append(now)
    # Tần suất request / giây
    rps = len(history) / RATE_WINDOW_SEC
    return rps <= RATE_LIMIT_PER_SEC


def handle_api_products(conn) -> tuple[int, dict]:
    """Lấy danh sách sản phẩm từ SQLite."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, category, price, stock, image_icon, description FROM products")
    rows = cursor.fetchall()
    products = [dict(row) for row in rows]
    return 200, {"success": True, "products": products}


def handle_api_search(conn, query_string: str) -> tuple[int, dict]:
    """Tìm kiếm sản phẩm theo từ khóa từ SQLite."""
    params = parse_qs(query_string)
    keyword = params.get("q", [""])[0].strip()
    cursor = conn.cursor()
    if keyword:
        cursor.execute(
            "SELECT id, name, category, price, stock, image_icon, description FROM products WHERE name LIKE ? OR category LIKE ?",
            (f"%{keyword}%", f"%{keyword}%")
        )
    else:
        cursor.execute("SELECT id, name, category, price, stock, image_icon, description FROM products")
    rows = cursor.fetchall()
    return 200, {"success": True, "keyword": keyword, "products": [dict(row) for row in rows]}


def handle_api_register(conn, body_json: dict) -> tuple[int, dict]:
    """Đăng ký tài khoản mới và lưu vào SQLite."""
    username = body_json.get("username", "").strip()
    password = body_json.get("password", "").strip()
    fullname = body_json.get("fullname", "").strip() or username
    email = body_json.get("email", "").strip() or f"{username}@gmail.com"

    if not username or not password:
        return 400, {"success": False, "message": "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu."}

    pwd_hash = hash_password(password)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, fullname, email) VALUES (?, ?, ?, ?)",
            (username, pwd_hash, fullname, email)
        )
        conn.commit()
        return 201, {"success": True, "message": f"Đăng ký tài khoản '{username}' thành công!"}
    except Exception as e:
        if "UNIQUE" in str(e):
            return 409, {"success": False, "message": f"Tài khoản '{username}' đã tồn tại!"}
        return 500, {"success": False, "message": f"Lỗi cơ sở dữ liệu: {str(e)}"}


def handle_api_login(conn, body_json: dict) -> tuple[int, dict, str | None]:
    """Đăng nhập, kiểm tra hash SQLite và tạo session cookie."""
    username = body_json.get("username", "").strip()
    password = body_json.get("password", "").strip()

    if not username or not password:
        return 400, {"success": False, "message": "Vui lòng nhập tên đăng nhập và mật khẩu."}, None

    pwd_hash = hash_password(password)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, fullname, email FROM users WHERE username = ? AND password_hash = ?", (username, pwd_hash))
    user = cursor.fetchone()

    if not user:
        return 401, {"success": False, "message": "Sai tên đăng nhập hoặc mật khẩu!"}, None

    # Tạo session token
    import secrets
    token = secrets.token_hex(16)
    user_dict = dict(user)
    with state_lock:
        sessions[token] = user_dict

    return 200, {"success": True, "message": f"Xin chào, {user_dict['fullname']}!", "user": user_dict}, token


def handle_api_cart_get(conn, user_id: int) -> tuple[int, dict]:
    """Lấy danh sách giỏ hàng của user từ SQLite."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.product_id, c.quantity, p.name, p.price, p.stock, p.category, p.image_icon
        FROM cart c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = ?
    """, (user_id,))
    rows = cursor.fetchall()
    items = [dict(row) for row in rows]
    total_price = sum(item["price"] * item["quantity"] for item in items)
    total_count = sum(item["quantity"] for item in items)
    return 200, {"success": True, "items": items, "total_price": total_price, "total_count": total_count}


def handle_api_cart_add(conn, user_id: int, body_json: dict) -> tuple[int, dict]:
    """Thêm sản phẩm vào giỏ hàng trong SQLite."""
    product_id = int(body_json.get("product_id", 0))
    qty = max(1, int(body_json.get("quantity", 1)))

    cursor = conn.cursor()
    cursor.execute("SELECT id, name, stock FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    if not product:
        return 404, {"success": False, "message": "Không tìm thấy sản phẩm."}
    if product["stock"] <= 0:
        return 400, {"success": False, "message": f"Sản phẩm '{product['name']}' đã hết hàng trong kho!"}

    cursor.execute("""
        INSERT INTO cart (user_id, product_id, quantity)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = quantity + excluded.quantity
    """, (user_id, product_id, qty))
    conn.commit()
    return handle_api_cart_get(conn, user_id)


def handle_api_cart_remove(conn, user_id: int, body_json: dict) -> tuple[int, dict]:
    """Xóa sản phẩm khỏi giỏ hàng trong SQLite."""
    product_id = int(body_json.get("product_id", 0))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    return handle_api_cart_get(conn, user_id)


def handle_api_cart_checkout(conn, user_id: int) -> tuple[int, dict]:
    """Đặt hàng: Trừ số lượng tồn kho (stock) trong SQLite và làm rỗng giỏ hàng."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.product_id, c.quantity, p.name, p.stock, p.price
        FROM cart c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = ?
    """, (user_id,))
    items = cursor.fetchall()

    if not items:
        return 400, {"success": False, "message": "Giỏ hàng đang trống, không thể thanh toán!"}

    # Kiểm tra tồn kho trước khi trừ
    for item in items:
        if item["stock"] < item["quantity"]:
            return 400, {
                "success": False,
                "message": f"Sản phẩm '{item['name']}' chỉ còn {item['stock']} sản phẩm trong kho (không đủ {item['quantity']})!"
            }

    # Trừ số lượng tồn kho từng sản phẩm trong SQLite
    for item in items:
        cursor.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (item["quantity"], item["product_id"]))

    # Xóa giỏ hàng của user sau khi đặt hàng thành công
    cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
    conn.commit()

    import random
    order_id = f"DH-{random.randint(100000, 999999)}"
    return 200, {
        "success": True,
        "message": f"🎉 Đặt hàng thành công! Mã đơn: {order_id}. Đã trừ tồn kho trong SQLite.",
        "order_id": order_id
    }


def process_client_socket(client_sock: socket.socket, client_addr: tuple[str, int], conn):
    """Xử lý từng kết nối HTTP client gửi tới."""
    global total_requests, blocked_requests, active_workers, defense_enabled

    with state_lock:
        active_workers += 1

    try:
        client_sock.settimeout(5.0)
        client_ip = client_addr[0]

        # Kiểm tra WAF / Phòng thủ nếu đang BẬT
        with state_lock:
            current_defense = defense_enabled

        if current_defense:
            if not check_rate_limit(client_ip):
                with state_lock:
                    blocked_requests += 1
                resp_body = json.dumps({"error": "TOO_MANY_REQUESTS", "message": "WAF phát hiện tần suất request bất thường (DDoS Protection)!"}).encode("utf-8")
                response = (
                    b"HTTP/1.1 429 Too Many Requests\r\n"
                    b"Content-Type: application/json; charset=utf-8\r\n"
                    b"Retry-After: 3\r\n"
                    b"Connection: close\r\n"
                    + f"Content-Length: {len(resp_body)}\r\n\r\n".encode("utf-8")
                    + resp_body
                )
                client_sock.sendall(response)
                return

        with state_lock:
            total_requests += 1

        # Đọc HTTP Request Header hoặc JSON Request
        req_data = b""
        while b"\r\n\r\n" not in req_data and b"\n" not in req_data:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            req_data += chunk
            if len(req_data) > 65536:
                break

        if not req_data:
            return

        # Hỗ trợ gói tin JSON từ TrafficSimulator
        if req_data.strip().startswith(b"{"):
            apply_dos_workload(current_defense)
            try:
                json_msg = json.loads(req_data.decode("utf-8", errors="replace").strip())
                cid = json_msg.get("client_id", "sim")
            except Exception:
                cid = "sim"
            resp_json = json.dumps({"status": "OK", "client_id": cid, "server_time": time.time()}).encode("utf-8") + b"\n"
            client_sock.sendall(resp_json)
            return

        # Tiếp tục đọc trọn vẹn HTTP header
        while b"\r\n\r\n" not in req_data:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            req_data += chunk
            if len(req_data) > 65536:
                break

        header_part, _, body_part = req_data.partition(b"\r\n\r\n")
        lines = header_part.decode("latin-1", errors="replace").split("\r\n")
        if not lines:
            return

        request_line = lines[0]
        parts = request_line.split(" ")
        if len(parts) < 2:
            return
        method, full_path = parts[0].upper(), parts[1]

        # Phân tích URL và query
        url_parsed = urlsplit(full_path)
        path = url_parsed.path
        query = url_parsed.query

        # Đốt CPU + Đốt RAM + Gây trễ hàng đợi khi bị DoS
        apply_dos_workload(current_defense)

        # Đọc Content-Length nếu có POST body
        content_length = 0
        cookie_header = ""
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip().lower()
                if k == "content-length":
                    try:
                        content_length = int(v.strip())
                    except ValueError:
                        pass
                elif k == "cookie":
                    cookie_header = v.strip()

        # Đọc nốt body nếu chưa đủ
        while len(body_part) < content_length:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            body_part += chunk

        # Lấy session người dùng từ Cookie
        current_user = None
        if cookie_header:
            cookie = http.cookies.SimpleCookie()
            cookie.load(cookie_header)
            if "session_token" in cookie:
                token = cookie["session_token"].value
                with state_lock:
                    current_user = sessions.get(token)

        # Xử lý các Route
        # 1. Trang chủ HTML và các route giao diện
        if path in ("/", "/index.html", "/home", "/products", "/search", "/cart", "/checkout", "/login") and method == "GET":
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                content = index_path.read_bytes()
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    b"Connection: close\r\n"
                    + f"Content-Length: {len(content)}\r\n\r\n".encode("utf-8")
                    + content
                )
                client_sock.sendall(resp)
            return

        # 2. File tĩnh CSS/JS
        if path.startswith("/static/"):
            filename = path.replace("/static/", "").strip("/\\")
            target_file = (STATIC_DIR / filename).resolve()
            if target_file.is_file() and str(target_file).startswith(str(STATIC_DIR)):
                mime = "text/css" if filename.endswith(".css") else ("application/javascript" if filename.endswith(".js") else "application/octet-stream")
                content = target_file.read_bytes()
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    + f"Content-Type: {mime}; charset=utf-8\r\n".encode("utf-8")
                    + b"Connection: close\r\n"
                    + f"Content-Length: {len(content)}\r\n\r\n".encode("utf-8")
                    + content
                )
                client_sock.sendall(resp)
                return
            else:
                client_sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                return

        # 3. API Lấy danh sách sản phẩm
        if path == "/api/products" and method == "GET":
            # Khi bị tấn công mà tắt phòng thủ, mô phỏng tải xử lý cơ sở dữ liệu
            status_code, data = handle_api_products(conn)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} OK\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 4. API Tìm kiếm sản phẩm
        if path == "/api/search" and method == "GET":
            status_code, data = handle_api_search(conn, query)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} OK\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 5. API Đăng ký
        if path == "/api/register" and method == "POST":
            try:
                body_json = json.loads(body_part.decode("utf-8", errors="replace")) if body_part else {}
            except Exception:
                body_json = {}
            status_code, data = handle_api_register(conn, body_json)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} Result\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 6. API Đăng nhập
        if path == "/api/login" and method == "POST":
            try:
                body_json = json.loads(body_part.decode("utf-8", errors="replace")) if body_part else {}
            except Exception:
                body_json = {}
            status_code, data, token = handle_api_login(conn, body_json)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            cookie_line = f"Set-Cookie: session_token={token}; Path=/; HttpOnly; Max-Age=86400\r\n" if token else ""
            resp = (
                f"HTTP/1.1 {status_code} Result\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + cookie_line.encode("utf-8")
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 7. API Kiểm tra trạng thái đăng nhập
        if path == "/api/me" and method == "GET":
            if current_user:
                body_bytes = json.dumps({"authenticated": True, "user": current_user}, ensure_ascii=False).encode("utf-8")
            else:
                body_bytes = json.dumps({"authenticated": False, "user": None}).encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 8. API Đăng xuất
        if path == "/api/logout" and method == "POST":
            body_bytes = json.dumps({"success": True}).encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Set-Cookie: session_token=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 8.1 API Giỏ Hàng (Lấy giỏ hàng từ SQLite)
        user_id = current_user["id"] if current_user else 1
        if path == "/api/cart" and method == "GET":
            status_code, data = handle_api_cart_get(conn, user_id)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} OK\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 8.2 API Thêm vào giỏ hàng (Lưu vào SQLite)
        if path == "/api/cart/add" and method == "POST":
            try:
                body_json = json.loads(body_part.decode("utf-8", errors="replace")) if body_part else {}
            except Exception:
                body_json = {}
            status_code, data = handle_api_cart_add(conn, user_id, body_json)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} Result\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 8.3 API Xóa khỏi giỏ hàng
        if path == "/api/cart/remove" and method == "POST":
            try:
                body_json = json.loads(body_part.decode("utf-8", errors="replace")) if body_part else {}
            except Exception:
                body_json = {}
            status_code, data = handle_api_cart_remove(conn, user_id, body_json)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} Result\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 8.4 API Đặt hàng & Trừ số lượng kho trong SQLite
        if path == "/api/cart/checkout" and method == "POST":
            status_code, data = handle_api_cart_checkout(conn, user_id)
            body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp = (
                f"HTTP/1.1 {status_code} Result\r\n".encode("utf-8")
                + b"Content-Type: application/json; charset=utf-8\r\n"
                + b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 9. API Trạng thái Server (Dành cho Dashboard/Header trên web)
        if path == "/api/server-status" and method == "GET":
            with state_lock:
                ram_mb = round(sum(len(b) for b in ram_stress_buffers) / (1024 * 1024), 1)
                is_stress = (not defense_enabled and active_workers >= 4)
                status_payload = {
                    "defense_enabled": defense_enabled,
                    "total_requests": total_requests,
                    "blocked_requests": blocked_requests,
                    "active_workers": active_workers,
                    "max_workers": WORKER_THREADS,
                    "worker_utilization_pct": round((active_workers / WORKER_THREADS) * 100, 1),
                    "ram_consumed_mb": ram_mb,
                    "cpu_load": "85% - 100% (QUÁ TẢI)" if is_stress else "1% - 5% (BÌNH THƯỜNG)",
                }
            body_bytes = json.dumps(status_payload).encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 10. API Bật/Tắt phòng thủ WAF
        if path == "/api/toggle-defense" and method == "POST":
            with state_lock:
                defense_enabled = not defense_enabled
                if defense_enabled:
                    ram_stress_buffers.clear()
                new_state = defense_enabled
            body_bytes = json.dumps({"defense_enabled": new_state}).encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
                + body_bytes
            )
            client_sock.sendall(resp)
            return

        # 404 cho các đường dẫn khác
        client_sock.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")

    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        try:
            client_sock.shutdown(socket.SHUT_WR)
        except Exception:
            pass
        try:
            client_sock.close()
        except Exception:
            pass
        with state_lock:
            active_workers = max(0, active_workers - 1)


def worker_loop(sock_queue: queue.Queue):
    """Vòng lặp của từng worker thread trong Pool."""
    # Mỗi worker thread duy trì 1 connection riêng tới SQLite
    conn = get_connection()
    while True:
        try:
            client_sock, client_addr = sock_queue.get()
            if client_sock is None:
                break
            process_client_socket(client_sock, client_addr, conn)
            sock_queue.task_done()
        except Exception:
            pass
    conn.close()


def run_web_server():
    """Khởi chạy server bán hàng chính."""
    init_db()

    # Hàng đợi socket kết nối
    sock_queue = queue.Queue(maxsize=128)

    # Khởi tạo Thread Pool với số worker cố định
    workers = []
    for i in range(WORKER_THREADS):
        t = threading.Thread(target=worker_loop, args=(sock_queue,), name=f"Worker-{i+1}", daemon=True)
        t.start()
        workers.append(t)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(128)

    print("=" * 65)
    print("🚀 TECHSTORE - HỆ THỐNG WEB BÁN HÀNG E-COMMERCE CHÂN THỰC")
    print(f"👉 Địa chỉ truy cập trên trình duyệt: http://{HOST}:{PORT}")
    print(f"👉 Worker Threads: {WORKER_THREADS} luồng xử lý đồng thời")
    print(f"👉 Cơ sở dữ liệu: SQLite ({BASE_DIR / 'shop.db'})")
    print(f"👉 Chế độ phòng thủ WAF mặc định: {'BẬT' if defense_enabled else 'TẮT (Sẵn sàng test DDoS)'}")
    print("=" * 65)
    print("Mẹo kiểm tra DoS/DDoS:")
    print("1. Mở Chrome/Edge vào http://127.0.0.1:5000 (Lướt web rất mượt).")
    print("2. Chạy attacker.py để dội DDoS vào cổng 5000.")
    print("3. Nhấn F5 trên Chrome -> Tab Chrome sẽ XOAY TÍT MÙ và KHÔNG THỂ TẢI TRANG!")
    print("4. Bật chế độ Phòng thủ WAF -> Trình duyệt Chrome tải lại bình thường ngay lập tức!")
    print("=" * 65)

    try:
        while True:
            client_sock, client_addr = server_sock.accept()
            try:
                # Đưa vào hàng đợi worker
                sock_queue.put((client_sock, client_addr), block=True, timeout=2.0)
            except queue.Full:
                # Hàng đợi bị đầy do DDoS
                try:
                    client_sock.sendall(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 5\r\n\r\nServer Backlog Full")
                    client_sock.close()
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\n[Server] Đang tắt web server...")
    finally:
        server_sock.close()


if __name__ == "__main__":
    run_web_server()
