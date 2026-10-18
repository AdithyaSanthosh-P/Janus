import test from 'node:test';
import assert from 'node:assert';
import { JanusEventReducer } from '../janusStore';
import { SAMPLE_EVENTS } from '../../data/sampleEvents';
import { isValidJanusPacket } from '../../utils/eventValidator';

test('isValidJanusPacket enforces contract rules', () => {
  // Valid packet
  const valid = { v: 1, seq: 1, t_ms: 100, type: 'session_started', backend: 'mock', packs: [] };
  assert.strictEqual(isValidJanusPacket(valid, 'janus.events', 'agent-001'), true);
  assert.strictEqual(isValidJanusPacket(valid, 'janus.events', 'janus-replay'), true);

  // Rejects invalid topic
  assert.strictEqual(isValidJanusPacket(valid, 'wrong.topic', 'agent-001'), false);

  // Rejects invalid sender
  assert.strictEqual(isValidJanusPacket(valid, 'janus.events', 'attacker-hacker'), false);

  // Rejects invalid schema version
  assert.strictEqual(isValidJanusPacket({ ...valid, v: 2 }, 'janus.events', 'agent-001'), false);
});

test('Processes all 76 recorded sample session events with high fidelity', () => {
  const store = new JanusEventReducer();

  assert.strictEqual(SAMPLE_EVENTS.length, 76);

  for (const event of SAMPLE_EVENTS) {
    const accepted = store.processEvent(event);
    assert.strictEqual(accepted, true);
  }

  const state = store.getState();

  // Check backend
  assert.strictEqual(state.backend, 'mock');

  // Check actions count
  // In sample_events: c-0001, c-0002, c-0003, c-0004, c-0005, c-0006, c-0007
  assert.strictEqual(state.actions.length, 7);

  // 1. Verify c-0002 was cancelled ("Never sent" key visual)
  const c0002 = state.actions.find(a => a.callId === 'c-0002');
  assert.ok(c0002);
  assert.strictEqual(c0002.status, 'cancelled');
  assert.strictEqual(c0002.statusText, 'Never sent');
  assert.strictEqual(c0002.cancelledReason, 'You changed it to 26');

  // 2. Verify c-0004 duplicate was blocked ("Already locked, not sent again")
  const c0004 = state.actions.find(a => a.callId === 'c-0004');
  assert.ok(c0004);
  assert.strictEqual(c0004.status, 'already');
  assert.strictEqual(c0004.statusText, 'Already done, not sent again');

  // 3. Verify c-0005 security confirmation declined
  const c0005 = state.actions.find(a => a.callId === 'c-0005');
  assert.ok(c0005);
  assert.strictEqual(c0005.security, true);
  assert.strictEqual(c0005.status, 'cancelled');
  assert.strictEqual(c0005.cancelledReason, 'You said no');

  // 4. Verify c-0006 was unconfirmed: MUST NOT be done! Must be "Sent, not confirmed"
  const c0006 = state.actions.find(a => a.callId === 'c-0006');
  assert.ok(c0006);
  assert.strictEqual(c0006.status, 'unconfirmed');
  assert.strictEqual(c0006.statusText, 'Sent, not confirmed');

  // 5. Verify c-0007 washer diagnosis
  const c0007 = state.actions.find(a => a.callId === 'c-0007');
  assert.ok(c0007);
  assert.strictEqual(c0007.status, 'confirmed');
  assert.strictEqual(state.devices['dev-washer'].value, 'pause');

  // 6. Verify Ledger Summary matches contract
  assert.strictEqual(state.ledger.committed, 2);
  assert.strictEqual(state.ledger.withdrawnBeforeSending, 2);
  assert.strictEqual(state.ledger.unconfirmed, 1);
  assert.strictEqual(state.ledger.duplicatesBlocked, 1);

  // 7. Verify Deduplication
  const replayed = store.processEvent(SAMPLE_EVENTS[0]);
  assert.strictEqual(replayed, false); // dropped as duplicate
});
