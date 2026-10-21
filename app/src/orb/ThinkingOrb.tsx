import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, AppState } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSequence, withSpring, withTiming } from 'react-native-reanimated';
import Svg, { Circle, Line } from 'react-native-svg';
import { MODE_FRAMES } from './engine/registry';
import { resolvePreset } from './presets';
import { OrbState } from './types';
import { SPRING } from '../theme/motion';

/**
 * A dotted thinking orb (geometry from the MIT-licensed `thinking-orbs`
 * engine, vendored in ./engine), drawn with react-native-svg. The engine
 * returns a finished, z-sorted dot list per instant; this only paints it.
 * The 20px preset is rendered scaled up through the viewBox, so a small
 * orb keeps the dot count (and the render cost) of the small design.
 *
 * Changing `state` plays a short settle (dip and spring back) so the shift
 * between patterns reads as one thing becoming another.
 */

const FRAME_MS = 1000 / 30;
const REDUCED_MOTION_T = 0.6;
const NATIVE = 20 as const;

interface Props {
  state: OrbState;
  size?: number;
  /** Ink: `dark` mirrors the engine's paper-theme greys for a dark ground. */
  dark: boolean;
  label?: string;
}

const grey = (white: number, dark: boolean, alpha = 1) => {
  const w = Math.min(1, Math.max(0, white));
  const g = Math.round((dark ? 1 - w : w) * 255);
  return `rgba(${g},${g},${g},${alpha})`;
};

export const ThinkingOrb: React.FC<Props> = ({ state, size = 34, dark, label }) => {
  const { mode, speed, opts } = useMemo(() => resolvePreset(state, NATIVE), [state]);
  const [frame, setFrame] = useState(() => MODE_FRAMES[mode](NATIVE, 0, opts));
  const [reduced, setReduced] = useState(false);
  const [active, setActive] = useState(AppState.currentState === 'active');
  const origin = useRef(Date.now());

  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled().then(v => alive && setReduced(v)).catch(() => {});
    const motion = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduced);
    const app = AppState.addEventListener('change', s => setActive(s === 'active'));
    return () => {
      alive = false;
      motion.remove();
      app.remove();
    };
  }, []);

  useEffect(() => {
    const build = MODE_FRAMES[mode];
    if (reduced) {
      setFrame(build(NATIVE, REDUCED_MOTION_T, opts));
      return;
    }
    const at = () => ((Date.now() - origin.current) / 1000) * speed;
    setFrame(build(NATIVE, at(), opts));
    if (!active) return;
    const id = setInterval(() => setFrame(build(NATIVE, at(), opts)), FRAME_MS);
    return () => clearInterval(id);
  }, [mode, opts, speed, reduced, active]);

  const settle = useSharedValue(1);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    settle.value = withSequence(withTiming(0.82, { duration: 90 }), withSpring(1, SPRING));
  }, [state]);
  const settleStyle = useAnimatedStyle(() => ({ opacity: 0.4 + 0.6 * settle.value, transform: [{ scale: settle.value }] }));

  return (
    <Animated.View
      accessible
      accessibilityRole="image"
      accessibilityLabel={label ?? 'Janus'}
      style={[{ width: size, height: size }, settleStyle]}
    >
      <Svg width={size} height={size} viewBox={`0 0 ${NATIVE} ${NATIVE}`}>
        {frame.lines.map((l, i) => (
          <Line key={`l${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={grey(l.white, dark, l.a ?? 1)} strokeWidth={l.w} />
        ))}
        {frame.dots.map((d, i) => (
          <Circle key={i} cx={d.x} cy={d.y} r={d.r} fill={grey(d.white, dark, d.a ?? 1)} />
        ))}
      </Svg>
    </Animated.View>
  );
};
