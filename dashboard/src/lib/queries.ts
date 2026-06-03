import { asNumber, asString, getDb } from "@/lib/db";
import { ensureSchema } from "@/lib/schema";
import type {
  DailySignal,
  DashboardData,
  SnapshotSummary,
  TradePosition,
} from "@/lib/types";

function mapSignal(row: Record<string, unknown>): DailySignal {
  return {
    symbol: asString(row.symbol),
    etf: asString(row.etf) || null,
    price: asNumber(row.price),
    sma50: asNumber(row.sma50),
    beta: asNumber(row.beta),
    quantScore: asNumber(row.quant_score),
    rsi: asNumber(row.rsi),
    signalLabel: asString(row.signal_label) || null,
    sector: asString(row.sector) || null,
    industry: asString(row.industry) || null,
  };
}

function mapPosition(row: Record<string, unknown>): TradePosition {
  return {
    id: Number(row.id),
    signalType: asString(row.signal_type) as "momentum" | "quant",
    symbol: asString(row.symbol),
    entryDate: asString(row.entry_date),
    entryPrice: asNumber(row.entry_price) ?? 0,
    exitDate: asString(row.exit_date) || null,
    exitPrice: asNumber(row.exit_price),
    lastPrice: asNumber(row.last_price),
    returnPct: asNumber(row.return_pct),
    status: asString(row.status) as "open" | "closed",
  };
}

export async function getDashboardData(): Promise<DashboardData> {
  const db = getDb();
  await ensureSchema(db);

  const snapshotResult = await db.execute(`
    SELECT
      s.id,
      s.snapshot_date,
      s.regime,
      s.pulse_updated_at,
      SUM(CASE WHEN d.signal_type = 'momentum' THEN 1 ELSE 0 END) AS momentum_count,
      SUM(CASE WHEN d.signal_type = 'quant' THEN 1 ELSE 0 END) AS quant_count
    FROM daily_snapshots s
    LEFT JOIN daily_signals d ON d.snapshot_id = s.id
    GROUP BY s.id
    ORDER BY s.snapshot_date DESC
    LIMIT 1
  `);

  if (snapshotResult.rows.length === 0) {
    return {
      snapshot: null,
      momentumSignals: [],
      quantSignals: [],
      openPositions: [],
      closedPositions: [],
      stats: {
        openCount: 0,
        closedCount: 0,
        avgOpenReturn: null,
        avgClosedReturn: null,
        winRate: null,
      },
    };
  }

  const snapshotRow = snapshotResult.rows[0] as Record<string, unknown>;
  const snapshot: SnapshotSummary = {
    id: Number(snapshotRow.id),
    snapshotDate: asString(snapshotRow.snapshot_date),
    regime: asString(snapshotRow.regime),
    pulseUpdatedAt: asString(snapshotRow.pulse_updated_at) || null,
    momentumCount: Number(snapshotRow.momentum_count ?? 0),
    quantCount: Number(snapshotRow.quant_count ?? 0),
  };

  const signalsResult = await db.execute({
    sql: `
      SELECT symbol, etf, price, sma50, beta, quant_score, rsi, signal_label, sector, industry, signal_type
      FROM daily_signals
      WHERE snapshot_id = ?
      ORDER BY signal_type, symbol
    `,
    args: [snapshot.id],
  });

  const momentumSignals: DailySignal[] = [];
  const quantSignals: DailySignal[] = [];

  for (const row of signalsResult.rows) {
    const mapped = mapSignal(row as Record<string, unknown>);
    if (asString((row as Record<string, unknown>).signal_type) === "momentum") {
      momentumSignals.push(mapped);
    } else {
      quantSignals.push(mapped);
    }
  }

  const openResult = await db.execute(`
    SELECT id, signal_type, symbol, entry_date, entry_price, exit_date, exit_price, last_price, return_pct, status
    FROM trade_positions
    WHERE status = 'open'
    ORDER BY return_pct DESC, symbol ASC
  `);

  const closedResult = await db.execute(`
    SELECT id, signal_type, symbol, entry_date, entry_price, exit_date, exit_price, last_price, return_pct, status
    FROM trade_positions
    WHERE status = 'closed'
    ORDER BY exit_date DESC, return_pct DESC
    LIMIT 50
  `);

  const openPositions = openResult.rows.map((row) =>
    mapPosition(row as Record<string, unknown>),
  );
  const closedPositions = closedResult.rows.map((row) =>
    mapPosition(row as Record<string, unknown>),
  );

  const openReturns = openPositions
    .map((position) => position.returnPct)
    .filter((value): value is number => value !== null);
  const closedReturns = closedPositions
    .map((position) => position.returnPct)
    .filter((value): value is number => value !== null);

  const avg = (values: number[]) =>
    values.length > 0
      ? values.reduce((sum, value) => sum + value, 0) / values.length
      : null;

  const wins = closedReturns.filter((value) => value > 0).length;

  return {
    snapshot,
    momentumSignals,
    quantSignals,
    openPositions,
    closedPositions,
    stats: {
      openCount: openPositions.length,
      closedCount: closedPositions.length,
      avgOpenReturn: avg(openReturns),
      avgClosedReturn: avg(closedReturns),
      winRate:
        closedReturns.length > 0 ? (wins / closedReturns.length) * 100 : null,
    },
  };
}
