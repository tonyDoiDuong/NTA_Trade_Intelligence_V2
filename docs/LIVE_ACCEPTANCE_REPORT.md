# Báo cáo LIVE ACCEPTANCE GATE — Phase 1 + Phase 2 Models A–C

Ngày kiểm tra: 2026-10-11, 22:45–23:00 (Asia/Saigon) — **Chủ nhật, thị trường XAUUSD đóng cửa**.
Người thực hiện: Claude Code (tiếp nối phiên Codex dừng sau bước preflight lúc 21:52).
Kết luận: **PASS các phần kiểm chứng được offline; các mục cần feed live là NOT TESTED do
thị trường đóng. Chưa đủ điều kiện mở Phase 3.**

## 1. Ma trận nghiệm thu

| # | Mục | Trạng thái | Bằng chứng |
|---|---|---|---|
| 1 | Bridge version và kết nối | PASS | Chỉ một listener cổng 8000 (PID 29580/30140, `uvicorn bridge.server:app`). `/api/health`: `phase=2`, `version=0.5.0`. `/api/dashboard`: `signal_engine=PHASE2_MODELS_A_B_C`, `execution_enabled=false`, `mode=SIGNAL_ONLY`. |
| 2 | MT5 → FastAPI | NOT TESTED (live) | Snapshot cuối nhận lúc `2026-10-11T05:47:32Z`; thị trường đóng nên EA không gửi tick mới. **Snapshot cuối có `ea_version=bridge-0.4`** → chart MT5 đang chạy EA cũ, chưa phải bản 1.200 (xem mục 3). |
| 3 | SQLite | PASS | `PRAGMA quick_check=ok`; 7 bảng; 729 tick giữ nguyên; DB không có lần ghi nào sau 21:46; backup nhất quán `backups/pre_live_acceptance_20261011_215210`. |
| 4 | WebSocket | PASS (automated) / NOT TESTED (live) | `tests/test_integration.py` 4/4 OK (HTTP→SQLite→engines→WS trên DB tạm). Chưa quan sát push WS với feed thật. |
| 5 | Dashboard navigation | PASS | 5 link sidebar `#signalRegistry #phase1 #phase2 #riskEngine #backtest` đều có đúng 1 phần tử đích. |
| 6 | 300–350 nến M5 đóng | PASS | 350 nến, `newest_is_closed=True`, nến cuối 2026-10-09 20:50 UTC. |
| 7 | Timestamp/UTC/broker time | PASS | Trùng lặp 0; tăng dần; 100% trên grid 300 s; offset +10800 nhất quán ở mọi dòng (`bar_time − bar_time_utc`). Một khoảng trống 65 phút 2026-10-08 20:55→22:00 UTC = giờ nghỉ hằng ngày của vàng, không phải mất dữ liệu. Giờ VN hiển thị 03:56:59 10/10 = 20:56:59 UTC 09/10 → đúng. |
| 8 | Phân vùng BTCUSD/XAUUSD | PASS | `m5_bars` chỉ có stream `Vantage…|VantageMarkets-Live 15 / XAUUSD / M5`; 680 tick BTCUSD legacy vẫn còn, không được dùng. |
| 9 | Fractal 2–2 | PASS | Tính lại độc lập: swing high 4197.27 @1791588000 (xác nhận 1791588600), swing low 4194.65 @1791588900 (xác nhận 1791589500), sequence LH/HL — khớp API 100%. |
| 10 | BOS/CHOCH | PASS | 10 sự kiện gần nhất + last event: close vượt level đúng chiều, level xác nhận trước nến sự kiện, close khớp OHLC — 0 sai lệch. |
| 11 | Liquidity Sweep | PASS | 10 sự kiện: wick vượt level, close quay lại, level đã xác nhận trước — 0 sai lệch. |
| 12 | FVG | PASS | 12 sự kiện kiểm lại từ nến 1/3 thô (biên lower/upper) — 0 sai lệch. |
| 13 | ATR14 | PASS | Wilder ATR tự tính 1.873564 = API 1.873564. |
| 14 | RVOL20 và Volume Intelligence | PASS | RVOL20 0.7615 = API; momentum 1.1372 = API; nhãn nguồn `MT5_TICK_VOLUME`. |
| 15 | Models A–C | PASS (WAIT đúng) | Không có setup tự nhiên trên dữ liệu đã đóng; Main Recommendation `WATCH/WAIT`, 4 scenario hiển thị, không tạo tín hiệu giả. |
| 16 | Signal Registry | PASS (automated) / NOT TESTED (live) | 0 signal được lưu; bảng hiển thị trạng thái rỗng rõ ràng. Lifecycle chỉ được kiểm qua test. |
| 17 | Entry Planner | PASS (automated) | Test Phase 2 OK; không có kế hoạch live để kiểm. |
| 18 | Risk Engine | PASS | `allocated_risk_pct=0`, `max_global_risk_pct=2.0`, `live_trading=false`. |
| 19 | Reconnect/stale/market-closed | PASS (stale/closed) / NOT TESTED (reconnect) | `connected=false`, badge "XAUUSD DATA STALE", mọi action `WAIT`. Reconnect cần EA đang gửi tick → chờ thị trường mở. |
| 20 | Không giao dịch thật | PASS | Quét source không có `OrderSend`/`CTrade`; API `execution_enabled=false`. |
| 21 | Python tests | PASS | `unittest discover`: 45/45 OK (chạy lại sau khi sửa UI); `compileall` exit 0; `audit_live_db.py` OK. |
| 22 | MQL5 compile | MQL5 NOT RECOMPILED — source unchanged | EA không bị sửa trong phiên này; lần compile gần nhất của Codex: 0 errors, 0 warnings. |
| 23 | Visual browser QA | PASS (sau sửa) | Edge headless, desktop 1440 px và mobile 390/360 px. Phát hiện và sửa 2 lỗi (mục 3). Sau sửa: `scrollWidth` = viewport, 0 phần tử tràn. |

## 2. File đã sửa

- `web/index.html` (backup: `backups/pre_ui_fix_20261011_224832/index.html`). Bridge phục vụ file
  này trực tiếp từ đĩa nên **không cần restart bridge**; người dùng nhấn Ctrl+F5.
- Tạo mới: `AGENTS.md`, `CLAUDE.md`, `docs/LIVE_ACCEPTANCE_REPORT.md`.

## 3. Lỗi phát hiện và nguyên nhân

1. **Entry/SL/TP hiển thị `0.00` khi chưa có setup** (API trả `null`). Nguyên nhân: hàm
   `number(n)=Number.isFinite(Number(n))` coi `null` là số vì `Number(null)===0`. Sửa: loại
   `null`/chuỗi rỗng → hiển thị `—`. Nguy cơ cũ: người xem tưởng có mức giá 0.00.
2. **Tràn ngang trên màn hình hẹp** (scrollWidth 432 px ở viewport 390 px). Nguyên nhân: cột
   grid `1fr` không co nhỏ hơn token dài không ngắt được (`MODEL_B_BOS_CONTINUATION_RETEST`).
   Sửa: `minmax(0,1fr)` cho `.scenarios/.metricrow/.pair` ở breakpoint ≤760 px và
   `min-width:0; overflow-wrap:anywhere` cho `.scenario-card`.
3. **EA trên chart là bản cũ** (`ea_version=bridge-0.4`), không phải 1.200. Không phải lỗi
   code — cần người dùng thay EA trên chart (làm khi thị trường đóng).
4. Ba bridge test cũ (code Phase 1) còn chạy ở cổng 8011–8013 do phiên Codex trước để lại;
   không ảnh hưởng luồng EA → 8000. Chưa tắt, chờ người dùng đồng ý.

## 4. Hạn chế còn lại

- Chưa nghiệm thu được: Bid/Ask cập nhật ≤10 s, spread live, reconnect khi tắt/bật bridge,
  đối chiếu 10 nến API với cửa sổ Data Window của MT5, vòng đời signal thật.
- Đối chiếu 10 nến hiện chỉ so được SQLite ↔ API (nguồn đều từ EA); phải so với MT5 bằng mắt.
- Hạn chế cũ từ Phase 2 vẫn còn: lịch nghỉ broker, offset DST theo từng nến, lot/RiskMoney N/A.

10 nến đóng gần nhất trong SQLite (giờ broker, UTC+3) để đối chiếu với MT5:

| Giờ broker | Open | High | Low | Close | Tick vol |
|---|---|---|---|---|---|
| 2026-10-09 23:05 | 4194.39 | 4195.15 | 4193.96 | 4194.68 | 923 |
| 2026-10-09 23:10 | 4194.67 | 4195.61 | 4194.28 | 4195.16 | 759 |
| 2026-10-09 23:15 | 4195.16 | 4196.17 | 4194.83 | 4196.15 | 649 |
| 2026-10-09 23:20 | 4196.16 | 4197.27 | 4195.65 | 4195.87 | 746 |
| 2026-10-09 23:25 | 4195.87 | 4196.50 | 4195.60 | 4196.29 | 690 |
| 2026-10-09 23:30 | 4196.26 | 4196.29 | 4195.53 | 4195.94 | 551 |
| 2026-10-09 23:35 | 4195.95 | 4196.02 | 4194.65 | 4195.67 | 629 |
| 2026-10-09 23:40 | 4195.68 | 4196.07 | 4195.30 | 4195.47 | 554 |
| 2026-10-09 23:45 | 4195.47 | 4195.87 | 4194.91 | 4195.22 | 736 |
| 2026-10-09 23:50 | 4195.21 | 4195.62 | 4193.84 | 4194.38 | 837 |

## 5. Các bước Windows cho người dùng

Ngay bây giờ (thị trường đóng):
1. Mở `http://127.0.0.1:8000`, nhấn **Ctrl+F5**: Entry/SL/TP phải hiện `—`, không còn `0.00`.
2. MT5 Vantage → chart XAUUSD M5 → gỡ EA cũ → gắn `NTA_Bridge_Test` bản 1.200 theo
   `docs/HUONG_DAN.txt` mục 1 và 4.
3. Mở chart XAUUSD M5 trong MT5, bật Data Window (Ctrl+D), so 10 nến ở bảng trên.

Khi thị trường mở (thường sáng thứ Hai giờ VN):
4. Dashboard phải chuyển LIVE trong 10 giây; health/latest phải có `ea_version=bridge-0.5`.
5. Kiểm tra reconnect: trong cửa sổ `start_dashboard.bat` nhấn Ctrl+C, chờ dashboard báo mất
   kết nối và action = WAIT; chạy lại `start_dashboard.bat`, dashboard phải tự kết nối lại.
6. Theo dõi đến khi có signal tự nhiên; mở `/api/signals/<SignalID>` kiểm tra transitions.

## 6. Kết luận

**Chưa đủ điều kiện mở Phase 3.** Toàn bộ logic Phase 1 tính lại độc lập khớp 100% với dữ
liệu thật, test và giao diện đạt; nhưng các mục 2, 4, 16, 19 (phần live) cần chạy lại khi thị
trường mở với EA 1.200.
