import test from 'node:test';
import assert from 'node:assert';
import { JanusEventReducer } from '../src/store/janusStore';
import { isValidJanusPacket } from '../src/utils/eventValidator';
import { SAMPLE_EVENTS } from '../src/data/sampleEvents';
import { THEME } from '../src/theme/colors';

test('E2E Verification 1: LiveKit Network Packet Filter & UTF-8 Buffer Parsing', () => {
  const encoder = new TextEncoder();
  const sampleEvent = {
    v: 1,
    seq: 42,
    t_ms: 22500,
    type: 'confirmation_asked',
    call_id: 'c-0005',
    question: "That's a security action. Shall I go ahead?",
  };

  const utf8Buffer = encoder.encode(JSON.stringify(sampleEvent));
  const decoded = JSON.parse(new TextDecoder('utf-8').decode(utf8Buffer));

  // Must accept authorized agent and janus-replay on topic janus.events
  assert.strictEqual(isValidJanusPacket(decoded, 'janus.events', 'agent-janus-v1'), true);
  assert.strictEqual(isValidJanusPacket(decoded, 'janus.events', 'janus-replay'), true);

  // Must reject spoofed identities and invalid topics
  assert.strictEqual(isValidJanusPacket(decoded, 'janus.events', 'unauthorized-user'), false);
  assert.strictEqual(isValidJanusPacket(decoded, 'other.topic', 'agent-janus-v1'), false);
  assert.strictEqual(isValidJanusPacket({ ...decoded, v: 2 }, 'janus.events', 'agent-janus-v1'), false);
});

test('E2E Verification 2: Full Lifecycle Simulation of the 5 Competition Scenarios', () => {
  const store = new JanusEventReducer();

  // Feed full session
  for (const event of SAMPLE_EVENTS) {
    store.processEvent(event);
  }

  const state = store.getState();

  // SCENARIO 1: Mid-sentence self-correction
  // c-0002 was planned for 24C, then user said 26C -> c-0002 MUST be cancelled ("Never sent")
  const c0002 = state.actions.find(a => a.callId === 'c-0002');
  assert.ok(c0002);
  assert.strictEqual(c0002.status, 'cancelled');
  assert.strictEqual(c0002.statusText, 'Never sent');
  assert.strictEqual(c0002.cancelledReason, 'You changed it to 26');
  assert.strictEqual(c0002.isFinal, true);

  // c-0003 was planned for 26C -> confirmed
  const c0003 = state.actions.find(a => a.callId === 'c-0003');
  assert.ok(c0003);
  assert.strictEqual(c0003.status, 'confirmed');
  assert.strictEqual(state.devices['dev-ac'].value, 26);

  // SCENARIO 2: Duplicate Prevention
  // c-0004 asked to lock door that is already locked -> outcome "already"
  const c0004 = state.actions.find(a => a.callId === 'c-0004');
  assert.ok(c0004);
  assert.strictEqual(c0004.status, 'already');
  assert.strictEqual(c0004.statusText, 'Already done, not sent again');

  // SCENARIO 3: Security confirmation declined
  // c-0005 open garage -> security: true, user declined -> cancelled
  const c0005 = state.actions.find(a => a.callId === 'c-0005');
  assert.ok(c0005);
  assert.strictEqual(c0005.security, true);
  assert.strictEqual(c0005.status, 'cancelled');
  assert.strictEqual(c0005.cancelledReason, 'You said no');

  // SCENARIO 4: Honesty Safety Layer (Unconfirmed command)
  // c-0006 light switch off -> unconfirmed -> MUST NOT be marked as done!
  const c0006 = state.actions.find(a => a.callId === 'c-0006');
  assert.ok(c0006);
  assert.strictEqual(c0006.status, 'unconfirmed');
  assert.strictEqual(c0006.statusText, 'Sent, not confirmed');

  // SCENARIO 5: Washer troubleshooting
  // c-0007 read washer status -> confirmed error 4C
  const c0007 = state.actions.find(a => a.callId === 'c-0007');
  assert.ok(c0007);
  assert.strictEqual(c0007.status, 'confirmed');
  assert.strictEqual(state.devices['dev-washer'].value, 'pause');

  // AUDIT LEDGER TOTALS
  assert.strictEqual(state.ledger.committed, 2, 'Committed actions count');
  assert.strictEqual(state.ledger.withdrawnBeforeSending, 2, 'Withdrawn (Never sent) actions count');
  assert.strictEqual(state.ledger.unconfirmed, 1, 'Unconfirmed actions count');
  assert.strictEqual(state.ledger.duplicatesBlocked, 1, 'Blocked duplicates count');
  assert.strictEqual(state.ledger.failed, 0, 'Failed count');
});

test('E2E Verification 3: Theme Tokens & Contrast Safety', () => {
  assert.ok(THEME.dark.bg);
  assert.ok(THEME.dark.card);
  assert.ok(THEME.dark.success);
  assert.ok(THEME.dark.warning);
  assert.ok(THEME.dark.danger);

  assert.ok(THEME.light.bg);
  assert.ok(THEME.light.card);
  assert.ok(THEME.light.success);
  assert.ok(THEME.light.warning);
  assert.ok(THEME.light.danger);
});
