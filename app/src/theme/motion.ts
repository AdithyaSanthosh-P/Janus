import {
  FadeIn,
  FadeInDown,
  FadeOut,
  LinearTransition,
} from 'react-native-reanimated';

/**
 * Springs as damping ratio + response (seconds), mapped to Reanimated's
 * mass/stiffness/damping: stiffness = (2π / response)², damping = 4πζ / response.
 * Everything is critically damped (ζ = 1): no overshoot unless a gesture
 * carried momentum, and nothing in this app is thrown.
 */
const spring = (response: number, dampingRatio = 1) => ({
  mass: 1,
  stiffness: Math.round((2 * Math.PI / response) ** 2),
  damping: Math.round((4 * Math.PI * dampingRatio) / response),
});

export const SPRING = spring(0.38); // layout, state changes
export const SPRING_QUICK = spring(0.2); // press feedback

const springify = <T extends { springify: () => any }>(anim: T, s = SPRING) =>
  anim.springify().mass(s.mass).stiffness(s.stiffness).damping(s.damping);

export const AppLayoutTransition = springify(LinearTransition);
export const CardEntryTransition = springify(FadeInDown);
export const BubbleEntryTransition = springify(FadeIn);

export const FadeQuick = FadeIn.duration(200);
export const FadeOutQuick = FadeOut.duration(200);
