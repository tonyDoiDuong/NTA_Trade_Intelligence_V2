# Báo cáo nghiệm thu Phase 1 — NTA Trade Intelligence V2.0

Ngày kiểm tra: 2026-10-11 (Asia/Saigon)  
Phạm vi: Market Structure Engine, Volume Intelligence, luồng Bridge và Dashboard.  
Kết luận: **đạt nghiệm thu tự động; chưa nghiệm thu live XAUUSD**. Không chuyển Phase 2 cho đến khi hoàn tất checklist live ở cuối báo cáo.

## 1. Ma trận nghiệm thu

| Module / tiêu chí | Trạng thái | Bằng chứng |
|---|---|---|
| Backup trước sửa | PASS | backups/pre_phase1_20261011 và backups/pre_phase1_acceptance_20261011; bản sau gồm SQLite backup nhất quán. |
| EA thu thập 300–350 nến M5 đã đóng | PASS (code) | CopyRates shift=1, mặc định 350, từ chối dưới 300; không lấy nến shift 0 vào history. |
| Biên dịch MQL5 | PASS | MetaEditor64 thực chạy ngày 2026-10-11: Result 0 errors, 0 warnings; tạo mt5/NTA_Bridge_Test.ex5. |
| Không đặt lệnh thật | PASS | EA không import trade library, không có OrderSend/CTrade; API có execution_enabled=false. |
| FastAPI validation | PASS | Bắt OHLC lỗi, spread sai, timestamp lệch, count ngoài 300–350, duplicate, sai thứ tự, timestamp không thẳng M5 và nến chưa đóng. |
| SQLite không xóa dữ liệu cũ | PASS | Đã bỏ lệnh retention DELETE; migration giữ bảng m5_bars_legacy_no_broker và copy sang schema mới; test migration bảo toàn số tick/bar. |
| Phân vùng broker–symbol–timeframe | PASS | Khóa mới: broker_key, symbol, timeframe, bar_time; cache và truy vấn dùng cùng khóa. Test có hai broker XAUUSD cùng timestamp, mỗi stream giữ đủ 320 bar. |
| BTCUSD không đi vào XAUUSD | PASS | Dashboard/API/WebSocket mặc định khóa XAUUSD; test seed BTC legacy vẫn trả snapshot XAU rỗng, không lấy BTC thay thế. |
| SQLite thật hiện hữu | PASS (audit read-only) | PRAGMA quick_check=ok; 709 tick: XAUUSD 48, BTCUSD 661; 0 OHLC M5; không sửa/xóa file thật trong audit. |
| Timestamp legacy | PASS (nhận diện giới hạn) | Cả 709 payload cũ không có broker ID/UTC offset; được gắn UNKNOWN_LEGACY, không tự suy đoán UTC. |
| Timestamp payload mới | PASS (logic) | Lưu broker epoch, offset, UTC quy đổi và offset_basis=CURRENT_OFFSET_AT_RECEIPT; unit test xác nhận explicit/legacy status. |
| Swing High/Low fractal 2–2 | PASS | Strict pivot; confirmed_at là nến phải thứ hai. Test cả pivot hợp lệ và equal-high bị loại. |
| BOS/CHOCH | PASS | Chỉ close của nến đã đóng vượt swing đã được xác nhận; một close phá nhiều mức không phát BOS cũ trễ. Test append-only chống look-ahead. |
| Liquidity Sweep + Rejection | PASS | Wick vượt swing đã xác nhận, close trở lại bên trong và nến đúng hướng; level đã bị close phá không còn là sweep candidate. |
| FVG 3 nến | PASS | Bullish: low nến 3 lớn hơn high nến 1; bearish đối xứng; trạng thái OPEN/RETESTED/FILLED chỉ cập nhật từ nến đóng sau đó. |
| ATR14 | PASS | Wilder ATR, khởi tạo bằng mean 14 true ranges; fixture range cố định cho kết quả 2.0. |
| RVOL20 | PASS | Volume hiện tại / mean 20 nến đóng trước; test normal, zero baseline và boundary. |
| Volume Spike / Dry-up | PASS | Spike RVOL20 từ 1.8, Dry-up đến 0.6; khi thiếu baseline trả N/A, không trả false giả. |
| Session RVOL / divergence / effort-result | PASS | Có sample guard, swing đã xác nhận và true-range comparison; nguồn luôn là MT5_TICK_VOLUME. |
| Bốn scenario Dashboard | PASS (API/source) | BUY, SELL, POI Retest, Breakout/Reversal; mỗi card có method, đạt/chưa đạt, WATCH/DETECTED, tick-volume và giải thích tiếng Việt. |
| JavaScript Dashboard | PASS (syntax/contract) | Node syntax check PASS; source contract test PASS; lỗi fetch/WS hiển thị và reconnect backoff tối đa 10 giây. |
| HTTP → SQLite → analytics → WebSocket | PASS (DB tạm) | Smoke test Uvicorn thật: POST 320 bar, GET snapshot, nhận phase1 qua WebSocket. Fixture được gắn SYNTHETIC_TEST_ONLY. |
| Python automated tests | PASS | 23/23 test bằng unittest sau thay đổi cuối. |
| Render trực quan trên browser | NOT TESTED | IAB và Chrome automation không có browser surface trong phiên này; không tuyên bố pixel-level QA. |
| Live MT5 XAUUSD → Dashboard với 300–350 bar thật | NOT TESTED | SQLite thật hiện có 0 OHLC M5 và EA mới chưa được gắn lại vào MT5; bắt buộc người dùng chạy checklist Windows. |
| Đúng UTC lịch sử qua lần đổi DST | NOT TESTED | EA gửi offset hiện tại cho snapshot 300–350 bar. Nếu cửa sổ cắt qua lúc broker đổi DST, cần calendar broker/per-bar offset để chứng minh tuyệt đối. |

## 2. Dữ liệu SQLite cũ

Audit dùng kết nối mode=ro, không chạy migration trên file thật:

- Integrity: ok.
- ticks: 709 bản ghi.
- XAUUSD M5: 48 tick legacy.
- BTCUSD M5: 661 tick legacy.
- m5_bars: 0.
- Broker/company/server: không có trong 709 payload.
- UTC offset: không có trong 709 payload.
- Market timestamps có dạng integer ở 709/709 dòng, nhưng không thể chứng minh là UTC do thiếu offset/broker metadata.

Khi Bridge 0.4 khởi động sau khi tắt tiến trình cũ:

1. ticks được bổ sung cột phân vùng và backfill UNKNOWN_LEGACY; payload JSON gốc không bị sửa.
2. Nếu có bảng M5 schema cũ, bảng được đổi tên thành m5_bars_legacy_no_broker, không drop.
3. Bảng mới dùng broker key; dữ liệu cũ được copy dưới UNKNOWN_LEGACY.
4. Không còn cơ chế tự xóa tick. Cần thiết kế archive riêng nếu database tăng lớn.

## 3. Luồng đã xác nhận và giới hạn

Luồng test tích hợp đã xác nhận:

Synthetic XAUUSD M5 320 closed bars → FastAPI validation → SQLite partition → Phase 1 analytics → HTTP Dashboard snapshot → WebSocket subscriber XAUUSD

Luồng production cần xác nhận thủ công:

MT5 XAUUSD M5 thật → NTA_Bridge_Test.ex5 → http://127.0.0.1:8000 → nta_data.sqlite3 → WebSocket XAUUSD → Dashboard

Không dùng dữ liệu synthetic để kết luận trạng thái thị trường, chất lượng setup, win rate hay expectancy.

## 4. Kiểm soát repaint / backtest bias

- Analytics chỉ nhận history từ MT5 shift 1 trở lên.
- Pivot 2–2 chỉ tồn tại tại confirmed_at, không backdate tín hiệu xác nhận về bar pivot.
- BOS/CHOCH chỉ dùng close sau khi swing đã confirmed.
- Sweep không dùng swing chưa confirmed và không tái sử dụng liquidity level đã bị close phá.
- FVG hình thành ở close nến thứ ba; retest/fill chỉ xét các bar sau.
- SQLite bar đã đóng là immutable. Nếu broker gửi giá trị khác cho cùng khóa, dữ liệu gốc giữ nguyên và revision được ghi vào m5_bar_conflicts.
- Test append future bars đảm bảo event BOS cũ không thay đổi. Trạng thái FVG có thể tiến triển OPEN → RETESTED → FILLED theo dữ liệu mới; đó là state evolution, không phải sửa lịch sử detection.

## 5. Rủi ro còn lại

- 709 tick legacy không có broker/offset nên không được dùng để xác nhận UTC hoặc phân tích XAU Phase 1.
- Offset lịch sử đang lấy từ offset broker tại thời điểm gửi; cần kiểm tra riêng nếu cửa sổ 350 bar đi qua DST.
- Không xóa tick theo yêu cầu bảo toàn dữ liệu, vì vậy cần theo dõi dung lượng và xây chính sách archive có phê duyệt.
- Dashboard chưa được QA trực quan bằng browser automation trong phiên này.
- Chưa có dữ liệu live XAUUSD 300–350 nến; toàn bộ PASS thuật toán hiện dựa trên fixture xác định và integration DB tạm.
- WATCH/DETECTED chỉ là quan sát Phase 1. Không phải recommendation MARKET/LIMIT và không có Entry/SL/TP/R.

## 6. Gate trước Phase 2

Chỉ mở Phase 2 sau khi tất cả điều kiện sau PASS trên máy Windows:

1. API health trả version 0.4.0.
2. Dashboard chỉ hiển thị XAUUSD, đúng broker/server.
3. market_structure.bar_count nằm trong 300–350.
4. Bar mới nhất đúng nến M5 vừa đóng trong MT5, không phải nến đang chạy.
5. tests/audit_live_db.py cho thấy XAUUSD được nhóm đúng broker/symbol/M5 và BTCUSD vẫn tồn tại riêng.
6. Bốn scenario hiển thị đủ trường và chỉ dùng WATCH/DETECTED.
7. Đối chiếu thủ công tối thiểu 10 bar OHLC + tick volume với Data Window/History Center MT5.
8. Không có error 422, database conflict bất thường hoặc time offset sai.

Phase 2 **chưa được triển khai** trong thay đổi này.
