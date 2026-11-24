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
import hmac
import http.cookies
import json
import os
import queue
import random
import re
import secrets
import socket
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from db import get_connection, hash_password, init_db

if not tracemalloc.is_tracing():
    tracemalloc.start()

# Thư mục gốc
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Cấu hình Web Server (Micro-VPS thực tế: tài nguyên thấp)
HOST = "0.0.0.0"             # Lắng nghe trên mọi card mạng (cho phép máy khác trong LAN truy cập)
PORT = 5000
WORKER_THREADS = 4           # 4 luồng xử lý đồng thời (tương đương VPS 2-4 core cấu hình thấp)
MAX_QUEUE_BACKLOG = 16       # Hàng đợi socket nhỏ gọn
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
_active_query_buffers: collections.deque[list] = collections.deque(maxlen=40)
db_search_lock = threading.Lock()
slowloris_active_conns = 0
last_login_hash_stress_at = 0.0
last_db_search_stress_at = 0.0
last_general_flood_at = 0.0

_last_cpu_time = time.process_time()
_last_wall_time = time.monotonic()
_measured_cpu_pct = 0.0

# Slowloris Defense (chỉ kích hoạt khi bật WAF)
HEADER_READ_TIMEOUT_SEC = 1.5       # Tổng thời gian tối đa để gửi trọn vẹn HTTP header (\r\n\r\n)
MAX_HEADER_BYTES_WAF = 8192         # Giới hạn dung lượng HTTP header tối đa (8 KB)
MAX_HEADER_LINES_WAF = 40           # Giới hạn số dòng HTTP header tối đa
MAX_CONCURRENT_CONNS_PER_IP = 3     # Giới hạn kết nối mở đồng thời tối đa trên mỗi IP
active_ip_connections: dict[str, int] = collections.defaultdict(int)

# Database Search GET Flood & Asymmetric POST Login Flood Defense (khi bật WAF)
SEARCH_RATE_LIMIT_PER_SEC = 4.0     # Tối đa 4 yêu cầu tìm kiếm/giây/IP
SEARCH_CACHE_TTL_SEC = 30.0         # Cache kết quả tìm kiếm SQLite trong 30 giây
MAX_SEARCH_KEYWORD_LEN = 64         # Giới hạn độ dài từ khóa tìm kiếm
search_query_cache: dict[str, tuple[float, list[dict]]] = {}

LOGIN_RATE_LIMIT_PER_SEC = 1.5      # Tối đa 1.5 yêu cầu POST /api/login mỗi giây
LOGIN_MAX_FAILURES = 5              # Khóa tạm thời sau 5 lần đăng nhập thất bại liên tiếp
LOGIN_LOCKOUT_SEC = 30.0            # Thời gian khóa đăng nhập tạm thời (30 giây)
login_failure_tracker: dict[str, list[float | int]] = {}  # ip -> [fail_count, locked_until_monotonic]

# JS Challenge (Browser Verification) cấu hình bảo vệ Tầng 7
JS_CHALLENGE_SECRET = secrets.token_bytes(32)
JS_CLEARANCE_TTL_SEC = 600  # Cookie xác minh trình duyệt có hiệu lực 10 phút


def _sign_waf_data(payload: str) -> str:
    return hmac.new(JS_CHALLENGE_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_js_clearance_token(client_ip: str) -> str:
    expires = int(time.time()) + JS_CLEARANCE_TTL_SEC
    sig = _sign_waf_data(f"clearance:{client_ip}:{expires}")
    return f"{expires}.{sig}"


def verify_js_clearance_token(token: str, client_ip: str) -> bool:
    if not token or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    try:
        expires = int(exp_str)
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    expected = _sign_waf_data(f"clearance:{client_ip}:{expires}")
    return hmac.compare_digest(sig, expected)


def build_js_challenge_html(client_ip: str, next_path: str) -> bytes:
    """Tạo trang JS Challenge kiểu Cloudflare để xác minh trình duyệt thật."""
    a = random.randint(12, 99)
    b = random.randint(11, 89)
    ts = int(time.time())
    sig = _sign_waf_data(f"chal:{client_ip}:{a}:{b}:{ts}")
    safe_next = next_path if next_path.startswith("/") else "/"
    verify_base = f"/waf-verify?a={a}&b={b}&ts={ts}&sig={sig}&next={quote(safe_next)}"
    html = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TechStore WAF — Đang xác minh trình duyệt...</title>
  <style>
    body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1120;color:#e2e8f0;font-family:system-ui,-apple-system,sans-serif}}
    .card{{max-width:460px;width:90%;background:#111827;border:1px solid #1f2937;border-radius:16px;padding:32px;text-align:center;box-shadow:0 20px 50px rgba(0,0,0,.45)}}
    .spinner{{width:44px;height:44px;border:4px solid #1e293b;border-top-color:#38bdf8;border-radius:50%;margin:0 auto 20px;animation:spin .9s linear infinite}}
    @keyframes spin{{to{{transform:rotate(360deg)}}}}
    h1{{font-size:1.25rem;margin:0 0 10px;color:#f8fafc}}
    p{{font-size:.92rem;color:#94a3b8;line-height:1.5;margin:0 0 14px}}
    .badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:rgba(56,189,248,.12);color:#38bdf8;font-size:.78rem;font-weight:600}}
    noscript{{display:block;margin-top:16px;color:#f87171;font-weight:600}}
  </style>
</head>
<body>
  <div class="card">
    <div class="spinner"></div>
    <span class="badge">LAYER 7 WAF · JS CHALLENGE</span>
    <h1>Đang kiểm tra trình duyệt của bạn...</h1>
    <p>Hệ thống phòng thủ DDoS đang xác minh bạn là người dùng thật trước khi truy cập <strong>TechStore</strong>. Quá trình này hoàn toàn tự động.</p>
    <p id="status" style="color:#38bdf8;font-weight:600;">Đang giải thử thách JavaScript...</p>
    <noscript>Vui lòng bật JavaScript để vượt qua bước xác minh bảo mật.</noscript>
  </div>
  <script>
    (function() {{
      var ans = ({a} * {b}) + 1337;
      setTimeout(function() {{
        document.getElementById('status').textContent = 'Xác minh thành công! Đang chuyển hướng...';
        window.location.replace("{verify_base}&ans=" + ans);
      }}, 700);
    }})();
  </script>
</body>
</html>"""
    return html.encode("utf-8")


def sample_real_telemetry() -> tuple[float, float]:
    """Đo % CPU thật (time.process_time) và RAM thật (tracemalloc) của tiến trình Python."""
    global _last_cpu_time, _last_wall_time, _measured_cpu_pct
    now_cpu = time.process_time()
    now_wall = time.monotonic()
    dt_wall = now_wall - _last_wall_time
    if dt_wall >= 0.4:
        dt_cpu = max(0.0, now_cpu - _last_cpu_time)
        # Quy đổi % trên 1 core xử lý của tiến trình server
        _measured_cpu_pct = min(100.0, round((dt_cpu / dt_wall) * 100.0, 1))
        _last_cpu_time = now_cpu
        _last_wall_time = now_wall
    current_bytes, _ = tracemalloc.get_traced_memory()
    ram_mb = round(current_bytes / (1024 * 1024), 2)
    return _measured_cpu_pct, ram_mb


def apply_dos_workload(current_defense: bool, is_bot: bool = False, attack_type: str = "GENERAL"):
    """Không dùng time.sleep hay fake RAM: dùng xử lý mật mã học & truy vấn SQLite thật."""
    global last_login_hash_stress_at, last_db_search_stress_at, last_general_flood_at
    if not current_defense:
        if attack_type == "POST_LOGIN":
            # handle_api_login sẽ chạy PBKDF2-HMAC-SHA256 250.000 vòng thật
            with state_lock:
                _active_query_buffers.clear()
                ram_stress_buffers.clear()
                last_login_hash_stress_at = time.monotonic()
        elif attack_type == "GET_SEARCH":
            # handle_api_search sẽ chạy SQLite recursive CTE scan thật khi use_cache=False
            with state_lock:
                last_db_search_stress_at = time.monotonic()
        elif is_bot:
            # HTTP/JSON Flood chung: xác thực token bằng PBKDF2 thật
            with state_lock:
                last_general_flood_at = time.monotonic()
            hashlib.pbkdf2_hmac("sha256", str(time.monotonic()).encode(), b"session_verify", 180_000)
    else:
        with state_lock:
            _active_query_buffers.clear()
            ram_stress_buffers.clear()


def check_rate_limit(identity_key: str, limit_per_sec: float = RATE_LIMIT_PER_SEC) -> bool:
    """Kiểm tra xem thực thể có đang gửi request quá tần suất cho phép hay không."""
    now = time.monotonic()
    with state_lock:
        history = ip_request_history[identity_key]
        while history and history[0] < now - RATE_WINDOW_SEC:
            history.popleft()
        history.append(now)
        rps = len(history) / RATE_WINDOW_SEC
    return rps <= limit_per_sec


def is_login_locked(client_ip: str) -> bool:
    """Kiểm tra xem IP có đang bị khóa tạm thời do sai mật khẩu liên tiếp hay không."""
    now = time.monotonic()
    with state_lock:
        entry = login_failure_tracker.get(client_ip)
        if not entry:
            return False
        locked_until = float(entry[1])
        if locked_until > 0.0:
            if now < locked_until:
                return True
            login_failure_tracker.pop(client_ip, None)
        return False


def record_login_result(client_ip: str, success: bool) -> None:
    """Ghi nhận kết quả đăng nhập để khóa tạm thời khi phát hiện spam/brute-force."""
    now = time.monotonic()
    with state_lock:
        if success:
            login_failure_tracker.pop(client_ip, None)
            return
        entry = login_failure_tracker.get(client_ip)
        if not entry:
            entry = [0, 0.0]
            login_failure_tracker[client_ip] = entry
        entry[0] = int(entry[0]) + 1
        if int(entry[0]) >= LOGIN_MAX_FAILURES:
            entry[1] = now + LOGIN_LOCKOUT_SEC


def _is_db_search_contended() -> bool:
    with state_lock:
        return (not defense_enabled) and ((time.monotonic() - last_db_search_stress_at) < 2.5)


def handle_api_products(conn) -> tuple[int, dict]:
    """Lấy danh sách sản phẩm từ SQLite."""
    def _query():
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, category, price, stock, image_icon, description FROM products")
        return [dict(row) for row in cursor.fetchall()]

    if _is_db_search_contended():
        with db_search_lock:
            products = _query()
    else:
        products = _query()
    return 200, {"success": True, "products": products}


def handle_api_search(conn, query_string: str, use_cache: bool = False) -> tuple[int, dict]:
    """Tìm kiếm sản phẩm theo từ khóa từ SQLite, có cache khi bật WAF."""
    params = parse_qs(query_string)
    keyword = params.get("q", [""])[0].strip()
    if use_cache and len(keyword) > MAX_SEARCH_KEYWORD_LEN:
        return 400, {
            "success": False,
            "error": "SEARCH_QUERY_TOO_LONG",
            "message": "WAF từ chối: Từ khóa tìm kiếm quá dài!",
        }

    cache_key = keyword.lower()
    if use_cache:
        now = time.monotonic()
        with state_lock:
            cached_entry = search_query_cache.get(cache_key)
            if cached_entry and (now - cached_entry[0]) < SEARCH_CACHE_TTL_SEC:
                return 200, {
                    "success": True,
                    "keyword": keyword,
                    "cached": True,
                    "products": cached_entry[1],
                }

    def _query():
        cursor = conn.cursor()
        if use_cache:
            if keyword:
                cursor.execute(
                    "SELECT id, name, category, price, stock, image_icon, description FROM products WHERE name LIKE ? OR category LIKE ?",
                    (f"%{keyword}%", f"%{keyword}%")
                )
            else:
                cursor.execute("SELECT id, name, category, price, stock, image_icon, description FROM products")
            return [dict(row) for row in cursor.fetchall()]

        # Khi tắt WAF (không cache/index): SQLite quét toàn bộ tập dữ liệu mở rộng thật
        like_kw = f"%{keyword}%" if keyword else "%"
        cursor.execute(
            """
            WITH RECURSIVE seq(x) AS (
                SELECT 1
                UNION ALL
                SELECT x + 1 FROM seq WHERE x < 15000
            )
            SELECT p.id, p.name, p.category, p.price, p.stock, p.image_icon, p.description, seq.x
            FROM products p
            CROSS JOIN seq
            WHERE p.name LIKE ? OR p.category LIKE ?
            """,
            (like_kw, like_kw),
        )
        raw_rows = [dict(row) for row in cursor.fetchall()]
        with state_lock:
            _active_query_buffers.append(raw_rows)

        seen_ids: set[int] = set()
        deduped: list[dict] = []
        for row in raw_rows:
            pid = row["id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                row.pop("x", None)
                deduped.append(row)
        return deduped

    if _is_db_search_contended():
        with db_search_lock:
            products = _query()
    else:
        products = _query()

    if use_cache:
        with state_lock:
            if len(search_query_cache) > 128:
                search_query_cache.clear()
            search_query_cache[cache_key] = (time.monotonic(), products)

    return 200, {"success": True, "keyword": keyword, "products": products}


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
    def _query():
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.product_id, c.quantity, p.name, p.price, p.stock, p.category, p.image_icon
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]

    if _is_db_search_contended():
        with db_search_lock:
            items = _query()
    else:
        items = _query()
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
    with state_lock:
        search_query_cache.clear()

    import random
    order_id = f"DH-{random.randint(100000, 999999)}"
    return 200, {
        "success": True,
        "message": f"Đặt hàng thành công! Mã đơn: {order_id}. Đã trừ tồn kho trong SQLite.",
        "order_id": order_id
    }


def _send_waf_rejection(client_sock: socket.socket, status_line: str, error_code: str, message: str) -> None:
    """Trả phản hồi từ chối gọn nhẹ từ WAF và không để chiếm worker."""
    try:
        resp_body = json.dumps({"error": error_code, "message": message}, ensure_ascii=False).encode("utf-8")
        response = (
            f"HTTP/1.1 {status_line}\r\n".encode("utf-8")
            + b"Content-Type: application/json; charset=utf-8\r\n"
            + b"Connection: close\r\n"
            + f"Content-Length: {len(resp_body)}\r\n\r\n".encode("utf-8")
            + resp_body
        )
        client_sock.sendall(response)
    except Exception:
        pass


def process_client_socket(client_sock: socket.socket, client_addr: tuple[str, int], conn, tracked_ip: str | None = None):
    """Xử lý từng kết nối HTTP client gửi tới."""
    global total_requests, blocked_requests, active_workers, defense_enabled

    with state_lock:
        active_workers += 1

    try:
        client_ip = client_addr[0]

        with state_lock:
            current_defense = defense_enabled
            total_requests += 1

        # Slowloris Defense: giới hạn tổng thời gian đọc HTTP header & kích thước header khi bật WAF
        header_deadline = time.monotonic() + (HEADER_READ_TIMEOUT_SEC if current_defense else 15.0)
        max_header_bytes = MAX_HEADER_BYTES_WAF if current_defense else 65536
        client_sock.settimeout(HEADER_READ_TIMEOUT_SEC if current_defense else 5.0)

        # Đọc HTTP Request Header hoặc JSON Request
        req_data = b""
        while b"\r\n\r\n" not in req_data and b"\n" not in req_data:
            if current_defense:
                remaining = header_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("SLOWLORIS_HEADER_TIMEOUT")
                client_sock.settimeout(remaining)
            try:
                chunk = client_sock.recv(4096)
            except socket.timeout as exc:
                if current_defense:
                    raise TimeoutError("SLOWLORIS_HEADER_TIMEOUT") from exc
                raise
            if not chunk:
                break
            req_data += chunk
            if len(req_data) > max_header_bytes:
                if current_defense:
                    with state_lock:
                        blocked_requests += 1
                    _send_waf_rejection(
                        client_sock,
                        "431 Request Header Fields Too Large",
                        "SLOWLORIS_HEADER_TOO_LARGE",
                        "WAF chặn kết nối: HTTP Header vượt quá kích thước cho phép!",
                    )
                    return
                break

        if not req_data:
            return

        # Hỗ trợ gói tin JSON từ TrafficSimulator
        if req_data.strip().startswith(b"{"):
            if current_defense and not check_rate_limit(f"bot:{client_ip}", 3.0):
                with state_lock:
                    blocked_requests += 1
                resp_json = json.dumps({"status": "BLOCKED", "reason": "RATE_LIMIT"}).encode("utf-8") + b"\n"
                client_sock.sendall(resp_json)
                return
            apply_dos_workload(current_defense, is_bot=True)
            try:
                json_msg = json.loads(req_data.decode("utf-8", errors="replace").strip())
                cid = json_msg.get("client_id", "sim")
            except Exception:
                cid = "sim"
            resp_json = json.dumps({"status": "OK", "client_id": cid, "server_time": time.time()}).encode("utf-8") + b"\n"
            client_sock.sendall(resp_json)
            return

        # Tiếp tục đọc trọn vẹn HTTP header (chống Slowloris nhỏ giọt từng dòng header)
        if b"\r\n\r\n" not in req_data:
            global slowloris_active_conns
            header_wait_started = time.monotonic()
            with state_lock:
                slowloris_active_conns += 1
            try:
                while b"\r\n\r\n" not in req_data:
                    if current_defense:
                        remaining = header_deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("SLOWLORIS_HEADER_TIMEOUT")
                        client_sock.settimeout(remaining)
                    try:
                        chunk = client_sock.recv(4096)
                    except socket.timeout as exc:
                        if current_defense:
                            raise TimeoutError("SLOWLORIS_HEADER_TIMEOUT") from exc
                        raise
                    if not chunk:
                        break
                    req_data += chunk
                    if current_defense and (
                        len(req_data) > max_header_bytes
                        or req_data.count(b"\r\n") > MAX_HEADER_LINES_WAF
                    ):
                        with state_lock:
                            blocked_requests += 1
                        _send_waf_rejection(
                            client_sock,
                            "431 Request Header Fields Too Large",
                            "SLOWLORIS_HEADER_LIMIT",
                            "WAF chặn kết nối: Quá nhiều dòng HTTP Header bất thường (Slowloris Protection)!",
                        )
                        return
                    if len(req_data) > 65536:
                        break
            finally:
                with state_lock:
                    slowloris_active_conns = max(0, slowloris_active_conns - 1)
                    if (not current_defense) and ((time.monotonic() - header_wait_started) > 0.5) and ram_stress_buffers:
                        ram_stress_buffers.clear()

        # Nếu client ngắt trước khi gửi xong kết thúc header (\r\n\r\n)
        if b"\r\n\r\n" not in req_data:
            if current_defense:
                with state_lock:
                    blocked_requests += 1
                _send_waf_rejection(
                    client_sock,
                    "408 Request Timeout",
                    "SLOWLORIS_INCOMPLETE_HEADER",
                    "WAF ngắt kết nối: HTTP Header chưa hoàn chỉnh (Slowloris Protection)!",
                )
            return

        # Khôi phục timeout đọc body bình thường sau khi đã nhận trọn vẹn header
        client_sock.settimeout(5.0)

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

        # Đọc Content-Length, Cookie, User-Agent, X-Forwarded-For
        content_length = 0
        cookie_header = ""
        user_agent = ""
        forwarded_for = ""
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
                elif k == "user-agent":
                    user_agent = v.strip().lower()
                elif k == "x-forwarded-for":
                    forwarded_for = v.strip()

        is_bot = any(
            marker in user_agent
            for marker in (
                "botnetsimulator",
                "dos-bot",
                "netguard-lab",
                "botnet-loginflood",
                "slowheaderlab",
                "slowloris-agent",
            )
        )

        # Đọc cookie sớm để phục vụ cả WAF JS Challenge lẫn phiên đăng nhập
        parsed_cookie = http.cookies.SimpleCookie()
        if cookie_header:
            try:
                parsed_cookie.load(cookie_header)
            except Exception:
                pass

        # Endpoint xác minh JS Challenge (/waf-verify)
        if path == "/waf-verify" and method == "GET":
            params = parse_qs(query)
            try:
                a_val = int(params.get("a", ["0"])[0])
                b_val = int(params.get("b", ["0"])[0])
                ts_val = int(params.get("ts", ["0"])[0])
                ans_val = int(params.get("ans", ["-1"])[0])
                sig_val = params.get("sig", [""])[0]
                next_url = params.get("next", ["/"])[0]
                if not next_url.startswith("/") or next_url.startswith("//"):
                    next_url = "/"
                expected_sig = _sign_waf_data(f"chal:{client_ip}:{a_val}:{b_val}:{ts_val}")
                valid_time = abs(int(time.time()) - ts_val) <= 60
                if valid_time and hmac.compare_digest(sig_val, expected_sig) and ans_val == (a_val * b_val) + 1337:
                    clearance = create_js_clearance_token(client_ip)
                    resp = (
                        b"HTTP/1.1 302 Found\r\n"
                        + f"Location: {next_url}\r\n".encode("utf-8")
                        + f"Set-Cookie: waf_clearance={clearance}; Path=/; HttpOnly; Max-Age={JS_CLEARANCE_TTL_SEC}\r\n".encode("utf-8")
                        + b"Connection: close\r\nContent-Length: 0\r\n\r\n"
                    )
                    client_sock.sendall(resp)
                    return
            except Exception:
                pass
            err_body = b"JS Challenge verification failed."
            client_sock.sendall(
                b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n"
                + f"Content-Length: {len(err_body)}\r\n\r\n".encode("utf-8")
                + err_body
            )
            return

        # Kiểm tra WAF / Phòng thủ nếu đang BẬT
        if current_defense and path not in ("/api/toggle-defense", "/api/server-status", "/waf-verify"):
            target_identity = forwarded_for or client_ip

            # Bảo vệ riêng cho endpoint tìm kiếm SQLite (Database Search GET Flood)
            if path == "/api/search":
                if not check_rate_limit(f"search:{target_identity}", 1.5 if is_bot else SEARCH_RATE_LIMIT_PER_SEC):
                    with state_lock:
                        blocked_requests += 1
                    _send_waf_rejection(
                        client_sock,
                        "429 Too Many Requests",
                        "SEARCH_FLOOD_BLOCKED",
                        "WAF chặn truy vấn: Tần suất tìm kiếm /api/search quá cao (Database Search Flood Protection)!",
                    )
                    return

            # Bảo vệ riêng cho endpoint đăng nhập (Asymmetric HTTP POST Login Flood)
            if path == "/api/login" and method == "POST":
                if is_login_locked(target_identity):
                    with state_lock:
                        blocked_requests += 1
                    _send_waf_rejection(
                        client_sock,
                        "429 Too Many Requests",
                        "LOGIN_BRUTEFORCE_LOCKED",
                        "WAF khóa đăng nhập tạm thời (30s) do sai mật khẩu liên tiếp (Asymmetric Login Flood Protection)!",
                    )
                    return
                if not check_rate_limit(f"login:{target_identity}", 1.0 if is_bot else LOGIN_RATE_LIMIT_PER_SEC):
                    with state_lock:
                        blocked_requests += 1
                    _send_waf_rejection(
                        client_sock,
                        "429 Too Many Requests",
                        "LOGIN_FLOOD_BLOCKED",
                        "WAF chặn đăng nhập: Phát hiện spam POST /api/login (Asymmetric Login Flood Protection)!",
                    )
                    return

            rate_key = f"bot:{target_identity}" if is_bot else f"human:{client_ip}"
            allowed_rate = 2.0 if is_bot else 60.0
            if not check_rate_limit(rate_key, allowed_rate):
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

            # Lớp 2 (Tầng 7): JS Challenge — chặn bot tự động không chạy được JavaScript
            if not path.startswith("/static/"):
                clearance_cookie = parsed_cookie["waf_clearance"].value if "waf_clearance" in parsed_cookie else ""
                if not verify_js_clearance_token(clearance_cookie, client_ip):
                    with state_lock:
                        blocked_requests += 1
                    if path.startswith("/api/"):
                        resp_body = json.dumps({
                            "error": "JS_CHALLENGE_REQUIRED",
                            "message": "WAF yêu cầu xác minh trình duyệt (JS Challenge). Hãy tải lại trang!"
                        }, ensure_ascii=False).encode("utf-8")
                        response = (
                            b"HTTP/1.1 403 Forbidden\r\n"
                            b"Content-Type: application/json; charset=utf-8\r\n"
                            b"Connection: close\r\n"
                            + f"Content-Length: {len(resp_body)}\r\n\r\n".encode("utf-8")
                            + resp_body
                        )
                    else:
                        challenge_html = build_js_challenge_html(client_ip, full_path)
                        response = (
                            b"HTTP/1.1 503 Service Unavailable\r\n"
                            b"Content-Type: text/html; charset=utf-8\r\n"
                            b"Cache-Control: no-store\r\n"
                            b"Connection: close\r\n"
                            + f"Content-Length: {len(challenge_html)}\r\n\r\n".encode("utf-8")
                            + challenge_html
                        )
                    client_sock.sendall(response)
                    return

        # Mô phỏng tải sát thực tế theo từng loại tấn công (trừ API điều khiển/giám sát)
        if path not in ("/api/toggle-defense", "/api/server-status"):
            if path == "/api/login" and method == "POST":
                attack_type = "POST_LOGIN"
            elif path == "/api/search":
                attack_type = "GET_SEARCH"
            else:
                attack_type = "GENERAL"
            apply_dos_workload(current_defense, is_bot=is_bot, attack_type=attack_type)

        # Đọc nốt body nếu chưa đủ
        while len(body_part) < content_length:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            body_part += chunk

        # Lấy session người dùng từ Cookie
        current_user = None
        if "session_token" in parsed_cookie:
            token = parsed_cookie["session_token"].value
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
            status_code, data = handle_api_search(conn, query, use_cache=current_defense)
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
            if current_defense:
                record_login_result(forwarded_for or client_ip, status_code == 200)
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
                cpu_pct, ram_mb = sample_real_telemetry()
                now_mono = time.monotonic()
                if not defense_enabled:
                    if (now_mono - last_login_hash_stress_at) < 2.5:
                        cpu_label = f"{cpu_pct:.1f}% (BĂM PBKDF2)"
                    elif (now_mono - last_db_search_stress_at) < 2.5:
                        cpu_label = f"{cpu_pct:.1f}% (QUÉT SQLITE)"
                    elif slowloris_active_conns >= 2:
                        cpu_label = f"{cpu_pct:.1f}% (CHỜ HEADER SOCKET)"
                    elif (now_mono - last_general_flood_at) < 2.5 or active_workers >= 3:
                        cpu_label = f"{cpu_pct:.1f}% (TẢI HTTP FLOOD)"
                    else:
                        cpu_label = f"{cpu_pct:.1f}% (BÌNH THƯỜNG)"
                else:
                    cpu_label = f"{cpu_pct:.1f}% (BÌNH THƯỜNG)"
                status_payload = {
                    "defense_enabled": defense_enabled,
                    "total_requests": total_requests,
                    "blocked_requests": blocked_requests,
                    "active_workers": active_workers,
                    "max_workers": WORKER_THREADS,
                    "worker_utilization_pct": round((active_workers / WORKER_THREADS) * 100, 1),
                    "ram_consumed_mb": ram_mb,
                    "cpu_load": cpu_label,
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
                    _active_query_buffers.clear()
                login_failure_tracker.clear()
                search_query_cache.clear()
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

    except TimeoutError:
        with state_lock:
            blocked_requests += 1
        _send_waf_rejection(
            client_sock,
            "408 Request Timeout",
            "SLOWLORIS_HEADER_TIMEOUT",
            f"WAF ngắt kết nối: Gửi HTTP Header quá chậm (vượt quá {HEADER_READ_TIMEOUT_SEC}s — Slowloris Protection)!",
        )
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
            if tracked_ip is not None:
                active_ip_connections[tracked_ip] = max(0, active_ip_connections[tracked_ip] - 1)


def worker_loop(sock_queue: queue.Queue):
    """Vòng lặp của từng worker thread trong Pool."""
    # Mỗi worker thread duy trì 1 connection riêng tới SQLite
    conn = get_connection()
    while True:
        try:
            client_sock, client_addr, tracked_ip = sock_queue.get()
            if client_sock is None:
                break
            process_client_socket(client_sock, client_addr, conn, tracked_ip=tracked_ip)
            sock_queue.task_done()
        except Exception:
            pass
    conn.close()


def run_web_server():
    """Khởi chạy server bán hàng chính."""
    init_db()

    # Hàng đợi socket kết nối
    sock_queue = queue.Queue(maxsize=MAX_QUEUE_BACKLOG)

    # Khởi tạo Thread Pool với số worker cố định
    workers = []
    for i in range(WORKER_THREADS):
        t = threading.Thread(target=worker_loop, args=(sock_queue,), name=f"Worker-{i+1}", daemon=True)
        t.start()
        workers.append(t)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(MAX_QUEUE_BACKLOG)

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
            client_ip = client_addr[0]
            tracked_ip: str | None = None

            # Slowloris Defense (lớp ngoài cùng tại accept): giới hạn số kết nối mở đồng thời trên mỗi IP
            with state_lock:
                waf_on = defense_enabled
                if waf_on:
                    if active_ip_connections[client_ip] >= MAX_CONCURRENT_CONNS_PER_IP:
                        blocked_requests += 1
                        too_many_conns = True
                    else:
                        active_ip_connections[client_ip] += 1
                        tracked_ip = client_ip
                        too_many_conns = False
                else:
                    too_many_conns = False

            if too_many_conns:
                _send_waf_rejection(
                    client_sock,
                    "429 Too Many Requests",
                    "SLOWLORIS_CONN_LIMIT",
                    f"WAF chặn kết nối: Quá {MAX_CONCURRENT_CONNS_PER_IP} kết nối mở đồng thời từ IP {client_ip} (Slowloris Protection)!",
                )
                try:
                    client_sock.close()
                except Exception:
                    pass
                continue

            try:
                # Đưa vào hàng đợi worker
                sock_queue.put((client_sock, client_addr, tracked_ip), block=True, timeout=2.0)
            except queue.Full:
                if tracked_ip is not None:
                    with state_lock:
                        active_ip_connections[tracked_ip] = max(0, active_ip_connections[tracked_ip] - 1)
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
