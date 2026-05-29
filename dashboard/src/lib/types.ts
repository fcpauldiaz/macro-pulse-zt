export type SnapshotSummary = {
  id: number;
  snapshotDate: string;
  regime: string;
  pulseUpdatedAt: string | null;
  momentumCount: number;
  quantCount: number;
};

export type DailySignal = {
  symbol: string;
  etf: string | null;
  price: number | null;
  sma50: number | null;
  beta: number | null;
  quantScore: number | null;
  rsi: number | null;
  signalLabel: string | null;
  sector: string | null;
  industry: string | null;
};

export type TradePosition = {
  id: number;
  signalType: "momentum" | "quant";
  symbol: string;
  entryDate: string;
  entryPrice: number;
  exitDate: string | null;
  exitPrice: number | null;
  lastPrice: number | null;
  returnPct: number | null;
  status: "open" | "closed";
};

export type DashboardData = {
  snapshot: SnapshotSummary | null;
  momentumSignals: DailySignal[];
  quantSignals: DailySignal[];
  openPositions: TradePosition[];
  closedPositions: TradePosition[];
  stats: {
    openCount: number;
    closedCount: number;
    avgOpenReturn: number | null;
    avgClosedReturn: number | null;
    winRate: number | null;
  };
};
