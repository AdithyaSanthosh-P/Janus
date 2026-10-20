// `var`: jest.mock is hoisted above these, and the module singleton registers on import.
var mockSend = jest.fn();
var mockConnected: Array<() => void> = [];

jest.mock('../livekitService', () => ({
  livekitService: {
    sendDrill: (...args: any[]) => (mockSend as jest.Mock)(...args),
    onConnected: (listener: () => void) => {
      (mockConnected = mockConnected || []).push(listener);
      return () => undefined;
    },
  },
}));

import { DrillService } from '../drillService';

describe('DrillService', () => {
  beforeEach(() => {
    mockSend.mockReset();
  });

  it('turns a drill on only when the packet was sent', async () => {
    const service = new DrillService();
    mockSend.mockResolvedValueOnce(false);
    expect(await service.setDrill('model_down', true)).toBe(false);
    expect(service.getState().model_down).toBe(false);
    expect(service.isAnyActive()).toBe(false);

    mockSend.mockResolvedValueOnce(true);
    expect(await service.setDrill('model_down', true)).toBe(true);
    expect(service.getState().model_down).toBe(true);
    expect(mockSend.mock.calls[1][0]).toEqual({ v: 1, type: 'drill', name: 'model_down', on: true, args: {} });
  });

  it('resets every drill on a fresh connect', async () => {
    const service = new DrillService();
    mockSend.mockResolvedValue(true);
    await service.setDrill('st_no_confirm', true);
    mockConnected[mockConnected.length - 1]();
    await Promise.resolve();
    expect(service.isAnyActive()).toBe(false);
    expect(mockSend).toHaveBeenLastCalledWith({ v: 1, type: 'drill', name: 'reset', on: true, args: {} });
  });
});
