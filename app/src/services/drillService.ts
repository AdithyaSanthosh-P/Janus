import {
  DrillState,
  INITIAL_DRILL_STATE,
  buildStUnreachablePacket,
  buildStNoConfirmPacket,
  buildStLatencyPacket,
  buildModelSlowPacket,
  buildModelDownPacket,
  buildExternalCommandPacket,
  buildResetPacket,
  DrillPacket,
} from '../utils/drillBuilder';
import { livekitService } from './livekitService';
import { janusStore } from '../store/janusStore';

export class DrillService {
  private state: DrillState = { ...INITIAL_DRILL_STATE };
  private listeners: Array<(state: DrillState, isAnyActive: boolean) => void> = [];

  constructor() {
    // Contract: Reset all toggles and send reset when a new session starts
    janusStore.onSessionStarted(() => {
      this.resetAll(true).catch(e => console.warn('[DrillService] Could not reset on session start:', e));
    });
  }

  public getState(): DrillState {
    return { ...this.state };
  }

  public isAnyActive(): boolean {
    return (
      this.state.st_unreachable ||
      this.state.st_no_confirm ||
      this.state.st_latency ||
      this.state.model_slow ||
      this.state.model_down
    );
  }

  public subscribe(listener: (state: DrillState, isAnyActive: boolean) => void): () => void {
    this.listeners.push(listener);
    listener(this.getState(), this.isAnyActive());
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  private notify() {
    const currentState = this.getState();
    const active = this.isAnyActive();
    for (const listener of this.listeners) {
      listener(currentState, active);
    }
  }

  /**
   * Toggle one drill and publish reliable janus.control packet.
   */
  public async setDrill(name: keyof DrillState, on: boolean): Promise<void> {
    const booleanOn = Boolean(on);
    this.state = {
      ...this.state,
      [name]: booleanOn,
    };
    this.notify();

    let packet: DrillPacket;
    switch (name) {
      case 'st_unreachable':
        packet = buildStUnreachablePacket(booleanOn);
        break;
      case 'st_no_confirm':
        packet = buildStNoConfirmPacket(booleanOn);
        break;
      case 'st_latency':
        packet = buildStLatencyPacket(booleanOn, 3000);
        break;
      case 'model_slow':
        packet = buildModelSlowPacket(booleanOn, 4000);
        break;
      case 'model_down':
        packet = buildModelDownPacket(booleanOn);
        break;
    }

    try {
      await livekitService.sendDrill(packet);
    } catch (err) {
      console.warn('[DrillService] Could not send drill packet:', err);
    }
  }

  /**
   * One-shot command sent by "someone else" outside Janus.
   */
  public async sendExternalCommand(command: 'unlock' | 'lock'): Promise<void> {
    const packet = buildExternalCommandPacket(command);
    try {
      await livekitService.sendDrill(packet);
    } catch (err) {
      console.warn('[DrillService] Could not send external command:', err);
    }
  }

  /**
   * Reset all drills: turn toggles off and send { name: 'reset', on: true }
   */
  public async resetAll(sendOverLiveKit = true): Promise<void> {
    this.state = { ...INITIAL_DRILL_STATE };
    this.notify();

    if (sendOverLiveKit) {
      try {
        await livekitService.sendDrill(buildResetPacket());
      } catch (err) {
        console.warn('[DrillService] Could not send reset packet:', err);
      }
    }
  }
}

export const drillService = new DrillService();
