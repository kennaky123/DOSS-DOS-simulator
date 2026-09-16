# Câu hỏi bảo vệ đồ án

## 1. Socket là gì?

Socket là điểm cuối để hai tiến trình trao đổi dữ liệu qua mạng. Một socket thường
được xác định bởi giao thức, địa chỉ IP và port.

## 2. IP và Port có vai trò gì?

IP xác định máy hoặc network interface. Port xác định ứng dụng/dịch vụ trên máy đó.
Trong project, client cần đúng IP:port của Test Server.

## 3. TCP khác UDP thế nào?

TCP hướng kết nối, đảm bảo thứ tự và truyền lại dữ liệu bị mất. UDP không kết nối,
gửi từng datagram, overhead thấp nhưng không đảm bảo delivery hay thứ tự.

## 4. TCP Server dùng bind/listen/accept để làm gì?

`bind()` gắn socket với IP:port. `listen()` chuyển socket sang trạng thái lắng nghe.
`accept()` lấy một kết nối đã hoàn thành handshake và trả về client socket riêng.

## 5. Tại sao UDP không có accept?

UDP connectionless nên không thiết lập kết nối và không có handshake. Server nhận
trực tiếp datagram cùng địa chỉ nguồn bằng `recvfrom()`.

## 6. Vì sao TCP server phải dùng multi-thread?

Nếu xử lý tuần tự, một client chậm có thể chặn accept loop. Mỗi client có handler
thread riêng nên server có thể phục vụ nhiều kết nối đồng thời.

## 7. Một máy có thể tạo nhiều client không?

Có. Một tiến trình có thể tạo nhiều thread và nhiều socket. Hệ điều hành phân biệt
các TCP connection bằng bộ địa chỉ IP/port nguồn và đích.

## 8. Logical client khác physical machine thế nào?

Logical client là worker có `client_id` riêng trong phần mềm. Nhiều logical client
trên cùng máy thường vẫn dùng chung IP thật; physical machine là thiết bị/VM riêng.

## 9. DoS khác DDoS ở điểm nào?

DoS thường có một nguồn tạo tải lớn. DDoS có nhiều nguồn phân tán. Trong project,
DoS dùng chung `dos_source_01`; DDoS dùng nhiều `ddos_client_XX`.

## 10. Tại sao đây chỉ là mô phỏng DDoS?

Các nguồn chủ yếu là logical client có giới hạn, chỉ nhắm Test Server trong private
lab. Project không có botnet, spoofing, raw packet hay kỹ thuật tấn công thật.

## 11. Requests/second là gì?

Đó là số request server nhận trong cửa sổ một giây gần nhất. Project lưu timestamp
trong deque và loại các timestamp cũ hơn một giây.

## 12. Latency là gì?

Latency phía client là thời gian khứ hồi từ lúc bắt đầu gửi đến khi nhận response.
Response time phía server chỉ đo thời gian xử lý request tại server.

## 13. Vì sao server có thể chậm khi số client tăng?

CPU phải lập lịch nhiều thread hơn, socket và bộ nhớ tăng, lock bị tranh chấp và hàng
đợi mạng dài hơn. Vì vậy throughput có giới hạn và latency có thể tăng.

## 14. Mô hình Client/Server hoạt động thế nào?

Server bind và chờ request. Client chủ động gửi request đến IP:port server. Server xử
lý, phản hồi; client nhận response và đo kết quả.

## 15. Project nằm ở tầng nào của OSI/TCP-IP?

JSON protocol nằm ở tầng ứng dụng. TCP/UDP thuộc tầng giao vận, IP thuộc tầng Internet;
Ethernet/Wi-Fi bên dưới thuộc tầng truy cập mạng.

## 16. TCP là stream, project chia message thế nào?

TCP không giữ ranh giới message. Project thêm newline sau mỗi JSON và đọc đến newline,
đồng thời giới hạn frame 4096 byte.

## 17. UDP datagram có cần newline không?

Không. Mỗi lần `recvfrom()` trả về một datagram nên ranh giới message đã được UDP giữ.
Project parse toàn bộ datagram như một JSON object.

## 18. Semaphore dùng để làm gì?

Semaphore giới hạn số TCP handler đồng thời. Khi đủ 100 slot, server trả `SERVER_BUSY`
thay vì tiếp tục tạo thread không giới hạn.

## 19. Lock dùng để làm gì?

Nhiều handler cùng sửa counter có thể gây race condition. `threading.Lock` bảo đảm
mỗi lần chỉ một thread cập nhật hoặc snapshot thống kê.

## 20. Graceful shutdown hoạt động thế nào?

`Ctrl+C` đặt stop event, đóng listening/datagram socket và chờ các service/handler kết
thúc. Client cũng đặt stop event, ngừng tạo request mới và join worker.

## 21. Vì sao không bind 0.0.0.0?

`0.0.0.0` lắng nghe trên mọi interface, có thể vô tình mở service ra mạng không thuộc
lab. Project yêu cầu một loopback/private IP cụ thể để phạm vi rõ ràng.

## 22. UDP success có nghĩa là UDP đáng tin cậy không?

Không. Success chỉ nghĩa là client đã nhận response trong lần chạy đó. UDP vẫn không
có cơ chế đảm bảo delivery, retransmission hay thứ tự ở tầng giao vận.

## 23. Bytes received/sent được tính để làm gì?

Hai metric cho thấy lượng dữ liệu ứng dụng server đã nhận và tạo phản hồi. Chúng không
bao gồm toàn bộ header Ethernet/IP/TCP/UDP trên đường truyền.

## 24. Rate limiter bảo vệ server thế nào?

Limiter dùng cửa sổ trượt một giây. Khi tổng request đạt hard limit, request mới nhận
response `RATE_LIMITED` và được tính thất bại thay vì xử lý không giới hạn.

