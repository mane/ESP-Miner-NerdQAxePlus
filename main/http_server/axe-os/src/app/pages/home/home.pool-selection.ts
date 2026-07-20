import { IDashboardV2Pool } from '../../models/IDashboardV2';

/**
 * Select an explicit pool for dual-pool tiles, or the backend-marked active
 * pool for the single failover tile. Legacy payloads fall back to index 0.
 */
export function selectDashboardPool(
  pools: readonly IDashboardV2Pool[] | null | undefined,
  index?: number,
): IDashboardV2Pool | undefined {
  if (!Array.isArray(pools) || pools.length === 0) return undefined;

  if (index !== undefined && Number.isInteger(index) && index >= 0) {
    return pools[index];
  }

  return pools.find((pool) => pool?.active === true) ?? pools[0];
}

export function dashboardPoolRejectRate(
  pools: readonly IDashboardV2Pool[] | null | undefined,
  index?: number,
): number {
  const pool = selectDashboardPool(pools, index);
  if (!pool) return 0;

  const rejected = Number(pool.rejected ?? 0);
  const accepted = Number(pool.accepted ?? 0);
  const total = accepted + rejected;
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(rejected)) return 0;

  return (rejected / total) * 100;
}
