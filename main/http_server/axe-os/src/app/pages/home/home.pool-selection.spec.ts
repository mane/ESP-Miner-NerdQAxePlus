import { IDashboardV2Pool } from '../../models/IDashboardV2';
import { dashboardPoolRejectRate, selectDashboardPool } from './home.pool-selection';

function pool(active: boolean | undefined, accepted: number, rejected: number): IDashboardV2Pool {
  return {
    active,
    host: active ? 'fallback.example' : 'primary.example',
    port: 3333,
    user: 'miner',
    connected: true,
    activeProtocol: 0,
    encrypted: false,
    accepted,
    rejected,
    bestDiff: 0,
    pingRtt: 0,
    pingLoss: 0,
    poolDifficulty: 1,
  };
}

describe('home pool selection', () => {
  it('shows failover stats and endpoint from the active fallback pool', () => {
    const pools = [pool(false, 0, 0), pool(true, 80, 20)];

    expect(selectDashboardPool(pools)).toBe(pools[1]);
    expect(dashboardPoolRejectRate(pools)).toBe(20);
  });

  it('honours explicit dual-pool indices even when active flags are present', () => {
    const pools = [pool(true, 90, 10), pool(true, 50, 0)];

    expect(selectDashboardPool(pools, 0)).toBe(pools[0]);
    expect(selectDashboardPool(pools, 1)).toBe(pools[1]);
    expect(dashboardPoolRejectRate(pools, 0)).toBe(10);
  });

  it('keeps compatibility with legacy payloads without an active flag', () => {
    const primary = pool(undefined, 3, 0);

    expect(selectDashboardPool([primary])).toBe(primary);
    expect(selectDashboardPool([])).toBeUndefined();
    expect(dashboardPoolRejectRate(undefined)).toBe(0);
  });
});
