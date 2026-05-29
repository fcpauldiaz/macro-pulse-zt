import { createClient, type Client } from "@libsql/client";

let client: Client | null = null;

export function getDb(): Client {
  if (client) {
    return client;
  }

  const url = process.env.TURSO_DATABASE_URL ?? "file:../local.db";
  const authToken = process.env.TURSO_AUTH_TOKEN;

  client = createClient({
    url,
    authToken,
  });

  return client;
}

export function asNumber(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function asString(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}
