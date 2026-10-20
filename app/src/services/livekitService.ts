import { isValidJanusPacket } from '../utils/eventValidator';
import { janusStore } from '../store/janusStore';
import { JanusEvent } from '../types/janus';
import { DrillPacket } from '../utils/drillBuilder';

export interface LiveKitConnectOptions {
  url: string;
  token: string;
  enableMic?: boolean;
}

export class LiveKitService {
  private room: any = null;
  private isMicEnabled = false;
  private isCameraEnabled = false;
  private listeners: Array<(state: { isConnected: boolean; isMicOn: boolean; isCameraOn: boolean; error: string | null }) => void> = [];
  private currentUrl = '';
  private currentToken = '';

  public subscribe(listener: (state: { isConnected: boolean; isMicOn: boolean; isCameraOn: boolean; error: string | null }) => void) {
    this.listeners.push(listener);
    this.notify();
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  private notify(error: string | null = null) {
    for (const listener of this.listeners) {
      listener({
        isConnected: this.room?.state === 'connected',
        isMicOn: this.isMicEnabled,
        isCameraOn: this.isCameraEnabled,
        error,
      });
    }
  }

  public async connect({ url, token, enableMic = true }: LiveKitConnectOptions): Promise<void> {
    try {
      janusStore.reset(false, false); // Clear demo devices and state for clean live session
      janusStore.setConnectionStatus('connecting');
      this.currentUrl = url.trim();
      this.currentToken = token.trim();

      // Lazy load livekit-client / @livekit/react-native to support runtime environments cleanly
      let RoomClass: any;
      let RoomEvent: any;

      try {
        const lk = require('@livekit/react-native');
        RoomClass = lk.Room;
        RoomEvent = lk.RoomEvent;
      } catch (e1) {
        try {
          const lkc = require('livekit-client');
          RoomClass = lkc.Room;
          RoomEvent = lkc.RoomEvent;
        } catch (e2) {
          console.warn('[LiveKitService] Native LiveKit modules will be available on native build.');
        }
      }

      if (!RoomClass) {
        throw new Error('LiveKit client module is not loaded.');
      }

      // Initialize room instance
      if (this.room) {
        await this.disconnect();
      }

      this.room = new RoomClass({
        adaptiveStream: true,
        dynacast: true,
      });

      // Data channel subscriber for topic "janus.events"
      this.room.on(RoomEvent.DataReceived, (payload: Uint8Array, participant: any, _kind: any, topic?: string) => {
        try {
          if (topic && topic !== 'janus.events') {
            return;
          }

          const decoder = new TextDecoder('utf-8');
          const jsonStr = decoder.decode(payload);
          const rawEvent = JSON.parse(jsonStr);

          const senderIdentity = participant?.identity || '';
          if (isValidJanusPacket(rawEvent, topic, senderIdentity)) {
            janusStore.processEvent(rawEvent as JanusEvent);
          }
        } catch (err) {
          console.error('[LiveKitService] Error parsing data packet:', err);
        }
      });

      // Track subscribed: play agent audio track
      this.room.on(RoomEvent.TrackSubscribed, (track: any, publication: any, participant: any) => {
        if (track.kind === 'audio') {
          console.log('[LiveKitService] Subscribed to agent audio track from:', participant.identity);
          if (typeof track.attach === 'function') {
            track.attach();
          }
        }
      });

      this.room.on(RoomEvent.Connected, () => {
        janusStore.setConnectionStatus('connected');
        this.notify();
      });

      this.room.on(RoomEvent.Reconnecting, () => {
        janusStore.setConnectionStatus('connecting');
        this.notify();
      });

      this.room.on(RoomEvent.Reconnected, () => {
        janusStore.setConnectionStatus('connected');
        this.notify();
      });

      this.room.on(RoomEvent.Disconnected, () => {
        janusStore.setConnectionStatus('disconnected');
        this.notify();
      });

      // Connect to the room
      await this.room.connect(this.currentUrl, this.currentToken);

      // Enable microphone if requested
      if (enableMic && this.room.localParticipant) {
        try {
          await this.room.localParticipant.setMicrophoneEnabled(true);
          this.isMicEnabled = true;
        } catch (micErr) {
          console.warn('[LiveKitService] Could not auto-enable microphone:', micErr);
        }
      }

      janusStore.setConnectionStatus('connected');
      this.notify();
    } catch (err: any) {
      const errMsg = err?.message || 'Failed to connect to LiveKit';
      janusStore.setConnectionStatus('error', errMsg);
      this.notify(errMsg);
      throw err;
    }
  }

  public async toggleMicrophone(): Promise<boolean> {
    if (!this.room || !this.room.localParticipant) return false;
    try {
      const nextState = !this.isMicEnabled;
      await this.room.localParticipant.setMicrophoneEnabled(nextState);
      this.isMicEnabled = nextState;
      this.notify();
      return this.isMicEnabled;
    } catch (err) {
      console.error('[LiveKitService] Error toggling mic:', err);
      return this.isMicEnabled;
    }
  }

  public async toggleCamera(): Promise<boolean> {
    if (!this.room || !this.room.localParticipant) return false;
    try {
      const nextState = !this.isCameraEnabled;
      await this.room.localParticipant.setCameraEnabled(nextState, {
        facingMode: 'environment', // Contract: back camera for device-care scene
      });
      this.isCameraEnabled = nextState;
      this.notify();
      return this.isCameraEnabled;
    } catch (err) {
      console.error('[LiveKitService] Error toggling camera:', err);
      return this.isCameraEnabled;
    }
  }

  /**
   * Send a failure drill message over LiveKit data channel.
   * Topic: `janus.control`, reliable: true.
   */
  public async sendDrill(packet: DrillPacket): Promise<void> {
    if (!this.room || !this.room.localParticipant) {
      console.warn('[LiveKitService] Cannot send drill: room is not connected.');
      return;
    }
    try {
      const encoded = new TextEncoder().encode(JSON.stringify(packet));
      await this.room.localParticipant.publishData(encoded, {
        reliable: true,
        topic: 'janus.control',
      });
    } catch (err) {
      console.error('[LiveKitService] Error publishing drill:', err);
      throw err;
    }
  }

  public async disconnect(): Promise<void> {
    if (this.room) {
      try {
        await this.room.disconnect();
      } catch (e) {
        console.warn('[LiveKitService] Disconnect warning:', e);
      }
      this.room = null;
    }
    this.isMicEnabled = false;
    this.isCameraEnabled = false;
    janusStore.setConnectionStatus('disconnected');
    this.notify();
  }
}

export const livekitService = new LiveKitService();
