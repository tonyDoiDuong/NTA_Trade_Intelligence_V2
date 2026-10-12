# AGENTS.md — NTA Trade Intelligence V2

Hướng dẫn chung cho mọi AI agent (Codex, Claude Code, …) làm việc trong dự án này.
Đọc hết file này trước khi sửa bất cứ thứ gì. Cập nhật mục **Tiến độ** khi kết thúc
mỗi phiên làm việc để agent sau nối tiếp được.

## Dự án là gì

Hệ thống phân tích XAUUSD M5 **chỉ phát tín hiệu (SIGNAL ONLY)**, không đặt lệnh:

```
MT5 (Vantage, chart XAUUSD M5)
  └─ EA mt5/NTA_Bridge_Test.mq5  --HTTP POST /api/mt5/tick (2 giây/lần)-->
       Bridge FastAPI bridge/server.py (127.0.0.1:8000)
         ├─ SQLite bridge/nta_data.sqlite3   (DỮ LIỆU THẬT — không được xóa)
         ├─ Phase 1: analytics.py, market_engine.py (Fractal, BOS/CHOCH, Sweep, FVG, ATR, Volume)
         ├─ Phase 2: signal_engine.py (Model A/B/C), signal_registry.py, risk_engine.py,
         │           scenario_engine.py, signal_schema.py
         └─ Dashboard web/index.html (GET /, /api/dashboard, WebSocket)
```

API: `GET /api/health`, `GET /api/dashboard?symbol=XAUUSD&timeframe=M5`,
`POST /api/mt5/tick`, `GET /api/signals/{id}`.

## Luật an toàn vận hành (bắt buộc)

1. **Không thêm khả năng giao dịch thật.** Cấm `OrderSend`, `CTrade`, `#include <Trade/...>`
   trong EA; backend giữ `execution_enabled=false`, `live_trading=false`.
2. **Không dừng/restart bridge cổng 8000** trong giờ thị trường mở nếu người dùng chưa đồng ý.
   Đó là instance vận hành, khởi động bằng `start_dashboard.bat`.
3. **Không xóa, ghi đè hay migrate phá hủy `bridge/nta_data.sqlite3`.** Trước mọi thay đổi
   code hoặc schema, sao lưu vào `backups/<mô_tả>_<YYYYMMDD_HHMMSS>/` (dùng SQLite backup API
   hoặc copy khi bridge không ghi). Chỉ thêm bảng/cột theo kiểu không phá hủy.
4. **Test trên DB bản sao + cổng khác**: đặt `NTA_DB_PATH` trỏ tới file tạm, chạy uvicorn ở
   cổng 8011+ (smoke test dùng 8014). **Tắt server test khi xong** — phiên Codex trước đã bỏ
   sót 3 server ở 8011–8013.
5. Chỉ thay EA trên chart MT5 khi thị trường đóng; người dùng tự chép và gắn EA.
6. Không phát số liệu giả: Win Rate, Sample Size, Expectancy, Lot, RiskMoney giữ `N/A` cho
   đến khi có backtest / dữ liệu hợp đồng đã xác minh.
7. Không triển khai đồng loạt các phương pháp còn lại; mỗi model mới phải qua test chống
   look-ahead (chỉ dùng nến đã đóng, pivot xác nhận trễ, fill ở tick tương lai).

## Lệnh thường dùng (PowerShell, tại `C:\NTA_Trade_Intelligence_V2`)

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v     # kỳ vọng: 45 tests OK
.venv\Scripts\python.exe -m compileall bridge tests            # kiểm tra cú pháp
.venv\Scripts\python.exe tests\audit_live_db.py                # audit DB thật, read-only
Invoke-RestMethod http://127.0.0.1:8000/api/health             # phase=2, version=0.5.0
```

Compile EA (MetaEditor là trình biên dịch duy nhất; phải dùng `Start-Process -Wait`, exit
code vô nghĩa, chỉ tin dòng `Result:` trong log):

```powershell
Start-Process "C:\Program Files\MetaTrader 5 IC Markets Global\MetaEditor64.exe" `
  -ArgumentList '/compile:"C:\NTA_Trade_Intelligence_V2\mt5\NTA_Bridge_Test.mq5"','/log:"C:\NTA_Trade_Intelligence_V2\mt5\compile.log"' -Wait
Get-Content C:\NTA_Trade_Intelligence_V2\mt5\compile.log | Select-Object -Last 3
```

Khi đổi logic, tăng `#property version` của EA và `version` của FastAPI app trong `server.py`.

## Quy ước

- Tài liệu, báo cáo, input EA mới: **tiếng Việt** (tên input không dấu PascalCase, mô tả có
  dấu). Không đổi tên input đã có.
- Báo cáo nghiệm thu mỗi phase: `docs/PHASEn_REPORT.md` (ma trận PASS/FAIL/NOT TESTED kèm
  bằng chứng). Hướng dẫn người dùng: `docs/HUONG_DAN.txt`.
- Dự án có git (từ 2026-10-12), remote public `https://github.com/tonyDoiDuong/NTA_Trade_Intelligence_V2`.
  `.gitignore` loại `*.sqlite3`, `backups/`, `.venv/`, `*.ex5`, `*.log` — DB thật **không** nằm
  trong git, nên luật sao lưu `backups/` ở trên vẫn bắt buộc. Repo public: không commit dữ liệu tài khoản.

## Tiến độ (cập nhật cuối mỗi phiên)

**2026-10-11 — trạng thái bàn giao**

- Phase 1 + Phase 2 xong phần code: 45/45 test, EA v1.200 compile 0 lỗi/0 cảnh báo,
  bridge 0.5.0 đang chạy ở cổng 8000. Chi tiết: `docs/PHASE2_REPORT.md`.
- Codex hết token khi đang **nghiệm thu live** (đã backup DB vào
  `backups/pre_live_acceptance_20261011_215210`). Thị trường đóng cửa (Chủ nhật) nên dữ liệu
  stale, hệ thống trả WAIT đúng thiết kế.

**2026-10-11 23:00 — Claude Code chạy LIVE ACCEPTANCE GATE (prompt gốc:
`~/.codex/attachments/b9cba36b-…/Pasted text.txt`) khi thị trường đóng**

- Kết quả: `docs/LIVE_ACCEPTANCE_REPORT.md`. Phase 1 tính lại độc lập khớp 100% dữ liệu thật;
  45/45 test; visual QA bằng Edge headless.
- Đã sửa `web/index.html`: null hiển thị `0.00` → `—`; tràn ngang ở màn hình ≤760 px.
- Phát hiện: chart MT5 vẫn chạy EA cũ (`ea_version=bridge-0.4`); 3 bridge test cũ còn ở
  cổng 8011–8013 (chưa tắt, chờ người dùng).

**2026-10-12 08:15 — Claude Code nghiệm thu live (thị trường mở)**

- Đưa dự án lên git + GitHub (commit đầu `2485c32`).
- `docs/LIVE_ACCEPTANCE_REPORT.md` mục 7: MT5→FastAPI PASS (`bridge-0.5`, trễ ≤1.9 s), WebSocket
  PASS, Signal Registry PASS với signal thật MODEL_B (BLOCKED do R:R < 2, đúng luật). Bridge
  8000 không bị restart; cổng 8011–8013 đã sạch.

**Việc tiếp theo** (mục 1 bên dưới đã xong: EA 1.200 đang chạy trên chart)

1. 23:12 đã chép EA 1.200 (.mq5/.ex5, MD5 khớp) vào `MQL5\Experts\` của Vantage
   (terminal 725B72F2…; backup bản 1.100 ở `backups/pre_ea_1200_deploy_20261011_231218`).
   Người dùng còn phải gắn lại EA lên chart XAUUSD (log cho thấy chart đang là M30) và xác
   nhận dòng "NTA Bridge Phase 2 signal-only started" trong tab Experts.
2. Còn lại: reconnect test (cần người dùng cho phép dừng bridge 8000) và người dùng đối chiếu
   10 nến ở mục 7 báo cáo với MT5. Các mục NOT TESTED khác trong `docs/LIVE_ACCEPTANCE_REPORT.md`
   (LIVE ≤10 s, `ea_version=bridge-0.5`, reconnect, đối chiếu 10 nến với MT5, signal lifecycle)
   và cập nhật báo cáo đó.
3. Chỉ khi LIVE acceptance PASS mới bàn Phase 3: backtest/event replay chống look-ahead, lịch
   giao dịch broker, dữ liệu hợp đồng để tính lot.
