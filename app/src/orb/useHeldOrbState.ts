import { useEffect, useRef, useState } from 'react';
import { OrbState } from './types';

/** Patterns shown at once, however recently the orb changed: the user should see it react to them immediately. */
const IMMEDIATE: OrbState[] = ['listening', 'connecting'];

/** How long to wait before showing `wanted` (0 = now), given how long the current pattern has been up. */
export const holdFor = (shown: OrbState, wanted: OrbState, elapsedMs: number, minMs: number): number =>
  wanted === shown || IMMEDIATE.includes(wanted) ? 0 : Math.max(0, minMs - elapsedMs);

/**
 * The orb's pattern with a minimum time on screen, so a state that lasts a
 * moment (a call planned and sent within a second) does not flicker past.
 * Listening and connecting switch at once; anything else waits until the
 * current pattern has been up for `minMs`, then shows whatever is current.
 */
export const useHeldOrbState = (wanted: OrbState, minMs = 1200): OrbState => {
  const [shown, setShown] = useState<OrbState>(wanted);
  const since = useRef(Date.now());

  useEffect(() => {
    if (wanted === shown) return;
    const wait = holdFor(shown, wanted, Date.now() - since.current, minMs);
    if (wait === 0) {
      since.current = Date.now();
      setShown(wanted);
      return;
    }
    const timer = setTimeout(() => {
      since.current = Date.now();
      setShown(wanted);
    }, wait);
    return () => clearTimeout(timer);
  }, [wanted, shown, minMs]);

  return shown;
};
