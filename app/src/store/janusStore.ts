import {
  JanusEvent,
  BackendType,
  ActionCardData,
  CaptionEntry,
  DeviceStateData,
  LedgerData,
  ActionCardStatus,
} from '../types/janus';

export interface JanusState {
  backend: BackendType;
  packs: string[];
  userSpeaking: boolean;
  captions: CaptionEntry[];
  actions: ActionCardData[];
  devices: Record<string, DeviceStateData>;
  ledger: LedgerData;
  connectionStatus: 'disconnected' | 'connecting' | 'connected' | 'demo' | 'error';
  connectionError: string | null;
  activeTurnId: string | null;
}

export const INITIAL_DEVICES: Record<string, DeviceStateData> = {
  'dev-lock': {
    device: 'dev-lock',
    label: 'Front door',
    capability: 'lock',
    attribute: 'lock',
    value: 'unlocked',
    backend: 'real',
    updatedAt: 0,
  },
  'dev-garage': {
    device: 'dev-garage',
    label: 'Garage',
    capability: 'garageDoorControl',
    attribute: 'door',
    value: 'closed',
    backend: 'real',
    updatedAt: 0,
  },
  'dev-ac': {
    device: 'dev-ac',
    label: 'AC',
    capability: 'thermostatCoolingSetpoint',
    attribute: 'coolingSetpoint',
    value: 24,
    backend: 'real',
    updatedAt: 0,
  },
  'dev-light': {
    device: 'dev-light',
    label: 'Living room light',
    capability: 'switch',
    attribute: 'switch',
    value: 'off',
    backend: 'real',
    updatedAt: 0,
  },
  'dev-washer': {
    device: 'dev-washer',
    label: 'Washer',
    capability: 'washerOperatingState',
    attribute: 'machineState',
    value: 'ready',
    backend: 'real',
    updatedAt: 0,
  },
};

export const INITIAL_STATE: JanusState = {
  backend: 'real',
  packs: ['smartthings', 'device_care'],
  userSpeaking: false,
  captions: [],
  actions: [],
  devices: {}, // Live mode starts with NO tiles; built dynamically from device_state events
  ledger: {
    committed: 0,
    withdrawnBeforeSending: 0,
    unconfirmed: 0,
    failed: 0,
    duplicatesBlocked: 0,
  },
  connectionStatus: 'disconnected',
  connectionError: null,
  activeTurnId: null,
};

export class JanusEventReducer {
  private seenSeqs = new Set<number>();
  private state: JanusState = { ...INITIAL_STATE, devices: {} };
  private listeners: Array<(state: JanusState) => void> = [];

  public getState(): JanusState {
    return this.state;
  }

  public subscribe(listener: (state: JanusState) => void): () => void {
    this.listeners.push(listener);
    listener(this.state);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  private notify() {
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }

  public reset(keepConnection: boolean = false, loadDemo: boolean = false) {
    this.seenSeqs.clear();
    const currentStatus = keepConnection ? this.state.connectionStatus : 'disconnected';
    this.state = {
      ...INITIAL_STATE,
      devices: loadDemo ? { ...INITIAL_DEVICES } : {},
      connectionStatus: currentStatus,
    };
    this.notify();
  }

  public loadDemoDevices() {
    this.state = {
      ...this.state,
      devices: { ...INITIAL_DEVICES },
    };
    this.notify();
  }

  public setConnectionStatus(
    status: JanusState['connectionStatus'],
    error: string | null = null
  ) {
    let nextDevices = this.state.devices;
    // When switching to live mode (connecting/connected), start with NO tiles
    if (status === 'connecting') {
      nextDevices = {};
    }
    this.state = {
      ...this.state,
      devices: nextDevices,
      connectionStatus: status,
      connectionError: error,
    };
    this.notify();
  }

  /**
   * Process a single validated event packet.
   * Deduplicates by seq, keys actions by call_id.
   */
  public processEvent(event: JanusEvent): boolean {
    // Deduplication by seq
    if (this.seenSeqs.has(event.seq)) {
      return false;
    }
    this.seenSeqs.add(event.seq);

    switch (event.type) {
      case 'session_started': {
        this.state = {
          ...this.state,
          backend: event.backend || this.state.backend,
          packs: event.packs || this.state.packs,
        };
        break;
      }

      case 'user_speaking': {
        this.state = {
          ...this.state,
          userSpeaking: event.active,
        };
        break;
      }

      case 'heard': {
        const turnId = event.turn_id;
        const text = event.text;
        const captions = [...this.state.captions];
        const lastCaption = captions[captions.length - 1];

        if (lastCaption && lastCaption.sender === 'user' && lastCaption.turnId === turnId) {
          // Streaming update within the same turn
          lastCaption.text = `${lastCaption.text} ${text}`.trim();
        } else {
          captions.push({
            id: `caption-${event.seq}`,
            sender: 'user',
            turnId,
            text,
            timestamp: event.t_ms,
          });
        }

        this.state = {
          ...this.state,
          captions,
          activeTurnId: turnId,
        };
        break;
      }

      case 'turn_closed': {
        this.state = {
          ...this.state,
          userSpeaking: false,
        };
        break;
      }

      case 'agent_said': {
        const captions = [...this.state.captions];
        captions.push({
          id: `caption-${event.seq}`,
          sender: 'agent',
          text: event.text,
          kind: event.kind,
          timestamp: event.t_ms,
        });

        this.state = {
          ...this.state,
          captions,
        };
        break;
      }

      case 'agent_interrupted': {
        const captions = [...this.state.captions];
        if (captions.length > 0) {
          const last = { ...captions[captions.length - 1], isInterrupted: true };
          captions[captions.length - 1] = last;
        }
        this.state = {
          ...this.state,
          captions,
        };
        break;
      }

      case 'call_planned': {
        const actions = [...this.state.actions];
        const existingIndex = actions.findIndex(a => a.callId === event.call_id);

        const cardData: ActionCardData = {
          callId: event.call_id,
          tool: event.tool,
          label: event.label,
          kind: event.kind,
          args: event.args || {},
          security: !!event.security,
          status: 'planned',
          statusText: 'Planned',
          isFinal: false,
          timestamp: event.t_ms,
        };

        if (existingIndex >= 0) {
          actions[existingIndex] = { ...actions[existingIndex], ...cardData };
        } else {
          actions.push(cardData);
        }

        this.state = { ...this.state, actions };
        break;
      }

      case 'call_held': {
        const actions = this.state.actions.map(action => {
          if (action.callId === event.call_id) {
            return {
              ...action,
              status: 'held' as ActionCardStatus,
              statusText: 'Held',
              heldReason: event.reason, // Contract: display reason exactly as received
            };
          }
          return action;
        });

        this.state = { ...this.state, actions };
        break;
      }

      case 'confirmation_asked': {
        const actions = this.state.actions.map(action => {
          if (action.callId === event.call_id) {
            return {
              ...action,
              status: 'waiting_for_yes' as ActionCardStatus,
              statusText: 'Waiting for your yes',
              confirmationQuestion: event.question,
            };
          }
          return action;
        });

        this.state = { ...this.state, actions };
        break;
      }

      case 'call_cancelled': {
        const actions = this.state.actions.map(action => {
          if (action.callId === event.call_id) {
            return {
              ...action,
              status: 'cancelled' as ActionCardStatus,
              statusText: 'Never sent',
              cancelledReason: event.reason,
              isFinal: true,
            };
          }
          return action;
        });

        this.state = { ...this.state, actions };
        break;
      }

      case 'call_sent': {
        const actions = this.state.actions.map(action => {
          if (action.callId === event.call_id) {
            return {
              ...action,
              status: 'sent' as ActionCardStatus,
              statusText: 'Sent',
              args: event.args || action.args,
            };
          }
          return action;
        });

        this.state = { ...this.state, actions };
        break;
      }

      case 'call_result': {
        const actions = this.state.actions.map(action => {
          if (action.callId === event.call_id) {
            // Teammate contract rule: Unknown outcome must NOT show as done.
            // Default to not-done/failed state. Only 'confirmed' and 'already' may ever show green.
            let status: ActionCardStatus = 'failed';
            let statusText = event.summary || 'Failed';

            if (event.outcome === 'confirmed') {
              status = 'confirmed';
              statusText = 'Confirmed';
            } else if (event.outcome === 'already') {
              status = 'already';
              statusText = 'Already done, not sent again';
            } else if (event.outcome === 'unconfirmed') {
              // Contract rule: Never show unconfirmed as done! Show "Sent, not confirmed" in amber.
              status = 'unconfirmed';
              statusText = 'Sent, not confirmed';
            } else if (event.outcome === 'refused') {
              status = 'refused';
              statusText = 'Refused';
            } else if (event.outcome === 'failed') {
              status = 'failed';
              statusText = event.summary || 'Failed';
            }

            return {
              ...action,
              status,
              statusText,
              outcome: event.outcome,
              resultSummary: event.summary,
              isFinal: true,
            };
          }
          return action;
        });

        this.state = { ...this.state, actions };
        break;
      }

      case 'device_state': {
        const currentDevice = this.state.devices[event.device] || {
          device: event.device,
          label: event.label,
          capability: event.capability,
          attribute: event.attribute,
          value: event.value,
          backend: event.backend || this.state.backend,
          updatedAt: event.t_ms,
        };

        const updatedDevice: DeviceStateData = {
          ...currentDevice,
          label: event.label || currentDevice.label,
          capability: event.capability || currentDevice.capability,
          attribute: event.attribute || currentDevice.attribute,
          value: event.value,
          backend: event.backend || this.state.backend,
          updatedAt: event.t_ms,
        };

        this.state = {
          ...this.state,
          backend: event.backend || this.state.backend,
          devices: {
            ...this.state.devices,
            [event.device]: updatedDevice,
          },
        };
        break;
      }

      case 'ledger_summary': {
        this.state = {
          ...this.state,
          ledger: {
            committed: event.committed,
            withdrawnBeforeSending: event.withdrawn_before_sending,
            unconfirmed: event.unconfirmed,
            failed: event.failed,
            duplicatesBlocked: event.duplicates_blocked,
          },
        };
        break;
      }

      case 'goal_finished': {
        // Subtle divider or goal notification in state
        break;
      }

      default: {
        // Contract rule: Safely ignore unknown event types
        break;
      }
    }

    this.notify();
    return true;
  }
}

export const janusStore = new JanusEventReducer();
