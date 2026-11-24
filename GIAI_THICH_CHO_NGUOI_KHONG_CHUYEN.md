# Giải Thích Dự Án DoS / DDoS Cho Người Không Chuyên (Non-Tech)

Tài liệu này giải thích toàn bộ các khái niệm mạng, gói tin, 7 tầng mạng, DoS/DDoS và cách dự án này hoạt động bằng **ví dụ đời thường**, không cần biết lập trình hay kiến thức IT trước đó.

---

## 1. Dự án này đang mô phỏng chuyện gì?

Hãy tưởng tượng bạn mở một **Siêu thị TechStore** (chính là Trang Web Bán Hàng ở cổng `5000`):
- Khách hàng bình thường đến xem hàng, tìm kiếm, bỏ vào giỏ, thanh toán.
- Bên cạnh đó, bạn có một **Bảng điều khiển mô phỏng** (NetGuard Lab ở cổng `8080`) để thử nghiệm xem:
  1. Nếu đột nhiên có **rất nhiều kẻ phá rối tràn vào làm nghẽn cửa hàng** (DoS / DDoS) thì khách thật có mua được hàng không?
  2. Nếu bạn thuê một **anh bảo vệ thông minh (WAF)** đứng trước cửa chặn kẻ phá rối thì cửa hàng có hoạt động mượt mà trở lại không?

---

## 2. Gói tin (Packet), IP và Cổng (Port) là gì?

Mỗi khi bạn bấm vào một trang web, máy tính của bạn không gửi cả trang web trong một lần mà chia dữ liệu thành nhiều phần nhỏ gọi là **Gói tin (Packet)** — giống như việc gửi hàng qua bưu điện:

- **Gói tin (Packet) = 1 thùng bưu kiện:** Bên trong chứa yêu cầu của bạn (ví dụ: *"Cho tôi xem danh sách điện thoại"*), bên ngoài dán nhãn người gửi và người nhận.
- **Địa chỉ IP = Địa chỉ tòa nhà:** Ví dụ `127.0.0.1` (máy của chính bạn) hoặc `192.168.1.10` (máy bạn bè cùng Wi-Fi). Nhờ IP mà bưu kiện đi đúng tới máy tính cần tìm.
- **Cổng (Port) = Số phòng trong tòa nhà:** Một máy tính có thể chạy nhiều ứng dụng cùng lúc, mỗi ứng dụng ngồi ở một "phòng" riêng:
  - Phòng **`5000`**: Cửa hàng bán hàng **TechStore**.
  - Phòng **`8080`**: Phòng điều khiển **NetGuard Lab**.

---

## 3. TCP và UDP là gì? (2 cách giao hàng)

Khi gửi bưu kiện (gói tin) qua mạng, có 2 dịch vụ chuyển phát phổ biến:

- **TCP (Chuyển phát bảo đảm có ký nhận):**
  - Trước khi gửi hàng, hai bên phải gọi xác nhận với nhau 3 bước (*"Alo nghe rõ không?" → "Nghe rõ, gửi đi!" → "Ok tôi gửi đây"* — gọi là **bắt tay 3 bước**).
  - Hàng gửi đi bị thất lạc sẽ được gửi lại, đảm bảo đến đủ và đúng thứ tự.
  - Web bán hàng dùng **TCP** để dữ liệu đơn hàng, giỏ hàng không bao giờ bị mất.
- **UDP (Phát tờ rơi / Gửi thư thường):**
  - Cứ ném gói tin sang phía bên kia liên tục, **không cần hỏi trước**, cũng không quan tâm người nhận đã nhận được chưa.
  - Ưu điểm là rất nhanh (hay dùng cho livestream, gọi video, chơi game), nhưng dễ bị lợi dụng để xả hàng loạt gói tin rác.

---

## 4. "7 tầng mạng" (Mô hình OSI) hiểu siêu đơn giản

Khi một tin nhắn đi từ máy bạn sang máy chủ, nó đi qua **7 bước xử lý** gọi là **7 tầng OSI** (tưởng tượng như quy trình đóng gói và vận chuyển một đơn hàng):

| Tầng | Tên tiếng Anh | Hiểu theo ví dụ gửi hàng | Liên quan gì đến DoS/DDoS? |
| :---: | :--- | :--- | :--- |
| **Tầng 7** | **Application** (Ứng dụng) | **Nội dung đơn hàng** bạn viết (xem sản phẩm, đăng nhập, tìm kiếm trên web) | **DDoS Tầng 7:** Giả làm khách thật gửi liên tục các yêu cầu khó xử lý (đăng nhập, tìm kiếm) để làm kiệt sức nhân viên. |
| **Tầng 6** | **Presentation** (Trình diễn) | **Dịch thuật & niêm phong** (mã hóa dữ liệu, nén file, định dạng chữ/ảnh) | Ít khi bị đánh riêng lẻ, thường đi kèm tầng 7. |
| **Tầng 5** | **Session** (Phiên) | **Giữ liên lạc xuyên suốt** (nhớ rằng bạn vẫn đang đăng nhập trong suốt phiên mua sắm) | Làm đầy bộ nhớ lưu phiên đăng nhập. |
| **Tầng 4** | **Transport** (Giao vận) | **Chọn kiểu giao hàng TCP hay UDP** và ghi **số phòng (Port)** | **DDoS Tầng 4:** Gọi điện đặt hàng nửa vời rồi cúp máy liên tục (SYN Flood) hoặc xả gói tin UDP. |
| **Tầng 3** | **Network** (Mạng) | **Ghi địa chỉ nhà (IP)** và tìm đường đi trên bản đồ Internet | **DDoS Tầng 3:** Làm tắc nghẽn toàn bộ con đường dẫn tới tòa nhà. |
| **Tầng 2** | **Data Link** (Liên kết dữ liệu) | **Shipper nội bộ trong cùng tòa nhà / cùng Wi-Fi** (dùng địa chỉ MAC) | Chỉ phá được khi ngồi chung Wi-Fi / mạng LAN. |
| **Tầng 1** | **Physical** (Vật lý) | **Dây cáp mạng, cáp quang, sóng Wi-Fi** | Rút dây mạng hoặc phá sóng Wi-Fi. |

> **Mẹo nhớ nhanh:** Khi nói về tấn công qua Internet, người ta chỉ hay nhắc **Tầng 3, Tầng 4** (làm tắc đường đi & nghẽn kết nối) và **Tầng 7** (giả dạng khách thật vào phá bên trong cửa hàng).

---

## 5. Phân biệt Normal, Flash Crowd, DoS và DDoS

Tiếp tục dùng ví dụ **Siêu thị TechStore**:

1. **Normal (Bình thường):**
   - Lác đác vài khách thật vào xem hàng, đi từ quầy này sang quầy khác một cách thong thả. Siêu thị chạy mượt mà.
2. **Flash Crowd (Khách thật đông đột biến — ví dụ ngày siêu sale 11/11):**
   - Rất đông **khách thật** ùa vào cùng lúc. Dù đông nhưng mỗi người chỉ hỏi mua bình thường, xem nhiều mặt hàng khác nhau và có khoảng nghỉ tự nhiên.
3. **DoS (Denial of Service — Tấn công từ 1 nguồn duy nhất):**
   - Chỉ có **1 người duy nhất (1 địa chỉ IP)** nhưng người đó dùng máy móc gọi món liên tục hàng chục lần mỗi giây (`25 luồng` song song) để chiếm hết thời gian của thu ngân.
   - *Điểm yếu của DoS:* Bảo vệ chỉ cần nhìn mặt (1 IP) rồi **mời đúng 1 người đó ra khỏi cửa** là xong ngay.
4. **DDoS (Distributed Denial of Service — Tấn công phân tán từ nhiều nguồn):**
   - Kẻ xấu thuê **hàng chục đến hàng triệu người ở khắp nơi (nhiều IP khác nhau — gọi là Botnet)** cùng ùa vào quán hỏi đi hỏi lại một câu khó (như trang `/login`) để làm tắc cứng quầy thu ngân.
   - *Tại sao DDoS nguy hiểm hơn DoS?* Vì quá nhiều người từ quá nhiều địa chỉ khác nhau, rất khó phân biệt đâu là khách thật, đâu là người được thuê tới phá.

---

## 6. Worker (8/8), CPU, RAM và Ping trên màn hình có nghĩa là gì?

Trên thanh trạng thái đầu trang **TechStore**, bạn sẽ thấy các thông số này:

- **Worker (`8 / 8 — 100%`):**
  - Hiểu là **8 quầy thu ngân** của siêu thị. Cùng một lúc siêu thị chỉ phục vụ được tối đa 8 yêu cầu.
  - Khi thấy `8 / 8 (100%)` nghĩa là cả 8 thu ngân đều đang bị kẹt cứng bởi kẻ phá rối. Khách thật đến sau phải **xếp hàng chờ** (tối đa 128 người trong hàng đợi). Nếu hàng đợi cũng chật kín, siêu thị phải đóng cửa báo lỗi **`503 Service Unavailable`** (quá tải).
- **CPU (`85% - 100% QUÁ TẢI`):**
  - Là **bộ não / sức tính toán** của máy chủ. Khi bị dội yêu cầu liên tục mà không có bảo vệ, máy chủ phải tính toán cật lực đến mức quá tải.
- **RAM DoS (`... MB`):**
  - Là **bàn làm việc tạm thời** của máy chủ. Càng nhiều yêu cầu dồn vào, bàn càng bị chất đầy hồ sơ, làm máy chậm đi.
- **Ping (`12ms Mượt` vs `Mất kết nối`):**
  - Là **thời gian chờ phản hồi** (tính bằng mili-giây, 1000ms = 1 giây).
  - Dưới `50ms` là siêu mượt (bấm là mở ngay). Khi bị DoS/DDoS, thời gian chờ tăng vọt lên vài giây hoặc trình duyệt quay tròn mãi không tải nổi.

---

## 7. Nút "Bật Phòng Thủ WAF" hoạt động như thế nào?

**WAF (Web Application Firewall)** giống như **anh bảo vệ chuyên nghiệp đứng ngay cửa ra vào**:

1. **Đếm tốc độ ra vào (Rate Limiting — Cửa sổ trượt 2 giây):**
   - Bảo vệ cầm đồng hồ bấm giờ: thấy ai đó gửi yêu cầu dồn dập quá mức cho phép trong 2 giây gần nhất thì **chặn ngay từ ngoài cửa**, không cho bước vào gặp thu ngân (Worker) hay chạm vào kho hàng (Database SQLite).
2. **Trả về thẻ từ chối (`429 Too Many Requests`):**
   - Kẻ phá rối bị từ chối tức thì với thông báo *"Bạn gửi quá nhiều yêu cầu, vui lòng chờ"*.
3. **Phân biệt khách thật và bot:**
   - Bot bị siết cực chặt (tối đa `2 yêu cầu/giây`), trong khi khách hàng thật vẫn được phép thao tác thoải mái (tối đa `60 yêu cầu/giây`).
4. **Kết quả ngay lập tức:**
   - Ngay khi bấm **Bật Phòng Thủ WAF**, cả 8 quầy thu ngân lập tức được giải phóng, RAM được dọn sạch, CPU hạ xuống `1% - 5%`, và khách hàng thật lại lướt web, tìm kiếm, đặt hàng mượt mà dưới `20ms` dù bên ngoài bot vẫn đang dội vào!

---

## 8. Bảng từ điển siêu ngắn (Dùng để nhớ nhanh)

| Thuật ngữ | Nghĩa đời thường |
| :--- | :--- |
| **Server (Máy chủ)** | Cửa hàng / Siêu thị phục vụ khách |
| **Client (Máy khách)** | Người đi mua hàng (trình duyệt của bạn) |
| **IP** | Địa chỉ nhà |
| **Port (Cổng)** | Số phòng trong tòa nhà (`5000` là shop, `8080` là bảng điều khiển) |
| **Packet (Gói tin)** | Thùng bưu kiện chứa yêu cầu/dữ liệu |
| **Worker** | Quầy thu ngân xử lý yêu cầu |
| **DoS** | 1 người đứng phá làm nghẽn cửa hàng |
| **DDoS** | Rất nhiều người từ nhiều nơi cùng kéo tới phá |
| **Flash Crowd** | Khách thật đến đông đột biến (không phải phá) |
| **WAF / Rate Limit** | Anh bảo vệ chặn người ra vào quá nhanh bất thường |
| **Lỗi 429** | Bảo vệ chặn lại vì gửi quá nhiều yêu cầu |
| **Lỗi 503** | Cửa hàng quá tải, không thể nhận thêm khách |
