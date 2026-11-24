"""Script kiểm tra trạng thái hoạt động (Health Check) của Web E-Commerce cổng 5000."""

import socket
import time


def check_ecommerce_status(host: str = "127.0.0.1", port: int = 5000) -> bool:
    """Kiểm tra xem Web E-Commerce có đang lắng nghe và phản hồi kết nối hay không."""
    start_time = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)

    try:
        sock.connect((host, port))
        latency = (time.perf_counter() - start_time) * 1000
        print(f"✅ Cổng {port} đang MỞ! Kết nối thành công tới {host}:{port} ({latency:.2f} ms).")
        return True
    except socket.timeout:
        print(f"⚠️ Hết thời gian chờ: Server tại {host}:{port} không phản hồi (có thể đang quá tải hoặc bị lọc).")
        return False
    except ConnectionRefusedError:
        print(f"❌ Cổng {port} đang ĐÓNG: Server Web chưa được khởi chạy tại {host}:{port}.")
        return False
    except OSError as err:
        print(f"❌ Lỗi mạng: {err}")
        return False
    finally:
        sock.close()


if __name__ == "__main__":
    check_ecommerce_status("127.0.0.1", 5000)