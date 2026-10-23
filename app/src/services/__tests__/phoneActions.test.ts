import { PhoneNative, runPhoneAction } from '../phoneActions';

function fakeNative(fail?: string) {
  const calls: any[] = [];
  const native: PhoneNative = {
    setAlarm: async (hour, minute, label) => {
      calls.push(['alarm', hour, minute, label]);
      if (fail) throw new Error(fail);
      return true;
    },
    setTimer: async (seconds, label) => {
      calls.push(['timer', seconds, label]);
      if (fail) throw new Error(fail);
      return true;
    },
  };
  return { native, calls };
}

describe('phone actions', () => {
  it('sets an alarm and answers ok', async () => {
    const { native, calls } = fakeNative();
    const result = await runPhoneAction({ type: 'phone_action', id: 'p-1', action: 'set_alarm', hour: 6, minute: 30, label: 'gym' }, native);
    expect(result).toEqual({ type: 'phone_result', id: 'p-1', ok: true });
    expect(calls).toEqual([['alarm', 6, 30, 'gym']]);
  });

  it('starts a timer', async () => {
    const { native, calls } = fakeNative();
    const result = await runPhoneAction({ type: 'phone_action', id: 'p-2', action: 'set_timer', seconds: 300 }, native);
    expect(result?.ok).toBe(true);
    expect(calls).toEqual([['timer', 300, null]]);
  });

  it('answers a refusal from the clock app as not ok', async () => {
    const { native } = fakeNative('no clock app on this phone takes it');
    const result = await runPhoneAction({ type: 'phone_action', id: 'p-3', action: 'set_alarm', hour: 6, minute: 30 }, native);
    expect(result).toEqual({ type: 'phone_result', id: 'p-3', ok: false, error: 'no clock app on this phone takes it' });
  });

  it('refuses a bad value without calling the phone', async () => {
    const { native, calls } = fakeNative();
    const result = await runPhoneAction({ type: 'phone_action', id: 'p-4', action: 'set_alarm', hour: 25, minute: 0 }, native);
    expect(result?.ok).toBe(false);
    expect(calls).toEqual([]);
  });

  it('says so in a build without the native module', async () => {
    const result = await runPhoneAction({ type: 'phone_action', id: 'p-5', action: 'set_timer', seconds: 60 }, undefined);
    expect(result?.ok).toBe(false);
  });

  it('ignores packets that are not phone actions', async () => {
    const { native } = fakeNative();
    expect(await runPhoneAction({ type: 'call_planned' }, native)).toBeNull();
    expect(await runPhoneAction(null, native)).toBeNull();
  });
});
