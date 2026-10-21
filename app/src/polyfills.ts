// Hermes in this React Native version has no WeakRef or FinalizationRegistry,
// which livekit-client uses ("Property 'WeakRef' doesn't exist" on connect).
// A strong reference never lets go of its target, which for a client that lives
// as long as one session is harmless; the registry never calls back.
const g = globalThis as any;

if (typeof g.WeakRef === 'undefined') {
  g.WeakRef = class WeakRef<T extends object> {
    private target: T;
    constructor(target: T) {
      this.target = target;
    }
    deref(): T | undefined {
      return this.target;
    }
  };
}

if (typeof g.FinalizationRegistry === 'undefined') {
  g.FinalizationRegistry = class FinalizationRegistry {
    constructor(_callback: (held: unknown) => void) {}
    register(): void {}
    unregister(): boolean {
      return false;
    }
  };
}

export {};
