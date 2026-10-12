# Báo cáo nghiệm thu Phase 2 — NTA Trade Intelligence V2.0

Ngày kiểm tra: 2026-10-11 (Asia/Saigon)  
Phạm vi: audit Phase 1, Models A–C, Signal Registry, Entry/Risk Planner, Dashboard.  
Kết luận: **PASS phần code, automated test, compile và smoke integration; dữ liệu XAU thật đạt chất lượng nhưng đang stale nên hệ thống giữ WAIT.** Không triển khai 17 phương pháp còn lại.

## 1. Ma trận nghiệm thu

| Module / tiêu chí | Trạng thái | Bằng chứng |
|---|---|---|
| Backup trước Phase 2 | PASS | `backups/pre_phase2_20261011`; SQLite được sao lưu nhất quán, không xóa dữ liệu. |
| Phase 1 regression | PASS | Fractal 2–2, BOS/CHOCH close-only, Sweep, FVG 3 nến, ATR14, RVOL20, spike/dry-up đều qua test. |
| SQLite integrity | PASS | `PRAGMA quick_check=ok`; 729 tick được giữ nguyên khi audit read-only. |
| Phân vùng dữ liệu | PASS | Khóa broker + symbol + timeframe + bar_time; BTCUSD legacy không được chọn cho dashboard XAUUSD. |
| XAUUSD M5 thực tế | PASS (quality) | VantageMarkets-Live 15: 350 bar, OHLC lỗi 0, order/grid lỗi 0, UTC mismatch 0, newest closed=true. |
| Độ tươi dữ liệu thật | FAIL tại thời điểm audit | Snapshot cuối cũ hơn 30.000 giây; API trên bản sao dữ liệu trả `connected=false`, Main `WATCH/WAIT`, cả 4 scenario `WAIT`. Đây là khóa an toàn đúng. |
| Model A Sweep + Rejection | PASS | BUY/SELL đối xứng; depth 0.05–0.75 ATR, wick >=40%, close location >=60%; MARKET dùng Ask/Bid live và chỉ fill ở tick tương lai. |
| Model B BOS Continuation + Retest | PASS | Bias + close BOS + displacement range >=1.2 ATR/body >=60%; chỉ LIMIT ở BOS/FVG zone, không đuổi MARKET. |
| Model C MSS + FVG + OTE | PASS | CHOCH dùng làm MSS close-confirmed; impulse neo tại swing đã xác nhận và event bar; OTE 61.8–79% phải giao FVG; LIMIT midpoint. |
| Anti-repaint / no future data | PASS | Test pivot delayed confirmation, closed-only BOS/MSS/FVG, OTE cố định khi thêm future high, snapshot append-only và forward-tick fill. |
| Status machine | PASS | WATCH → DETECTED → CONFIRMED → READY → ARMED → virtual/terminal; transition sai bị reject; READY thiếu field bị reject. |
| Signal Registry | PASS | SignalID UUID không trùng; transition/snapshot append-only; dedup theo stream/method/idea/confirmation. |
| Entry Planner | PASS | WAIT/LIMIT/MARKET; STOP chỉ có trong schema, chưa kích hoạt; Entry/SL/TP không null khi READY/ARMED. |
| Risk Engine | PASS | MinimumRR=2, suggested=0.5%, per setup/global cap=2%; duplicate idea và tổng risk bị block; spread/stale/closed gates. |
| Spread recheck | PASS | High spread block action trên dashboard và không virtual-fill setup ARMED. |
| Lot/RiskMoney | PASS (N/A đúng quy tắc) | Không giả định account/contract; cả hai giữ N/A cho đến khi có dữ liệu broker đã xác minh. |
| Volume per setup | PASS | RVOL20, session RVOL, momentum, spike/dry-up, effort/result, divergence; ghi rõ MT5 tick volume; CONFIRM/WARNING/NEUTRAL. |
| Bốn scenario | PASS | BEST BUY, BEST SELL, POI RETEST, BREAKOUT/REVERSAL; fallback WATCH/WAIT, không tạo tín hiệu giả. |
| Dashboard source contract | PASS | Main Recommendation và toàn bộ trường yêu cầu hiện diện; API text dùng `textContent`; XAUUSD khóa cứng; reconnect backoff. |
| Render trực quan browser | NOT TESTED | Môi trường automation trả `apps=[]`, `browsers=[]`; không tuyên bố pixel-level QA. |
| HTTP → SQLite → engines → HTTP/WS | PASS | Uvicorn thật trên DB test-only: POST 320 bar → SQLite → Phase 1/2 → GET → WebSocket. |
| Python tests | PASS | 45/45 tests, `unittest`, sau thay đổi cuối. |
| Python syntax | PASS | `compileall bridge tests` exit 0. |
| MQL5 compile | PASS | MetaEditor64 thực chạy: 0 errors, 0 warnings, 650 ms; tạo EA version 1.200. |
| Không giao dịch thật | PASS | Không có OrderSend/CTrade/Buy/Sell execution; `execution_enabled=false`, `live_trading=false`. |
| Win Rate/Expectancy | PASS (N/A) | Không phát số liệu giả; dashboard và signal giữ N/A. |

## 2. Kết quả dữ liệu thật

- 729 tick: 680 BTCUSD legacy, 48 XAUUSD legacy và 1 XAUUSD có broker metadata.
- 728 payload legacy không có broker/UTC offset; tiếp tục được giữ dưới phạm vi legacy, không dùng để suy đoán timezone.
- Stream OHLC dùng cho phân tích: `Vantage Markets (Pty) Ltd|VantageMarkets-Live 15 / XAUUSD / M5`.
- 350 nến, từ `1791481500` đến `1791589800`; timestamps nằm đúng grid M5 và quy đổi UTC nhất quán với offset +10800 tại lúc nhận.
- Audit không xóa/sửa file thật. Test migration và Phase 2 production-like được chạy trên bản sao SQLite.
- Snapshot đã stale tại thời điểm audit, vì vậy không thể nghiệm thu trạng thái LIVE hoặc một setup thật đang READY; hệ thống đã trả WAIT đúng thiết kế.

## 3. Logic ba setup

**Model A — Liquidity Sweep + Rejection:** chỉ xét sweep ở nến đóng mới nhất, dùng swing fractal 2–2 đã xác nhận. Độ sâu, tỷ lệ wick và vị trí close đều định lượng. Entry MARKET dùng quote hiện tại; TP phải là mục tiêu cấu trúc đạt tối thiểu 2R sau spread. Thiếu target hoặc risk gate không đạt thì WAIT/BLOCKED.

**Model B — BOS Continuation + Retest:** cần bias cùng hướng, BOS bằng close và breakout displacement. Entry luôn là LIMIT tại FVG liên quan hoặc vùng BOS; SL ở swing bảo vệ, TP ở cấu trúc đạt tối thiểu 2R. Không phát MARKET khi giá đã chạy xa.

**Model C — MSS + FVG + OTE:** dùng CHOCH close-confirmed làm MSS. Impulse được cố định bằng swing gốc đã xác nhận và extreme của event bar, không cập nhật bằng future high/low. FVG phải giao vùng OTE 61.8–79%; LIMIT ở midpoint phần giao, SL ngoài swing gốc, TP theo cấu trúc.

## 4. File thay đổi / tạo mới

- Backend: `bridge/analytics.py`, `market_engine.py`, `scenario_engine.py`, `server.py`, `signal_schema.py`, `signal_engine.py`, `signal_registry.py`, `risk_engine.py`.
- EA: `mt5/NTA_Bridge_Test.mq5`, `NTA_Bridge_Test.ex5`, `compile_phase2.log`.
- Dashboard: `web/index.html`.
- Test: `tests/fixtures_phase2.py`, `test_phase2.py`, `test_analytics.py`, `test_validation.py`, `test_integration.py`, `audit_live_db.py`, `smoke_running_bridge.py`.
- Tài liệu: `docs/HUONG_DAN.txt`, `docs/PHASE2_REPORT.md`.

## 5. Hạn chế còn lại

1. Chưa có browser surface để visual QA; người dùng cần mở dashboard và kiểm tra responsive/render.
2. Feed XAU hiện stale, nên nghiệm thu live reconnect và một setup thị trường thật vẫn chưa hoàn tất.
3. Market-open tự động mới dùng weekday UTC; giờ nghỉ hằng ngày, holiday và maintenance riêng của broker chưa có calendar.
4. Offset lịch sử dùng offset hiện tại lúc nhận snapshot; cửa sổ cắt qua lần đổi DST cần per-bar offset/calendar broker để chứng minh tuyệt đối.
5. Chưa có dữ liệu account/contract đã xác minh để tính lot hoặc RiskMoney.
6. Chưa có backtest/walk-forward; Win Rate, Sample Size và Expectancy phải tiếp tục là N/A.

## 6. Đề xuất Phase 3 — chưa triển khai

Chỉ bắt đầu sau khi hoàn thành checklist live trong `HUONG_DAN.txt`: thu một chu kỳ reconnect thật, đối chiếu 10 bar với MT5, quan sát ít nhất một signal lifecycle và xác minh lịch giao dịch broker. Sau đó ưu tiên framework backtest/event replay chống look-ahead và hiệu chỉnh broker contract/calendar trước khi thêm từng model tiếp theo. Không nên triển khai đồng loạt 17 phương pháp.
