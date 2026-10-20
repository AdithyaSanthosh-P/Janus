export type DrillName =
  | 'st_unreachable'
  | 'st_no_confirm'
  | 'st_latency'
  | 'model_slow'
  | 'model_down'
  | 'external_command'
  | 'reset';

export interface DrillPacket {
  v: 1;
  type: 'drill';
  name: DrillName;
  on: boolean;
  args: Record<string, any>;
}

export interface DrillState {
  st_unreachable: boolean;
  st_no_confirm: boolean;
  st_latency: boolean;
  model_slow: boolean;
  model_down: boolean;
}

export const INITIAL_DRILL_STATE: DrillState = {
  st_unreachable: false,
  st_no_confirm: false,
  st_latency: false,
  model_slow: false,
  model_down: false,
};

/**
 * Message builder for LiveKit topic `janus.control`.
 * Contract (app/INTEGRATION.md §6):
 * - v: 1
 * - type: "drill"
 * - name: string
 * - on: explicit boolean (true / false)
 * - args: object
 */
export function buildDrillPacket(
  name: DrillName,
  on: boolean,
  args: Record<string, any> = {}
): DrillPacket {
  return {
    v: 1,
    type: 'drill',
    name,
    on: Boolean(on), // Guarantees primitive boolean
    args: args || {},
  };
}

export function buildStUnreachablePacket(on: boolean): DrillPacket {
  return buildDrillPacket('st_unreachable', on, {});
}

export function buildStNoConfirmPacket(on: boolean): DrillPacket {
  return buildDrillPacket('st_no_confirm', on, {});
}

export function buildStLatencyPacket(on: boolean, latencyMs = 3000): DrillPacket {
  return buildDrillPacket('st_latency', on, on ? { ms: latencyMs } : {});
}

export function buildModelSlowPacket(on: boolean, slowMs = 4000): DrillPacket {
  return buildDrillPacket('model_slow', on, on ? { ms: slowMs } : {});
}

export function buildModelDownPacket(on: boolean): DrillPacket {
  return buildDrillPacket('model_down', on, {});
}

export function buildExternalCommandPacket(
  command: 'unlock' | 'lock',
  device = 'Janus Lock',
  capability = 'lock'
): DrillPacket {
  return buildDrillPacket('external_command', true, {
    device,
    capability,
    command,
  });
}

export function buildResetPacket(): DrillPacket {
  return buildDrillPacket('reset', true, {});
}
