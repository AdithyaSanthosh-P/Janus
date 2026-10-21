import { ActionCardData } from '../types/janus';
import { OrbState } from './types';

export const ORB_STATES: OrbState[] = [
  'working', 'searching', 'solving', 'listening', 'connecting', 'weaving', 'composing', 'breathing', 'shaping',
];

export interface OrbInputs {
  connectionStatus: 'disconnected' | 'connecting' | 'connected' | 'demo' | 'error';
  userSpeaking: boolean;
  /** True while Janus's last spoken line is still being said. */
  agentSpeaking: boolean;
  actions: ActionCardData[];
}

/**
 * What Janus is doing, as one of the orb's patterns. First match wins, in the
 * order of what the user most needs to see:
 *   connecting      the room is being joined
 *   listening       the user is speaking
 *   shaping         a question is waiting for the user's yes
 *   searching       a command is out and the device has not confirmed yet
 *   solving         an action is planned or held, being worked out
 *   composing       Janus is speaking
 *   breathing       idle
 */
export const deriveOrbState = ({ connectionStatus, userSpeaking, agentSpeaking, actions }: OrbInputs): OrbState => {
  if (connectionStatus === 'connecting') return 'connecting';
  if (userSpeaking) return 'listening';
  if (actions.some(a => a.status === 'waiting_for_yes')) return 'shaping';
  if (actions.some(a => a.status === 'sent')) return 'searching';
  if (actions.some(a => a.status === 'planned' || a.status === 'held')) return 'solving';
  if (agentSpeaking) return 'composing';
  return 'breathing';
};
