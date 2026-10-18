import { JanusEvent } from '../types/janus';

const VALID_TOPIC = 'janus.events';

/**
 * Validates whether an incoming event packet satisfies the Janus contract:
 * 1. Must be topic "janus.events" (if topic check enabled)
 * 2. Participant identity starts with "agent-" or is "janus-replay" (if sender check enabled)
 * 3. v == 1
 * 4. seq is a positive integer
 * 5. has valid type string
 */
export function isValidJanusPacket(
  payload: any,
  topic?: string,
  senderIdentity?: string
): payload is JanusEvent {
  if (topic !== undefined && topic !== VALID_TOPIC) {
    return false;
  }

  if (senderIdentity !== undefined) {
    const isAllowedSender =
      senderIdentity.startsWith('agent-') || senderIdentity === 'janus-replay';
    if (!isAllowedSender) {
      return false;
    }
  }

  if (!payload || typeof payload !== 'object') {
    return false;
  }

  if (payload.v !== 1) {
    return false;
  }

  if (typeof payload.seq !== 'number' || typeof payload.t_ms !== 'number') {
    return false;
  }

  if (typeof payload.type !== 'string' || !payload.type) {
    return false;
  }

  return true;
}
