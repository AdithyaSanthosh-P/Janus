import { JanusEventReducer } from '../janusStore';
import { SAMPLE_EVENTS } from '../../data/sampleEvents';
import { isValidJanusPacket } from '../../utils/eventValidator';
import { JanusEvent } from '../../types/janus';

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

  it('Live mode starts with no tiles and builds them dynamically from device_state events', () => {
    const store = new JanusEventReducer();
    // Default initial devices in live mode should be empty
    expect(Object.keys(store.getState().devices).length).toBe(0);

    // Live agent sends device_state events for mock or real ST devices
    store.processEvent({
      v: 1,
      seq: 1,
      t_ms: 50,
      type: 'device_state',
      device: 'lock-1',
      label: 'Janus Lock',
      capability: 'lock',
      attribute: 'lock',
      value: 'unlocked',
      backend: 'mock',
    } as JanusEvent);

    store.processEvent({
      v: 1,
      seq: 2,
      t_ms: 60,
      type: 'device_state',
      device: 'garage-1',
      label: 'Janus Garage',
      capability: 'garageDoorControl',
      attribute: 'door',
      value: 'closed',
      backend: 'mock',
    } as JanusEvent);

    const state = store.getState();
    expect(Object.keys(state.devices).length).toBe(2);
    expect(state.devices['lock-1'].label).toBe('Janus Lock');
    expect(state.devices['lock-1'].capability).toBe('lock');
    expect(state.devices['garage-1'].label).toBe('Janus Garage');
  });

  it('Unknown call_result outcome must NOT show as done (defaults to not-done/failed)', () => {
    const store = new JanusEventReducer();

    // Plan a call
    store.processEvent({
      v: 1,
      seq: 1,
      t_ms: 100,
      type: 'call_planned',
      call_id: 'call-unknown-test',
      tool: 'custom_device_action',
      label: 'Trigger unknown action',
      kind: 'write',
      args: {},
      security: false,
    } as JanusEvent);

    // Emit result with an unrecognized outcome
    store.processEvent({
      v: 1,
      seq: 2,
      t_ms: 200,
      type: 'call_result',
      call_id: 'call-unknown-test',
      outcome: 'unrecognized_third_party_state' as any,
      summary: 'Third party service timeout',
    } as JanusEvent);

    const action = store.getState().actions.find(a => a.callId === 'call-unknown-test');
    expect(action).toBeDefined();
    expect(action?.status).toBe('failed'); // Must default to not-done/failed!
    expect(action?.status).not.toBe('confirmed');
    expect(action?.statusText).toBe('Third party service timeout');
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

  it('A LiveKit reconnect keeps the device tiles (the agent does not resend them)', () => {
    const store = new JanusEventReducer();
    store.setConnectionStatus('connecting');
    store.setConnectionStatus('connected');
    store.processEvent({
      v: 1, seq: 1, t_ms: 10, type: 'device_state', device: 'lock-1', label: 'Janus Lock',
      capability: 'lock', attribute: 'lock', value: 'locked', backend: 'mock',
    } as JanusEvent);

    store.setConnectionStatus('connecting'); // RoomEvent.Reconnecting
    store.setConnectionStatus('connected'); // RoomEvent.Reconnected
    expect(store.getState().devices['lock-1'].value).toBe('locked');

    store.reset(false, false); // a fresh live session (LiveKitService.connect)
    expect(Object.keys(store.getState().devices).length).toBe(0);
  });

  it('agent_interrupted marks the last agent caption, never a user caption', () => {
    const store = new JanusEventReducer();
    store.processEvent({ v: 1, seq: 1, t_ms: 10, type: 'agent_said', text: 'The washer shows', kind: 'inform' } as JanusEvent);
    store.processEvent({ v: 1, seq: 2, t_ms: 20, type: 'heard', turn_id: 't-2', text: 'wait' } as JanusEvent);
    store.processEvent({ v: 1, seq: 3, t_ms: 30, type: 'agent_interrupted' } as JanusEvent);

    const [agent, user] = store.getState().captions;
    expect(agent.isInterrupted).toBe(true);
    expect(user.isInterrupted).toBeFalsy();
  });
});

describe('Card category wording', () => {
  it('names the area and kind instead of the raw tool', () => {
    const { cardCategory } = require('../../utils/labels');
    expect(cardCategory('st_close_garage', 'write')).toBe('SmartThings · Action');
    expect(cardCategory('st_get_status', 'read')).toBe('SmartThings · Check');
    expect(cardCategory('lookup_error_code', 'read')).toBe('Device care · Check');
  });

  it('keeps the read-set X-ray: what a card was built on, and what changed to withdraw it', () => {
    const store = new JanusEventReducer();
    store.processEvent({
      v: 1, seq: 1, t_ms: 10, type: 'call_planned', call_id: 'c-1', tool: 'book_technician',
      label: 'Book a technician for tomorrow morning', kind: 'write', args: {}, security: false,
      based_on: [{ what: 'date', value: 'tomorrow', source: 'offer' }],
    } as JanusEvent);
    expect(store.getState().actions[0].basedOn).toEqual([{ what: 'date', value: 'tomorrow', source: 'offer' }]);
    store.processEvent({
      v: 1, seq: 2, t_ms: 20, type: 'call_cancelled', call_id: 'c-1', reason: 'Washer error code cleared while waiting',
      based_on: [{ what: 'date', value: 'tomorrow', source: 'offer' }, { what: 'washer', value: '4C', source: 'world' }],
      changed: [{ what: 'washer', was: '4C', now: 'clear', source: 'world' }],
    } as JanusEvent);
    const card = store.getState().actions[0];
    expect(card.status).toBe('cancelled');
    expect(card.basedOn?.length).toBe(2);
    expect(card.changed).toEqual([{ what: 'washer', was: '4C', now: 'clear', source: 'world' }]);
  });

  it('a card from an agent without the X-ray fields has none', () => {
    const store = new JanusEventReducer();
    store.processEvent({
      v: 1, seq: 1, t_ms: 10, type: 'call_planned', call_id: 'c-1', tool: 'st_lock', label: 'Lock the front door',
      kind: 'write', args: {}, security: false,
    } as JanusEvent);
    store.processEvent({ v: 1, seq: 2, t_ms: 20, type: 'call_cancelled', call_id: 'c-1', reason: 'x' } as JanusEvent);
    expect(store.getState().actions[0].basedOn).toBeUndefined();
    expect(store.getState().actions[0].changed).toBeUndefined();
  });
});
