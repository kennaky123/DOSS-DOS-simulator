# Tổng Quan Dự Án: Network DoS / DDoS Lab Simulator

Tài liệu tổng hợp **Tech Stack**, **Thư viện sử dụng**, **Kiến trúc**, cùng chi tiết từng **Chức năng Mô phỏng Tấn công (Traffic Simulator)** và **Chức năng Phòng thủ & An toàn** của dự án `network_dos_ddos_simulator`.

---

## 1. Tổng Quan Kiến Trúc

Dự án gồm 2 thành phần chính chạy trên mạng cục bộ (`localhost` / Private LAN):

1. **NetGuard Lab — Web Dashboard & Traffic Simulator (`http://127.0.0.1:8080`):**
   - Giao diện điều khiển tập trung để cấu hình kịch bản lưu lượng (Normal, Flash Crowd, DoS, DDoS), chọn giao thức (TCP/UDP), bật/tắt phòng thủ và theo dõi tiến độ chạy mô phỏng.
2. **TechStore — Web Bán Hàng E-Commerce (`http://127.0.0.1:5000`):**
   - Ứng dụng web thương mại điện tử đầy đủ chức năng (xem sản phẩm, tìm kiếm, đăng ký/đăng nhập bằng session cookie, giỏ hàng, thanh toán trừ tồn kho trong SQLite) đóng vai trò **mục tiêu thử nghiệm** để quan sát trực tiếp hiện tượng quá tải khi tắt phòng thủ và khôi phục khi bật WAF.

---

## 2. Tech Stack

| Lớp (Layer) | Công nghệ sử dụng | Ghi chú |
| :--- | :--- | :--- |
| **Ngôn ngữ chính** | Python 3 | Toàn bộ backend, socket networking, simulator, HTTP server |
| **Web Server (Dashboard)** | `http.server.ThreadingHTTPServer` | Dependency-free, không dùng framework ngoài (Flask/FastAPI) |
| **Web Server (TechStore)** | Raw TCP `socket` + `threading` Thread Pool (`8` workers) + `queue.Queue` | Tự xử lý HTTP/1.1 request/response ở tầng socket để mô phỏng nghẽn worker chân thực |
| **Frontend** | HTML5, CSS3, Vanilla JavaScript (Fetch API) | Không dùng thư viện frontend ngoài |
| **Cơ sở dữ liệu** | SQLite 3 (`real_ecommerce_shop/shop.db`) | Lưu trữ `users`, `products`, `cart` |
| **Giao thức mạng** | TCP (`SOCK_STREAM`), UDP (`SOCK_DGRAM`), HTTP/1.1, JSON framing | Hoạt động ở Tầng 4 (Transport) và Tầng 7 (Application) |
| **Kiểm thử** | `unittest` (Python Standard Library) | Kiểm thử controller, giới hạn an toàn, chặn Public IP |

---

## 3. Thư Viện Sử Dụng

### 3.1. Thư viện bên thứ 3 (`requirements.txt`)
- **`rich>=13.7,<15`**: Dùng cho giao diện terminal (CLI Dashboard — bảng trạng thái live trong `client/simulator.py`). Dự án vẫn chạy bình thường nếu chưa cài (có fallback về in text thuần).

### 3.2. Thư viện chuẩn Python (Standard Library)
- **`socket`**: Tạo kết nối TCP (`SOCK_STREAM`) và UDP (`SOCK_DGRAM`), bind địa chỉ nguồn, thiết lập timeout.
- **`threading` & `queue`**: Quản lý đa luồng cho worker của simulator (`TrafficSimulator`) và Thread Pool (`8` worker threads + `sock_queue`) của Web Bán Hàng.
- **`http.server`, `http.cookies`, `urllib.parse`, `mimetypes`**: Phục vụ giao diện Web Dashboard, REST API JSON, phân tích URL/query và quản lý phiên đăng nhập (`session_token`).
- **`sqlite3`**: Kết nối và thao tác cơ sở dữ liệu sản phẩm, tài khoản, giỏ hàng.
- **`hashlib`, `hmac` & `secrets`**: Băm mật khẩu (`SHA-256` + salt), tạo session token, ký/xác thực cookie `waf_clearance` bằng **HMAC-SHA256** cho JS Challenge, và mô phỏng tải CPU trong kịch bản không có phòng thủ.
- **`ipaddress`**: Kiểm tra địa chỉ IP thuộc dải `localhost` / Private LAN (RFC1918), chặn tuyệt đối Public IP.
- **`collections` (`deque`, `defaultdict`)**: Triển khai thuật toán **Sliding Window Rate Limiter** để đếm request theo cửa sổ thời gian trượt.
- **`argparse`, `json`, `logging`, `time`, `dataclasses`, `pathlib`, `random`**: Xử lý CLI, mã hóa/giải mã JSON, ghi log (`server.log`), đo độ trễ (`time.perf_counter`, `time.monotonic`).

---

## 4. Cấu Trúc Thư Mục Dự Án

```text
network_dos_ddos_simulator/
├── main.py                     # Entry point chạy `web` hoặc `client`
├── requirements.txt            # Khai báo thư viện (rich)
├── common/
│   ├── config.py               # Cấu hình mặc định (port 5000) & Hard Limits an toàn
│   ├── network_utils.py        # Validate Private IP/Port & SlidingWindowRateLimiter
│   └── protocol.py             # Đóng gói / giải mã thông điệp JSON qua TCP/UDP
├── client/
│   ├── client.py               # CLI chạy Traffic Simulator
│   ├── simulator.py            # Điều phối đa luồng (logical clients), nhịp gửi (WorkerPacer)
│   ├── tcp_client.py           # Gửi HTTP GET (cổng 5000) hoặc JSON qua TCP socket
│   └── udp_client.py           # Gửi datagram JSON qua UDP socket
├── web/
│   ├── app.py                  # Web Dashboard server (cổng 8080) + tự khởi chạy TechStore (cổng 5000)
│   └── static/
│       ├── index.html          # Giao diện NetGuard Lab (Simulator, Defense, Activity)
│       ├── app.js              # Logic gọi API, đồng bộ chế độ IP DoS/DDoS, cập nhật trạng thái
│       └── app.css             # Giao diện Dark UI
├── real_ecommerce_shop/
│   ├── shop_server.py          # Server Web Bán Hàng (cổng 5000) + WAF Rate Limiter + Thread Pool
│   ├── db.py                   # Khởi tạo & truy vấn SQLite (users, products, cart)
│   └── static/                 # Giao diện HTML/CSS/JS của trang TechStore
└── tests/
    └── test_web.py             # Unit tests cho WebController & giới hạn an toàn
```

---

## 5. Chi Tiết Chức Năng Mô Phỏng Tấn Công / Tạo Tải (Simulator)

Tất cả chế độ đều chạy thông qua `client/simulator.py` (`TrafficSimulator`), hỗ trợ cả **Web Dashboard (`:8080`)** lẫn **CLI (`python main.py client`)**.

### 5.1. Các kịch bản lưu lượng (Scenario Modes)

| Kịch bản | Mục đích mô phỏng | Mặc định (Web) | Giới hạn tối đa (`common/config.py`) | Đường dẫn mục tiêu |
| :--- | :--- | :--- | :--- | :--- |
| **`NORMAL`** | Lưu lượng người dùng bình thường (có độ trễ tự nhiên `jitter = 35%`) | 2 clients · 1 r/s · 10s | 10 clients · 5 r/s | Luân phiên `/home`, `/products`, `/search`, `/detail`, `/cart`, `/checkout` |
| **`FLASH_CROWD`** | Người dùng thật truy cập tăng đột biến (ví dụ săn sale), vẫn có nhịp tự nhiên | 35 clients · 4.5 r/s · 15s | 80 clients · 20 r/s | Luân phiên nhiều trang người dùng thật |
| **`DOS_SIMULATION`** | Mô phỏng tấn công **DoS từ 1 nguồn duy nhất** (1 IP mở nhiều luồng đồng thời dội tải) | 25 luồng · 8 r/s · 20s | 80 luồng · 30 r/s | Tập trung vào `/login` |
| **`DDOS_SIMULATION`** | Mô phỏng tấn công **DDoS phân tán từ nhiều nguồn** (nhiều IP / bot khác nhau) | 35 bots · 10 r/s · 20s | 100 bots · 40 r/s | Tập trung vào `/login` |

### 5.2. Giao thức & Chế độ IP nguồn (IP Source Modes)
- **Giao thức hỗ trợ:**
  - **`TCP`**: Khi nhắm vào cổng `5000` (TechStore), gửi gói tin **HTTP/1.1 GET** kèm header `User-Agent: BotnetSimulator/<client_id>` và `X-Forwarded-For: <source_ip>`.
  - **`UDP`**: Gửi datagram JSON qua `SOCK_DGRAM` có timeout bắt buộc.
- **Chế độ IP nguồn:**
  - **`SINGLE_IP` (1 IP nguồn duy nhất — DoS):** Toàn bộ các luồng dùng chung 1 IP và 1 định danh `dos_source_01`. Khi chọn `DOS_SIMULATION`, cả giao diện lẫn backend đều **khóa cứng** ở chế độ này (khóa `MULTI_IP` và `UDP_SPOOF`).
  - **`MULTI_IP` (Đa IP nguồn — TCP & UDP):** Mỗi luồng/bot giữ 1 địa chỉ IP riêng (`127.0.1.x` trên loopback hoặc `192.168.100.x` trong LAN) và `client_id` riêng (`ddos_client_01..N`).
  - **`UDP_SPOOF` (Mô phỏng đổi IP nguồn qua UDP):** Chỉ mở khi dùng giao thức `UDP` (vì TCP yêu cầu bắt tay 3 bước) và không ở chế độ `DOS_SIMULATION`; mỗi gói tin sử dụng IP loopback/private ngẫu nhiên trong phòng lab.

### 5.3. Hiệu ứng mô phỏng quá tải trên Web Bán Hàng (khi tắt phòng thủ)
Trong `real_ecommerce_shop/shop_server.py`, hàm `apply_dos_workload()` mô phỏng tình trạng nghẽn tài nguyên khi WAF đang **TẮT** và có tải tấn công:
- **Tải CPU:** Thực hiện vòng lặp băm `SHA-256` (45.000 lần/request) mô phỏng truy vấn/xử lý nặng.
- **Tải RAM:** Tích lũy buffer tạm (`512 KB`/request, giới hạn vòng đệm `maxlen=400`) để quan sát mức RAM tăng trên thanh giám sát.
- **Nghẽn Worker Pool:** Giữ luồng xử lý (`0.35s – 1.3s`) khiến cả `8/8` worker bị chiếm dụng, trình duyệt người dùng thật bị xoay vòng hoặc mất kết nối.

---

## 6. Chi Tiết Chức Năng Phòng Thủ & Kiểm Soát An Toàn

### 6.1. Phòng thủ ứng dụng (WAF) trên Web Bán Hàng (`real_ecommerce_shop/shop_server.py`)
1. **Sliding Window Rate Limiter (`check_rate_limit`):**
   - Sử dụng `collections.deque` theo cửa sổ trượt `RATE_WINDOW_SEC = 2.0` giây, đảm bảo thread-safe bằng `state_lock`.
   - Khi vượt ngưỡng, lập tức từ chối request ở lớp ngoài cùng bằng mã **`HTTP 429 Too Many Requests`** kèm header `Retry-After: 3` mà không cho đi vào xử lý nặng / truy vấn SQLite.
2. **JS Challenge — Xác minh trình duyệt Tầng 7 (kiểu Cloudflare):**
   - Khi WAF đang BẬT, mọi request chưa có cookie hợp lệ `waf_clearance` sẽ bị chặn ở lớp ngoài cùng:
     - Request HTML nhận trang **`HTTP 503 Service Unavailable`** hiển thị màn hình *"Đang kiểm tra trình duyệt của bạn (JS Challenge)..."*. Trình duyệt thật tự động chạy đoạn JavaScript giải phép tính kèm chữ ký **HMAC-SHA256**, gọi `/waf-verify`, nhận cookie `waf_clearance` (`HttpOnly`, hiệu lực 10 phút, gắn theo IP) rồi tự chuyển hướng vào trang web.
     - Request API (`/api/...`) hoặc bot/script không thực thi được JavaScript sẽ bị chặn ngay bằng **`HTTP 403 Forbidden`** (`JS_CHALLENGE_REQUIRED`), không thể chạm vào SQLite hay chiếm Worker, kể cả khi giả mạo `User-Agent`.
3. **Chống Slowloris (Slow HTTP Header Protection):**
   - **Giới hạn kết nối mở đồng thời trên mỗi IP (`MAX_CONCURRENT_CONNS_PER_IP = 5`):** Kiểm tra ngay tại tầng `accept()` trước khi đưa vào hàng đợi worker; vượt ngưỡng sẽ từ chối ngay bằng **`HTTP 429 Too Many Requests`** (`SLOWLORIS_CONN_LIMIT`).
   - **Thời hạn tổng đọc HTTP Header (`HEADER_READ_TIMEOUT_SEC = 1.5s`):** Tính thời hạn tổng (wall-clock deadline) thay vì reset timeout theo từng gói `recv()`; nếu client gửi nhỏ giọt quá `1.5s` chưa xong `\r\n\r\n` sẽ bị ngắt ngay bằng **`HTTP 408 Request Timeout`** (`SLOWLORIS_HEADER_TIMEOUT`).
   - **Giới hạn kích thước & số dòng Header (`MAX_HEADER_BYTES_WAF = 8 KB`, `MAX_HEADER_LINES_WAF = 40`):** Chặn kiểu nhỏ giọt nhiều dòng header giả bằng **`HTTP 431 Request Header Fields Too Large`**.
4. **Chống Database Search GET Flood (`GET /api/search`):**
   - **Rate limit riêng cho tìm kiếm:** Giới hạn tần suất truy vấn `/api/search` (`1.5 req/s` với bot, `4.0 req/s` với người dùng thường) và từ chối ngay bằng **`HTTP 429 Too Many Requests`** (`SEARCH_FLOOD_BLOCKED`).
   - **Giới hạn độ dài từ khóa (`MAX_SEARCH_KEYWORD_LEN = 64`):** Chặn từ khóa quá dài gây chậm câu lệnh `LIKE`.
   - **In-Memory Search Query Cache (`SEARCH_CACHE_TTL_SEC = 30s`):** Lưu đệm kết quả tìm kiếm trong RAM theo từ khóa để trả về tức thì mà không cần truy vấn lặp lại vào SQLite (tự xóa cache khi tồn kho thay đổi sau thanh toán).
5. **Chống Asymmetric HTTP POST Login Flood (`POST /api/login`):**
   - **Rate limit nghiêm ngặt riêng cho đăng nhập:** Giới hạn tối đa `1.0 req/s` với bot (`1.5 req/s` thường), chặn ngay bằng **`HTTP 429 Too Many Requests`** (`LOGIN_FLOOD_BLOCKED`) trước khi đọc body hay băm `SHA-256`.
   - **Khóa tạm thời chống Brute-Force / Login Flood (`LOGIN_MAX_FAILURES = 5`, `LOGIN_LOCKOUT_SEC = 30s`):** Tự động khóa tạm thời IP sai mật khẩu liên tiếp 5 lần trong 30 giây (`429 LOGIN_BRUTEFORCE_LOCKED`) để ngăn tiêu tốn CPU băm mật khẩu và truy vấn bảng `users`.
6. **Phân tách định danh & hạn mức (Bot vs Người dùng thật):**
   - Nhận diện lưu lượng bot dựa trên `User-Agent` (`BotnetSimulator`, `dos-bot`, `NetGuard-Lab`, `Botnet-LoginFlood`, `SlowHeaderLab`, `Slowloris-Agent`) và địa chỉ `X-Forwarded-For` / `client_ip`.
   - Áp dụng ngưỡng chặt cho bot (`2.0 req/s` với HTTP, `3.0 req/s` với JSON) trong khi vẫn giữ ngưỡng rộng cho người dùng thật (`60.0 req/s`) để khách hàng thật vẫn lướt web, tìm kiếm, đặt hàng mượt mà ngay cả khi đang bị tấn công.
7. **Giải phóng tài nguyên tức thì khi bật WAF:**
   - Khi bật phòng thủ, `ram_stress_buffers` được xóa sạch ngay lập tức, bỏ qua workload nặng và trả worker về trạng thái rảnh.
   - Endpoint `/api/toggle-defense` được miễn trừ workload và rate-limit để quản trị viên luôn bật/tắt WAF tức thì kể cả lúc server đang nghẽn.
8. **Bảo vệ hàng đợi kết nối (Backlog & Thread Pool Protection):**
   - Giới hạn cứng `WORKER_THREADS = 8` và hàng đợi `sock_queue(maxsize=128)`.
   - Khi hàng đợi đầy do lưu lượng quá lớn, server tự động trả **`HTTP 503 Service Unavailable`** (`Server Backlog Full`) thay vì tạo vô hạn thread làm treo hệ điều hành.
9. **Đồng bộ trạng thái phòng thủ 2 chiều & Giám sát thời gian thực:**
   - Nút bật/tắt phòng thủ trên Web Dashboard (`:8080`) đồng bộ trực tiếp vào biến `shop_server.defense_enabled` của Web Bán Hàng (`:5000`).
   - API `/api/server-status` cập nhật liên tục: trạng thái WAF, số Worker đang bận (`active_workers / 8`), mức CPU, RAM DoS, tổng request, số request bị chặn và độ trễ Ping thực tế.

### 6.2. Cơ chế an toàn phòng Lab (Guardrails — không thể vượt qua qua UI/CLI)
1. **Chặn tuyệt đối Public IP (`common/network_utils.py`):**
   - Chỉ chấp nhận `localhost`, `0.0.0.0`, `127.0.0.0/8`, và 3 dải mạng riêng RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). Mọi địa chỉ IP công cộng đều bị từ chối ở cả tầng Web API lẫn tầng Socket Client.
2. **Giới hạn trần cứng (`common/config.py`):**
   - `MAX_CLIENTS = 100`, `MAX_REQUEST_RATE = 50.0 req/client/s`, `MAX_DURATION = 300 giây`, `MAX_MESSAGE_BYTES = 4096 bytes`.
3. **Bảo mật cơ bản cho Web Dashboard & Shop:**
   - Dashboard giới hạn chỉ cho phép client từ Private LAN truy cập (`_client_allowed`), đặt header `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, và giới hạn body API tối đa `8 KB`.
   - Trang bán hàng dùng truy vấn SQLite tham số hóa (chống SQL Injection), băm mật khẩu `SHA-256` + salt, và gắn cờ `HttpOnly` cho cookie phiên đăng nhập.

---

## 7. Hướng Dẫn Chạy Nhanh

```bash
# 1. Cài thư viện hiển thị CLI (tùy chọn)
pip install -r requirements.txt

# 2. Khởi chạy đồng thời Web Dashboard (port 8080) và Web Bán Hàng TechStore (port 5000)
python main.py web

# 3. Hoặc chạy Client Simulator qua dòng lệnh (CLI)
python main.py client
```

- **Web Dashboard điều khiển:** `http://127.0.0.1:8080`
- **Web Bán Hàng TechStore (mục tiêu thử nghiệm):** `http://127.0.0.1:5000`
