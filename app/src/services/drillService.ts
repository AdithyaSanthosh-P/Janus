import {
  DrillState,
  INITIAL_DRILL_STATE,
  buildStUnreachablePacket,
  buildStNoConfirmPacket,
  buildStLatencyPacket,
  buildModelSlowPacket,
  buildModelDownPacket,
  buildExternalCommandPacket,
  buildGaragePacket,
  buildOfferPacket,
  OfferKind,
  buildResetPacket,
  DrillPacket,
} from '../utils/drillBuilder';
import { livekitService } from './livekitService';

export class DrillService {
  private state: DrillState = { ...INITIAL_DRILL_STATE };
  private listeners: Array<(state: DrillState, isAnyActive: boolean) => void> = [];

  constructor() {
    // Contract: reset all toggles and send reset when a new session starts.
    // On a fresh connect, not on session_started: the agent re-sends
    // session_started whenever anyone joins the room, which would switch a
    // drill off in the middle of the demo.
    livekitService.onConnected(() => {
      this.resetAll(true).catch(e => console.warn('[DrillService] Could not reset on connect:', e));
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
   * Toggle one drill and publish reliable janus.control packet. The toggle
   * changes only once the packet was sent; otherwise it stays as it was.
   * Resolves whether it was sent.
   */
  public async setDrill(name: keyof DrillState, on: boolean): Promise<boolean> {
    const booleanOn = Boolean(on);
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

    const sent = await livekitService.sendDrill(packet);
    if (sent) {
      this.state = {
        ...this.state,
        [name]: booleanOn,
      };
    } else {
      console.warn('[DrillService] Drill not sent; toggle left unchanged:', name);
    }
    this.notify();
    return sent;
  }

  /**
   * One-shot command sent by "someone else" outside Janus.
   */
  /**
   * One-shot: someone else opens or closes the garage, outside Janus.
   */
  public async sendGarage(command: 'open' | 'close'): Promise<boolean> {
    const sent = await livekitService.sendDrill(buildGaragePacket(command));
    if (!sent) {
      console.warn('[DrillService] Garage command not sent:', command);
    }
    return sent;
  }

  /**
   * One-shot: ask Janus to speak first with an offer.
   */
  public async sendOffer(kind: OfferKind): Promise<boolean> {
    const sent = await livekitService.sendDrill(buildOfferPacket(kind));
    if (!sent) {
      console.warn('[DrillService] Offer not sent:', kind);
    }
    return sent;
  }

  public async sendExternalCommand(command: 'unlock' | 'lock'): Promise<boolean> {
    const sent = await livekitService.sendDrill(buildExternalCommandPacket(command));
    if (!sent) {
      console.warn('[DrillService] External command not sent:', command);
    }
    return sent;
  }

  /**
   * Reset all drills: turn toggles off and send { name: 'reset', on: true }
   */
  public async resetAll(sendOverLiveKit = true): Promise<void> {
    this.state = { ...INITIAL_DRILL_STATE };
    this.notify();

    if (sendOverLiveKit) {
      await livekitService.sendDrill(buildResetPacket());
    }
  }
}

export const drillService = new DrillService();
