# TechStore — Ứng Dụng Web Bán Hàng E-Commerce Thật (Mô Phỏng DoS / DDoS Layer 7)

## 1. Giới thiệu
Đây là một website thương mại điện tử độc lập, chân thực, được xây dựng để kiểm thử tấn công và phòng thủ DoS/DDoS một cách thực tế nhất:
- **Cơ sở dữ liệu SQLite (`shop.db`)**: Lưu trữ danh mục sản phẩm, tìm kiếm từ khóa, và lưu thông tin người dùng đăng ký / đăng nhập (mật khẩu băm SHA-256).
- **Mô hình Worker Thread Pool (8 workers)**: Tương tự như Apache / Gunicorn trong môi trường sản xuất.
- **Trải nghiệm Lag / Xoay vòng trên Trình duyệt THẬT**:
  - Khi bị tấn công DoS/DDoS mà chưa bật phòng thủ: Toàn bộ worker threads bị chiếm dụng. Khách hàng thật mở Chrome/Edge truy cập `http://127.0.0.1:5000` sẽ thấy **biểu tượng vòng xoay loading trên tab trình duyệt xoay tít mù (vô tận)**, không tải được trang hoặc bị Timeout!
  - Khi bật chế độ Phòng thủ WAF: Web phát hiện botnet và chặn ngay lập tức (HTTP 429), giải phóng luồng xử lý, khách hàng thật tải lại trang vèo vèo trong 15ms.

## 2. Hướng dẫn chạy & Demo thực tế

### Bước 1: Khởi động Web Server
Chạy file `run_server.bat` hoặc mở Terminal gõ:
```powershell
cd D:\VScode\lapTrinhMang\real_ecommerce_shop
python server.py
```
Server sẽ chạy tại: **`http://127.0.0.1:5000`**

### Bước 2: Trải nghiệm người dùng bình thường
1. Mở trình duyệt Chrome/Edge truy cập `http://127.0.0.1:5000`.
2. Thử các chức năng thực tế:
   - Tìm kiếm sản phẩm (gõ `laptop`, `iphone`, `tai nghe`... dữ liệu lấy từ SQLite).
   - Bấm **"Đăng ký mới"** (nhập tên tài khoản, mật khẩu -> lưu vào SQLite).
   - Bấm **"Đăng nhập"** (thử tài khoản `admin` / `admin123` hoặc tài khoản vừa tạo).
   - Bấm **"Thêm vào giỏ"** và xem giỏ hàng.
3. Để ý thanh giám sát ở trên cùng: Phòng thủ WAF mặc định đang **TẮT**.

### Bước 3: Thực hiện tấn công DoS / DDoS (Làm web bị lag & xoay vòng thật)
Mở một cửa sổ Terminal mới hoặc chạy file `run_attack.bat`:
```powershell
cd D:\VScode\lapTrinhMang\real_ecommerce_shop
python attacker.py
```
Chọn một trong 3 hình thức:
- **[1] HTTP GET Flood**: Đánh tràn ngập trang chủ và API tìm kiếm SQLite.
- **[2] HTTP POST Login Flood**: Tấn công bất đối xứng, ép CPU băm SHA-256 và truy vấn liên tục.
- **[3] Slowloris Attack**: Treo socket, giữ chân toàn bộ worker threads.

### Bước 4: Quan sát trên Trình duyệt THẬT
1. Trong lúc `attacker.py` đang bắn, quay lại tab Chrome `http://127.0.0.1:5000` và nhấn **F5** (hoặc click vào nút Đăng nhập / Tìm kiếm).
2. **KẾT QUẢ:**
   - **Vòng xoay trên tab Chrome (Favicon spinner) bắt đầu xoay liên tục không ngừng!**
   - Góc dưới Chrome hiện dòng thông báo thực: `Đang đợi 127.0.0.1...` (Waiting for 127.0.0.1).
   - Widget góc dưới hiển thị: `Ping: > 3000ms (NGHẼN MẠNG / QUÁ TẢI)`.
   - Chứng minh rõ ràng trước hội đồng/giáo viên: Website đang bị tấn công thật vào luồng xử lý chứ không hề có text giả lập hay hardcode.

### Bước 5: Bật phòng thủ WAF và chứng minh hiệu quả
1. Nhấn nút **"Bật Phòng Thủ WAF"** trên thanh màu đen ở trên cùng website (hoặc trước khi bắt đầu đợt tấn công mới).
2. Khi bật WAF: Tự động rate-limit và drop các bot vượt quá ngưỡng 10 req/s.
3. Lúc này dù `attacker.py` đang bắn ầm ầm, bạn nhấn **F5** trên Chrome thì trang web vẫn tải **mượt mà ngay lập tức (< 20ms)**!
