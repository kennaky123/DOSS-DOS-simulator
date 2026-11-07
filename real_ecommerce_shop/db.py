"""Quản lý Cơ sở dữ liệu SQLite cho Web Bán Hàng Real-Time."""

import sqlite3
import hashlib
import os
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "shop.db"


def get_connection():
    """Tạo kết nối SQLite có hỗ trợ timeout để tránh deadlock khi tải cao."""
    conn = sqlite3.connect(DB_FILE, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    """Băm mật khẩu bằng SHA-256 kèm salt cố định."""
    salt = "techstore_secure_salt_2026"
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def init_db():
    """Khởi tạo bảng Users và Products, thêm dữ liệu mẫu nếu chưa có."""
    conn = get_connection()
    cursor = conn.cursor()

    # Bảng người dùng
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            fullname TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Bảng sản phẩm
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL,
            stock INTEGER NOT NULL,
            image_icon TEXT NOT NULL,
            description TEXT
        )
    """)

    # Bảng giỏ hàng của người dùng trong SQLite
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, product_id)
        )
    """)

    # Kiểm tra xem đã có sản phẩm chưa, nếu chưa thì seed 8 sản phẩm mẫu
    cursor.execute("SELECT COUNT(*) as count FROM products")
    if cursor.fetchone()["count"] == 0:
        sample_products = [
            ("iPhone 16 Pro Max 256GB", "Điện thoại", 34990000, 15, "📱", "Màn hình OLED 6.9 inch, chip Apple A18 Pro mạnh mẽ, camera tiềm vọng 5x."),
            ("Samsung Galaxy S24 Ultra", "Điện thoại", 31990000, 20, "📱", "Khung viền Titanium, tích hợp bút S-Pen, trí tuệ nhân tạo Galaxy AI đỉnh cao."),
            ("MacBook Pro 14 M3 Pro", "Laptop", 49990000, 8, "💻", "18GB RAM, 512GB SSD, màn hình Liquid Retina XDR 120Hz siêu sắc nét."),
            ("Laptop Gaming ASUS ROG Strix G16", "Laptop", 36500000, 12, "💻", "Intel Core i7-14650HX, card đồ họa RTX 4060, tản nhiệt buồng hơi thông minh."),
            ("Tai nghe Sony WH-1000XM5", "Phụ kiện", 7990000, 35, "🎧", "Khử tiếng ồn chủ động hàng đầu ngành, thời lượng pin 30 giờ liên tục."),
            ("Bàn phím cơ Custom Keychron Q1 Pro", "Phụ kiện", 4500000, 25, "⌨️", "Vỏ nhôm CNC nguyên khối, kết nối Bluetooth/Type-C, switch Gateron Jupiter."),
            ("Chuột không dây Logitech MX Master 3S", "Phụ kiện", 2390000, 40, "🖱️", "Cảm biến 8K DPI trên mọi bề mặt, con lăn điện từ MagSpeed cực êm."),
            ("Đồng hồ Apple Watch Ultra 2", "Phụ kiện", 21490000, 10, "⌚", "Vỏ titan quân đội 49mm, pin 72 giờ, định vị GPS tần số kép chuẩn xác.")
        ]
        cursor.executemany("""
            INSERT INTO products (name, category, price, stock, image_icon, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, sample_products)

    # Thêm 1 tài khoản mẫu admin nếu chưa có
    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username = 'admin'")
    if cursor.fetchone()["count"] == 0:
        cursor.execute("""
            INSERT INTO users (username, password_hash, fullname, email)
            VALUES (?, ?, ?, ?)
        """, ("admin", hash_password("admin123"), "Quản trị viên", "admin@techstore.vn"))

    conn.commit()
    conn.close()
    print(f"[SQLite] Đã khởi tạo cơ sở dữ liệu: {DB_FILE}")


if __name__ == "__main__":
    init_db()
