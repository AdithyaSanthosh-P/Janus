import { isValidJanusPacket } from '../utils/eventValidator';
import { janusStore } from '../store/janusStore';
import { JanusEvent } from '../types/janus';
import { DrillPacket } from '../utils/drillBuilder';
import { PHONE_TOPIC, phoneNative, runPhoneAction } from './phoneActions';

export interface LiveKitConnectOptions {
  url: string;
  token: string;
  enableMic?: boolean;
}

export class LiveKitService {
  private connectedListeners: Array<() => void> = [];
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

      // Room and RoomEvent come from livekit-client; @livekit/react-native
      // only supplies the WebRTC globals (registerGlobals, in index.js) and the
      // native audio session. It exports no Room: looking for one there made
      // every live connect fail with "LiveKit client module is not loaded".
      let RoomClass: any;
      let RoomEvent: any;
      try {
        const lkc = require('livekit-client');
        RoomClass = lkc.Room;
        RoomEvent = lkc.RoomEvent;
      } catch (e) {
        console.warn('[LiveKitService] livekit-client could not be loaded:', e);
      }
      if (!RoomClass || !RoomEvent) {
        throw new Error('LiveKit client module is not loaded.');
      }
      try {
        const { AudioSession } = require('@livekit/react-native');
        await AudioSession.startAudioSession();
      } catch (e) {
        console.warn('[LiveKitService] Could not start the native audio session:', e);
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
          if (topic === PHONE_TOPIC) {
            this.handlePhoneAction(payload, participant?.identity || '');
            return;
          }
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

      // Track subscribed: on a phone the WebRTC layer plays remote audio by
      // itself. Calling track.attach() here creates an <audio> element through
      // `document`, which does not exist in React Native; when the agent is
      // already in the room the call runs inside connect() and fails the join
      // ("Property 'document' doesn't exist").
      this.room.on(RoomEvent.TrackSubscribed, (track: any, _publication: any, participant: any) => {
        if (track.kind === 'audio') {
          console.log('[LiveKitService] Subscribed to agent audio track from:', participant.identity);
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
      janusStore.setConnectionStatus('connected');
      for (const listener of this.connectedListeners) {
        try {
          listener();
        } catch (e) {
          console.warn('[LiveKitService] Error in onConnected listener:', e);
        }
      }

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
   * Run `listener` after every fresh connect (not after a resume: a resumed
   * session keeps the agent's drill state, a fresh one may not).
   */
  public onConnected(listener: () => void): () => void {
    this.connectedListeners.push(listener);
    return () => {
      this.connectedListeners = this.connectedListeners.filter(l => l !== listener);
    };
  }

  /**
   * Send a failure drill message over LiveKit data channel.
   * Topic: `janus.control`, reliable: true.
   * Resolves true only when the packet was handed to LiveKit; false when the
   * room is not connected or publishing failed, so the caller never shows a
   * drill as on that the agent never received.
   */
  public async sendDrill(packet: DrillPacket): Promise<boolean> {
    if (!this.room || !this.room.localParticipant || janusStore.getState().connectionStatus !== 'connected') {
      console.warn('[LiveKitService] Cannot send drill: room is not connected.');
      return false;
    }
    try {
      const encoded = new TextEncoder().encode(JSON.stringify(packet));
      await this.room.localParticipant.publishData(encoded, {
        reliable: true,
        topic: 'janus.control',
      });
      return true;
    } catch (err) {
      console.error('[LiveKitService] Error publishing drill:', err);
      return false;
    }
  }

  /**
   * An alarm or a timer the agent asks for (phoneActions.ts): run it on this
   * phone and answer on `janus.phone`. Only the agent may ask.
   */
  private async handlePhoneAction(payload: Uint8Array, senderIdentity: string): Promise<void> {
    if (!senderIdentity.startsWith('agent-')) {
      return;
    }
    try {
      const message = JSON.parse(new TextDecoder('utf-8').decode(payload));
      const result = await runPhoneAction(message, phoneNative());
      if (result && this.room?.localParticipant) {
        await this.room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(result)), {
          reliable: true,
          topic: PHONE_TOPIC,
        });
      }
    } catch (err) {
      console.error('[LiveKitService] Error running a phone action:', err);
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
