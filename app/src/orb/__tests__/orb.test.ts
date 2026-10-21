import { MODE_FRAMES } from '../engine/registry';
import { resolvePreset } from '../presets';
import { deriveOrbState, ORB_STATES } from '../orbState';
import { ActionCardData } from '../../types/janus';

describe('vendored thinking-orbs engine', () => {
  it.each(ORB_STATES)('%s draws finite, bounded frames at both sizes', state => {
    for (const size of [20, 64] as const) {
      const { mode, speed, opts } = resolvePreset(state, size);
      for (const t of [0.2, 1.3, 7.9]) {
        const frame = MODE_FRAMES[mode](size, t * speed, opts);
        expect(frame.dots.length).toBeGreaterThan(0);
        expect(frame.dots.length).toBeLessThan(700);
        for (const d of frame.dots) {
          expect(Number.isFinite(d.x + d.y + d.r + d.white)).toBe(true);
        }
      }
    }
  });
});

const card = (status: ActionCardData['status']) => ({ callId: status, status } as ActionCardData);
const idle = { connectionStatus: 'connected' as const, userSpeaking: false, agentSpeaking: false, actions: [] as ActionCardData[] };

describe('deriveOrbState', () => {
  it('idles as breathing', () => expect(deriveOrbState(idle)).toBe('breathing'));
  it('connecting wins over everything', () =>
    expect(deriveOrbState({ ...idle, connectionStatus: 'connecting', userSpeaking: true })).toBe('connecting'));
  it('listens while the user speaks, even with work in flight', () =>
    expect(deriveOrbState({ ...idle, userSpeaking: true, actions: [card('sent')] })).toBe('listening'));
  it('a question for the user takes the shaping pattern', () =>
    expect(deriveOrbState({ ...idle, actions: [card('sent'), card('waiting_for_yes')] })).toBe('shaping'));
  it('a sent, unconfirmed command searches', () => expect(deriveOrbState({ ...idle, actions: [card('sent')] })).toBe('searching'));
  it('planned or held work is solving', () => {
    expect(deriveOrbState({ ...idle, actions: [card('planned')] })).toBe('solving');
    expect(deriveOrbState({ ...idle, actions: [card('held')] })).toBe('solving');
  });
  it('finished actions do not keep it busy', () =>
    expect(deriveOrbState({ ...idle, actions: [card('confirmed'), card('cancelled'), card('unconfirmed')] })).toBe('breathing'));
  it('speaks when idle and Janus is talking', () => expect(deriveOrbState({ ...idle, agentSpeaking: true })).toBe('composing'));
});


describe('orb minimum hold', () => {
  const { holdFor } = require('../useHeldOrbState');
  it('switches to listening and connecting at once', () => {
    expect(holdFor('solving', 'listening', 100, 1200)).toBe(0);
    expect(holdFor('breathing', 'connecting', 0, 1200)).toBe(0);
  });
  it('holds any other pattern until it has been up long enough', () => {
    expect(holdFor('searching', 'solving', 300, 1200)).toBe(900);
    expect(holdFor('searching', 'breathing', 1500, 1200)).toBe(0);
  });
});
