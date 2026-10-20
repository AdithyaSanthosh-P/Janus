import { JanusEventReducer } from '../janusStore';
import { SAMPLE_EVENTS } from '../../data/sampleEvents';
import { isValidJanusPacket } from '../../utils/eventValidator';

describe('JanusStore Event Reducer & Validator', () => {
  it('isValidJanusPacket enforces contract rules', () => {
    // Valid packet
    const valid = { v: 1, seq: 1, t_ms: 100, type: 'session_started', backend: 'mock', packs: [] };
    expect(isValidJanusPacket(valid, 'janus.events', 'agent-001')).toBe(true);
    expect(isValidJanusPacket(valid, 'janus.events', 'janus-replay')).toBe(true);

    // Rejects invalid topic
    expect(isValidJanusPacket(valid, 'wrong.topic', 'agent-001')).toBe(false);

    // Rejects invalid sender
    expect(isValidJanusPacket(valid, 'janus.events', 'attacker-hacker')).toBe(false);

    // Rejects invalid schema version
    expect(isValidJanusPacket({ ...valid, v: 2 }, 'janus.events', 'agent-001')).toBe(false);
  });

  it('Processes all 76 recorded sample session events with high fidelity', () => {
    const store = new JanusEventReducer();

    expect(SAMPLE_EVENTS.length).toBe(76);

    for (const event of SAMPLE_EVENTS) {
      const accepted = store.processEvent(event);
      expect(accepted).toBe(true);
    }

    const state = store.getState();

    // Check backend
    expect(state.backend).toBe('mock');

    // Check actions count
    expect(state.actions.length).toBe(7);

    // 1. Verify c-0002 was cancelled ("Never sent" key visual)
    const c0002 = state.actions.find(a => a.callId === 'c-0002');
    expect(c0002).toBeDefined();
    expect(c0002?.status).toBe('cancelled');
    expect(c0002?.statusText).toBe('Never sent');
    expect(c0002?.cancelledReason).toBe('You changed it to 26');

    // 2. Verify c-0004 duplicate was blocked ("Already locked, not sent again")
    const c0004 = state.actions.find(a => a.callId === 'c-0004');
    expect(c0004).toBeDefined();
    expect(c0004?.status).toBe('already');
    expect(c0004?.statusText).toBe('Already done, not sent again');

    // 3. Verify c-0005 security confirmation declined
    const c0005 = state.actions.find(a => a.callId === 'c-0005');
    expect(c0005).toBeDefined();
    expect(c0005?.security).toBe(true);
    expect(c0005?.status).toBe('cancelled');
    expect(c0005?.cancelledReason).toBe('You said no');

    // 4. Verify c-0006 was unconfirmed: MUST NOT be done! Must be "Sent, not confirmed"
    const c0006 = state.actions.find(a => a.callId === 'c-0006');
    expect(c0006).toBeDefined();
    expect(c0006?.status).toBe('unconfirmed');
    expect(c0006?.statusText).toBe('Sent, not confirmed');

    // 5. Verify c-0007 washer diagnosis
    const c0007 = state.actions.find(a => a.callId === 'c-0007');
    expect(c0007).toBeDefined();
    expect(c0007?.status).toBe('confirmed');
    expect(state.devices['dev-washer'].value).toBe('pause');

    // 6. Verify Ledger Summary matches contract
    expect(state.ledger.committed).toBe(2);
    expect(state.ledger.withdrawnBeforeSending).toBe(2);
    expect(state.ledger.unconfirmed).toBe(1);
    expect(state.ledger.duplicatesBlocked).toBe(1);

    // 7. Verify Deduplication
    const replayed = store.processEvent(SAMPLE_EVENTS[0]);
    expect(replayed).toBe(false); // dropped as duplicate
  });
});
