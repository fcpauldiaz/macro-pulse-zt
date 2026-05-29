import type { TradePosition } from "@/lib/types";

function formatPct(value: number | null): string {
  if (value === null) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function returnClass(value: number | null): string {
  if (value === null) {
    return "return-neutral";
  }
  if (value > 0) {
    return "return-positive";
  }
  if (value < 0) {
    return "return-negative";
  }
  return "return-neutral";
}

type PerformanceTableProps = {
  title: string;
  rows: TradePosition[];
  emptyMessage: string;
};

export function PerformanceTable({
  title,
  rows,
  emptyMessage,
}: PerformanceTableProps) {
  return (
    <section className="panel">
      <div className="signal-panel-header">
        <div>
          <p className="signal-kicker">Trade tracking</p>
          <h3 className="signal-title">{title}</h3>
        </div>
        <span className="signal-count">{rows.length} positions</span>
      </div>

      {rows.length === 0 ? (
        <div className="empty-state compact">{emptyMessage}</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Ticker</th>
                <th>Entry</th>
                <th>Last</th>
                <th>Return</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <span
                      className={`type-pill ${
                        row.signalType === "momentum"
                          ? "type-momentum"
                          : "type-quant"
                      }`}
                    >
                      {row.signalType}
                    </span>
                  </td>
                  <td className="mono ticker-symbol">{row.symbol}</td>
                  <td className="mono">
                    <div>{row.entryDate}</div>
                    <div className="ticker-meta">${row.entryPrice.toFixed(2)}</div>
                  </td>
                  <td className="mono">
                    {row.status === "closed" ? (
                      <>
                        <div>{row.exitDate}</div>
                        <div className="ticker-meta">
                          ${(row.exitPrice ?? row.lastPrice ?? 0).toFixed(2)}
                        </div>
                      </>
                    ) : (
                      `$${(row.lastPrice ?? row.entryPrice).toFixed(2)}`
                    )}
                  </td>
                  <td className={`mono ${returnClass(row.returnPct)}`}>
                    {formatPct(row.returnPct)}
                  </td>
                  <td>
                    <span
                      className={`status-pill ${
                        row.status === "open" ? "status-open" : "status-closed"
                      }`}
                    >
                      {row.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
