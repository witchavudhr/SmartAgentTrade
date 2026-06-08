//+------------------------------------------------------------------+
//|                                         SmartPartialClose.mq5    |
//|  v4 - Added KeepBest, Breakeven, Trailing Stop (Staircase)       |
//+------------------------------------------------------------------+
#property copyright "Custom EA"
#property version   "4.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade         trade;
CPositionInfo  posInfo;

input ulong  InpMagicNumber = 0;  // Magic Number (0 = all positions)

//--- Panel layout (X, Y เป็น global เพื่อให้ drag ย้ายได้)
int    PANEL_X = 15;
int    PANEL_Y = 32;
#define PANEL_W      340
#define PANEL_ROWS   15
#define ROW_H        26
#define FEAT_W       108   // feature label column width
#define INPUT_W      182   // input area width
#define TOG_W        46    // toggle button width
#define MINI_E       56    // mini edit box width
#define FONT_SIZE    9
#define FONT_SMALL   8
#define PREFIX       "SPC_"
#define CORNER       CORNER_LEFT_LOWER
#define INPUT_AREA_X (PANEL_X+FEAT_W)           // 123 — where input area starts
#define TOGGLE_X     (PANEL_X+FEAT_W+INPUT_W)   // 305 — where toggle button sits

//--- ── Section 1: Auto TP ─────────────────────────────────────────
bool   g_AutoSetTP         = false;
double g_TP_Ticks          = 10000.0;

//--- ── Section 2: Partial Close ───────────────────────────────────
bool   g_EnablePartial        = false;
double g_PartialClosePct      = 50.0;
double g_PartialTriggerTicks  = 1000.0; // ปิดบางส่วนเมื่อกำไร X ticks จาก open

//--- ── Section 3: Profit Target (shared TP) ───────────────────────
bool   g_EnableProfitTarget = false;
double g_TotalProfitTarget  = 200.0;

//--- ── Section 3b: Total SL ($) ───────────────────────────────────
bool   g_EnableTotalSL  = false;
double g_TotalSLAmount  = 100.0;  // ขาดทุนรวมสูงสุด $

//--- ── Section 3c: Profit Trail (High Water Mark) ─────────────────
// ติดตาม peak profit → ถ้ากำไรหล่นลงมาถึง floor$ → ปิดหมด
bool   g_EnableProfitTrail = false;
double g_ProfitArmAt       = 200.0;  // เริ่ม protect เมื่อกำไรถึงระดับนี้ก่อน
double g_ProfitFloor       = 0.0;    // ถ้าหล่นมาถึงนี้หลัง arm แล้ว → close all
double g_PeakProfit        = 0.0;    // high water mark

//--- ── Section 4: Keep Best N Positions ───────────────────────────
bool   g_EnableKeepBest    = false;
int    g_KeepBestN         = 2;

//--- ── Section 4c: Pair Close ─────────────────────────────────────
// จับคู่ worst+best ทุกไม้ close ที่กำไร $X ต่อคู่ จนเหลือ N
bool   g_EnablePairClose   = false;
double g_PairCloseTarget   = 10.0;

//--- ── Section 4d: Pair Guard ─────────────────────────────────────
// ปกป้อง best N → จับคู่เฉพาะไม้ที่เหลือ close ที่กำไร $X ต่อคู่
bool   g_EnablePairGuard   = false;
double g_PairGuardTarget   = 10.0;

//--- ── Section 4e: Per-Position Breakeven Guard ────────────────────
// เมื่อแต่ละไม้กำไรถึง trigger$ → ย้าย SL มาหน้าทุน (open + lockTicks)
// ทำงานเฉพาะตอน KeepBest / PairClose / PairGuard เปิดอยู่
// ถ้าไม้โดน SL ออกไป → feature อื่นจะ recalculate TP อัตโนมัติ tick ถัดไป
bool   g_EnablePosGuard    = false;
double g_PosGuardTrigger   = 0.0;   // กำไร $ ที่ trigger (0 = disable)
double g_PosGuardLockTicks = 10.0;  // lock ticks เหนือ open (initial BE)
double g_PosGuardStepTicks = 0.0;   // trailing step ticks หลัง lock (0 = ไม่ trail)

//--- POS TARGET: set TP ของ best N ไม้ รวมกำไรได้ tar$
bool   g_EnablePosTarget   = false;
int    g_PosTargetN        = 5;
double g_PosTargetDollar   = 1000.0;

//--- ── Section 4b: DCA (ถัวเฉลี่ย ใช้เฉพาะตอน KeepBest ON) ─────
bool   g_EnableDCA         = false;
double g_DCALotSize        = 0.01;   // lot ต่อไม้ (base)
double g_DCAStepTicks      = 500.0;  // เปิดไม้ใหม่ทุก X ticks
int    g_DCAMaxPositions   = 10;     // เปิดได้ไม่เกิน X ไม้เพิ่ม
int    g_DCATierSize       = 3;      // จำนวนไม้ต่อชั้น (0 = ปิด tier — ใช้ base lot ทุกไม้)
double g_DCALotStep        = 0.01;   // เพิ่ม lot ทุกชั้น (0 = ปิด tier)

//--- DCA state
int    g_DCAOpenedCount    = 0;      // จำนวนไม้ DCA ที่เปิดไปแล้ว
double g_DCALastPrice      = 0.0;    // ราคาที่เปิดไม้ DCA ล่าสุด
int    g_DCADirection      = 0;      // 1=BUY, -1=SELL (ตาม net direction ของ worst)

//--- ── Section 5: Breakeven SL (กันทุน) ──────────────────────────
// เมื่อราคาวิ่งกำไร X ticks แล้วย้อนกลับ → SL จะจับไว้ไม่ให้ขาดทุน
// โดยเลื่อน SL มาที่ open + lockTicks (กำไรน้อยๆ หรือ 0 = ทุน)
bool   g_EnableBreakeven   = false;
double g_BETriggerTicks    = 1000.0;  // ราคาต้องกำไรกี่ tick ก่อน → ถึงจะย้าย SL
double g_BELockTicks       = 20.0;    // SL ใหม่ = open + X ticks (0 = ทุนพอดี)

//--- ── Section 6: Trailing Stop (Staircase) ───────────────────────
bool   g_EnableTrailing    = false;
double g_TrailStepTicks    = 500.0;   // ทุก X ticks ให้เลื่อน SL

//--- State tracking
ulong  g_partialDoneTickets[];    // tickets ที่ปิดถึง 0.01 แล้ว (stop)
ulong  g_partialTrackTickets[];   // tickets ที่ tracking ระดับราคาปิดล่าสุด
double g_partialLastPrice[];      // ราคาที่ partial close ครั้งล่าสุด (parallel array)
ulong  g_tpSetTickets[];
ulong  g_beSetTickets[];
ulong  g_trailTickets[];
double g_trailHighTicks[];

//--- KeepBest monitor (beTP price + net direction)
double g_keepBestBeTP    = 0;
double g_keepBestNetLots = 0;

//--- Panel drag state
bool g_isDragging      = false;
int  g_dragOffsetX     = 0;
int  g_dragOffsetY     = 0;

//--- KeepBest state
ulong  g_keepWorstTickets[];   // tickets ของ worst positions ที่รอ breakeven
bool   g_keepBestPending    = false;  // true = กำลังรอ worst positions ปิด
int    g_keepBestTotalCount = 0;      // จำนวน positions ทั้งหมดตอนที่ pending set (ไว้ detect ไม้ใหม่)

//--- Close All confirm state
bool     g_closeAllArmed    = false;
datetime g_closeAllArmedAt  = 0;
#define  CLOSEALL_TIMEOUT   3          // วินาที ก่อน reset กลับถ้าไม่กด confirm

//+------------------------------------------------------------------+
int OnInit()
{
   //--- nuke any leftover objects จาก template เก่า / version ก่อนหน้า
   ObjectsDeleteAll(0, PREFIX);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(10);
   ArrayResize(g_partialDoneTickets, 0);
   ArrayResize(g_partialTrackTickets, 0);
   ArrayResize(g_partialLastPrice, 0);
   ArrayResize(g_tpSetTickets, 0);
   ArrayResize(g_beSetTickets, 0);
   ArrayResize(g_trailTickets, 0);
   ArrayResize(g_trailHighTicks, 0);
   ArrayResize(g_keepWorstTickets, 0);
   g_keepBestPending = false;

   LoadSettings();   // โหลดค่าทุกอย่างจาก GlobalVariable ก่อนสร้าง panel
   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, true);  // enable mouse drag

   CreatePanel();
   DrawSessionLines();

   //--- ถ้า DCA เปิดอยู่จากครั้งก่อน → init state จาก positions ปัจจุบัน
   if(g_EnableDCA) InitDCAState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   SaveSettings();
   DeletePanel();
   Comment("");   // clear debug comment ที่อาจค้างอยู่
   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, false);
}

//+------------------------------------------------------------------+
void ResetCloseAllBtn()
{
   g_closeAllArmed  = false;
   g_closeAllArmedAt = 0;
   string btn = PREFIX+"BTN_CLOSEALL";
   if(ObjectFind(0,btn)<0) return;
   ObjectSetString (0,btn,OBJPROP_TEXT,        "✕  CLOSE ALL");
   ObjectSetInteger(0,btn,OBJPROP_BGCOLOR,      C'160,20,20');
   ObjectSetInteger(0,btn,OBJPROP_BORDER_COLOR, C'200,50,50');
   ObjectSetInteger(0,btn,OBJPROP_STATE,        false);
   ChartRedraw();
}

void OnTick()
{
   UpdateBELine();          // เส้น BE รวม
   UpdateSessionBar();      // session + bar countdown
   UpdatePositionLabels();  // P&L label ของแต่ละไม้
   UpdateFeatureLines();    // เส้น trigger ของ BE/Trail/Partial

   //--- timeout confirm close all
   if(g_closeAllArmed && TimeCurrent()-g_closeAllArmedAt >= CLOSEALL_TIMEOUT)
      ResetCloseAllBtn();

   //--- DCA ทำงานเป็นอิสระ (ไม่ต้องรอ KeepBest pending)
   if(g_EnableDCA) CheckDCA();

   //--- KeepBest ทำงานก่อนเสมอ
   if(g_EnableKeepBest)
   {
      CheckKeepBest();

      //--- Monitor: ถ้าราคาถึง beTP → close worst positions at market
      if(g_keepBestPending && g_keepBestBeTP > 0)
      {
         string s   = Symbol();
         double bid = SymbolInfoDouble(s, SYMBOL_BID);
         double ask = SymbolInfoDouble(s, SYMBOL_ASK);
         bool hit = (g_keepBestNetLots > 0 && bid >= g_keepBestBeTP)
                 || (g_keepBestNetLots < 0 && ask <= g_keepBestBeTP);
         if(hit)
         {
            Print("KeepBest | beTP=", g_keepBestBeTP, " reached → closing worst at market");
            for(int k=ArraySize(g_keepWorstTickets)-1; k>=0; k--)
               if(posInfo.SelectByTicket(g_keepWorstTickets[k]))
                  trade.PositionClose(g_keepWorstTickets[k]);
         }
      }

      //--- ถ้ายังมี worst positions ค้างอยู่ → block feature อื่น แต่ยัง run ProfitTarget สำหรับ best N
      if(g_keepBestPending && WorstPositionsStillOpen())
      {
         UpdateKeepBestStatus();
         if(g_EnableProfitTarget) ApplyProfitTargetTP();  // self-filters to best N only
         if(g_EnableProfitTrail)  CheckProfitTrail();     // ทำงานตลอด ไม่รอ pending
         return;
      }

      //--- worst positions ปิดหมดแล้ว → reset flag + BE tracking
      if(g_keepBestPending)
      {
         g_keepBestPending    = false;
         g_keepBestTotalCount = 0;
         ArrayResize(g_beSetTickets, 0);   // ให้ combined BE คำนวณใหม่สำหรับ N ที่เหลือ
         ArrayResize(g_trailTickets, 0);   // reset trailing ด้วย (เริ่มนับใหม่จาก BE)
         ArrayResize(g_trailHighTicks, 0);
         Print("KeepBest: all worst positions closed → activating other features");
      }
   }

   //--- อัปเดต status bar ทุก tick
   UpdateKeepBestStatus();

   //--- Feature อื่นๆ ทำงานเฉพาะหลังจาก worst positions ปิดหมดแล้ว
   if(g_EnablePairClose)     CheckPairClose();
   if(g_EnablePairGuard)     CheckPairGuard();
   //--- Per-position BE guard: ทำงานเฉพาะตอนมี pair/keep feature เปิดอยู่
   if(g_EnablePosGuard && g_PosGuardTrigger > 0
      && (g_EnableKeepBest || g_EnablePairClose || g_EnablePairGuard))
      CheckPerPosGuard();
   //--- POS TARGET: set/show TP รวมของ best N ไม้ (ทำงานทุก tick เพื่ออัปเดตเส้น)
   ApplyPosTargetTP();
   //--- ใช้ ProfitTarget เป็นตัวจัดการ TP เพียงตัวเดียว (ปิด AutoTP)
   if(g_EnableProfitTarget) ApplyProfitTargetTP();
   if(g_EnablePartial)       CheckPartialClose();
   if(g_EnableTotalSL)       ApplyTotalSL();
   if(g_EnableProfitTrail)   CheckProfitTrail();
   if(g_EnableBreakeven)     CheckBreakeven();
   if(g_EnableTrailing)      CheckTrailingStop();
}

//+------------------------------------------------------------------+
//--- ลบ TP ทุกไม้ (เรียกตอน feature ที่ set TP ถูก toggle off)
void ClearTPAll()
{
   string sym = Symbol();
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      if(posInfo.TakeProfit()==0.0) continue;
      trade.PositionModify(posInfo.Ticket(), posInfo.StopLoss(), 0.0);
   }
}
//--- ลบ SL ทุกไม้
void ClearSLAll()
{
   string sym = Symbol();
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      if(posInfo.StopLoss()==0.0) continue;
      trade.PositionModify(posInfo.Ticket(), 0.0, posInfo.TakeProfit());
   }
}
//--- ลบ TP เฉพาะ tickets ที่ระบุ
void ClearTPTickets(ulong &tix[])
{
   for(int k=0; k<ArraySize(tix); k++)
   {
      if(!posInfo.SelectByTicket(tix[k])) continue;
      if(posInfo.TakeProfit()==0.0) continue;
      trade.PositionModify(tix[k], posInfo.StopLoss(), 0.0);
   }
}
//--- ลบ SL เฉพาะ tickets ที่ระบุ
void ClearSLTickets(ulong &tix[])
{
   for(int k=0; k<ArraySize(tix); k++)
   {
      if(!posInfo.SelectByTicket(tix[k])) continue;
      if(posInfo.StopLoss()==0.0) continue;
      trade.PositionModify(tix[k], 0.0, posInfo.TakeProfit());
   }
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   //--- Redraw session labels เมื่อ chart zoom/scroll
   if(id == CHARTEVENT_CHART_CHANGE)
      DrawSessionLines();

   //--- Panel drag: ใช้ mouse + title bar
   if(id == CHARTEVENT_MOUSE_MOVE)
   {
      int    mx    = (int)lparam;
      int    my    = (int)dparam;
      bool   lDown = (StringToInteger(sparam) & 1) != 0;

      int chartH = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
      int yFromBottom = chartH - my;

      //--- title bar bounds
      int titleY  = PANEL_Y + (PANEL_ROWS-1) * ROW_H;
      int titleX1 = PANEL_X - 5;
      int titleX2 = PANEL_X + PANEL_W - 115;
      int titleY1 = titleY + 1;
      int titleY2 = titleY + ROW_H;

      if(lDown && !g_isDragging)
      {
         if(mx >= titleX1 && mx <= titleX2
            && yFromBottom >= titleY1 && yFromBottom <= titleY2)
         {
            g_isDragging  = true;
            g_dragOffsetX = mx - PANEL_X;
            g_dragOffsetY = yFromBottom - PANEL_Y;
         }
      }
      else if(lDown && g_isDragging)
      {
         int newX = mx - g_dragOffsetX;
         int newY = yFromBottom - g_dragOffsetY;
         if(newX < 0) newX = 0;
         if(newY < 0) newY = 0;
         if(MathAbs(newX - PANEL_X) >= 3 || MathAbs(newY - PANEL_Y) >= 3)
         {
            PANEL_X = newX;
            PANEL_Y = newY;
            DeletePanelOnly();
            CreatePanel();
            ChartRedraw();
         }
      }
      else if(!lDown && g_isDragging)
      {
         g_isDragging = false;
         SaveSettings();
      }
   }


   if(id == CHARTEVENT_OBJECT_ENDEDIT)
   {
      ReadPanelValues();
      SaveSettings();
      UpdateFeatureLines();
      ChartRedraw();

      //--- ถ้าแก้ตัวเลข N ของ KeepBest → reset pending เพื่อ recalculate TP ใหม่ทันที
      if(sparam == PREFIX+"EDT_KN" && g_EnableKeepBest)
      {
         g_keepBestPending    = false;
         g_keepBestTotalCount = 0;
         ArrayResize(g_keepWorstTickets, 0);
         ArrayResize(g_tpSetTickets, 0);
         Print("KeepBest: N changed to ", g_KeepBestN, " → recalculating...");
         if(g_EnableKeepBest) CheckKeepBest();
         if(g_EnableProfitTarget) ApplyProfitTargetTP();
      }
   }

   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      if(sparam == PREFIX+"TOG_PARTIAL")  { g_EnablePartial      = !g_EnablePartial;      RefreshToggle(sparam, g_EnablePartial); }
      if(sparam == PREFIX+"TOG_PROFTGT")  { g_EnableProfitTarget = !g_EnableProfitTarget; RefreshToggle(sparam, g_EnableProfitTarget);
                                            if(!g_EnableProfitTarget) ClearTPAll(); }
      if(sparam == PREFIX+"TOG_KEEPBEST")
      {
         g_EnableKeepBest = !g_EnableKeepBest;
         RefreshToggle(sparam, g_EnableKeepBest);
         //--- Exclusive + RESET state ตอนเปิด → บังคับ recalculate
         if(g_EnableKeepBest)
         {
            if(g_EnablePairClose) { ClearTPAll(); g_EnablePairClose=false; RefreshToggle(PREFIX+"TOG_PAIRCLOSE",false); }
            if(g_EnablePairGuard) { ClearTPAll(); g_EnablePairGuard=false; RefreshToggle(PREFIX+"TOG_PAIRGUARD",false); }
            //--- Reset state เผื่อค้างจาก session ก่อน
            g_keepBestPending    = false;
            g_keepBestTotalCount = 0;
            g_keepBestBeTP       = 0;
            g_keepBestNetLots    = 0;
            ArrayResize(g_keepWorstTickets, 0);
            ClearTPAll();   // ล้าง TP ทุกไม้ก่อน → ให้ KeepBest set ใหม่หมด
            Print("KeepBest ON: state reset, will recalculate next tick");
         }
         //--- ปิด KeepBest → reset state + คำนวณ TP ใหม่
         if(!g_EnableKeepBest)
         {
            ClearTPTickets(g_keepWorstTickets);   // ลบ beTP ออกจาก worst positions
            g_keepBestPending    = false;
            g_keepBestTotalCount = 0;
            ArrayResize(g_keepWorstTickets, 0);
            ArrayResize(g_tpSetTickets, 0);
            g_DCAOpenedCount = 0; g_DCALastPrice = 0; g_DCADirection = 0;
            Print("KeepBest OFF: state reset");

            //--- ถ้า Profit Target เปิดอยู่ → คำนวณ TP ใหม่ทันที
            if(g_EnableProfitTarget)
            {
               ApplyProfitTargetTP();
               Print("KeepBest OFF: recalculated TP from Profit Target");
            }
            //--- ถ้า AutoTP เปิดอยู่ → จะ set TP ใหม่ใน next tick อัตโนมัติ
         }
      }
      if(sparam == PREFIX+"TOG_PAIRCLOSE")
      {
         g_EnablePairClose = !g_EnablePairClose;
         RefreshToggle(sparam, g_EnablePairClose);
         if(g_EnablePairClose)
         {  //--- Exclusive: ปิด KeepBest + PairGuard
            if(g_EnableKeepBest) { ClearTPTickets(g_keepWorstTickets); g_keepBestPending=false; g_keepBestTotalCount=0; ArrayResize(g_keepWorstTickets,0); g_EnableKeepBest=false; RefreshToggle(PREFIX+"TOG_KEEPBEST",false); }
            if(g_EnablePairGuard){ ClearTPAll(); g_EnablePairGuard=false; RefreshToggle(PREFIX+"TOG_PAIRGUARD",false); }
         }
         else ClearTPAll();
      }
      if(sparam == PREFIX+"TOG_PAIRGUARD")
      {
         g_EnablePairGuard = !g_EnablePairGuard;
         RefreshToggle(sparam, g_EnablePairGuard);
         if(g_EnablePairGuard)
         {  //--- Exclusive: ปิด KeepBest + PairClose
            if(g_EnableKeepBest)  { ClearTPTickets(g_keepWorstTickets); g_keepBestPending=false; g_keepBestTotalCount=0; ArrayResize(g_keepWorstTickets,0); g_EnableKeepBest=false; RefreshToggle(PREFIX+"TOG_KEEPBEST",false); }
            if(g_EnablePairClose) { ClearTPAll(); g_EnablePairClose=false; RefreshToggle(PREFIX+"TOG_PAIRCLOSE",false); }
         }
         else ClearTPAll();
      }
      if(sparam == PREFIX+"TOG_POSTGT")
      {
         g_EnablePosTarget = !g_EnablePosTarget;
         RefreshToggle(sparam, g_EnablePosTarget);
         if(!g_EnablePosTarget) ClearTPAll();   // ลบ TP ที่ set ไว้ตอนปิด feature
         SaveSettings(); UpdateFeatureLines(); ChartRedraw();
      }
      if(sparam == PREFIX+"TOG_POSGUARD")
      {
         g_EnablePosGuard = !g_EnablePosGuard;
         RefreshToggle(sparam, g_EnablePosGuard);
         if(!g_EnablePosGuard) ClearSLAll();   // ลบ SL ที่ set ไว้ตอนปิด feature
      }
      if(sparam == PREFIX+"TOG_PROFTRAIL") { g_EnableProfitTrail  = !g_EnableProfitTrail;
                                            RefreshToggle(sparam, g_EnableProfitTrail);
                                            if(!g_EnableProfitTrail) { g_PeakProfit = 0.0; ClearSLAll(); } }
      if(sparam == PREFIX+"TOG_BE")       { g_EnableBreakeven    = !g_EnableBreakeven;    RefreshToggle(sparam, g_EnableBreakeven);
                                            if(!g_EnableBreakeven) { ClearSLTickets(g_beSetTickets); ArrayResize(g_beSetTickets,0); } }
      if(sparam == PREFIX+"TOG_TRAIL")    { g_EnableTrailing     = !g_EnableTrailing;     RefreshToggle(sparam, g_EnableTrailing);
                                            if(!g_EnableTrailing) { ClearSLTickets(g_trailTickets); ArrayResize(g_trailTickets,0); ArrayResize(g_trailHighTicks,0); } }
      if(sparam == PREFIX+"TOG_DCA")
      {
         g_EnableDCA = !g_EnableDCA;
         RefreshToggle(sparam, g_EnableDCA);
         if(!g_EnableDCA) { g_DCAOpenedCount=0; g_DCALastPrice=0; g_DCADirection=0; }
         else             InitDCAState();
      }
      if(sparam == PREFIX+"TOG_TOTALSL")  { g_EnableTotalSL      = !g_EnableTotalSL;      RefreshToggle(sparam, g_EnableTotalSL);
                                            if(g_EnableTotalSL) ApplyTotalSL(); else ClearSLAll(); }
      if(sparam == PREFIX+"BTN_CLOSEALL")
      {
         if(!g_closeAllArmed)
         {
            //--- คลิกแรก: arm และเปลี่ยนสี/ข้อความ
            g_closeAllArmed   = true;
            g_closeAllArmedAt = TimeCurrent();
            ObjectSetString (0,sparam,OBJPROP_TEXT,    "✓ CONFIRM?");
            ObjectSetInteger(0,sparam,OBJPROP_BGCOLOR,  C'200,110,0');
            ObjectSetInteger(0,sparam,OBJPROP_BORDER_COLOR, C'255,160,0');
         }
         else
         {
            //--- คลิกสอง: execute
            CloseAllPositions();
            ResetCloseAllBtn();
         }
         ObjectSetInteger(0,sparam,OBJPROP_STATE,false);
      }

      ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
      SaveSettings();   // persist toggle change
      ChartRedraw();
   }
}

//+------------------------------------------------------------------+
//| FEATURE 1 — Auto TP                                              |
//+------------------------------------------------------------------+
void CheckAndSetTP()
{
   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;

      ulong ticket = posInfo.Ticket();
      if(IsInArray(g_tpSetTickets, ticket)) continue;
      if(posInfo.TakeProfit() != 0.0) continue;

      double tpDist  = g_TP_Ticks * tickSize;
      double tpPrice = (posInfo.PositionType() == POSITION_TYPE_BUY)
                       ? NormalizeDouble(posInfo.PriceOpen() + tpDist, digits)
                       : NormalizeDouble(posInfo.PriceOpen() - tpDist, digits);

      if(trade.PositionModify(ticket, posInfo.StopLoss(), tpPrice))
      {
         Print("AutoTP | Ticket:", ticket, " TP:", tpPrice, " (", g_TP_Ticks, " ticks)");
         Append(g_tpSetTickets, ticket);
      }
   }
}

//+------------------------------------------------------------------+
//| FEATURE 2 — Partial Close                                        |
//+------------------------------------------------------------------+
//--- Partial Close (Combined) — trigger จาก combined gain, ปิด % ของแต่ละไม้
//--- ใช้ g_partialLastPrice[0] เป็น "global last combined price" (slot 0 ของ tracking arrays)
void CheckPartialClose()
{
   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double minLot   = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double keepLot  = MathMax(0.01, minLot);
   if(tickSize == 0) return;

   double bePrice, netLots, cur, curTicks;
   if(!ComputeCombinedBE(bePrice, netLots, cur, curTicks)) return;

   //--- หา refPrice: ราคาปิดครั้งล่าสุด (combined), ถ้ายังไม่เคย → ใช้ bePrice
   //--- ใช้ ticket = 0 เป็น slot global
   double refPrice = bePrice;
   int    globalIdx = -1;
   for(int k=0; k<ArraySize(g_partialTrackTickets); k++)
      if(g_partialTrackTickets[k]==0){ globalIdx=k; refPrice=g_partialLastPrice[k]; break; }

   //--- trigger: ราคาวิ่งเพิ่มอีก triggerTicks จาก refPrice (ทิศ net)
   double triggerD = g_PartialTriggerTicks * tickSize;
   bool   hit = (netLots > 0 && cur >= refPrice + triggerD)
             || (netLots < 0 && cur <= refPrice - triggerD);
   if(!hit) return;

   //--- ปิด % ของแต่ละไม้ที่ยังมี lot > keepLot
   bool anyClose = false;
   int  closedCount = 0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      ulong  tk     = posInfo.Ticket();
      double curVol = posInfo.Volume();
      //--- skip ถ้าเหลือ ≤ keepLot
      if(curVol <= keepLot + 0.0001) continue;

      double closeLots = NormVol(sym, curVol * g_PartialClosePct / 100.0);
      //--- ถ้าปิดแล้วเหลือ < keepLot → ปรับให้เหลือ keepLot พอดี
      if(curVol - closeLots < keepLot - 0.0001)
         closeLots = NormVol(sym, curVol - keepLot);
      if(closeLots < minLot) continue;

      if(trade.PositionClosePartial(tk, closeLots))
      {
         Print("PartialClose | Ticket:", tk,
               " | Closed:", closeLots, " | Remain:", curVol-closeLots);
         anyClose = true;
         closedCount++;
      }
   }

   if(anyClose)
   {
      //--- update global refPrice = cur (ราคาที่ trigger ครั้งนี้)
      if(globalIdx >= 0)
         g_partialLastPrice[globalIdx] = cur;
      else
      {
         int n = ArraySize(g_partialTrackTickets);
         ArrayResize(g_partialTrackTickets, n+1);
         ArrayResize(g_partialLastPrice,    n+1);
         g_partialTrackTickets[n] = 0;     // slot global
         g_partialLastPrice[n]    = cur;
      }
      Print("PartialClose Combined | trigger price=", cur,
            " | refPrice=", refPrice, " | closed ", closedCount, " positions");
   }
}

//+------------------------------------------------------------------+
//| FEATURE 3 — Shared TP for Total Profit Target                    |
//| ถ้า KeepBest ON → คำนวณจากเฉพาะ N ไม้ที่ดีที่สุด               |
//+------------------------------------------------------------------+
void ApplyProfitTargetTP()
{
   string sym         = Symbol();
   double tickValue   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize    = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits      = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point       = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickSize==0 || tickValue==0) return;
   double vpp = tickValue / tickSize; // value per point

   ulong tickets[]; double lots[]; double opens[]; int types[]; double sls[];
   int count    = CollectPositions(sym, tickets, lots, opens, types, sls);
   int countAll = count;   // เก็บจำนวนจริงทั้งหมดไว้ใช้ใน log
   if(count == 0) return;

   //=================================================================
   //  N-SELECTION FILTER: กรอง positions ให้เหลือเฉพาะไม้ที่จะ "remain"
   //  KeepBest  → top N ที่ดีที่สุด
   //  PairGuard → top N ที่ดีที่สุด (ปกป้องไว้)
   //  PairClose → middle N (ไม้กลาง หลังจากปิด best+worst pairs)
   //=================================================================
   bool needFilter = g_EnableKeepBest || g_EnablePairGuard || g_EnablePairClose;
   if(needFilter && count > g_KeepBestN)
   {
      //--- PairClose ต้อง sort ตาม open price (เหมือน CheckPairClose) เพื่อหา middle N ได้ถูก
      //--- KeepBest/PairGuard ใช้ P&L ปกติ
      double sortKey[];
      ArrayResize(sortKey, count);
      if(g_EnablePairClose)
      {
         double netDir=0;
         for(int j=0;j<count;j++) netDir+=(types[j]==POSITION_TYPE_BUY?lots[j]:-lots[j]);
         double ds=(netDir>=0)?-1.0:1.0;
         for(int j=0;j<count;j++) sortKey[j]=ds*opens[j];
      }
      else
      {
         for(int j=0;j<count;j++)
            sortKey[j]=posInfo.SelectByTicket(tickets[j])?posInfo.Profit()+posInfo.Swap():-DBL_MAX;
      }
      int sortIdx[];
      SortByProfit(sortKey, sortIdx, count);  // ascending: [0]=worst, [count-1]=best

      ulong  fTix[]; double fLts[], fOpn[], fSls[]; int fTyp[];
      int filtered = 0;

      if(g_EnableKeepBest && g_keepBestPending && ArraySize(g_keepWorstTickets) > 0)
      {
         //--- KeepBest pending: exclude worst tickets
         for(int j=0; j<count; j++)
         {
            if(IsInArray(g_keepWorstTickets, tickets[j])) continue;
            ArrayResize(fTix,filtered+1); ArrayResize(fLts,filtered+1);
            ArrayResize(fOpn,filtered+1); ArrayResize(fSls,filtered+1); ArrayResize(fTyp,filtered+1);
            fTix[filtered]=tickets[j]; fLts[filtered]=lots[j];
            fOpn[filtered]=opens[j];   fTyp[filtered]=types[j]; fSls[filtered]=sls[j];
            filtered++;
         }
         if(filtered == 0) return;
      }
      else
      {
         int startIdx, endIdx;
         if(g_EnablePairClose)
         {
            //--- PairClose: middle N (pairs ปิด global worst+best)
            //--- pairs ครอบที่ sortIdx[0..numPairs-1] + sortIdx[count-numPairs..count-1]
            //--- odd (ถ้ามี) = sortIdx[numPairs] = worst ของ middle
            //--- remaining N = sortIdx[numPairs+oddOffset..count-numPairs-1]
            int closeCount = count - g_KeepBestN;
            int nPairs     = closeCount / 2;
            int oddOffset  = closeCount % 2;
            startIdx = nPairs + oddOffset;
            endIdx   = count - nPairs;
         }
         else
         {
            //--- KeepBest / PairGuard: top N (best)
            startIdx = count - g_KeepBestN;
            endIdx   = count;
         }
         filtered = endIdx - startIdx;
         if(filtered <= 0) return;
         ArrayResize(fTix,filtered); ArrayResize(fLts,filtered);
         ArrayResize(fOpn,filtered); ArrayResize(fSls,filtered); ArrayResize(fTyp,filtered);
         for(int k=0; k<filtered; k++)
         {
            int idx = sortIdx[startIdx + k];
            fTix[k]=tickets[idx]; fLts[k]=lots[idx];
            fOpn[k]=opens[idx];   fTyp[k]=types[idx]; fSls[k]=sls[idx];
         }
      }

      ArrayCopy(tickets,fTix); ArrayCopy(lots,fLts);
      ArrayCopy(opens,fOpn);   ArrayCopy(types,fTyp); ArrayCopy(sls,fSls);
      count = filtered;
   }

   double buyLots=0, sellLots=0, buyW=0, sellW=0;
   for(int j=0;j<count;j++)
   {
      if(types[j]==POSITION_TYPE_BUY){ buyLots+=lots[j]; buyW+=opens[j]*lots[j]; }
      else                           { sellLots+=lots[j]; sellW+=opens[j]*lots[j]; }
   }
   double netLots = buyLots - sellLots;
   if(MathAbs(netLots) < 0.0001) return;

   double tpPrice = NormalizeDouble(
      (g_TotalProfitTarget / vpp + buyW - sellW) / netLots, digits);

   double bid = SymbolInfoDouble(sym,SYMBOL_BID), ask = SymbolInfoDouble(sym,SYMBOL_ASK);

   //--- ถ้า target ถึงแล้ว (TP ต่ำกว่าราคาปัจจุบัน) → ปิด N positions ทันที
   bool alreadyMet = (netLots > 0 && tpPrice <= ask) || (netLots < 0 && tpPrice >= bid);
   if(alreadyMet)
   {
      Print("ProfitTarget: target $", DoubleToString(g_TotalProfitTarget,2),
            " already met (TP=", tpPrice, ") → closing ", count, " positions at market");
      for(int j=0; j<count; j++) trade.PositionClose(tickets[j]);
      return;
   }

   //--- set TP บน N positions
   string filterInfo = (needFilter && countAll > count)
      ? StringFormat(" | scope: %d/%d pos", count, countAll) : "";
   for(int j=0;j<count;j++)
      if(posInfo.SelectByTicket(tickets[j]))
         if(MathAbs(posInfo.TakeProfit() - tpPrice) > point*2)
            if(trade.PositionModify(tickets[j], sls[j], tpPrice))
               Print("ProfitTarget | Ticket:", tickets[j], " | TP:", tpPrice, filterInfo);
}

//+------------------------------------------------------------------+
//| FEATURE 4 — Keep Best N Positions                                |
//+------------------------------------------------------------------+
//| Helper: sort positions by P&L ascending (worst first)           |
//+------------------------------------------------------------------+
void SortByProfit(double &profits[], int &sortIdx[], int count)
{
   ArrayResize(sortIdx, count);
   for(int i=0;i<count;i++) sortIdx[i]=i;
   for(int a=0;a<count-1;a++)
      for(int b=a+1;b<count;b++)
         if(profits[sortIdx[a]]>profits[sortIdx[b]])
         { int t=sortIdx[a]; sortIdx[a]=sortIdx[b]; sortIdx[b]=t; }
}

//--- คำนวณ TP ราคาที่คู่นี้มีกำไรรวม = target$
//--- คืน true ถ้าสำเร็จ, false ถ้า hedged หรือ already past target
bool PairTP(int idxA, int idxB,
            ulong &tickets[], double &lots[], double &opens[], int &types[], double &profits[],
            double target, double vpp, int digits,
            double bid, double ask, double &tpOut)
{
   double buyLots=0,sellLots=0,buyW=0,sellW=0;
   int pair[2]={idxA,idxB};
   for(int k=0;k<2;k++)
   {
      int i=pair[k];
      if(types[i]==POSITION_TYPE_BUY){ buyLots+=lots[i]; buyW+=opens[i]*lots[i]; }
      else                           { sellLots+=lots[i]; sellW+=opens[i]*lots[i]; }
   }
   double netLots=buyLots-sellLots;

   if(MathAbs(netLots)<0.0001)
   {  // hedged pair: P&L คงที่ → ตรวจตอนนี้เลย
      if(profits[idxA]+profits[idxB]>=target) tpOut=-1; // signal = close at market
      else tpOut=0;
      return false;
   }
   tpOut = NormalizeDouble((target/vpp+buyW-sellW)/netLots, digits);
   // validate direction
   if(netLots>0 && tpOut<=ask){ tpOut=-1; return false; } // already past → market close
   if(netLots<0 && tpOut>=bid){ tpOut=-1; return false; }
   return true;
}

//+------------------------------------------------------------------+
//| FEATURE: Pair Close                                              |
//| จับคู่ worst[i]+best[i] ทุกไม้ set TP รวม = target$ ต่อคู่     |
//| ทำซ้ำจนเหลือ N ไม้                                             |
//+------------------------------------------------------------------+
void CheckPairClose()
{
   string sym       = Symbol();
   double tickValue = SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   int    digits    = (int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym,SYMBOL_POINT);
   if(tickSize==0||tickValue==0) return;
   double vpp=tickValue/tickSize;

   ulong tickets[]; double lots[],opens[],sls[],profits[]; int types[];
   int count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0&&posInfo.Magic()!=InpMagicNumber) continue;
      ArrayResize(tickets,count+1);ArrayResize(lots,count+1);ArrayResize(opens,count+1);
      ArrayResize(types,count+1);ArrayResize(sls,count+1);ArrayResize(profits,count+1);
      tickets[count]=posInfo.Ticket();lots[count]=posInfo.Volume();
      opens[count]=posInfo.PriceOpen();types[count]=(int)posInfo.PositionType();
      sls[count]=posInfo.StopLoss();profits[count]=posInfo.Profit()+posInfo.Swap();
      count++;
   }
   if(count<=g_KeepBestN) return;

   //--- sort by open price: BUY net → highest open = worst (rank0), SELL net → lowest open = worst
   double netDir=0;
   for(int j=0;j<count;j++) netDir+=(types[j]==POSITION_TYPE_BUY?lots[j]:-lots[j]);
   double dirSign=(netDir>=0)?-1.0:1.0;  // BUY: -open → ascending ให้ highest open อยู่ index 0
   double sortKey[]; ArrayResize(sortKey,count);
   for(int j=0;j<count;j++) sortKey[j]=dirSign*opens[j];
   int sortIdx[]; SortByProfit(sortKey,sortIdx,count);
   //--- PairClose: จับคู่ global worst + global best → เหลือ middle N ไม้
   //--- pairs: (sortIdx[0], sortIdx[count-1]), (sortIdx[1], sortIdx[count-2]), ...

   double bid=SymbolInfoDouble(sym,SYMBOL_BID), ask=SymbolInfoDouble(sym,SYMBOL_ASK);
   int closeCount = count - g_KeepBestN;
   int numPairs   = closeCount / 2;

   for(int p=0;p<numPairs;p++)
   {
      int iA=sortIdx[p],           // p-th worst (global)
          iB=sortIdx[count-1-p];   // p-th best (global) → middle N คือ rank6 ที่เหลือ
      double tp; bool ok=PairTP(iA,iB,tickets,lots,opens,types,profits,g_PairCloseTarget,vpp,digits,bid,ask,tp);

      if(!ok)
      {  if(tp==-1){ trade.PositionClose(tickets[iA]); trade.PositionClose(tickets[iB]);
                     Print("PairClose | Pair[",p,"] market close | iA:",tickets[iA]," iB:",tickets[iB]); }
         continue;
      }
      int pair[2]={iA,iB};
      for(int k=0;k<2;k++)
      {
         if(!posInfo.SelectByTicket(tickets[pair[k]])) continue;
         if(MathAbs(posInfo.TakeProfit()-tp)>point*2)
            if(trade.PositionModify(tickets[pair[k]],sls[pair[k]],tp))
               Print("PairClose | Pair[",p,"] Ticket:",tickets[pair[k]]," TP:",tp," tgt:$",g_PairCloseTarget);
      }
   }
   //--- ไม้ odd: set soloTP ให้ปิดที่กำไร target$ แล้ว ApplyProfitTargetTP จะ set TP ให้ middle N
   int oddIdx = -1;
   if(closeCount%2==1 && numPairs>0)      oddIdx = sortIdx[numPairs];
   else if(closeCount==1 && numPairs==0)  oddIdx = sortIdx[0];
   if(oddIdx >= 0 && posInfo.SelectByTicket(tickets[oddIdx]))
   {
      double lotOdd = lots[oddIdx];
      if(lotOdd > 0)
      {
         double soloTP = (types[oddIdx]==POSITION_TYPE_BUY)
            ? NormalizeDouble(opens[oddIdx] + g_PairCloseTarget/(vpp*lotOdd), digits)
            : NormalizeDouble(opens[oddIdx] - g_PairCloseTarget/(vpp*lotOdd), digits);
         bool past = (types[oddIdx]==POSITION_TYPE_BUY) ? (soloTP<=ask) : (soloTP>=bid);
         if(past) { trade.PositionClose(tickets[oddIdx]); Print("PairClose | Odd close at market | Ticket:",tickets[oddIdx]); }
         else if(MathAbs(posInfo.TakeProfit()-soloTP)>point*2)
            if(trade.PositionModify(tickets[oddIdx],sls[oddIdx],soloTP))
               Print("PairClose | Odd soloTP | Ticket:",tickets[oddIdx]," TP:",soloTP);
      }
   }
}

//+------------------------------------------------------------------+
//| FEATURE: Pair Guard                                              |
//| ปกป้อง best N → จับคู่เฉพาะไม้นอก N                           |
//| Pair: worst[0]+best_of_unprotected[0], ...                      |
//+------------------------------------------------------------------+
void CheckPairGuard()
{
   string sym       = Symbol();
   double tickValue = SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   int    digits    = (int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym,SYMBOL_POINT);
   if(tickSize==0||tickValue==0) return;
   double vpp=tickValue/tickSize;

   ulong tickets[]; double lots[],opens[],sls[],profits[]; int types[];
   int count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0&&posInfo.Magic()!=InpMagicNumber) continue;
      ArrayResize(tickets,count+1);ArrayResize(lots,count+1);ArrayResize(opens,count+1);
      ArrayResize(types,count+1);ArrayResize(sls,count+1);ArrayResize(profits,count+1);
      tickets[count]=posInfo.Ticket();lots[count]=posInfo.Volume();
      opens[count]=posInfo.PriceOpen();types[count]=(int)posInfo.PositionType();
      sls[count]=posInfo.StopLoss();profits[count]=posInfo.Profit()+posInfo.Swap();
      count++;
   }

   int unprotected=count-g_KeepBestN;
   if(unprotected<=0) return;

   int sortIdx[]; SortByProfit(profits,sortIdx,count);
   // sortIdx[0..unprotected-1]  = unprotected (worst first)
   // sortIdx[unprotected..count-1] = protected best N (untouched)

   double bid=SymbolInfoDouble(sym,SYMBOL_BID), ask=SymbolInfoDouble(sym,SYMBOL_ASK);
   int numPairs=unprotected/2;

   for(int p=0;p<numPairs;p++)
   {
      int iA=sortIdx[p],              // p-th worst of unprotected
          iB=sortIdx[unprotected-1-p]; // p-th best of unprotected
      double tp; bool ok=PairTP(iA,iB,tickets,lots,opens,types,profits,g_PairGuardTarget,vpp,digits,bid,ask,tp);

      if(!ok)
      {  if(tp==-1){ trade.PositionClose(tickets[iA]); trade.PositionClose(tickets[iB]);
                     Print("PairGuard | Pair[",p,"] market close"); }
         continue;
      }
      int pair[2]={iA,iB};
      for(int k=0;k<2;k++)
      {
         if(!posInfo.SelectByTicket(tickets[pair[k]])) continue;
         if(MathAbs(posInfo.TakeProfit()-tp)>point*2)
            if(trade.PositionModify(tickets[pair[k]],sls[pair[k]],tp))
               Print("PairGuard | Pair[",p,"] Ticket:",tickets[pair[k]]," TP:",tp," tgt:$",g_PairGuardTarget);
      }
   }
   //--- ไม้ odd: ไม่ set TP ที่นี่ → ให้ ApplyProfitTargetTP จัดการแบบ shared TP แทน
}

//+------------------------------------------------------------------+
//| FEATURE: Per-Position Breakeven Guard                            |
//| เช็คแต่ละไม้แยก — ถ้ากำไรถึง trigger$ → set SL = open + lock  |
//| ทำงานเฉพาะตอน KeepBest / PairClose / PairGuard ON               |
//| ถ้าไม้โดน SL → feature อื่น recalculate TP อัตโนมัติ tick ถัดไป|
//+------------------------------------------------------------------+
void CheckPerPosGuard()
{
   string sym       = Symbol();
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   int    digits    = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickSize == 0 || tickValue == 0) return;

   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;

      double openPrice = posInfo.PriceOpen();
      bool   isBuy     = (posInfo.PositionType() == POSITION_TYPE_BUY);
      double existSL   = posInfo.StopLoss();
      double lots      = posInfo.Volume();

      //--- ใช้ raw price profit (ไม่รวม swap/commission) เพื่อ trigger ที่แม่นกว่า
      double priceDist  = isBuy ? (bid - openPrice) : (openPrice - ask);
      double rawProfit  = (priceDist / tickSize) * tickValue * lots;
      if(rawProfit < g_PosGuardTrigger) continue;   // ยังไม่ถึง trigger$

      //--- initialSL = open + lockTicks (กันทุน)
      double initialSL = isBuy
         ? NormalizeDouble(openPrice + g_PosGuardLockTicks * tickSize, digits)
         : NormalizeDouble(openPrice - g_PosGuardLockTicks * tickSize, digits);

      double guardSL = initialSL;   // default = set initial guard

      //--- Trailing staircase
      if(g_PosGuardStepTicks > 0)
      {
         double profitPerTick = tickValue * lots;
         double triggerTicks  = (profitPerTick > 0) ? g_PosGuardTrigger / profitPerTick : 0;
         double P0 = isBuy
            ? NormalizeDouble(openPrice + triggerTicks * tickSize, digits)
            : NormalizeDouble(openPrice - triggerTicks * tickSize, digits);

         double stepDist = g_PosGuardStepTicks * tickSize;

         //--- ใช้ bid ปัจจุบันหา step ที่ควรเป็น
         double distFromP0 = isBuy ? (bid - P0) : (P0 - ask);
         if(distFromP0 >= 0 && stepDist > 0)
         {
            int    n      = (int)MathFloor(distFromP0 / stepDist + 1e-9); // epsilon กันเศษ float
            double stepSL = isBuy
               ? NormalizeDouble(initialSL + n * stepDist, digits)
               : NormalizeDouble(initialSL - n * stepDist, digits);
            //--- DEBUG: log เมื่อ n >= 1 (ควรจะ step แล้ว)
            if(n >= 1)
               PrintFormat("PosGuard STAIR | tk:%d open:%.2f P0:%.2f bid:%.2f dist:%.2f n:%d stepSL:%.2f existSL:%.2f",
                  (int)posInfo.Ticket(), openPrice, P0, bid, distFromP0, n, stepSL, existSL);
            if(isBuy ? (stepSL > guardSL) : (stepSL < guardSL))
               guardSL = stepSL;
         }
         else if(distFromP0 < 0)
            PrintFormat("PosGuard BELOW_P0 | tk:%d bid:%.2f P0:%.2f rawProfit:%.2f",
               (int)posInfo.Ticket(), bid, P0, rawProfit);

         //--- high-water mark: ถ้า existSL อยู่สูงกว่า step ที่คำนวณได้ (จาก bid ปัจจุบัน)
         //--- ให้คง existSL ไว้ (ราคาเคยขึ้นสูงกว่า bid ปัจจุบัน → stair ไม่ถอยหลัง)
         if(isBuy  && existSL > guardSL) guardSL = existSL;
         if(!isBuy && existSL > 0 && existSL < guardSL) guardSL = existSL;
      }

      //--- เลื่อน SL เฉพาะทิศที่ protective มากขึ้น (ไม่ถอยหลัง)
      bool needMove = isBuy
         ? (existSL < guardSL - point)
         : (existSL == 0 || existSL > guardSL + point);

      if(!needMove) continue;

      //--- ตรวจว่า SL ไม่ชนราคาปัจจุบัน
      if(isBuy  && guardSL >= bid) continue;
      if(!isBuy && guardSL <= ask) continue;

      if(trade.PositionModify(posInfo.Ticket(), guardSL, posInfo.TakeProfit()))
         Print("PosGuard OK | tk:", posInfo.Ticket(),
               " open:", openPrice, " guardSL:", guardSL,
               " rawProfit:$", DoubleToString(rawProfit,2));
      else
         Print("PosGuard FAIL | tk:", posInfo.Ticket(),
               " err:", trade.ResultRetcode(),
               " guardSL:", guardSL, " bid:", bid);
   }
}

//+------------------------------------------------------------------+
//| POS TARGET: set TP ของ best N ไม้ ให้กำไรรวม = tar$             |
//| OFF → วาดเส้นประ, ON → set TP จริง + override TP feature อื่น  |
//+------------------------------------------------------------------+
void ApplyPosTargetTP()
{
   string sym       = Symbol();
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   int    digits    = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickSize == 0 || tickValue == 0) return;

   //--- Collect positions
   ulong  tickets[]; double opens[], lots[], sls[]; int types[];
   int    count = CollectPositions(sym, tickets, lots, opens, types, sls);
   if(count == 0) { DelFTLine(PREFIX+"FT_PTGT"); return; }

   //--- Net direction → sort key (same as KeepBest/PairClose)
   double netDir = 0;
   for(int i=0; i<count; i++) netDir += (types[i]==POSITION_TYPE_BUY ? lots[i] : -lots[i]);
   bool isBuyDir = (netDir >= 0);

   double dirSign = isBuyDir ? -1.0 : 1.0;
   double sortKey[]; ArrayResize(sortKey, count);
   for(int i=0; i<count; i++) sortKey[i] = dirSign * opens[i];

   int sortIdx[]; SortByProfit(sortKey, sortIdx, count);
   // sortIdx[count-1] = best, sortIdx[count-2] = second best, ...

   //--- Select best N
   int n = MathMin(g_PosTargetN, count);

   //--- Compute volume-weighted avg open of best N
   double totalLots = 0, weightedOpen = 0;
   for(int i = count-n; i < count; i++)
   {
      int idx = sortIdx[i];
      totalLots    += lots[idx];
      weightedOpen += opens[idx] * lots[idx];
   }
   if(totalLots <= 0) { DelFTLine(PREFIX+"FT_PTGT"); return; }

   double avgOpen   = weightedOpen / totalLots;
   double vpp       = tickValue / tickSize;          // $ per 1 price unit per lot
   double tpDist    = g_PosTargetDollar / (totalLots * vpp);
   double targetTP  = NormalizeDouble(isBuyDir ? avgOpen + tpDist : avgOpen - tpDist, digits);

   //--- Draw dashed line (always — ON หรือ OFF)
   datetime barTime = iTime(sym, PERIOD_CURRENT, 0);
   int      period  = PeriodSeconds(PERIOD_CURRENT);
   datetime lineEnd = (datetime)((long)barTime + (long)period * 8);
   DrawFTLine(PREFIX+"FT_PTGT", barTime, lineEnd, targetTP, C'255,200,60', STYLE_DASHDOT,
              StringFormat(" PosTarget %d pos @ $%.0f → TP %s",
                           n, g_PosTargetDollar, DoubleToString(targetTP, digits)));

   //--- ON: set TP จริงบน best N, clear TP บน positions อื่น (ที่ไม่อยู่ใน best N)
   if(!g_EnablePosTarget) return;

   for(int i = count-n; i < count; i++)
   {
      int idx = sortIdx[i];
      if(!posInfo.SelectByTicket(tickets[idx])) continue;
      double existTP = posInfo.TakeProfit();
      if(MathAbs(existTP - targetTP) > point)
         trade.PositionModify(tickets[idx], posInfo.StopLoss(), targetTP);
   }
}

//+------------------------------------------------------------------+
//| ปิดไม้ที่แย่ที่สุด เก็บไว้แค่ N ไม้ (หรือทุกไม้ถ้า g_KeepAll) |
//| คำนวณ TP ของไม้ที่จะปิด ให้กำไรรวม cover ขาดทุนของไม้ที่เก็บ  |
//+------------------------------------------------------------------+
void CheckKeepBest()
{
   string sym       = Symbol();
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits    = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickSize==0 || tickValue==0) return;
   double vpp = tickValue / tickSize;

   //--- KeepBest OFF = keep all → ปล่อยทำงานปกติ (handled in OnTick)

   //--- ถ้า pending อยู่แล้ว → ตรวจว่ามีไม้ใหม่เปิดมาไหม
   if(g_keepBestPending)
   {
      //--- นับ positions ปัจจุบัน
      int curCount = 0;
      for(int i=PositionsTotal()-1; i>=0; i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Symbol()!=sym) continue;
         if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
         curCount++;
      }
      //--- ถ้า count ไม่เปลี่ยน → รอต่อ
      if(curCount == g_keepBestTotalCount) return;
      //--- count เปลี่ยน (เพิ่มหรือลด) → reset แล้ว recalculate
      Print("KeepBest: position count changed (", g_keepBestTotalCount, " → ", curCount, ") → recalculating");
      g_keepBestPending = false;
      g_keepBestTotalCount = 0;
      ArrayResize(g_keepWorstTickets, 0);
      ArrayResize(g_beSetTickets, 0);
      //--- fall through → recalculate below
   }

   //--- เก็บข้อมูลทุก position
   ulong  tickets[]; double lots[]; double opens[]; int types[];
   double sls[];     double profits[];
   int count = 0;

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;
      ArrayResize(tickets,count+1); ArrayResize(lots,count+1);
      ArrayResize(opens,count+1);   ArrayResize(types,count+1);
      ArrayResize(sls,count+1);     ArrayResize(profits,count+1);
      tickets[count]=posInfo.Ticket(); lots[count]=posInfo.Volume();
      opens[count]=posInfo.PriceOpen(); types[count]=(int)posInfo.PositionType();
      sls[count]=posInfo.StopLoss(); profits[count]=posInfo.Profit()+posInfo.Swap();
      count++;
   }
   if(count == 0) return;

   int keepN  = MathMin(g_KeepBestN, count);
   int closeN = count - keepN;
   if(closeN == 0) return;

   //--- Sort ascending: sortIdx[0]=worst, sortIdx[last]=best
   int sortIdx[];
   ArrayResize(sortIdx, count);
   for(int i=0;i<count;i++) sortIdx[i]=i;
   for(int a=0;a<count-1;a++)
      for(int b=a+1;b<count;b++)
         if(profits[sortIdx[a]] > profits[sortIdx[b]])
         { int tmp=sortIdx[a]; sortIdx[a]=sortIdx[b]; sortIdx[b]=tmp; }

   //--- ======================================================
   //--- คำนวณ TP breakeven ให้ closeN ไม้แย่สุด
   //--- หา price P ที่ทำให้ P&L รวมของ closeN ไม้ = $0
   //--- profit(P) = vpp * [ P*(buyLots-sellLots) + (sellW-buyW) ] = 0
   //--- => P = (sellW - buyW) / (buyLots - sellLots)
   //--- ======================================================
   double wBuyLots=0, wSellLots=0, wBuyW=0, wSellW=0;
   for(int k=0; k<closeN; k++)
   {
      int idx = sortIdx[k]; // worst positions
      if(types[idx]==POSITION_TYPE_BUY) { wBuyLots+=lots[idx]; wBuyW+=opens[idx]*lots[idx]; }
      else                              { wSellLots+=lots[idx]; wSellW+=opens[idx]*lots[idx]; }
   }
   double wNetLots = wBuyLots - wSellLots;
   if(MathAbs(wNetLots) < 0.0001)
   {
      Print("KeepBest: worst positions fully hedged → cannot find single breakeven TP");
      return;
   }

   //--- TP breakeven = price ที่กำไรรวมของ worst = 0
   //--- สูตร: profit=0 → beTP = (buyWeighted - sellWeighted) / (buyLots - sellLots)
   double beTP = NormalizeDouble((wBuyW - wSellW) / wNetLots, digits);

   //--- ถ้า worst positions ผ่าน beTP ไปแล้ว (กำไรอยู่แล้ว) → ปิดทันทีที่ market
   double bid = SymbolInfoDouble(sym,SYMBOL_BID);
   double ask = SymbolInfoDouble(sym,SYMBOL_ASK);
   bool alreadyPast = (wNetLots > 0 && beTP <= ask) || (wNetLots < 0 && beTP >= bid);
   if(alreadyPast)
   {
      Print("KeepBest: worst positions already past BE (beTP=", beTP,
            ") → closing ", closeN, " worst at market");
      ArrayResize(g_keepWorstTickets, 0);
      for(int k=0; k<closeN; k++)
      {
         int idx = sortIdx[k];
         Append(g_keepWorstTickets, tickets[idx]);
         if(trade.PositionClose(tickets[idx]))
            Print("KeepBest | Closed at market | Ticket:", tickets[idx],
                  " | PnL: $", DoubleToString(profits[idx],2));
      }
      g_keepBestPending    = true;
      g_keepBestTotalCount = count;
      return;
   }

   Print("KeepBest: beTP=", beTP, " | netLots=", wNetLots,
         " | buyW=", wBuyW, " | sellW=", wSellW);

   //--- Set beTP บน worst positions
   //--- + เก็บ beTP/netLots ไว้สำหรับ monitor (backup: close at market ถ้า TP fail)
   double bidNow = SymbolInfoDouble(sym, SYMBOL_BID);
   double askNow = SymbolInfoDouble(sym, SYMBOL_ASK);
   long   stopLvl = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopDist = stopLvl * SymbolInfoDouble(sym, SYMBOL_POINT);

   ArrayResize(g_keepWorstTickets, 0);
   int setOK = 0, setFail = 0;
   for(int k=0; k<closeN; k++)
   {
      int idx = sortIdx[k];
      Append(g_keepWorstTickets, tickets[idx]);
      if(!posInfo.SelectByTicket(tickets[idx])) continue;
      if(MathAbs(posInfo.TakeProfit() - beTP) <= point*2) { setOK++; continue; }

      //--- ตรวจ broker stop level — TP ต้องห่างราคาปัจจุบันอย่างน้อย stopLvl ticks
      bool valid = (types[idx]==POSITION_TYPE_BUY)
                   ? (beTP > askNow + minStopDist)
                   : (beTP < bidNow - minStopDist);
      if(!valid)
      {
         Print("KeepBest | TP rejected (too close to market) | Ticket:", tickets[idx],
               " | beTP=", beTP, " | bid=", bidNow, " | minStop=", minStopDist);
         setFail++;
         continue;
      }

      if(trade.PositionModify(tickets[idx], sls[idx], beTP))
      {
         Print("KeepBest | Set TP OK | Ticket:", tickets[idx], " | beTP=", beTP);
         setOK++;
      }
      else
      {
         Print("KeepBest | FAILED PositionModify | Ticket:", tickets[idx],
               " | beTP=", beTP, " | open=", opens[idx], " | ask=", askNow,
               " | retcode=", trade.ResultRetcode(), " | ", trade.ResultRetcodeDescription(),
               " | _LastError=", _LastError);
         setFail++;
      }
   }
   g_keepBestBeTP    = beTP;
   g_keepBestNetLots = wNetLots;
   if(setFail > 0)
      Print("KeepBest | ", setFail, " TP set failed → monitor will close at market when bePrice reached");

   //--- Mark pending: รอ worst positions ปิดก่อน feature อื่นจะทำงาน
   g_keepBestPending    = true;
   g_keepBestTotalCount = count;  // บันทึกจำนวนไม้ตอนนี้ → ตรวจจับไม้ใหม่ใน tick ถัดไป

   double worstPnL = 0;
   for(int k=0;k<closeN;k++) worstPnL += profits[sortIdx[k]];
   double bestPnL  = 0;
   for(int k=closeN;k<count;k++) bestPnL += profits[sortIdx[k]];

   Print("KeepBest activated | Keep best:", keepN, " | Close worst:", closeN,
         " | BreakevenTP:", beTP,
         " | WorstPnL: $", worstPnL,
         " | BestPnL: $", bestPnL,
         " | Estimated net at TP: $", worstPnL + (worstPnL*-1));
}

//--- ตรวจว่า worst positions ยังเปิดอยู่มั้ย
bool WorstPositionsStillOpen()
{
   for(int k=0; k<ArraySize(g_keepWorstTickets); k++)
      if(posInfo.SelectByTicket(g_keepWorstTickets[k]))
         return true;  // ยังมีค้างอยู่
   return false;  // ปิดหมดแล้ว
}

//+------------------------------------------------------------------+
//| FEATURE 3c — Profit Trail (High Water Mark)                      |
//| ตั้ง SL รวม ณ ราคาที่ P&L รวมทุกไม้ = floor$                   |
//| SL อยู่ฝั่ง broker → ทำงานแม้ EA ปิดอยู่ (ไม่ต้องการ VPS)     |
//+------------------------------------------------------------------+
void CheckProfitTrail()
{
   string sym       = Symbol();
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits    = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickSize==0 || tickValue==0) return;
   double vpp = tickValue / tickSize;

   //--- รวบรวม positions + คำนวณกำไรรวม
   ulong  tickets[]; double lots[]; double opens[]; int types[]; double tps[];
   int count = 0;
   double totalProfit = 0;

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;
      ArrayResize(tickets,count+1); ArrayResize(lots,count+1);
      ArrayResize(opens,count+1);   ArrayResize(types,count+1); ArrayResize(tps,count+1);
      tickets[count]=posInfo.Ticket(); lots[count]=posInfo.Volume();
      opens[count]=posInfo.PriceOpen(); types[count]=(int)posInfo.PositionType();
      tps[count]=posInfo.TakeProfit();
      totalProfit += posInfo.Profit() + posInfo.Swap();
      count++;
   }

   //--- ไม่มี position → reset peak
   if(count == 0) { g_PeakProfit = 0; return; }

   //--- อัปเดต high water mark
   if(totalProfit > g_PeakProfit)
      g_PeakProfit = totalProfit;

   //--- arm condition: peak ต้องถึง g_ProfitArmAt ก่อน
   if(g_PeakProfit < g_ProfitArmAt) return;

   //--- คำนวณ slPrice ที่ทำให้ P&L รวม = g_ProfitFloor
   //--- สูตร: P = (floor/vpp + buyW - sellW) / netLots
   double buyLots=0, sellLots=0, buyW=0, sellW=0;
   for(int j=0; j<count; j++)
   {
      if(types[j]==POSITION_TYPE_BUY){ buyLots+=lots[j]; buyW+=opens[j]*lots[j]; }
      else                           { sellLots+=lots[j]; sellW+=opens[j]*lots[j]; }
   }
   double netLots = buyLots - sellLots;
   if(MathAbs(netLots) < 0.0001) return;  // hedged → คำนวณไม่ได้

   double slPrice = NormalizeDouble(
      (g_ProfitFloor / vpp + buyW - sellW) / netLots, digits);

   //--- ตรวจว่า SL อยู่ฝั่งขาดทุน (ไม่ใช่ทะลุราคาปัจจุบัน)
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   if(netLots > 0 && slPrice >= bid) return;  // SL สูงเกิน (เกินราคาตลาด)
   if(netLots < 0 && slPrice <= ask) return;  // SL ต่ำเกิน

   //--- Set SL บน broker ทุกไม้ (ทำงานแม้ EA ปิด)
   //--- เลื่อน SL เฉพาะเมื่อ protective มากขึ้น (BUY: SL สูงขึ้น, SELL: SL ต่ำลง)
   for(int j=0; j<count; j++)
   {
      if(!posInfo.SelectByTicket(tickets[j])) continue;
      double existSL = posInfo.StopLoss();
      bool needMove  = (netLots > 0 && slPrice > existSL + point*2)
                    || (netLots < 0 && (existSL == 0 || slPrice < existSL - point*2));
      if(!needMove) continue;

      if(trade.PositionModify(tickets[j], slPrice, tps[j]))
         Print("ProfitTrail SL | Ticket:", tickets[j],
               " | SL:", slPrice,
               " | Floor: $", DoubleToString(g_ProfitFloor,2),
               " | Peak: $",  DoubleToString(g_PeakProfit,2));
   }
}

//+------------------------------------------------------------------+
//| FEATURE 5 — Breakeven                                            |
//| เมื่อกำไร X ticks → ย้าย SL ไปที่ open + lockTicks             |
//+------------------------------------------------------------------+
//--- คำนวณ combined breakeven price + net direction + cur price + curTicks
//--- คืน true ถ้าสำเร็จ (มี positions + ไม่ hedged), false ถ้าใช้ไม่ได้
bool ComputeCombinedBE(double &bePriceOut, double &netLotsOut, double &curOut, double &curTicksOut)
{
   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(tickSize == 0) return false;

   double buyLots=0, sellLots=0, buyW=0, sellW=0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      double op = posInfo.PriceOpen(), lt = posInfo.Volume();
      if(posInfo.PositionType()==POSITION_TYPE_BUY){ buyLots+=lt; buyW+=op*lt; }
      else                                          { sellLots+=lt; sellW+=op*lt; }
   }
   double netLots = buyLots - sellLots;
   if(MathAbs(netLots) < 0.0001) return false;   // hedged

   double bePrice = NormalizeDouble((buyW - sellW) / netLots, digits);
   double cur = (netLots > 0) ? SymbolInfoDouble(sym,SYMBOL_BID) : SymbolInfoDouble(sym,SYMBOL_ASK);
   double curTicks = (netLots > 0) ? (cur - bePrice)/tickSize : (bePrice - cur)/tickSize;

   bePriceOut   = bePrice;
   netLotsOut   = netLots;
   curOut       = cur;
   curTicksOut  = curTicks;
   return true;
}

//+------------------------------------------------------------------+
//| FEATURE 5 — Breakeven SL (Combined — เฉลี่ยทุกไม้ที่ถืออยู่)   |
//| คำนวณ BE price จาก weighted avg → set SL เดียวกันทุกไม้        |
//+------------------------------------------------------------------+
void CheckBreakeven()
{
   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(tickSize == 0) return;

   double bePrice, netLots, cur, curTicks;
   if(!ComputeCombinedBE(bePrice, netLots, cur, curTicks)) return;

   //--- trigger: กำไรจาก combined BE ถึง trigger ticks
   if(curTicks < g_BETriggerTicks) return;

   //--- SL รวม = bePrice + lockTicks (ทิศ net)
   double combinedSL = (netLots > 0)
      ? NormalizeDouble(bePrice + g_BELockTicks * tickSize, digits)
      : NormalizeDouble(bePrice - g_BELockTicks * tickSize, digits);

   //--- SL ต้องอยู่ฝั่งขาดทุน (ไม่ทะลุราคาปัจจุบัน)
   if(netLots > 0 && combinedSL >= cur) return;
   if(netLots < 0 && combinedSL <= cur) return;

   //--- set SL เดียวกันทุกไม้
   bool anySet = false;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      ulong  tk      = posInfo.Ticket();
      double existSL = posInfo.StopLoss();
      bool   needMove = (netLots > 0 && combinedSL > existSL)
                     || (netLots < 0 && (existSL == 0 || combinedSL < existSL));
      if(needMove && trade.PositionModify(tk, combinedSL, posInfo.TakeProfit()))
      {
         Print("BE | Ticket:", tk, " | SL:", combinedSL, " | bePrice:", bePrice);
         anySet = true;
      }
      if(!IsInArray(g_beSetTickets, tk)) Append(g_beSetTickets, tk);
   }
   if(anySet) Print("BE Combined | SL=", combinedSL, " | bePrice=", bePrice);
}

//+------------------------------------------------------------------+
//| FEATURE 6 — Trailing Stop (Combined — เฉลี่ยทุกไม้)             |
//| หลัง combined BE trigger: ทุก step ticks → เลื่อน SL รวมขึ้น   |
//+------------------------------------------------------------------+
//--- high water mark ของ curTicks รวม (combined)
double g_trailCombinedHigh = 0;

void CheckTrailingStop()
{
   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(tickSize == 0) return;

   double bePrice, netLots, cur, curTicks;
   if(!ComputeCombinedBE(bePrice, netLots, cur, curTicks)) return;

   //--- ต้องผ่าน BE trigger ก่อน
   if(curTicks < g_BETriggerTicks) return;

   //--- อัปเดต high water mark (combined)
   if(curTicks > g_trailCombinedHigh) g_trailCombinedHigh = curTicks;

   //--- ตรวจว่าผ่าน step ใหม่หรือยัง (ใช้ high water mark)
   double step      = g_TrailStepTicks;
   double stepsGone = MathFloor((g_trailCombinedHigh - g_BETriggerTicks) / step);
   if(stepsGone < 1) return;

   //--- SL รวมใหม่ = bePrice + (lock + stepsGone × step) ticks (ทิศ net)
   double newSLTicks = g_BELockTicks + stepsGone * step;
   double newSL = (netLots > 0)
      ? NormalizeDouble(bePrice + newSLTicks * tickSize, digits)
      : NormalizeDouble(bePrice - newSLTicks * tickSize, digits);

   //--- SL ต้องอยู่ฝั่งขาดทุน
   if(netLots > 0 && newSL >= cur) return;
   if(netLots < 0 && newSL <= cur) return;

   //--- set SL เดียวกันทุกไม้ (เลื่อนเฉพาะที่ improvement)
   bool anySet = false;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;
      ulong tk = posInfo.Ticket();
      double existSL = posInfo.StopLoss();
      bool needMove = (netLots > 0 && newSL > existSL)
                   || (netLots < 0 && (existSL == 0 || newSL < existSL));
      if(needMove && trade.PositionModify(tk, newSL, posInfo.TakeProfit()))
         anySet = true;
   }
   if(anySet)
      Print("Trail Combined | step#", stepsGone, " | SL=", newSL,
            " | bePrice=", bePrice, " | curTicks=", curTicks);
}

//+------------------------------------------------------------------+
//| Panel                                                             |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| DCA — ถัวเฉลี่ย ใช้เฉพาะตอน KeepBest pending                   |
//+------------------------------------------------------------------+
void CheckDCA()
{
   if(g_DCAOpenedCount >= g_DCAMaxPositions) return;

   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double bid      = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask      = SymbolInfoDouble(sym, SYMBOL_ASK);

   //--- หา net direction จาก worst positions (ถัวตามทิศทางของไม้ที่แย่)
   if(g_DCADirection == 0)
   {
      double netLots = 0;
      for(int k=0; k<ArraySize(g_keepWorstTickets); k++)
      {
         if(!posInfo.SelectByTicket(g_keepWorstTickets[k])) continue;
         netLots += (posInfo.PositionType()==POSITION_TYPE_BUY ? 1.0 : -1.0) * posInfo.Volume();
      }
      g_DCADirection = (netLots >= 0) ? 1 : -1;
   }

   //--- ราคา reference = "best position open" (ใกล้ราคาปัจจุบันที่สุดของฝั่ง DCA)
   //--- BUY: lowest open, SELL: highest open → ปรับอัตโนมัติเมื่อมีการเปิด/ปิดไม้
   double refPrice = DCAReferencePrice();
   if(refPrice <= 0) return;

   double stepDist  = g_DCAStepTicks * tickSize;
   double curPrice  = (g_DCADirection == 1) ? bid : ask;
   double threshold = (g_DCADirection == 1)
                      ? refPrice - stepDist   // BUY: ถัวลงเมื่อราคาลง
                      : refPrice + stepDist;   // SELL: ถัวขึ้นเมื่อราคาขึ้น

   bool triggered = (g_DCADirection == 1 && curPrice <= threshold)
                 || (g_DCADirection == -1 && curPrice >= threshold);
   if(!triggered) return;

   //--- หา base lot จาก "ไม้แรก" (oldest position by time, fallback = lowest ticket)
   double baseLot = 0;
   datetime oldestTime = 0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      datetime t = (datetime)posInfo.Time();
      if(oldestTime==0 || t < oldestTime)
      { oldestTime = t; baseLot = posInfo.Volume(); }
   }
   if(baseLot <= 0) baseLot = g_DCALotSize;  // fallback

   //--- คำนวณ lot ตาม tier: lot = base + tier × lotStep
   //--- ถ้า tierSize<=0 หรือ lotStep<=0 → ใช้ base lot ทุกไม้
   double tierLot = baseLot;
   int    curTier = 0;
   if(g_DCATierSize > 0 && g_DCALotStep > 0)
   {
      curTier = g_DCAOpenedCount / g_DCATierSize;
      tierLot = baseLot + curTier * g_DCALotStep;
   }

   double lot = NormVol(sym, tierLot);
   if(lot <= 0) return;

   string cmt = StringFormat("DCA#%d/T%d", g_DCAOpenedCount+1, curTier);
   bool ok = false;
   if(g_DCADirection == 1)
      ok = trade.Buy(lot, sym, 0, 0, 0, cmt);
   else
      ok = trade.Sell(lot, sym, 0, 0, 0, cmt);

   if(ok)
   {
      g_DCAOpenedCount++;
      Print("DCA opened | #", g_DCAOpenedCount, "/", g_DCAMaxPositions,
            " | Lot:", lot,
            " | Dir:", (g_DCADirection==1?"BUY":"SELL"),
            " | At:", curPrice,
            " | Step:", g_DCAStepTicks, " ticks");

      //--- Recalculate beTP ใหม่รวมไม้ DCA ด้วย
      g_keepBestPending = false;  // reset ให้ CheckKeepBest คำนวณใหม่ใน tick ถัดไป
      ArrayResize(g_keepWorstTickets, 0);
   }
}

//+------------------------------------------------------------------+
//| Close All Positions                                              |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   string sym = Symbol();
   int closed = 0;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;
      if(trade.PositionClose(posInfo.Ticket()))
         closed++;
   }
   Print("CloseAll: closed ", closed, " positions");
}

//+------------------------------------------------------------------+
//| Total SL — set SL ทุกไม้ให้ขาดทุนรวม = g_TotalSLAmount         |
//| ใช้สูตรเดียวกับ Profit Target แต่ target = -SLAmount            |
//+------------------------------------------------------------------+
void ApplyTotalSL()
{
   string sym       = Symbol();
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits    = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickSize==0 || tickValue==0) return;
   double vpp = tickValue / tickSize;

   ulong tickets[]; double lots[]; double opens[]; int types[]; double tps[];
   int count = 0;

   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber != 0 && posInfo.Magic() != InpMagicNumber) continue;
      ArrayResize(tickets,count+1); ArrayResize(lots,count+1);
      ArrayResize(opens,count+1);   ArrayResize(types,count+1); ArrayResize(tps,count+1);
      tickets[count]=posInfo.Ticket(); lots[count]=posInfo.Volume();
      opens[count]=posInfo.PriceOpen(); types[count]=(int)posInfo.PositionType();
      tps[count]=posInfo.TakeProfit(); count++;
   }
   if(count == 0) return;

   //--- รวม weighted lots
   double buyLots=0, sellLots=0, buyW=0, sellW=0;
   for(int j=0;j<count;j++)
   {
      if(types[j]==POSITION_TYPE_BUY){ buyLots+=lots[j]; buyW+=opens[j]*lots[j]; }
      else                           { sellLots+=lots[j]; sellW+=opens[j]*lots[j]; }
   }
   double netLots = buyLots - sellLots;
   if(MathAbs(netLots) < 0.0001) return;

   //--- SL price ที่ทำให้ขาดทุนรวม = -g_TotalSLAmount
   //--- profit(P) = -g_TotalSLAmount  →  P = (-SL/vpp + buyW - sellW) / netLots
   double slPrice = NormalizeDouble(
      (-g_TotalSLAmount / vpp + buyW - sellW) / netLots, digits);

   //--- Validate: SL ต้องอยู่ฝั่งขาดทุน
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   if(netLots > 0 && slPrice >= bid) { Print("TotalSL: slPrice ", slPrice, " >= bid, SL too tight"); return; }
   if(netLots < 0 && slPrice <= ask) { Print("TotalSL: slPrice ", slPrice, " <= ask, SL too tight"); return; }

   //--- Set SL ทุกไม้
   for(int j=0; j<count; j++)
      if(posInfo.SelectByTicket(tickets[j]))
         if(MathAbs(posInfo.StopLoss() - slPrice) > point*2)
            if(trade.PositionModify(tickets[j], slPrice, tps[j]))
               Print("TotalSL | Ticket:", tickets[j], " | SL:", slPrice, " | MaxLoss: $", g_TotalSLAmount);
}

//+------------------------------------------------------------------+
//| Live status: แสดง progress ของ worst positions ว่าใกล้ BE แค่ไหน|
//+------------------------------------------------------------------+
void UpdateKeepBestStatus()
{
   static datetime lastUpdate = 0;
   if(TimeCurrent() == lastUpdate) return;
   lastUpdate = TimeCurrent();

   string sym = Symbol();

   //--- ไม่มี feature ใดเปิดอยู่ → ซ่อน status
   if(!g_EnableKeepBest && !g_EnablePairClose && !g_EnablePairGuard)
   {
      SetStatus1("", clrNONE, clrNONE);
      SetStatus2("", clrNONE, clrNONE);
      return;
   }

   //--- ══ PairClose Status ══
   if(g_EnablePairClose)
   {
      int total=0; double totalPnL=0, pairPnL=0, remainPnL=0;
      double opens[],lots[],profits[]; int types[];
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Symbol()!=sym) continue;
         if(InpMagicNumber!=0&&posInfo.Magic()!=InpMagicNumber) continue;
         ArrayResize(opens,total+1);ArrayResize(lots,total+1);
         ArrayResize(profits,total+1);ArrayResize(types,total+1);
         opens[total]=posInfo.PriceOpen();lots[total]=posInfo.Volume();
         profits[total]=posInfo.Profit()+posInfo.Swap();
         types[total]=(int)posInfo.PositionType();
         totalPnL+=profits[total]; total++;
      }
      int closeCount=total-g_KeepBestN, numPairs=closeCount/2, oddCnt=closeCount%2;
      int remainN=MathMax(0,total-closeCount);
      // sort by open price
      double netDir=0;
      for(int j=0;j<total;j++) netDir+=(types[j]==POSITION_TYPE_BUY?lots[j]:-lots[j]);
      double ds=(netDir>=0)?-1.0:1.0;
      double sk[]; ArrayResize(sk,total);
      for(int j=0;j<total;j++) sk[j]=ds*opens[j];
      int si[]; SortByProfit(sk,si,total);
      // pair PnL = pairs + odd, remain PnL = middle N
      for(int p=0;p<numPairs;p++)
         { pairPnL+=profits[si[p]]+profits[si[total-1-p]]; }
      if(oddCnt&&numPairs<closeCount) pairPnL+=profits[si[numPairs]];
      remainPnL=totalPnL-pairPnL;
      string l1=StringFormat("PairClose: %d pos | %d pairs%s | remain %d | tgt $%.0f",
                             total,numPairs,(oddCnt?" +odd":""),remainN,g_PairCloseTarget);
      string l2=StringFormat("Pairs PnL: $%.2f  |  Remain PnL: $%.2f  |  Net: $%.2f",
                             pairPnL,remainPnL,totalPnL);
      SetStatus1(l1,clrNONE,C'120,220,255');
      SetStatus2(l2,clrNONE,C'180,220,255');
      return;
   }

   //--- ══ PairGuard Status ══
   if(g_EnablePairGuard)
   {
      int total=0; double totalPnL=0,unpairPnL=0,protPnL=0;
      double profits[],opens[],lots[]; int types[];
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Symbol()!=sym) continue;
         if(InpMagicNumber!=0&&posInfo.Magic()!=InpMagicNumber) continue;
         ArrayResize(profits,total+1);ArrayResize(opens,total+1);
         ArrayResize(lots,total+1);ArrayResize(types,total+1);
         profits[total]=posInfo.Profit()+posInfo.Swap();
         opens[total]=posInfo.PriceOpen();lots[total]=posInfo.Volume();
         types[total]=(int)posInfo.PositionType();
         totalPnL+=profits[total]; total++;
      }
      int unprot=total-g_KeepBestN, numPairs=unprot/2, oddCnt=unprot%2;
      int si[]; SortByProfit(profits,si,total);
      for(int k=0;k<unprot&&k<total;k++) unpairPnL+=profits[si[k]];
      protPnL=totalPnL-unpairPnL;
      string l1=StringFormat("PairGuard: %d pos | prot %d | unprot %d (%d pairs%s) | tgt $%.0f",
                             total,MathMin(g_KeepBestN,total),unprot,numPairs,
                             (oddCnt?" +odd":""),g_PairGuardTarget);
      string l2=StringFormat("Unprot PnL: $%.2f  |  Prot PnL: $%.2f  |  Net: $%.2f",
                             unpairPnL,protPnL,totalPnL);
      SetStatus1(l1,clrNONE,C'180,255,120');
      SetStatus2(l2,clrNONE,C'200,255,160');
      return;
   }

   //--- ══ KeepBest Status ══ (ของเดิม)
   //--- OFF
   if(!g_EnableKeepBest)
   {
      SetStatus1("", clrNONE, clrNONE);
      SetStatus2("", clrNONE, clrNONE);
      return;
   }

   //--- หลัง breakeven เสร็จแล้ว
   if(!g_keepBestPending && ArraySize(g_keepWorstTickets) > 0)
   {
      SetStatus1("DONE  Breakeven complete", C'35,35,55', clrWhite);
      SetStatus2("Other features now active", C'28,28,45', C'180,180,220');
      return;
   }

   //--- Pending: worst positions กำลังรอ beTP
   if(g_keepBestPending)
   {
      double worstPnL = 0, bestPnL = 0;
      double beTP     = 0;
      int    worstCnt = 0, bestCnt = 0;
      double wOpen=0, wLots=0;

      //--- worst positions
      for(int k=0; k<ArraySize(g_keepWorstTickets); k++)
      {
         if(!posInfo.SelectByTicket(g_keepWorstTickets[k])) continue;
         worstPnL += posInfo.Profit() + posInfo.Swap();
         beTP      = posInfo.TakeProfit();
         wOpen    += posInfo.PriceOpen() * posInfo.Volume();
         wLots    += posInfo.Volume();
         worstCnt++;
      }
      if(worstCnt == 0)
      {
         SetStatus1("DONE  Breakeven complete", C'15,70,15', clrLightGreen);
         SetStatus2("Running best N positions...", C'10,55,10', C'120,220,120');
         return;
      }

      //--- best positions (ทุก position ที่ไม่ใช่ worst)
      for(int i=PositionsTotal()-1; i>=0; i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Symbol()!=sym) continue;
         if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
         if(!IsInArray(g_keepWorstTickets, posInfo.Ticket())) continue;
         bestPnL += posInfo.Profit() + posInfo.Swap();
         bestCnt++;
      }
      // fix: best = ไม่อยู่ใน worst list
      bestPnL = 0; bestCnt = 0;
      for(int i=PositionsTotal()-1; i>=0; i--)
      {
         if(!posInfo.SelectByIndex(i)) continue;
         if(posInfo.Symbol()!=sym) continue;
         if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
         if(IsInArray(g_keepWorstTickets, posInfo.Ticket())) continue;
         bestPnL += posInfo.Profit() + posInfo.Swap();
         bestCnt++;
      }

      //--- คำนวณ $ ที่ยังขาดอยู่เพื่อ breakeven
      double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double bid       = SymbolInfoDouble(sym, SYMBOL_BID);
      double remaining = 0;
      if(beTP != 0 && tickSize > 0 && tickValue > 0 && wLots > 0)
      {
         double vpp     = tickValue / tickSize;
         double avgOpen = wOpen / wLots;
         // กำไรที่จะได้ถ้าปิดที่ราคาปัจจุบัน
         double curPnLAtClose = vpp * (bid - avgOpen) * wLots;
         remaining = -worstPnL - curPnLAtClose; // $ ที่ยังขาดอยู่
         if(remaining < 0) remaining = 0;
      }

      string line1 = StringFormat("Close %d  PnL: $%.2f  Need: $%.2f  BE@%s",
                                   worstCnt, worstPnL,
                                   (-worstPnL),       // ต้องกำไรอีกเท่าไรจึงจะ BE
                                   DoubleToString(beTP, 5));
      string line2 = StringFormat("Keep  %d  PnL: $%.2f  |  Net now: $%.2f",
                                   bestCnt, bestPnL, worstPnL + bestPnL);
      SetStatus1(line1, C'35,35,55', C'220,220,255');
      SetStatus2(line2, C'28,28,45', C'180,180,220');
      return;
   }

   //--- ยังไม่ activate → scan positions
   double worstPnL=0, bestPnL=0;
   int    total=0;

   // collect + sort
   ulong  tix[]; double profs[];
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      ArrayResize(tix,total+1); ArrayResize(profs,total+1);
      profs[total] = posInfo.Profit()+posInfo.Swap();
      total++;
   }
   int keepN  = MathMin(g_KeepBestN, total);
   int closeN = total - keepN;

   // sort ascending
   int idx[]; ArrayResize(idx,total);
   for(int i=0;i<total;i++) idx[i]=i;
   for(int a=0;a<total-1;a++)
      for(int b=a+1;b<total;b++)
         if(profs[idx[a]]>profs[idx[b]]){int t=idx[a];idx[a]=idx[b];idx[b]=t;}

   for(int k=0;k<closeN;k++)  worstPnL += profs[idx[k]];
   for(int k=closeN;k<total;k++) bestPnL += profs[idx[k]];

   string line1 = StringFormat("Ready: %d pos total  |  Will close: %d  Keep: %d",
                                total, closeN, keepN);
   string line2 = StringFormat("Closing PnL: $%.2f  |  Keeping PnL: $%.2f  |  Net: $%.2f",
                                worstPnL, bestPnL, worstPnL+bestPnL);
   SetStatus1(line1, C'35,35,55', C'220,220,255');
   SetStatus2(line2, C'28,28,45', C'180,180,220');
}

void SetStatus1(string txt, color bg, color clr)
{
   string nm = PREFIX+"CST1";
   if(ObjectFind(0,nm)<0)
   {
      ObjectCreate(0,nm,OBJ_LABEL,0,0,0);
      ObjectSetInteger(0,nm,OBJPROP_CORNER,     CORNER_RIGHT_UPPER);
      ObjectSetInteger(0,nm,OBJPROP_ANCHOR,     ANCHOR_RIGHT_UPPER);
      ObjectSetInteger(0,nm,OBJPROP_XDISTANCE,  6);
      ObjectSetInteger(0,nm,OBJPROP_YDISTANCE,  20);
      ObjectSetString (0,nm,OBJPROP_FONT,       "Arial Bold");
      ObjectSetInteger(0,nm,OBJPROP_FONTSIZE,   9);
      ObjectSetInteger(0,nm,OBJPROP_BACK,       false);
      ObjectSetInteger(0,nm,OBJPROP_SELECTABLE, false);
   }
   ObjectSetString (0,nm,OBJPROP_TEXT,  txt);
   ObjectSetInteger(0,nm,OBJPROP_COLOR, clr);
}
void SetStatus2(string txt, color bg, color clr)
{
   string nm = PREFIX+"CST2";
   if(ObjectFind(0,nm)<0)
   {
      ObjectCreate(0,nm,OBJ_LABEL,0,0,0);
      ObjectSetInteger(0,nm,OBJPROP_CORNER,     CORNER_RIGHT_UPPER);
      ObjectSetInteger(0,nm,OBJPROP_ANCHOR,     ANCHOR_RIGHT_UPPER);
      ObjectSetInteger(0,nm,OBJPROP_XDISTANCE,  6);
      ObjectSetInteger(0,nm,OBJPROP_YDISTANCE,  36);
      ObjectSetString (0,nm,OBJPROP_FONT,       "Arial");
      ObjectSetInteger(0,nm,OBJPROP_FONTSIZE,   8);
      ObjectSetInteger(0,nm,OBJPROP_BACK,       false);
      ObjectSetInteger(0,nm,OBJPROP_SELECTABLE, false);
   }
   ObjectSetString (0,nm,OBJPROP_TEXT,  txt);
   ObjectSetInteger(0,nm,OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
void CreatePanel()
{
   // 16 rows: Title | Session | ATP | PC | PT | TSL | PROFTRAIL | KB | PAIRCLOSE | PAIRGUARD | Status1 | Status2 | DCA | DCATIER | BE | TR
   int totalRows = PANEL_ROWS;
   //--- BG ขนาด 50px (พอดี ไม่ขยาย ไม่ค้างใต้ panel)
   int bgH = 50;

   CreateBG(PREFIX+"BG", PANEL_X-5, PANEL_Y+1, PANEL_W+10, bgH);

   int row = totalRows - 1;  // count down from top

   //--- ══ TITLE + CLOSE ALL (single row) ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateBG2(PREFIX+"TITLE_BG", PANEL_X-5, y, PANEL_W+10, ROW_H, C'15,25,50');
      CreateLbl(PREFIX+"TITLE", PANEL_X+4, y, "Gold Smart Trade  by Beam", clrWhite, FONT_SIZE, true);
      // Close All button — right portion of title row
      ObjectCreate(0,PREFIX+"BTN_CLOSEALL",OBJ_BUTTON,0,0,0);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_XDISTANCE, PANEL_X+PANEL_W-108);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_YDISTANCE, y+2);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_XSIZE, 106);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_YSIZE, ROW_H-4);
      ObjectSetString (0,PREFIX+"BTN_CLOSEALL",OBJPROP_TEXT, "✕  CLOSE ALL");
      ObjectSetString (0,PREFIX+"BTN_CLOSEALL",OBJPROP_FONT, "Arial Bold");
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_FONTSIZE, FONT_SMALL);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_BGCOLOR, C'160,20,20');
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_BORDER_COLOR, C'200,50,50');
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_CORNER, CORNER);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0,PREFIX+"BTN_CLOSEALL",OBJPROP_STATE, false);
      row--;
   }

   //--- ══ SESSION + BAR COUNTDOWN ══
   {
      int y = PANEL_Y + row * ROW_H;
      // BG
      ObjectCreate(0,PREFIX+"INFO_BG",OBJ_RECTANGLE_LABEL,0,0,0);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_XDISTANCE,  PANEL_X-5);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_YDISTANCE,  y+1);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_XSIZE,      PANEL_W+10);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_YSIZE,      ROW_H-1);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_BGCOLOR,    C'18,22,38');
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_BORDER_COLOR,C'40,40,65');
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_CORNER,     CORNER);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_BACK,       false);
      ObjectSetInteger(0,PREFIX+"INFO_BG",OBJPROP_SELECTABLE, false);
      // Session label — ซ้าย
      ObjectCreate(0,PREFIX+"SES_TXT",OBJ_LABEL,0,0,0);
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_XDISTANCE,  PANEL_X+4);
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_YDISTANCE,  y+7);
      ObjectSetString (0,PREFIX+"SES_TXT",OBJPROP_TEXT,       "○ LOADING...");
      ObjectSetString (0,PREFIX+"SES_TXT",OBJPROP_FONT,       "Arial Bold");
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_FONTSIZE,   FONT_SMALL);
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_COLOR,      clrSilver);
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_CORNER,     CORNER);
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_BACK,       false);
      ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_SELECTABLE, false);
      row--;
   }

   //--- ══ PARTIAL CLOSE ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PC", y, "PARTIAL CLOSE", C'0,80,120', "TOG_PARTIAL", g_EnablePartial);
      InlLabel(PREFIX+"IL_PC1", INPUT_AREA_X+2,   y, "%:");
      CreateMiniEdit(PREFIX+"EDT_PC",  INPUT_AREA_X+18,  y, 52,  DoubleToString(g_PartialClosePct,1));
      InlLabel(PREFIX+"IL_PC2", INPUT_AREA_X+74,  y, "trig:");
      CreateMiniEdit(PREFIX+"EDT_TR",  INPUT_AREA_X+104, y, 72,  DoubleToString(g_PartialTriggerTicks,0));
      row--;
   }

   //--- ══ PROFIT TARGET ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PT", y, "PROFIT TARGET", C'0,90,50', "TOG_PROFTGT", g_EnableProfitTarget);
      InlLabel(PREFIX+"IL_PT1", INPUT_AREA_X+4,  y, "$:");
      CreateMiniEdit(PREFIX+"EDT_PT",  INPUT_AREA_X+22, y, 130, DoubleToString(g_TotalProfitTarget,2));
      row--;
   }

   //--- ══ TOTAL SL ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_TSL", y, "TOTAL SL ($)", C'130,40,0', "TOG_TOTALSL", g_EnableTotalSL);
      InlLabel(PREFIX+"IL_TSL1", INPUT_AREA_X+4,  y, "$:");
      CreateMiniEdit(PREFIX+"EDT_TSL", INPUT_AREA_X+22, y, 130, DoubleToString(g_TotalSLAmount,2));
      row--;
   }

   //--- ══ PROFIT TRAIL ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PTD", y, "PROFIT TRAIL", C'120,60,140', "TOG_PROFTRAIL", g_EnableProfitTrail);
      InlLabel(PREFIX+"IL_PTD1", INPUT_AREA_X+2,  y, "arm$:");
      CreateMiniEdit(PREFIX+"EDT_PTARM", INPUT_AREA_X+38, y, 62, DoubleToString(g_ProfitArmAt,2));
      InlLabel(PREFIX+"IL_PTD2", INPUT_AREA_X+104, y, "fl$:");
      CreateMiniEdit(PREFIX+"EDT_PTD",  INPUT_AREA_X+126, y, 52, DoubleToString(g_ProfitFloor,2));
      row--;
   }

   //--- ══ KEEP BEST N ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_KB", y, "KEEP BEST N", C'0,100,60', "TOG_KEEPBEST", g_EnableKeepBest);
      InlLabel(PREFIX+"IL_KB1", INPUT_AREA_X+4,  y, "N:");
      CreateMiniEdit(PREFIX+"EDT_KN",  INPUT_AREA_X+22, y, 60, IntegerToString(g_KeepBestN));
      row--;
   }

   //--- ══ PAIR CLOSE ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PC2", y, "PAIR CLOSE", C'20,100,90', "TOG_PAIRCLOSE", g_EnablePairClose);
      InlLabel(PREFIX+"IL_PCT1", INPUT_AREA_X+4,  y, "tgt$:");
      CreateMiniEdit(PREFIX+"EDT_PCT", INPUT_AREA_X+40, y, 110, DoubleToString(g_PairCloseTarget,2));
      row--;
   }

   //--- ══ PAIR GUARD ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PG", y, "PAIR GUARD", C'80,100,20', "TOG_PAIRGUARD", g_EnablePairGuard);
      InlLabel(PREFIX+"IL_PGT1", INPUT_AREA_X+4,  y, "tgt$:");
      CreateMiniEdit(PREFIX+"EDT_PGT", INPUT_AREA_X+40, y, 110, DoubleToString(g_PairGuardTarget,2));
      row--;
   }

   //--- ══ POS GUARD (per-position breakeven) ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PBE", y, "POS GUARD", C'60,80,120', "TOG_POSGUARD", g_EnablePosGuard);
      InlLabel(PREFIX+"IL_PBE1", INPUT_AREA_X+2,   y, "$:");
      CreateMiniEdit(PREFIX+"EDT_PBETRIG", INPUT_AREA_X+16,  y, 44, DoubleToString(g_PosGuardTrigger,0));
      InlLabel(PREFIX+"IL_PBE2", INPUT_AREA_X+63,  y, "lk:");
      CreateMiniEdit(PREFIX+"EDT_PBELK",   INPUT_AREA_X+81,  y, 38, DoubleToString(g_PosGuardLockTicks,0));
      InlLabel(PREFIX+"IL_PBE3", INPUT_AREA_X+122, y, "stp:");
      CreateMiniEdit(PREFIX+"EDT_PBESTP",  INPUT_AREA_X+144, y, 38, DoubleToString(g_PosGuardStepTicks,0));
      row--;
   }

   //--- ══ POS TARGET: best N positions combined TP ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_PTGT", y, "POS TARGET", C'60,60,130', "TOG_POSTGT", g_EnablePosTarget);
      InlLabel(PREFIX+"IL_PTG1", INPUT_AREA_X+2,  y, "pos:");
      CreateMiniEdit(PREFIX+"EDT_PTGTN", INPUT_AREA_X+33, y, 38, IntegerToString(g_PosTargetN));
      InlLabel(PREFIX+"IL_PTG2", INPUT_AREA_X+78, y, "tar$:");
      CreateMiniEdit(PREFIX+"EDT_PTGTD", INPUT_AREA_X+108, y, 72, DoubleToString(g_PosTargetDollar,0));
      row--;
   }

   //--- ══ DCA ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_DCA", y, "DCA (avg)", C'60,40,100', "TOG_DCA", g_EnableDCA);
      InlLabel(PREFIX+"IL_DCA2", INPUT_AREA_X+2,   y, "stp:");
      CreateMiniEdit(PREFIX+"EDT_DCASTEP", INPUT_AREA_X+30, y, 80, DoubleToString(g_DCAStepTicks,0));
      InlLabel(PREFIX+"IL_DCA3", INPUT_AREA_X+118, y, "mx:");
      CreateMiniEdit(PREFIX+"EDT_DCAMAX",  INPUT_AREA_X+138, y, 44, IntegerToString(g_DCAMaxPositions));
      // EDT_DCALOT ซ่อนไว้ (ใช้เป็น fallback ถ้าไม่มี position) — สร้าง offscreen
      ObjectCreate(0, PREFIX+"EDT_DCALOT", OBJ_EDIT, 0, 0, 0);
      ObjectSetString (0, PREFIX+"EDT_DCALOT", OBJPROP_TEXT, DoubleToString(g_DCALotSize,2));
      ObjectSetInteger(0, PREFIX+"EDT_DCALOT", OBJPROP_XDISTANCE, -999);
      row--;
   }

   //--- ══ DCA TIER ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_DCAT", y, "DCA TIER", C'80,55,120', "TOG_DCATIER_DUMMY", false);
      // ซ่อน toggle (ใช้ DCA ตัวเดียวพอ) → set ออกนอก visible area
      ObjectSetInteger(0, PREFIX+"TOG_DCATIER_DUMMY", OBJPROP_XDISTANCE, -999);
      InlLabel(PREFIX+"IL_DCAT1", INPUT_AREA_X+2,   y, "each:");
      CreateMiniEdit(PREFIX+"EDT_DCATIER", INPUT_AREA_X+34, y, 50, IntegerToString(g_DCATierSize));
      InlLabel(PREFIX+"IL_DCAT2", INPUT_AREA_X+90,  y, "+lot:");
      CreateMiniEdit(PREFIX+"EDT_DCALSTEP", INPUT_AREA_X+122, y, 60, DoubleToString(g_DCALotStep,2));
      row--;
   }

   //--- ══ BREAKEVEN SL ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_BE", y, "BREAKEVEN SL", C'100,80,0', "TOG_BE", g_EnableBreakeven);
      InlLabel(PREFIX+"IL_BE1", INPUT_AREA_X+2,   y, "trig:");
      CreateMiniEdit(PREFIX+"EDT_BET",  INPUT_AREA_X+30,  y, 66, DoubleToString(g_BETriggerTicks,0));
      InlLabel(PREFIX+"IL_BE2", INPUT_AREA_X+100, y, "lock:");
      CreateMiniEdit(PREFIX+"EDT_BEL",  INPUT_AREA_X+128, y, 50, DoubleToString(g_BELockTicks,0));
      row--;
   }

   //--- ══ TRAILING ══
   {
      int y = PANEL_Y + row * ROW_H;
      CreateCompactRow(PREFIX+"R_TR2", y, "TRAILING (stairs)", C'100,50,0', "TOG_TRAIL", g_EnableTrailing);
      InlLabel(PREFIX+"IL_TR1", INPUT_AREA_X+4,  y, "step:");
      CreateMiniEdit(PREFIX+"EDT_TS",  INPUT_AREA_X+36, y, 118, DoubleToString(g_TrailStepTicks,0));
      // row-- not needed (last row)
   }

   ChartRedraw();
}

//--- Compact feature row: colored label section (FEAT_W) + dark input area + toggle
void CreateCompactRow(string n, int y, string featureLabel, color featBg, string togName, bool state)
{
   //--- Full row dark BG
   ObjectCreate(0,n+"_BG",OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,n+"_BG",OBJPROP_XDISTANCE,PANEL_X-5);
   ObjectSetInteger(0,n+"_BG",OBJPROP_YDISTANCE,y+1);
   ObjectSetInteger(0,n+"_BG",OBJPROP_XSIZE,PANEL_W+10);
   ObjectSetInteger(0,n+"_BG",OBJPROP_YSIZE,ROW_H-1);
   ObjectSetInteger(0,n+"_BG",OBJPROP_BGCOLOR,C'38,38,55');
   ObjectSetInteger(0,n+"_BG",OBJPROP_BORDER_COLOR,C'28,28,42');
   ObjectSetInteger(0,n+"_BG",OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n+"_BG",OBJPROP_SELECTABLE,false);
   //--- Colored feature label section (left FEAT_W columns)
   ObjectCreate(0,n+"_FLBG",OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_XDISTANCE,PANEL_X-5);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_YDISTANCE,y+1);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_XSIZE,FEAT_W+5);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_YSIZE,ROW_H-1);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_BGCOLOR,featBg);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_BORDER_COLOR,featBg);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n+"_FLBG",OBJPROP_SELECTABLE,false);
   //--- Feature label text
   //    MT5 CORNER_LEFT_LOWER + ANCHOR_LEFT_LOWER: YDISTANCE = BOTTOM of text, renders upward.
   //    FLBG: y+1..y+26 (center y+13.5). For 12px text: bottom=y+7 → top=y+19, center=y+13 ✓
   ObjectCreate(0,n+"_FLT",OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,n+"_FLT",OBJPROP_XDISTANCE,PANEL_X);
   ObjectSetInteger(0,n+"_FLT",OBJPROP_YDISTANCE,y+7);
   ObjectSetString (0,n+"_FLT",OBJPROP_TEXT,featureLabel);
   ObjectSetString (0,n+"_FLT",OBJPROP_FONT,"Arial Bold");
   ObjectSetInteger(0,n+"_FLT",OBJPROP_FONTSIZE,FONT_SMALL);
   ObjectSetInteger(0,n+"_FLT",OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,n+"_FLT",OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n+"_FLT",OBJPROP_SELECTABLE,false);
   //--- Toggle button at right edge
   CreateToggle(PREFIX+togName, TOGGLE_X, y+1, state);
}

//--- Tiny inline label for input context (e.g. "$:", "trig:", "N:")
void InlLabel(string n, int x, int y, string txt)
{
   ObjectCreate(0,n,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y+7);   // ANCHOR_LEFT_LOWER: bottom of text → center in 25px row
   ObjectSetString (0,n,OBJPROP_TEXT,txt);
   ObjectSetString (0,n,OBJPROP_FONT,"Arial");
   ObjectSetInteger(0,n,OBJPROP_FONTSIZE,FONT_SMALL);
   ObjectSetInteger(0,n,OBJPROP_COLOR,C'170,180,200');
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}

//--- Status row (read-only, อัปเดตแบบ live)
void CreateStatusRow(string n, int x, int y, string initTxt, color bg)
{
   ObjectCreate(0,n+"_BG",OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,n+"_BG",OBJPROP_XDISTANCE,x-5);
   ObjectSetInteger(0,n+"_BG",OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,n+"_BG",OBJPROP_XSIZE,PANEL_W+10);
   ObjectSetInteger(0,n+"_BG",OBJPROP_YSIZE,ROW_H);
   ObjectSetInteger(0,n+"_BG",OBJPROP_BGCOLOR,bg);
   ObjectSetInteger(0,n+"_BG",OBJPROP_BORDER_COLOR,C'28,28,42');
   ObjectSetInteger(0,n+"_BG",OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n+"_BG",OBJPROP_SELECTABLE,false);

   ObjectCreate(0,n,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x+6);
   ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y+7);   // ANCHOR_LEFT_LOWER: bottom=y+7, center=y+13 ≈ BG center
   ObjectSetString (0,n,OBJPROP_TEXT,initTxt);
   ObjectSetString (0,n,OBJPROP_FONT,"Arial Bold");
   ObjectSetInteger(0,n,OBJPROP_FONTSIZE,FONT_SMALL);
   ObjectSetInteger(0,n,OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}

void ReadPanelValues()
{
   g_TP_Ticks             = MathMax(1,    StringToDouble(ObjTxt(PREFIX+"EDT_TP")));
   g_PartialClosePct      = Clamp(1,99,   StringToDouble(ObjTxt(PREFIX+"EDT_PC")));
   g_PartialTriggerTicks  = MathMax(1,    StringToDouble(ObjTxt(PREFIX+"EDT_TR")));
   g_TotalProfitTarget    = MathMax(0.01, StringToDouble(ObjTxt(PREFIX+"EDT_PT")));
   g_KeepBestN         = (int)MathMax(0, StringToDouble(ObjTxt(PREFIX+"EDT_KN")));  // 0 = close all at breakeven
   g_PairCloseTarget   = MathMax(0,    StringToDouble(ObjTxt(PREFIX+"EDT_PCT")));
   g_PairGuardTarget   = MathMax(0,    StringToDouble(ObjTxt(PREFIX+"EDT_PGT")));
   g_TotalSLAmount     = MathMax(0.01, StringToDouble(ObjTxt(PREFIX+"EDT_TSL")));
   g_ProfitArmAt       = MathMax(0,    StringToDouble(ObjTxt(PREFIX+"EDT_PTARM")));
   g_ProfitFloor       = MathMax(0,    StringToDouble(ObjTxt(PREFIX+"EDT_PTD")));
   g_PosTargetN        = (int)MathMax(1, StringToDouble(ObjTxt(PREFIX+"EDT_PTGTN")));
   g_PosTargetDollar   = MathMax(0.01, StringToDouble(ObjTxt(PREFIX+"EDT_PTGTD")));
   g_DCALotSize        = MathMax(0.01, StringToDouble(ObjTxt(PREFIX+"EDT_DCALOT")));
   g_DCAStepTicks      = MathMax(1,    StringToDouble(ObjTxt(PREFIX+"EDT_DCASTEP")));
   g_DCAMaxPositions   = (int)MathMax(1, StringToDouble(ObjTxt(PREFIX+"EDT_DCAMAX")));
   g_DCATierSize       = (int)MathMax(0, StringToDouble(ObjTxt(PREFIX+"EDT_DCATIER")));
   g_DCALotStep        = MathMax(0,    StringToDouble(ObjTxt(PREFIX+"EDT_DCALSTEP")));
   g_BETriggerTicks    = MathMax(1,    StringToDouble(ObjTxt(PREFIX+"EDT_BET")));
   g_BELockTicks       = MathMax(0,    StringToDouble(ObjTxt(PREFIX+"EDT_BEL")));
   g_TrailStepTicks    = MathMax(1,    StringToDouble(ObjTxt(PREFIX+"EDT_TS")));
   g_PosGuardTrigger   = MathMax(0, StringToDouble(ObjTxt(PREFIX+"EDT_PBETRIG")));
   g_PosGuardLockTicks = MathMax(0, StringToDouble(ObjTxt(PREFIX+"EDT_PBELK")));
   g_PosGuardStepTicks = MathMax(0, StringToDouble(ObjTxt(PREFIX+"EDT_PBESTP")));
}

string ObjTxt(string n) { return ObjectGetString(0, n, OBJPROP_TEXT); }
double Clamp(double mn, double mx, double v) { return MathMax(mn, MathMin(mx, v)); }

void PrintConfig()
{
   Print("=== Config Applied ===");
   Print("AutoTP:", g_AutoSetTP, " Ticks:", g_TP_Ticks);
   Print("Partial:", g_EnablePartial, " Close%:", g_PartialClosePct, " TrigTicks:", g_PartialTriggerTicks);
   Print("ProfitTarget:", g_EnableProfitTarget, " $:", g_TotalProfitTarget);
   Print("KeepBest:", g_EnableKeepBest, " N:", g_KeepBestN);
   Print("BE:", g_EnableBreakeven, " TrigTicks:", g_BETriggerTicks, " LockTicks:", g_BELockTicks);
   Print("Trail:", g_EnableTrailing, " Step:", g_TrailStepTicks);
}

void RefreshToggle(string name, bool state)
{
   ObjectSetInteger(0,name,OBJPROP_STATE,  false);  // reset pressed first
   ObjectSetString (0,name,OBJPROP_TEXT,   state?"ON":"OFF");
   ObjectSetInteger(0,name,OBJPROP_BGCOLOR, state?C'34,120,34':C'120,34,34');
   ObjectSetInteger(0,name,OBJPROP_COLOR,   clrWhite);  // ensure text color
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Widget creators                                                   |
//+------------------------------------------------------------------+
void CreateBG(string n, int x, int y, int w, int h)
{
   //--- ลบทิ้งก่อน เพื่อให้ refresh size ใหม่ทุกครั้ง (ป้องกัน stale BG จาก template)
   ObjectDelete(0,n);
   ObjectCreate(0,n,OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x); ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,n,OBJPROP_XSIZE,w);     ObjectSetInteger(0,n,OBJPROP_YSIZE,h);
   ObjectSetInteger(0,n,OBJPROP_BGCOLOR,C'28,28,42');
   ObjectSetInteger(0,n,OBJPROP_BORDER_COLOR,C'160,160,200');   // สว่างขึ้น → เห็นขอบชัด
   ObjectSetInteger(0,n,OBJPROP_WIDTH,2);                        // เส้นขอบหนาขึ้น
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER); ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}

void CreateBG2(string n, int x, int y, int w, int h, color bg)
{
   ObjectCreate(0,n,OBJ_RECTANGLE_LABEL,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x); ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,n,OBJPROP_XSIZE,w);     ObjectSetInteger(0,n,OBJPROP_YSIZE,h);
   ObjectSetInteger(0,n,OBJPROP_BGCOLOR,bg);  ObjectSetInteger(0,n,OBJPROP_BORDER_COLOR,bg);
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER); ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}

void CreateLbl(string n, int x, int y, string txt, color clr, int fs, bool bold=false)
{
   ObjectCreate(0,n,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x); ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y+7);
   ObjectSetString (0,n,OBJPROP_TEXT,txt);     ObjectSetInteger(0,n,OBJPROP_COLOR,clr);
   ObjectSetString (0,n,OBJPROP_FONT,bold?"Arial Bold":"Arial");
   ObjectSetInteger(0,n,OBJPROP_FONTSIZE,fs);
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER); ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
}

void CreateToggle(string n, int x, int y, bool state)
{
   ObjectCreate(0,n,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x);    ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y+1);
   ObjectSetInteger(0,n,OBJPROP_XSIZE,TOG_W);    ObjectSetInteger(0,n,OBJPROP_YSIZE,ROW_H-3);
   ObjectSetString (0,n,OBJPROP_TEXT,state?"ON":"OFF");
   ObjectSetString (0,n,OBJPROP_FONT,"Arial Bold"); ObjectSetInteger(0,n,OBJPROP_FONTSIZE,FONT_SMALL);
   ObjectSetInteger(0,n,OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,n,OBJPROP_BGCOLOR,state?C'30,140,30':C'150,30,30');
   ObjectSetInteger(0,n,OBJPROP_BORDER_COLOR,C'60,60,60');
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER); ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,n,OBJPROP_STATE,false);
}

//--- Mini edit box with explicit width (replaces old fixed-width CreateEdit)
void CreateMiniEdit(string n, int x, int y, int w, string val)
{
   ObjectCreate(0,n,OBJ_EDIT,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y+3);
   ObjectSetInteger(0,n,OBJPROP_XSIZE,w);
   ObjectSetInteger(0,n,OBJPROP_YSIZE,ROW_H-6);
   ObjectSetString (0,n,OBJPROP_TEXT,val);
   ObjectSetString (0,n,OBJPROP_FONT,"Consolas");
   ObjectSetInteger(0,n,OBJPROP_FONTSIZE,FONT_SIZE);
   ObjectSetInteger(0,n,OBJPROP_COLOR,C'20,20,20');
   ObjectSetInteger(0,n,OBJPROP_BGCOLOR,C'240,245,180');
   ObjectSetInteger(0,n,OBJPROP_BORDER_COLOR,C'100,100,60');
   ObjectSetInteger(0,n,OBJPROP_ALIGN,ALIGN_RIGHT);
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER);
   ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,n,OBJPROP_BACK,false);   // always on top — clickable
}

void CreateBtn(string n, int x, int y, int w, int h, string txt, color clr, color bg)
{
   ObjectCreate(0,n,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,n,OBJPROP_XDISTANCE,x); ObjectSetInteger(0,n,OBJPROP_YDISTANCE,y);
   ObjectSetInteger(0,n,OBJPROP_XSIZE,w);     ObjectSetInteger(0,n,OBJPROP_YSIZE,h);
   ObjectSetString (0,n,OBJPROP_TEXT,txt);
   ObjectSetString (0,n,OBJPROP_FONT,"Arial Bold"); ObjectSetInteger(0,n,OBJPROP_FONTSIZE,FONT_SIZE);
   ObjectSetInteger(0,n,OBJPROP_COLOR,clr);   ObjectSetInteger(0,n,OBJPROP_BGCOLOR,bg);
   ObjectSetInteger(0,n,OBJPROP_BORDER_COLOR,C'20,160,20');
   ObjectSetInteger(0,n,OBJPROP_CORNER,CORNER); ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,n,OBJPROP_STATE,false);
}

//+------------------------------------------------------------------+
//| เส้น Breakeven รวม — อัปเดตทุก tick                             |
//+------------------------------------------------------------------+
void UpdateBELine()
{
   string sym    = Symbol();
   string lnName = PREFIX+"BE_LINE";
   string txName = PREFIX+"BE_TEXT";

   //--- รวม positions
   double buyLots=0, sellLots=0, buyW=0, sellW=0, totalProfit=0;
   int    count=0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      if(posInfo.PositionType()==POSITION_TYPE_BUY)
         { buyLots+=posInfo.Volume(); buyW+=posInfo.PriceOpen()*posInfo.Volume(); }
      else
         { sellLots+=posInfo.Volume(); sellW+=posInfo.PriceOpen()*posInfo.Volume(); }
      totalProfit += posInfo.Profit()+posInfo.Swap();
      count++;
   }

   //--- ไม่มีไม้ หรือ hedged สมบูรณ์ → ลบเส้น
   double netLots = buyLots - sellLots;
   if(count==0 || MathAbs(netLots)<0.0001)
   {
      ObjectDelete(0,lnName); ObjectDelete(0,txName);
      return;
   }

   int    digits  = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double bePrice = NormalizeDouble((buyW-sellW)/netLots, digits);
   double point   = SymbolInfoDouble(sym, SYMBOL_POINT);

   //--- สร้าง/อัปเดต เส้น dash
   if(ObjectFind(0,lnName)<0)
   {
      ObjectCreate(0,lnName,OBJ_HLINE,0,0,bePrice);
      ObjectSetInteger(0,lnName,OBJPROP_COLOR,  C'220,180,0');
      ObjectSetInteger(0,lnName,OBJPROP_STYLE,  STYLE_DASH);
      ObjectSetInteger(0,lnName,OBJPROP_WIDTH,  1);
      ObjectSetInteger(0,lnName,OBJPROP_BACK,   true);
      ObjectSetInteger(0,lnName,OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0,lnName,OBJPROP_HIDDEN,  false);
   }
   else
      ObjectSetDouble(0,lnName,OBJPROP_PRICE,bePrice);

   //--- tooltip บนเส้น (hover เพื่อดู)
   ObjectSetString(0,lnName,OBJPROP_TOOLTIP,
      StringFormat("Combined BE: %s | %d positions | P&L: $%.2f",
                   DoubleToString(bePrice,digits), count, totalProfit));

   //--- label ข้างขวา: ใช้ OBJ_TEXT ติด bar ล่าสุด + 3 bars ไปทางขวา
   datetime tx = iTime(sym,PERIOD_CURRENT,0) + (long)PeriodSeconds(PERIOD_CURRENT)*3;
   string   txt = StringFormat(" BE %s  ($%.2f)", DoubleToString(bePrice,digits), totalProfit);
   color    clr = (totalProfit >= 0) ? C'220,200,60' : C'220,100,60';

   if(ObjectFind(0,txName)<0)
   {
      ObjectCreate(0,txName,OBJ_TEXT,0,tx,bePrice);
      ObjectSetString (0,txName,OBJPROP_FONT,    "Arial Bold");
      ObjectSetInteger(0,txName,OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0,txName,OBJPROP_BACK,     false);
      ObjectSetInteger(0,txName,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(0,txName,OBJPROP_ANCHOR,   ANCHOR_LEFT);
   }
   ObjectSetInteger(0,txName,OBJPROP_TIME,  tx);
   ObjectSetDouble (0,txName,OBJPROP_PRICE, bePrice + point*4);  // เลื่อนขึ้นนิดนึงไม่ทับเส้น
   ObjectSetString (0,txName,OBJPROP_TEXT,  txt);
   ObjectSetInteger(0,txName,OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
//| Session strip (panel) + Bar countdown (chart) — ทุกวินาที       |
//+------------------------------------------------------------------+
string SessionName(double h, color &clr)
{
   bool isAsian  = (h >= 0  && h < 9);
   bool isLondon = (h >= 7  && h < 16);
   bool isNY     = (h >= 12 && h < 21);
   bool isSydney = (h >= 21 || h < 2);
   if(isSydney && !isAsian && !isLondon && !isNY) { clr=C'80,140,200';  return "● SYD"; }
   if(isAsian  && !isLondon && !isNY)             { clr=C'80,160,220';  return "● TOKYO"; }
   if(isAsian  && isLondon  && !isNY)             { clr=C'80,220,160';  return "● TOKYO + LON"; }
   if(isLondon && !isNY)                          { clr=C'60,200,100';  return "● LONDON"; }
   if(isLondon && isNY)                           { clr=C'240,200,60';  return "● LON + NY  ★"; }
   if(isNY     && !isLondon)                      { clr=C'220,120,40';  return "● NEW YORK"; }
   clr=C'70,70,90'; return "○ CLOSED";
}

void UpdateSessionBar()
{
   static datetime lastSec = 0;
   datetime now = TimeCurrent();
   if(now == lastSec) return;
   lastSec = now;

   //--- Session
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   double h = dt.hour + dt.min/60.0;
   color  sesClr; string sesTxt = SessionName(h, sesClr);
   if(ObjectFind(0,PREFIX+"SES_TXT")>=0)
   { ObjectSetString(0,PREFIX+"SES_TXT",OBJPROP_TEXT,sesTxt); ObjectSetInteger(0,PREFIX+"SES_TXT",OBJPROP_COLOR,sesClr); }

   //--- Bar countdown → OBJ_TEXT เหนือแท่งเทียนปัจจุบัน
   string sym    = Symbol();
   int    period = PeriodSeconds(PERIOD_CURRENT);
   datetime barOpen = iTime(sym, PERIOD_CURRENT, 0);
   int secsLeft = (int)((barOpen + period) - now);
   if(secsLeft < 0) secsLeft = 0;
   string barTxt;
   if(secsLeft >= 3600)
      barTxt = StringFormat("%dh%02dm%02ds", secsLeft/3600, (secsLeft%3600)/60, secsLeft%60);
   else
      barTxt = StringFormat("%02d:%02d", secsLeft/60, secsLeft%60);

   color barClr = (secsLeft<=10) ? C'220,80,80' : (secsLeft<=30) ? C'220,180,60' : C'160,230,160';

   //--- ตำแหน่ง: กึ่งกลางแท่ง (time) + เหนือ high นิดหน่อย (price)
   datetime cntTime  = (datetime)((long)barOpen + period / 2);
   double   barHigh  = iHigh(sym, PERIOD_CURRENT, 0);
   double   rng      = ChartGetDouble(0,CHART_PRICE_MAX) - ChartGetDouble(0,CHART_PRICE_MIN);
   double   cntPrice = barHigh + rng * 0.012;   // สูงกว่า high 1.2% ของ visible range

   string cntName = PREFIX+"BAR_CNT";
   //--- ถ้า object ยังไม่มี หรือเป็นประเภทผิด (เก่าเป็น OBJ_LABEL) → recreate
   if(ObjectFind(0,cntName)>=0 && ObjectGetInteger(0,cntName,OBJPROP_TYPE)!=OBJ_TEXT)
      ObjectDelete(0,cntName);
   if(ObjectFind(0,cntName)<0)
   {
      ObjectCreate(0,cntName,OBJ_TEXT,0,cntTime,cntPrice);
      ObjectSetString (0,cntName,OBJPROP_FONT,       "Arial Bold");
      ObjectSetInteger(0,cntName,OBJPROP_FONTSIZE,   9);
      ObjectSetInteger(0,cntName,OBJPROP_ANCHOR,     ANCHOR_LOWER);
      ObjectSetInteger(0,cntName,OBJPROP_BACK,       false);
      ObjectSetInteger(0,cntName,OBJPROP_SELECTABLE, false);
   }
   ObjectSetInteger(0,cntName,OBJPROP_TIME,  cntTime);
   ObjectSetDouble (0,cntName,OBJPROP_PRICE, cntPrice);
   ObjectSetString (0,cntName,OBJPROP_TEXT,  barTxt);
   ObjectSetInteger(0,cntName,OBJPROP_COLOR, barClr);

   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Session vertical lines + labels บน chart                        |
//+------------------------------------------------------------------+
void DrawSessionLines()
{
   string sym = Symbol();

   //--- ลบเส้นและ label session เก่าทั้งหมด
   for(int i=ObjectsTotal(0)-1; i>=0; i--)
   {
      string nm = ObjectName(0,i);
      if(StringFind(nm, PREFIX+"SSL_")==0 || StringFind(nm, PREFIX+"SSLT_")==0)
         ObjectDelete(0, nm);
   }

   //--- GMT offset ของ broker server
   int gmtOff = (int)(TimeCurrent() - TimeGMT());

   //--- Session definitions: [UTC hour, color, label]
   int    sesHour[4]    = {0,    7,    12,   21};    // UTC open
   int    sesCloseH[4]  = {9,    16,   21,   2};     // UTC close (Sydney close = 02:00 ของวันถัดไป)
   int    thaiHour[4]   = {7,    14,   19,   4};     // เวลาไทย เปิด
   int    thaiCloseH[4] = {16,   23,   4,    9};     // เวลาไทย ปิด
   color  sesClr[4]     = {C'60,120,220', C'50,180,80', C'200,140,30', C'100,70,180'};
   string sesName[4]    = {"TOKYO", "LONDON", "NEW YORK", "SYDNEY"};

   //--- ราคาบนสุดของ chart ปัจจุบัน (สำหรับวาง label)
   double chartMax = ChartGetDouble(0, CHART_PRICE_MAX);
   double chartMin = ChartGetDouble(0, CHART_PRICE_MIN);
   double labelY   = chartMax - (chartMax - chartMin) * 0.05;  // 5% ต่ำกว่า top

   //--- วาดย้อนหลัง 14 วัน
   datetime nowUTC = TimeGMT();
   MqlDateTime utcDt;
   TimeToStruct(nowUTC, utcDt);
   utcDt.hour=0; utcDt.min=0; utcDt.sec=0;
   datetime dayStart = StructToTime(utcDt);  // 00:00 UTC วันนี้

   for(int d=0; d<=14; d++)
   {
      for(int s=0; s<4; s++)
      {
         //--- วาด 2 เส้น: 0=open, 1=close
         for(int oc=0; oc<2; oc++)
         {
            int    utcHr     = (oc==0) ? sesHour[s]    : sesCloseH[s];
            int    thHr      = (oc==0) ? thaiHour[s]   : thaiCloseH[s];
            string tag       = (oc==0) ? ""            : " CLOSE";
            int    dayOffset = 0;
            //--- Sydney close (UTC 02:00) อยู่วันถัดไปจาก open (UTC 21:00)
            if(s==3 && oc==1) dayOffset = -1;  // close วันถัดไป = d-1

            datetime sessUTC    = (datetime)((long)dayStart - (long)(d+dayOffset)*86400 + (long)utcHr*3600);
            datetime sessServer = (datetime)((long)sessUTC + gmtOff);
            if(sessServer > (datetime)((long)TimeCurrent() + 3600)) continue;

            string ln = PREFIX+"SSL_"+IntegerToString(d)+"_"+IntegerToString(s)+"_"+IntegerToString(oc);
            string lt = PREFIX+"SSLT_"+IntegerToString(d)+"_"+IntegerToString(s)+"_"+IntegerToString(oc);

            //--- เส้นแนวตั้ง (open=DOT, close=DASHDOT จางกว่า)
            if(ObjectFind(0,ln)<0)
               ObjectCreate(0,ln,OBJ_VLINE,0,sessServer,0);
            ObjectSetInteger(0,ln,OBJPROP_TIME,    sessServer);
            ObjectSetInteger(0,ln,OBJPROP_COLOR,   sesClr[s]);
            ObjectSetInteger(0,ln,OBJPROP_STYLE,   (oc==0) ? STYLE_DOT : STYLE_DASHDOT);
            ObjectSetInteger(0,ln,OBJPROP_WIDTH,   1);
            ObjectSetInteger(0,ln,OBJPROP_BACK,    true);
            ObjectSetInteger(0,ln,OBJPROP_SELECTABLE, false);

            //--- Text label
            string labelStr = StringFormat(" %s%s  %02d:00", sesName[s], tag, thHr);
            ObjectCreate(0,lt,OBJ_TEXT,0,sessServer,labelY);
            ObjectSetInteger(0,lt,OBJPROP_TIME,    sessServer);
            ObjectSetDouble (0,lt,OBJPROP_PRICE,   labelY);
            ObjectSetString (0,lt,OBJPROP_TEXT,    labelStr);
            ObjectSetString (0,lt,OBJPROP_FONT,    (oc==0) ? "Arial Bold" : "Arial");
            ObjectSetInteger(0,lt,OBJPROP_FONTSIZE, (oc==0) ? 13 : 10);
            ObjectSetInteger(0,lt,OBJPROP_COLOR,   sesClr[s]);
            ObjectSetInteger(0,lt,OBJPROP_ANCHOR,  ANCHOR_LEFT_UPPER);
            ObjectSetInteger(0,lt,OBJPROP_BACK,    false);
            ObjectSetInteger(0,lt,OBJPROP_SELECTABLE, false);
         }
      }
   }
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Save / Load settings ผ่าน GlobalVariable (persist ข้าม restart) |
//+------------------------------------------------------------------+
#define SAVE_PFX (PREFIX+"SAVE_")

//--- คืน "best position open" ของฝั่ง DCA — ใช้เป็น reference สำหรับ step
//--- BUY DCA (averaging down): best = lowest open (ใกล้ราคาปัจจุบันที่สุดถ้าราคาลง)
//--- SELL DCA (averaging up): best = highest open
//--- คืน 0 ถ้าไม่มี position
double DCAReferencePrice()
{
   string sym = Symbol();
   double best = 0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      double op = posInfo.PriceOpen();
      if(best == 0) { best = op; continue; }
      if(g_DCADirection == 1)  { if(op < best) best = op; }  // BUY: lowest
      else                     { if(op > best) best = op; }  // SELL: highest
   }
   return best;
}

//--- Init DCA state จาก positions ปัจจุบัน (ใช้ตอน toggle on หรือ restart)
void InitDCAState()
{
   string sym2 = Symbol();
   int    cnt  = 0;
   double netLots2 = 0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym2) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      cnt++;
      netLots2 += (posInfo.PositionType()==POSITION_TYPE_BUY ? 1.0 : -1.0) * posInfo.Volume();
   }
   g_DCAOpenedCount = cnt;
   g_DCADirection   = (netLots2 >= 0) ? 1 : -1;
   g_DCALastPrice   = (g_DCADirection == 1)
                      ? SymbolInfoDouble(sym2, SYMBOL_BID)
                      : SymbolInfoDouble(sym2, SYMBOL_ASK);
   Print("DCA init: counted ", cnt, " positions | dir=",
         (g_DCADirection==1?"BUY":"SELL"),
         " | lastPrice=", g_DCALastPrice);
}

void SaveSettings()
{
   GlobalVariableSet(SAVE_PFX+"AutoTP",      g_AutoSetTP);
   GlobalVariableSet(SAVE_PFX+"TPTicks",     g_TP_Ticks);
   GlobalVariableSet(SAVE_PFX+"Partial",     g_EnablePartial);
   GlobalVariableSet(SAVE_PFX+"PartPct",     g_PartialClosePct);
   GlobalVariableSet(SAVE_PFX+"PartTrig",    g_PartialTriggerTicks);
   GlobalVariableSet(SAVE_PFX+"ProfTgt",     g_EnableProfitTarget);
   GlobalVariableSet(SAVE_PFX+"ProfTgtVal",  g_TotalProfitTarget);
   GlobalVariableSet(SAVE_PFX+"TotalSL",     g_EnableTotalSL);
   GlobalVariableSet(SAVE_PFX+"TotalSLVal",  g_TotalSLAmount);
   GlobalVariableSet(SAVE_PFX+"ProfTrail",   g_EnableProfitTrail);
   GlobalVariableSet(SAVE_PFX+"ProfArm",     g_ProfitArmAt);
   GlobalVariableSet(SAVE_PFX+"ProfFloor",   g_ProfitFloor);
   GlobalVariableSet(SAVE_PFX+"KeepBest",    g_EnableKeepBest);
   GlobalVariableSet(SAVE_PFX+"KeepN",       g_KeepBestN);
   GlobalVariableSet(SAVE_PFX+"PairClose",   g_EnablePairClose);
   GlobalVariableSet(SAVE_PFX+"PairCloseT",  g_PairCloseTarget);
   GlobalVariableSet(SAVE_PFX+"PairGuard",   g_EnablePairGuard);
   GlobalVariableSet(SAVE_PFX+"PairGuardT",  g_PairGuardTarget);
   GlobalVariableSet(SAVE_PFX+"DCA",         g_EnableDCA);
   GlobalVariableSet(SAVE_PFX+"DCALot",      g_DCALotSize);
   GlobalVariableSet(SAVE_PFX+"DCAStep",     g_DCAStepTicks);
   GlobalVariableSet(SAVE_PFX+"DCAMax",      g_DCAMaxPositions);
   GlobalVariableSet(SAVE_PFX+"DCATier",     g_DCATierSize);
   GlobalVariableSet(SAVE_PFX+"DCALotStep",  g_DCALotStep);
   GlobalVariableSet(SAVE_PFX+"BE",          g_EnableBreakeven);
   GlobalVariableSet(SAVE_PFX+"BETrig",      g_BETriggerTicks);
   GlobalVariableSet(SAVE_PFX+"BELock",      g_BELockTicks);
   GlobalVariableSet(SAVE_PFX+"Trail",       g_EnableTrailing);
   GlobalVariableSet(SAVE_PFX+"TrailStep",   g_TrailStepTicks);
   GlobalVariableSet(SAVE_PFX+"PosGuard",    g_EnablePosGuard);
   GlobalVariableSet(SAVE_PFX+"PosGuardTrig",g_PosGuardTrigger);
   GlobalVariableSet(SAVE_PFX+"PosGuardLk",  g_PosGuardLockTicks);
   GlobalVariableSet(SAVE_PFX+"PosGuardStp", g_PosGuardStepTicks);
   GlobalVariableSet(SAVE_PFX+"PosTarget",   g_EnablePosTarget);
   GlobalVariableSet(SAVE_PFX+"PosTargetN",  g_PosTargetN);
   GlobalVariableSet(SAVE_PFX+"PosTargetD",  g_PosTargetDollar);
   GlobalVariableSet(SAVE_PFX+"PanelX",      PANEL_X);
   GlobalVariableSet(SAVE_PFX+"PanelY",      PANEL_Y);
}

double GVGet(string n, double def) { return GlobalVariableCheck(n) ? GlobalVariableGet(n) : def; }
bool   GVBool(string n, bool def)  { return GlobalVariableCheck(n) ? (GlobalVariableGet(n)!=0) : def; }

void LoadSettings()
{
   g_AutoSetTP            = GVBool(SAVE_PFX+"AutoTP",     g_AutoSetTP);
   g_TP_Ticks             = GVGet (SAVE_PFX+"TPTicks",    g_TP_Ticks);
   g_EnablePartial        = GVBool(SAVE_PFX+"Partial",    g_EnablePartial);
   g_PartialClosePct      = GVGet (SAVE_PFX+"PartPct",    g_PartialClosePct);
   g_PartialTriggerTicks  = GVGet (SAVE_PFX+"PartTrig",   g_PartialTriggerTicks);
   g_EnableProfitTarget   = GVBool(SAVE_PFX+"ProfTgt",    g_EnableProfitTarget);
   g_TotalProfitTarget    = GVGet (SAVE_PFX+"ProfTgtVal", g_TotalProfitTarget);
   g_EnableTotalSL        = GVBool(SAVE_PFX+"TotalSL",    g_EnableTotalSL);
   g_TotalSLAmount        = GVGet (SAVE_PFX+"TotalSLVal", g_TotalSLAmount);
   g_EnableProfitTrail    = GVBool(SAVE_PFX+"ProfTrail",  g_EnableProfitTrail);
   g_ProfitArmAt          = GVGet (SAVE_PFX+"ProfArm",    g_ProfitArmAt);
   g_ProfitFloor          = GVGet (SAVE_PFX+"ProfFloor",  g_ProfitFloor);
   g_EnableKeepBest       = GVBool(SAVE_PFX+"KeepBest",   g_EnableKeepBest);
   g_KeepBestN       = (int)GVGet (SAVE_PFX+"KeepN",      g_KeepBestN);
   g_EnablePairClose      = GVBool(SAVE_PFX+"PairClose",  g_EnablePairClose);
   g_PairCloseTarget      = GVGet (SAVE_PFX+"PairCloseT", g_PairCloseTarget);
   g_EnablePairGuard      = GVBool(SAVE_PFX+"PairGuard",  g_EnablePairGuard);
   g_PairGuardTarget      = GVGet (SAVE_PFX+"PairGuardT", g_PairGuardTarget);
   g_EnableDCA            = GVBool(SAVE_PFX+"DCA",        g_EnableDCA);
   g_DCALotSize           = GVGet (SAVE_PFX+"DCALot",     g_DCALotSize);
   g_DCAStepTicks         = GVGet (SAVE_PFX+"DCAStep",    g_DCAStepTicks);
   g_DCAMaxPositions = (int)GVGet (SAVE_PFX+"DCAMax",     g_DCAMaxPositions);
   g_DCATierSize     = (int)GVGet (SAVE_PFX+"DCATier",    g_DCATierSize);
   g_DCALotStep           = GVGet (SAVE_PFX+"DCALotStep", g_DCALotStep);
   g_EnableBreakeven      = GVBool(SAVE_PFX+"BE",         g_EnableBreakeven);
   g_BETriggerTicks       = GVGet (SAVE_PFX+"BETrig",     g_BETriggerTicks);
   g_BELockTicks          = GVGet (SAVE_PFX+"BELock",     g_BELockTicks);
   g_EnableTrailing       = GVBool(SAVE_PFX+"Trail",       g_EnableTrailing);
   g_TrailStepTicks       = GVGet (SAVE_PFX+"TrailStep",   g_TrailStepTicks);
   g_EnablePosGuard       = GVBool(SAVE_PFX+"PosGuard",    g_EnablePosGuard);
   g_PosGuardTrigger      = GVGet (SAVE_PFX+"PosGuardTrig",g_PosGuardTrigger);
   g_PosGuardLockTicks    = GVGet (SAVE_PFX+"PosGuardLk",  g_PosGuardLockTicks);
   g_PosGuardStepTicks    = GVGet (SAVE_PFX+"PosGuardStp", g_PosGuardStepTicks);
   g_EnablePosTarget      = GVBool(SAVE_PFX+"PosTarget",   g_EnablePosTarget);
   g_PosTargetN      = (int)GVGet (SAVE_PFX+"PosTargetN",  g_PosTargetN);
   g_PosTargetDollar      = GVGet (SAVE_PFX+"PosTargetD",  g_PosTargetDollar);
   PANEL_X           = (int)GVGet (SAVE_PFX+"PanelX",      PANEL_X);
   PANEL_Y           = (int)GVGet (SAVE_PFX+"PanelY",     PANEL_Y);
}

//+------------------------------------------------------------------+
//| Update position labels บน chart (P&L real-time แต่ละไม้)        |
//+------------------------------------------------------------------+
void UpdatePositionLabels()
{
   string sym = Symbol();
   //--- เก็บ tickets ปัจจุบันไว้ลบ label เก่าที่ไม่มี position แล้ว
   ulong  liveTickets[];
   int    n = 0;

   datetime barTime = iTime(sym, PERIOD_CURRENT, 0);
   int      period  = PeriodSeconds(PERIOD_CURRENT);
   datetime labelT  = (datetime)((long)barTime + (long)period * 6);   // ขวาของแท่งล่าสุด 6 bars

   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;

      ulong  ticket = posInfo.Ticket();
      double open   = posInfo.PriceOpen();
      double lot    = posInfo.Volume();
      double pnl    = posInfo.Profit() + posInfo.Swap();
      bool   isBuy  = (posInfo.PositionType()==POSITION_TYPE_BUY);

      ArrayResize(liveTickets, n+1);
      liveTickets[n++] = ticket;

      string name = PREFIX+"POS_"+IntegerToString(ticket);
      string txt  = StringFormat(" %s %.2fL  $%.2f", isBuy?"B":"S", lot, pnl);
      color  clr  = (pnl >= 0) ? C'120,220,120' : C'230,110,110';

      if(ObjectFind(0,name) < 0)
      {
         ObjectCreate(0,name,OBJ_TEXT,0,labelT,open);
         ObjectSetString (0,name,OBJPROP_FONT,    "Arial Bold");
         ObjectSetInteger(0,name,OBJPROP_FONTSIZE, 8);
         ObjectSetInteger(0,name,OBJPROP_ANCHOR,   ANCHOR_LEFT);
         ObjectSetInteger(0,name,OBJPROP_BACK,     false);
         ObjectSetInteger(0,name,OBJPROP_SELECTABLE, false);
      }
      ObjectSetInteger(0,name,OBJPROP_TIME,  labelT);
      ObjectSetDouble (0,name,OBJPROP_PRICE, open);
      ObjectSetString (0,name,OBJPROP_TEXT,  txt);
      ObjectSetInteger(0,name,OBJPROP_COLOR, clr);
   }

   //--- ลบ label ของ positions ที่ปิดไปแล้ว
   int total = ObjectsTotal(0);
   for(int i=total-1; i>=0; i--)
   {
      string oname = ObjectName(0,i);
      if(StringFind(oname, PREFIX+"POS_")!=0) continue;
      //--- extract ticket จากชื่อ
      string ticketStr = StringSubstr(oname, StringLen(PREFIX+"POS_"));
      ulong  tk = (ulong)StringToInteger(ticketStr);
      bool found = false;
      for(int k=0; k<n; k++) if(liveTickets[k]==tk){ found=true; break; }
      if(!found) ObjectDelete(0,oname);
   }
}

//--- ลบเฉพาะ panel objects (เก็บ chart annotations อื่นๆ)
void DeletePanelOnly()
{
   for(int i=ObjectsTotal(0)-1; i>=0; i--)
   {
      string nm = ObjectName(0,i);
      if(StringFind(nm, PREFIX) != 0) continue;
      if(StringFind(nm, PREFIX+"SSL_")  == 0) continue;
      if(StringFind(nm, PREFIX+"SSLT_") == 0) continue;
      if(StringFind(nm, PREFIX+"POS_")  == 0) continue;
      if(StringFind(nm, PREFIX+"FT_")   == 0) continue;  // feature trigger lines
      if(nm == PREFIX+"BE_LINE")  continue;
      if(nm == PREFIX+"BE_TEXT")  continue;
      if(nm == PREFIX+"BAR_CNT")  continue;
      ObjectDelete(0, nm);
   }
}

//+------------------------------------------------------------------+
//| Feature trigger lines — แสดงราคาที่ BE/Trail/Partial จะทำงาน    |
//+------------------------------------------------------------------+
//--- วาดเส้น DCA next entry (global)
void DrawDCALine(datetime barTime, datetime lineEnd, double tickSize, int digits)
{
   string sym = Symbol();

   //--- ถ้า direction ยังไม่ init → init จาก positions ปัจจุบัน
   if(g_EnableDCA && g_DCADirection == 0)
      InitDCAState();

   double refPrice = DCAReferencePrice();
   if(g_EnableDCA
      && g_DCAOpenedCount < g_DCAMaxPositions
      && g_DCADirection != 0
      && refPrice > 0)
   {
      double stepDist  = g_DCAStepTicks * tickSize;
      double threshold = (g_DCADirection == 1)
                         ? NormalizeDouble(refPrice - stepDist, digits)
                         : NormalizeDouble(refPrice + stepDist, digits);

      //--- คำนวณ lot ของ DCA ตัวถัดไป (ตาม CheckDCA)
      double bLot = 0;
      datetime oldestT = 0;
      for(int j=PositionsTotal()-1; j>=0; j--)
      {
         if(!posInfo.SelectByIndex(j)) continue;
         if(posInfo.Symbol() != sym) continue;
         if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
         datetime tt = (datetime)posInfo.Time();
         if(oldestT==0 || tt < oldestT){ oldestT=tt; bLot=posInfo.Volume(); }
      }
      if(bLot <= 0) bLot = g_DCALotSize;
      double nextLot = bLot;
      if(g_DCATierSize > 0 && g_DCALotStep > 0)
      {
         int nextTier = g_DCAOpenedCount / g_DCATierSize;
         nextLot      = bLot + nextTier * g_DCALotStep;
      }

      string dir = (g_DCADirection==1) ? "BUY" : "SELL";
      DrawFTLine(PREFIX+"FT_DCA", barTime, lineEnd, threshold, C'180,120,220', STYLE_DASH,
                 StringFormat(" DCA#%d %s %.2fL @ %s",
                              g_DCAOpenedCount+1, dir, nextLot, DoubleToString(threshold,digits)));
   }
   else DelFTLine(PREFIX+"FT_DCA");
}

void UpdateFeatureLines()
{
   string sym      = Symbol();
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int    digits   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(tickSize == 0) return;

   datetime barTime = iTime(sym, PERIOD_CURRENT, 0);
   int      period  = PeriodSeconds(PERIOD_CURRENT);
   datetime lineEnd = (datetime)((long)barTime + (long)period * 8);

   //--- ตรวจ pending state: ถ้า KeepBest/PairClose/PairGuard ยังจัดการอยู่ → ซ่อนเส้นต่อๆ position
   //--- แต่ยังคงแสดงเส้น DCA (เพราะ DCA ทำงานเป็นอิสระ)
   int posCount = 0;
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      posCount++;
   }
   bool pendingActive = false;
   if(g_EnableKeepBest  && g_keepBestPending)             pendingActive = true;
   if(g_EnablePairClose && posCount > g_KeepBestN)        pendingActive = true;
   if(g_EnablePairGuard && posCount > g_KeepBestN)        pendingActive = true;

   if(pendingActive)
   {
      //--- ลบแค่เส้น per-position (FT_BET_*, FT_BES_*, FT_TR_*, FT_TRSL_*, FT_PC_*)
      //--- เส้น combined (FT_BE, FT_TR, FT_PC) ยังวาดต่อด้านล่าง → ไม่ return ที่นี่
      for(int i=ObjectsTotal(0)-1; i>=0; i--)
      {
         string nm = ObjectName(0,i);
         if(StringFind(nm, PREFIX+"FT_BET_") == 0
         || StringFind(nm, PREFIX+"FT_BES_") == 0
         || StringFind(nm, PREFIX+"FT_TR_")  == 0
         || StringFind(nm, PREFIX+"FT_TRSL_")== 0
         || StringFind(nm, PREFIX+"FT_PC_")  == 0)
            ObjectDelete(0, nm);
      }
   }

   //--- คำนวณ combined BE (ใช้ร่วมกัน BE, Trail, Partial)
   double bePrice, netLots, cur, curTicks;
   bool hasCombined = ComputeCombinedBE(bePrice, netLots, cur, curTicks);
   int sign = (netLots > 0) ? 1 : -1;

   //--- BE TRIGGER (combined) — ราคาที่ต้องถึงเพื่อ activate combined BE
   if(g_EnableBreakeven && hasCombined)
   {
      double trigPrice = NormalizeDouble(bePrice + sign * g_BETriggerTicks * tickSize, digits);
      DrawFTLine(PREFIX+"FT_BE", barTime, lineEnd, trigPrice, C'220,180,40', STYLE_DASH,
                 StringFormat(" BE trig @ %s (bePrice %s)",
                              DoubleToString(trigPrice,digits), DoubleToString(bePrice,digits)));
   }
   else DelFTLine(PREFIX+"FT_BE");

   //--- TRAILING next step (combined) — ใช้ g_trailCombinedHigh
   if(g_EnableTrailing && hasCombined)
   {
      double maxT = (curTicks > g_trailCombinedHigh) ? curTicks : g_trailCombinedHigh;
      double nextStepNum;
      if(maxT < g_BETriggerTicks) nextStepNum = 1;
      else
      {
         double stepsGone = MathFloor((maxT - g_BETriggerTicks) / g_TrailStepTicks);
         nextStepNum = stepsGone + 1;
      }
      double nextTicks = g_BETriggerTicks + nextStepNum * g_TrailStepTicks;
      double trigPrice = NormalizeDouble(bePrice + sign * nextTicks * tickSize, digits);
      DrawFTLine(PREFIX+"FT_TR", barTime, lineEnd, trigPrice, C'90,200,220', STYLE_DASH,
                 StringFormat(" Trail step#%.0f @ %s", nextStepNum, DoubleToString(trigPrice,digits)));
   }
   else DelFTLine(PREFIX+"FT_TR");

   //--- PARTIAL CLOSE next trigger (combined)
   if(g_EnablePartial && hasCombined)
   {
      //--- refPrice = global last partial price (slot ticket=0) หรือ bePrice
      double refPrice = bePrice;
      for(int k=0; k<ArraySize(g_partialTrackTickets); k++)
         if(g_partialTrackTickets[k]==0){ refPrice = g_partialLastPrice[k]; break; }
      double trigPrice = NormalizeDouble(refPrice + sign * g_PartialTriggerTicks * tickSize, digits);
      DrawFTLine(PREFIX+"FT_PC", barTime, lineEnd, trigPrice, C'200,120,200', STYLE_DASH,
                 StringFormat(" Partial %.0f%% @ %s", g_PartialClosePct, DoubleToString(trigPrice,digits)));
   }
   else DelFTLine(PREFIX+"FT_PC");

   //--- ลบ per-position FT lines เก่า (ถ้า pendingActive ลบไปแล้ว, ถ้าไม่ก็ลบที่นี่)
   if(!pendingActive)
   {
      for(int i=ObjectsTotal(0)-1; i>=0; i--)
      {
         string nm = ObjectName(0,i);
         if(StringFind(nm, PREFIX+"FT_BET_") == 0
         || StringFind(nm, PREFIX+"FT_BES_") == 0
         || StringFind(nm, PREFIX+"FT_TR_")  == 0
         || StringFind(nm, PREFIX+"FT_TRSL_")== 0
         || StringFind(nm, PREFIX+"FT_PC_")  == 0)
            ObjectDelete(0, nm);
      }
   }

   //--- DCA next entry line (global — ไม่ผูกกับ position)
   DrawDCALine(barTime, lineEnd, tickSize, digits);
   ChartRedraw();
}

//--- Helper: ลบทั้งเส้นและ text label
void DelFTLine(string name)
{
   ObjectDelete(0, name);
   ObjectDelete(0, name+"_TX");
}

//--- Helper: วาด HLINE + text label ด้านขวา
//--- ใช้ OBJ_HLINE แทน OBJ_TREND เพื่อความเสถียร (ไม่ต้องอัพเดต time coordinates ทุก tick)
void DrawFTLine(string name, datetime t1, datetime t2, double price, color clr, ENUM_LINE_STYLE style, string label)
{
   //--- HLINE — stable, ไม่กระพริบ
   //--- ถ้า object มีอยู่แต่เป็น type อื่น (เช่น OBJ_TREND เก่า) → ลบแล้วสร้างใหม่
   if(ObjectFind(0,name)>=0 && ObjectGetInteger(0,name,OBJPROP_TYPE)!=OBJ_HLINE)
      ObjectDelete(0,name);
   if(ObjectFind(0,name)<0)
   {
      ObjectCreate(0,name,OBJ_HLINE,0,0,price);
      ObjectSetInteger(0,name,OBJPROP_BACK,       false);
      ObjectSetInteger(0,name,OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0,name,OBJPROP_WIDTH,      1);
   }
   ObjectSetDouble (0,name,OBJPROP_PRICE, price);
   ObjectSetInteger(0,name,OBJPROP_COLOR, clr);
   ObjectSetInteger(0,name,OBJPROP_STYLE, style);

   //--- text label ขวาของ bar ล่าสุด (อัพเดต position + text ถ้าเปลี่ยน)
   string txName = name+"_TX";
   if(ObjectFind(0,txName)<0)
   {
      ObjectCreate(0,txName,OBJ_TEXT,0,t2,price);
      ObjectSetString (0,txName,OBJPROP_FONT,       "Arial");
      ObjectSetInteger(0,txName,OBJPROP_FONTSIZE,   7);
      ObjectSetInteger(0,txName,OBJPROP_ANCHOR,     ANCHOR_LEFT);
      ObjectSetInteger(0,txName,OBJPROP_BACK,       false);
      ObjectSetInteger(0,txName,OBJPROP_SELECTABLE, false);
   }
   //--- time ต้องอัพเดตทุกครั้ง ให้ label ลอยขอบขวาตาม bar ใหม่เสมอ
   ObjectSetInteger(0,txName,OBJPROP_TIME, t2);
   //--- price/text อัพเดตเฉพาะเมื่อเปลี่ยน เพื่อลด flicker
   if(MathAbs(ObjectGetDouble(0,txName,OBJPROP_PRICE)-price) > 1e-10
   || ObjectGetString(0,txName,OBJPROP_TEXT) != label)
   {
      ObjectSetDouble (0,txName,OBJPROP_PRICE, price);
      ObjectSetString (0,txName,OBJPROP_TEXT,  label);
      ObjectSetInteger(0,txName,OBJPROP_COLOR, clr);
   }
}

void DeletePanel() { ObjectsDeleteAll(0,PREFIX); ChartRedraw(); }

//+------------------------------------------------------------------+
//| Helpers                                                           |
//+------------------------------------------------------------------+
int CollectPositions(string sym, ulong &tix[], double &lts[], double &opn[],
                     int &typ[], double &sl[])
{
   int cnt=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol()!=sym) continue;
      if(InpMagicNumber!=0 && posInfo.Magic()!=InpMagicNumber) continue;
      ArrayResize(tix,cnt+1); ArrayResize(lts,cnt+1);
      ArrayResize(opn,cnt+1); ArrayResize(typ,cnt+1); ArrayResize(sl,cnt+1);
      tix[cnt]=posInfo.Ticket(); lts[cnt]=posInfo.Volume();
      opn[cnt]=posInfo.PriceOpen(); typ[cnt]=(int)posInfo.PositionType();
      sl[cnt]=posInfo.StopLoss(); cnt++;
   }
   return cnt;
}

double NormVol(string sym, double vol)
{
   double step=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   double minL=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double maxL=SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX);
   vol=MathFloor(vol/step)*step;
   if(vol<minL) return 0;
   return MathMin(NormalizeDouble(vol,2),maxL);
}

bool IsInArray(ulong &arr[], ulong val)
{ for(int i=0;i<ArraySize(arr);i++) if(arr[i]==val) return true; return false; }

void Append(ulong &arr[], ulong val)
{ int s=ArraySize(arr); ArrayResize(arr,s+1); arr[s]=val; }

void AppendDouble(double &arr[], double val)
{ int s=ArraySize(arr); ArrayResize(arr,s+1); arr[s]=val; }
