//+------------------------------------------------------------------+
//| NTA Bridge Phase 1 - diagnostics and closed M5 data, NO trading  |
//| Attach to the broker symbol; allow http://127.0.0.1:8000         |
//+------------------------------------------------------------------+
#property strict
#property version "1.200"

input string BridgeURL = "http://127.0.0.1:8000/api/mt5/tick";
input int SendIntervalSeconds = 2;
input bool PrintSuccessfulSends = false;
input bool SendOnlyFreshTicks = true;
input int SoNenM5DaDong = 350;             // Chỉ nhận 300-350 nến M5 đã đóng
input int ChuKyDongBoLichSuGiay = 60;      // Chu kỳ gửi lại lịch sử nến

long last_sent_tick_msc = 0;
ulong last_history_sync_ms = 0;
datetime last_history_bar_sent = 0;

//+------------------------------------------------------------------+
long BrokerUtcOffsetSeconds()
{
   long raw_offset=(long)(TimeTradeServer()-TimeGMT());
   // Broker offsets normally change in 15-minute increments. Rounding also
   // removes a possible one-second sampling difference between the two calls.
   return (long)MathRound((double)raw_offset/900.0)*900;
}

//+------------------------------------------------------------------+
string JsonEscape(string value)
{
   StringReplace(value,"\\","\\\\");
   StringReplace(value,"\"","\\\"");
   StringReplace(value,"\r","\\r");
   StringReplace(value,"\n","\\n");
   return value;
}

//+------------------------------------------------------------------+
string BuildClosedM5BarsJson(const int requested,int &copied_count)
{
   MqlRates bars[];
   ArraySetAsSeries(bars,true);
   copied_count=CopyRates(_Symbol,PERIOD_M5,1,requested,bars);
   if(copied_count<=0)
   {
      copied_count=0;
      return "[]";
   }

   string json="[";
   // With a series array, index 0 is the latest closed bar. Send oldest first.
   for(int i=copied_count-1;i>=0;i--)
   {
      if(i<copied_count-1) json+=",";
      json+=StringFormat(
         "{\"time\":%I64d,\"open\":%s,\"high\":%s,\"low\":%s,\"close\":%s,\"tick_volume\":%I64d,\"real_volume\":%I64d,\"spread\":%d}",
         (long)bars[i].time,
         DoubleToString(bars[i].open,_Digits),
         DoubleToString(bars[i].high,_Digits),
         DoubleToString(bars[i].low,_Digits),
         DoubleToString(bars[i].close,_Digits),
         (long)bars[i].tick_volume,
         (long)bars[i].real_volume,
         bars[i].spread
      );
   }
   json+="]";
   return json;
}

//+------------------------------------------------------------------+
int OnInit()
{
   if(SendIntervalSeconds<1 || SoNenM5DaDong<300 || SoNenM5DaDong>350 ||
      ChuKyDongBoLichSuGiay<30)
      return INIT_PARAMETERS_INCORRECT;
   EventSetTimer(SendIntervalSeconds);
   Print("NTA Bridge Phase 2 signal-only started. WebRequest allow URL: http://127.0.0.1:8000");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Network work stays on the timer. This EA never sends or modifies orders.
}

//+------------------------------------------------------------------+
void OnTimer()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<=0) return;
   if(SendOnlyFreshTicks && (tick.time_msc==0 || tick.time_msc<=last_sent_tick_msc)) return;

   MqlRates current[],closed[];
   if(CopyRates(_Symbol,PERIOD_M5,0,1,current)!=1 ||
      CopyRates(_Symbol,PERIOD_M5,1,1,closed)!=1)
      return;

   ulong now_ms=GetTickCount64();
   bool history_due=(last_history_sync_ms==0 ||
                     last_history_bar_sent!=closed[0].time ||
                     now_ms-last_history_sync_ms>=(ulong)ChuKyDongBoLichSuGiay*1000);
   string history_json="[]";
   int copied_bars=0;
   if(history_due)
   {
      history_json=BuildClosedM5BarsJson(SoNenM5DaDong,copied_bars);
      if(copied_bars<300) return;
   }

   double spread=tick.ask-tick.bid;
   long broker_offset=BrokerUtcOffsetSeconds();
   string broker=JsonEscape(AccountInfoString(ACCOUNT_COMPANY));
   string broker_server=JsonEscape(AccountInfoString(ACCOUNT_SERVER));
   // Symbol data comes from MT5 and the API validates its allowed character set.
   string json=StringFormat(
      "{\"source\":\"MT5\",\"symbol\":\"%s\",\"timeframe\":\"M5\",\"broker\":\"%s\",\"broker_server\":\"%s\",\"bid\":%s,\"ask\":%s,\"spread\":%s,\"tick_volume_current\":%I64d,\"tick_volume_closed\":%I64d,\"last_closed_bar\":%I64d,\"broker_time\":%I64d,\"broker_time_msc\":%I64d,\"broker_utc_offset_seconds\":%I64d,\"ea_version\":\"bridge-0.5\",\"m5_bars\":%s}",
      _Symbol,
      broker,
      broker_server,
      DoubleToString(tick.bid,_Digits),
      DoubleToString(tick.ask,_Digits),
      DoubleToString(spread,_Digits),
      (long)current[0].tick_volume,
      (long)closed[0].tick_volume,
      (long)closed[0].time,
      (long)tick.time,
      (long)tick.time_msc,
      broker_offset,
      history_json
   );

   char body[],result[];
   string response_headers;
   int bytes=StringToCharArray(json,body,0,StringLen(json),CP_UTF8);
   if(bytes<1) return;
   string headers="Content-Type: application/json\r\n";
   ResetLastError();
   int http_status=WebRequest("POST",BridgeURL,headers,5000,body,result,response_headers);
   if(http_status!=200)
   {
      PrintFormat("NTA Bridge send failed: HTTP=%d MQL5_error=%d response=%s",
                  http_status,GetLastError(),CharArrayToString(result));
      return;
   }

   last_sent_tick_msc=tick.time_msc;
   if(history_due)
   {
      last_history_sync_ms=now_ms;
      last_history_bar_sent=closed[0].time;
   }
   if(PrintSuccessfulSends)
      PrintFormat("NTA Bridge sent %s bid=%s closed_bars=%d",
                  _Symbol,DoubleToString(tick.bid,_Digits),copied_bars);
}
