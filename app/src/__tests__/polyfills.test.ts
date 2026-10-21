describe('Hermes polyfills', () => {
  const g = globalThis as any;
  const saved = { WeakRef: g.WeakRef, FinalizationRegistry: g.FinalizationRegistry };

  afterEach(() => {
    g.WeakRef = saved.WeakRef;
    g.FinalizationRegistry = saved.FinalizationRegistry;
    jest.resetModules();
  });

  it('adds WeakRef and FinalizationRegistry when the engine lacks them', () => {
    delete g.WeakRef;
    delete g.FinalizationRegistry;
    jest.isolateModules(() => {
      require('../polyfills');
    });
    const target = { id: 1 };
    expect(new g.WeakRef(target).deref()).toBe(target);
    const registry = new g.FinalizationRegistry(() => undefined);
    expect(() => registry.register({}, 'x')).not.toThrow();
    expect(registry.unregister({})).toBe(false);
  });

  it('leaves the engine own versions alone', () => {
    const own = class {};
    g.WeakRef = own;
    jest.isolateModules(() => {
      require('../polyfills');
    });
    expect(g.WeakRef).toBe(own);
  });
});
