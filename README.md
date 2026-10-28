# Xây dựng chương trình mô phỏng công cụ DoS, DDoS

## 1. Mục tiêu

Project minh họa kiến thức Lập trình mạng bằng một hệ thống **multi-client load
simulation** chạy trên server do project tự tạo. Chương trình gồm TCP Server, UDP
Server và Client Simulator có thể tạo nhiều logical client bằng thread.

## 2. Mô hình Client/Server

```text
MÁY 2 - CLIENT SIMULATOR
  +-- logical client 01 -- TCP connect/send/recv hoặc UDP sendto/recvfrom
  +-- logical client 02
  +-- logical client 03
                 |
                 | localhost / private LAN
                 v
MÁY 1 - TEST SERVER
  +-- TCP listening socket -> accept -> client handler thread
  +-- UDP datagram socket  -> recvfrom -> process -> sendto
  +-- Statistics + terminal dashboard + server.log
```

Logical client là thread/task có `client_id` riêng, không phải một máy vật lý khác.
Chỉ khi chạy nhiều instance simulator trên nhiều máy/VM trong LAN thì server mới thấy
nhiều IP nguồn thật. Project không giả mạo IP.

## 3. Kiến thức áp dụng

- Mô hình Client/Server, IPv4 và Port.
- TCP socket và UDP socket bằng thư viện chuẩn `socket`.
- TCP server đa luồng, một handler riêng cho mỗi client socket.
- Timeout, exception handling và graceful shutdown.
- JSON application protocol, TCP message framing và UDP datagram.
- Lock, semaphore, rate limiter và realtime monitoring.
- So sánh connection-oriented TCP với connectionless UDP.

## 4. Kiến trúc project

```text
network_dos_ddos_simulator/
|-- common/
|   |-- config.py          # Cấu hình và hard limit dùng chung
|   |-- network_utils.py   # Private-IP validation, rate limiter
|   `-- protocol.py        # JSON encode/decode, TCP framing
|-- server/
|   |-- server.py          # CLI và điều phối TCP/UDP/BOTH
|   |-- tcp_server.py      # bind/listen/accept
|   |-- udp_server.py      # bind/recvfrom/sendto
|   |-- client_handler.py  # Xử lý request và logging
|   |-- statistics.py      # Bộ đếm thread-safe
|   `-- monitor.py         # Dashboard terminal
|-- client/
|   |-- client.py          # Input, validation và CLI
|   |-- tcp_client.py      # connect/send/recv
|   |-- udp_client.py      # sendto/recvfrom
|   `-- simulator.py       # Logical clients và client monitoring
|-- shared/                # Compatibility với phiên bản TCP-only
|-- docs/
|   `-- defense_questions.md
|-- main.py
|-- requirements.txt
`-- README.md
```

## 5. Giao thức JSON

Request:

```json
{
  "client_id": "client_01",
  "type": "REQUEST",
  "timestamp": "2026-08-30T12:00:00+00:00"
}
```

Response thành công:

```json
{
  "client_id": "client_01",
  "status": "OK",
  "server_time": "2026-08-30T12:00:00+00:00"
}
```

TCP là byte stream nên mỗi JSON frame kết thúc bằng newline. UDP bảo toàn ranh giới
datagram nên không cần newline. Mọi message bị giới hạn tối đa 4096 byte.

## 6. TCP Socket

TCP Server thể hiện trực tiếp:

```text
socket(AF_INET, SOCK_STREAM) -> bind() -> listen() -> accept()
-> handler thread -> recv() -> sendall() -> close()
```

Mỗi kết nối được giao cho một handler thread. Semaphore giới hạn tối đa 100 kết nối
đồng thời; socket timeout ngăn một client giữ handler vô thời hạn.

TCP Client thể hiện:

```text
socket(AF_INET, SOCK_STREAM) -> connect() -> sendall() -> recv() -> close()
```

## 7. UDP Socket

UDP Server không có `listen()` và `accept()`:

```text
socket(AF_INET, SOCK_DGRAM) -> bind() -> recvfrom() -> sendto() -> close()
```

UDP Client dùng `sendto()` và `recvfrom()`. Timeout là bắt buộc vì UDP không đảm bảo
datagram hoặc response sẽ tới đích.

## 8. Multi-client và Threaded Server

TCP accept loop chỉ nhận kết nối. Việc đọc, kiểm tra JSON và phản hồi được chuyển sang
`handle_tcp_client()` chạy trên thread riêng, nên nhiều client có thể được phục vụ
đồng thời. Metric dùng `threading.Lock` để tránh race condition.

UDP không tạo connection/handler riêng. Một socket nhận từng datagram và phản hồi ngay;
đây là đặc trưng connectionless cần thể hiện, không phải thiếu sót của implementation.

## 9. Monitoring và logging

Server dashboard cập nhật mỗi giây:

- IP, Port, Protocol.
- Current logical clients và current TCP connections.
- Total TCP connections và total requests.
- Requests/second, successful, failed, average server response time.
- Bytes received, bytes sent và server uptime.

Client dashboard hiển thị mode, protocol, server, logical clients, duration, rate theo
từng client, sent/successful/failed, requests/second, average round-trip latency và
elapsed time.

Log nằm tại `server.log`, ví dụ:

```text
2026-08-30 19:41:35 | INFO | protocol=UDP client_id=normal_client_01 client=127.0.0.1:56201 event=DATAGRAM latency_ms=0.03 status=SUCCESS
```

Log không chứa dữ liệu nhạy cảm.

## 10. Giới hạn an toàn

Target và bind address chỉ được thuộc:

- `127.0.0.0/8` (bao gồm `127.0.0.1` và `localhost`).
- `10.0.0.0/8`.
- `172.16.0.0/12`.
- `192.168.0.0/16`.

Public IP, hostname Internet, `0.0.0.0`, link-local và IPv6 đều bị từ chối với thông
báo: `Project chỉ cho phép localhost hoặc private LAN IP.`

Hard limit:

| Giới hạn                  |                Giá trị |
| ------------------------- | ---------------------: |
| Logical clients           |                     50 |
| Request rate              | 20 request/client/giây |
| Duration                  |                60 giây |
| TCP connections đồng thời |                    100 |
| Tổng server rate          |      1000 request/giây |

Mode còn có giới hạn thấp hơn trong `common/config.py`. Không có chế độ gửi vô hạn.

## 11. Cài đặt

Yêu cầu Python 3.10 trở lên:

```powershell
cd D:\VScode\lapTrinhMang\network_dos_ddos_simulator
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`Rich` tạo dashboard tại một vị trí terminal. Nếu chưa cài Rich, chương trình dùng
fallback text và chức năng mạng vẫn hoạt động.

## 12. Chạy localhost

Terminal 1, chạy đồng thời TCP và UDP trên port 9000:

```powershell
python server/server.py
```

Terminal 2, nhập tương tác:

```powershell
python client/client.py
```

Chạy nhanh TCP và UDP:

```powershell
python client/client.py --protocol TCP --mode NORMAL --clients 2 --rate 1 --duration 10 --non-interactive
python client/client.py --protocol UDP --mode NORMAL --clients 2 --rate 1 --duration 10 --non-interactive
```

Có thể chỉ chạy một protocol server:

```powershell
python server/server.py --protocol TCP
python server/server.py --protocol UDP
```

Entry point tương đương:

```powershell
python main.py server --protocol BOTH
python main.py client --protocol TCP --mode NORMAL --non-interactive
```

## 13. Chạy hai máy cùng LAN

Ví dụ Máy 1 có IP `192.168.1.10`.

Máy 1:

```powershell
python server/server.py --host 192.168.1.10 --port 5000 --protocol BOTH
```

Máy 2:

```powershell
python client/client.py
```

Nhập target `192.168.1.10`, port `5000`, sau đó chọn TCP hoặc UDP. Hai máy phải cùng
LAN, ping được nhau và firewall của Máy 1 phải cho phép port 5000 trong profile
Private. Chỉ mở port cho mạng lab; không port-forward router và không public dịch vụ
ra Internet.

## 14. Các kịch bản traffic

### Normal

Ít client và rate thấp:

```powershell
python client/client.py --protocol TCP --mode NORMAL --clients 5 --rate 1 --duration 10 --non-interactive
```

### DoS Simulation

Một simulator/host tạo nhiều worker đồng thời. Các worker dùng chung logical ID
`dos_source_01`:

```powershell
python client/client.py --protocol TCP --mode DOS_SIMULATION --clients 20 --rate 5 --duration 15 --non-interactive
```

### DDoS Simulation

Mỗi worker dùng `ddos_client_XX` riêng. Có thể chạy cùng lệnh trên nhiều máy/VM trong
private LAN để có nhiều IP thật, nhưng không spoof IP:

```powershell
python client/client.py --protocol TCP --mode DDOS_SIMULATION --clients 30 --rate 5 --duration 15 --non-interactive
```

Đổi `--protocol UDP` để chạy cùng kịch bản bằng UDP.

## 15. So sánh TCP và UDP

| Tiêu chí       | TCP                                                     | UDP                               |
| -------------- | ------------------------------------------------------- | --------------------------------- |
| Loại           | Connection-oriented                                     | Connectionless                    |
| Server         | `listen`, `accept`                                      | Không `listen`, không `accept`    |
| Client         | `connect`                                               | Không cần `connect` trong project |
| Truyền dữ liệu | `send/recv`, byte stream                                | `sendto/recvfrom`, datagram       |
| Đảm bảo        | Thứ tự, retransmission, delivery khi kết nối còn hợp lệ | Không đảm bảo delivery/thứ tự     |
| Overhead       | Cao hơn                                                 | Thấp hơn                          |
| Project đo     | Kết nối, request, latency                               | Datagram, request, timeout/loss   |

JSON là giao thức tầng ứng dụng của project. Với TCP, nó nằm trên TCP rồi IP; với
UDP, nó nằm trên UDP rồi IP.

## 16. Kịch bản demo 5 phút

1. **0:00-0:35** - Giới thiệu mô hình hai máy, private-IP guard và hard limit.
2. **0:35-1:15** - Chạy server `BOTH`; mở `tcp_server.py`, chỉ chuỗi
   `socket/bind/listen/accept`, rồi gửi một TCP client để thấy connect/send/recv/close.
3. **1:15-2:00** - Chạy Normal 5 client; quan sát connections, requests/sec và latency.
4. **2:00-2:45** - Chạy DoS Simulation trong giới hạn; giải thích một host và chung
   `dos_source_01`, quan sát RPS tăng.
5. **2:45-3:35** - Chạy DDoS Simulation; chỉ các `ddos_client_XX` trong log và nói rõ
   đây là logical/distributed simulation, không phải botnet.
6. **3:35-4:25** - Chuyển client sang UDP; chỉ `recvfrom/sendto`, so sánh không có
   connection và có timeout.
7. **4:25-5:00** - Chỉ dashboard/log, nhấn `Ctrl+C` và quan sát shutdown sạch.

## 17. Giới hạn của mô phỏng

- Logical client không đồng nghĩa máy vật lý hoặc IP nguồn riêng.
- Thread và kết nối ngắn hạn không đại diện mọi workload thực tế.
- UDP success chỉ cho biết simulator nhận response trong timeout; UDP vẫn không có
  bảo đảm delivery ở tầng giao vận.
- Metric terminal phục vụ minh họa, không thay thế benchmark/profiler chuyên dụng.
- Project không triển khai HTTP mode vì TCP và UDP là trọng tâm bắt buộc.

## 18. Nguyên tắc an toàn

Chỉ chạy với Test Server thuộc chính project trong localhost/private LAN và khi có
quyền kiểm thử. Không sửa validation/hard limit để nhắm hệ thống khác. Không dùng
project cho website, public IP, firewall bypass, amplification, spoofing, malware,
botnet hoặc bất kỳ hoạt động gây gián đoạn thực tế nào.

## 19. Kết quả localhost đã kiểm tra

Trên Python 3.14.4, server `BOTH` tại `127.0.0.1:19000` đã nhận:

- TCP Normal: 12 sent, 12 successful, 0 failed.
- UDP Normal: 12 sent, 12 successful, 0 failed.
- Server tổng: 24 requests, 12 TCP connections, byte RX/TX được cập nhật.
- Public IP và `0.0.0.0` bị từ chối; `Ctrl+C` đóng cả TCP và UDP sạch.

## 20. Phòng thủ nhiều lớp (bản nâng cấp)

Server dùng chung một `TrafficGuard` cho cả TCP và UDP, nên đổi giao thức không làm mới
giới hạn. Mỗi request được kiểm tra trong cửa sổ trượt một giây theo ba lớp:

| Lớp                            |         Cảnh báo |              Chặn |
| ------------------------------ | ---------------: | ----------------: |
| Toàn server                    |                - | 1000 request/giây |
| Một IP nguồn                   | 100 request/giây |  250 request/giây |
| Một logical client trên một IP |  15 request/giây |   40 request/giây |

Dashboard hiển thị `NORMAL`, `ELEVATED` hoặc `BLOCKING`, tổng request đáng ngờ, tổng request
bị chặn và lý do chặn gần nhất. Các lý do gồm `CLIENT_RATE_LIMIT`, `SOURCE_RATE_LIMIT`,
`GLOBAL_RATE_LIMIT` và `SERVER_BUSY`. State theo client/IP cũ được dọn để tránh tăng bộ nhớ
không giới hạn.

Chạy kiểm thử:

```powershell
python -m unittest discover -v
```

## 21. Web dashboard

Khởi chạy giao diện web tại localhost, không cần cài thêm framework:

```powershell
python main.py web
```

Sau đó mở `http://127.0.0.1:8080`. Từ dashboard có thể:

- Khởi động/dừng TCP, UDP hoặc server chạy đồng thời cả hai giao thức.
- Chạy các kịch bản Normal, DoS Simulation và DDoS Simulation trong hard limit.
- Theo dõi realtime RPS, client, connection, latency, request thành công/thất bại.
- Xem trạng thái phòng thủ, request đáng ngờ, request bị chặn và lý do chặn.
- Dừng simulation đang chạy mà không cần đóng web app.

Nếu cần mở dashboard cho một máy khác trong private LAN, bind đúng IP private của máy chạy
project (không dùng `0.0.0.0`):

```powershell
python main.py web --host 192.168.1.10 --port 8080
```

Web API chỉ nhận client từ localhost/RFC1918, chỉ nhận JSON nhỏ và tái sử dụng toàn bộ validation,
rate limit cũng như giới hạn thời gian của CLI.
