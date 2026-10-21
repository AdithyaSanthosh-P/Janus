import {
  buildDrillPacket,
  buildStUnreachablePacket,
  buildStNoConfirmPacket,
  buildStLatencyPacket,
  buildModelSlowPacket,
  buildModelDownPacket,
  buildExternalCommandPacket,
  buildResetPacket,
} from '../drillBuilder';

describe('Drill Message Builder (topic: janus.control)', () => {
  it('builds valid v1 drill packet with strict boolean on field', () => {
    const packet = buildDrillPacket('st_unreachable', true);
    expect(packet).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_unreachable',
      on: true,
      args: {},
    });
    expect(typeof packet.on).toBe('boolean');

    // Truthy and falsy values must coerce to exact boolean primitives
    const coercedTrue = buildDrillPacket('st_unreachable', (1 as any));
    expect(coercedTrue.on).toBe(true);
    expect(typeof coercedTrue.on).toBe('boolean');

    const coercedFalse = buildDrillPacket('st_unreachable', (0 as any));
    expect(coercedFalse.on).toBe(false);
    expect(typeof coercedFalse.on).toBe('boolean');
  });

  it('builds st_unreachable toggle packet', () => {
    expect(buildStUnreachablePacket(true)).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_unreachable',
      on: true,
      args: {},
    });

    expect(buildStUnreachablePacket(false)).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_unreachable',
      on: false,
      args: {},
    });
  });

  it('builds st_no_confirm toggle packet', () => {
    expect(buildStNoConfirmPacket(true)).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_no_confirm',
      on: true,
      args: {},
    });

    expect(buildStNoConfirmPacket(false)).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_no_confirm',
      on: false,
      args: {},
    });
  });

  it('builds st_latency toggle packet with ms argument', () => {
    expect(buildStLatencyPacket(true, 3000)).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_latency',
      on: true,
      args: { ms: 3000 },
    });

    expect(buildStLatencyPacket(false)).toEqual({
      v: 1,
      type: 'drill',
      name: 'st_latency',
      on: false,
      args: {},
    });
  });

  it('builds model_slow toggle packet with ms argument', () => {
    expect(buildModelSlowPacket(true, 4000)).toEqual({
      v: 1,
      type: 'drill',
      name: 'model_slow',
      on: true,
      args: { ms: 4000 },
    });

    expect(buildModelSlowPacket(false)).toEqual({
      v: 1,
      type: 'drill',
      name: 'model_slow',
      on: false,
      args: {},
    });
  });

  it('builds model_down toggle packet', () => {
    expect(buildModelDownPacket(true)).toEqual({
      v: 1,
      type: 'drill',
      name: 'model_down',
      on: true,
      args: {},
    });

    expect(buildModelDownPacket(false)).toEqual({
      v: 1,
      type: 'drill',
      name: 'model_down',
      on: false,
      args: {},
    });
  });

  it('builds external_command packets for unlock and lock', () => {
    const unlockPacket = buildExternalCommandPacket('unlock');
    expect(unlockPacket).toEqual({
      v: 1,
      type: 'drill',
      name: 'external_command',
      on: true,
      args: {
        device: 'Janus Lock',
        capability: 'lock',
        command: 'unlock',
      },
    });
    expect(typeof unlockPacket.on).toBe('boolean');

    const lockPacket = buildExternalCommandPacket('lock');
    expect(lockPacket).toEqual({
      v: 1,
      type: 'drill',
      name: 'external_command',
      on: true,
      args: {
        device: 'Janus Lock',
        capability: 'lock',
        command: 'lock',
      },
    });
    expect(typeof lockPacket.on).toBe('boolean');
  });

  it('builds reset packet with on: true', () => {
    const resetPacket = buildResetPacket();
    expect(resetPacket).toEqual({
      v: 1,
      type: 'drill',
      name: 'reset',
      on: true,
      args: {},
    });
    expect(typeof resetPacket.on).toBe('boolean');
  });
});

describe('Offer and garage packets', () => {
  it('builds an offer packet the agent accepts', () => {
    const { buildOfferPacket } = require('../drillBuilder');
    expect(buildOfferPacket('garage_open')).toEqual({ v: 1, type: 'drill', name: 'offer', on: true, args: { kind: 'garage_open' } });
    expect(buildOfferPacket('door_unlocked').args).toEqual({ kind: 'door_unlocked' });
  });

  it('builds garage commands with the doorControl capability', () => {
    const { buildGaragePacket } = require('../drillBuilder');
    expect(buildGaragePacket('open')).toEqual({
      v: 1, type: 'drill', name: 'external_command', on: true,
      args: { device: 'Janus Garage', capability: 'doorControl', command: 'open' },
    });
    expect(buildGaragePacket('close').args.command).toBe('close');
  });
});
