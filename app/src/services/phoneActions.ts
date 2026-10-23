/**
 * Phone actions (the agent's prism_rt/phone.py): Janus asks the app to set an
 * alarm or a timer on this phone, as JSON on the data topic `janus.phone`, and
 * the app answers on the same topic. The answer is the tool's result, so
 * "ok: true" goes back only once the clock app has taken the request.
 *
 *   request: {"type": "phone_action", "id", "action": "set_alarm", "hour", "minute", "label"?}
 *            {"type": "phone_action", "id", "action": "set_timer", "seconds", "label"?}
 *   answer:  {"type": "phone_result", "id", "ok", "error"?}
 */

export const PHONE_TOPIC = 'janus.phone';

export interface PhoneNative {
  setAlarm(hour: number, minute: number, label: string | null): Promise<boolean>;
  setTimer(seconds: number, label: string | null): Promise<boolean>;
}

export interface PhoneResult {
  type: 'phone_result';
  id: string;
  ok: boolean;
  error?: string;
}

function isInt(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;
}

/** The native module, or undefined in a build without it (and in Jest). */
export function phoneNative(): PhoneNative | undefined {
  try {
    const { NativeModules } = require('react-native');
    return NativeModules?.JanusPhone;
  } catch {
    return undefined;
  }
}

/**
 * Run one request. null for a packet that is not a phone action (nothing is
 * answered); otherwise the answer, never a thrown error.
 */
export async function runPhoneAction(message: any, native: PhoneNative | undefined): Promise<PhoneResult | null> {
  if (!message || message.type !== 'phone_action' || typeof message.id !== 'string') {
    return null;
  }
  const fail = (error: string): PhoneResult => ({ type: 'phone_result', id: message.id, ok: false, error });
  if (!native) {
    return fail('this app build cannot set alarms');
  }
  const label = typeof message.label === 'string' && message.label.trim() ? message.label.trim().slice(0, 40) : null;
  try {
    if (message.action === 'set_alarm') {
      if (!isInt(message.hour, 0, 23) || !isInt(message.minute, 0, 59)) {
        return fail('not a time');
      }
      await native.setAlarm(message.hour, message.minute, label);
    } else if (message.action === 'set_timer') {
      if (!isInt(message.seconds, 1, 86400)) {
        return fail('not a timer length');
      }
      await native.setTimer(message.seconds, label);
    } else {
      return fail(`unknown action ${String(message.action)}`);
    }
  } catch (err: any) {
    return fail(err?.message || 'the clock app refused it');
  }
  return { type: 'phone_result', id: message.id, ok: true };
}
