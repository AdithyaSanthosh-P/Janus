import {
  FadeIn,
  FadeInDown,
  FadeInUp,
  FadeOut,
  FadeOutUp,
  LinearTransition,
} from 'react-native-reanimated';

/**
 * Lucid Motion Physics Configurations (as per SKILL.md)
 * Organic, fluid, snappy spring configurations.
 */
export const LUCID_SPRING_CONFIG = {
  damping: 15,
  stiffness: 120,
  mass: 0.5,
};

export const GENTLE_SPRING_CONFIG = {
  damping: 20,
  stiffness: 90,
  mass: 0.8,
};

export const SNAPPY_SPRING_CONFIG = {
  damping: 12,
  stiffness: 150,
  mass: 0.4,
};

// Reusable Reanimated Layout Transitions
export const AppLayoutTransition = LinearTransition.springify()
  .damping(LUCID_SPRING_CONFIG.damping)
  .stiffness(LUCID_SPRING_CONFIG.stiffness)
  .mass(LUCID_SPRING_CONFIG.mass);

export const CardEntryTransition = FadeInDown.springify()
  .damping(LUCID_SPRING_CONFIG.damping)
  .stiffness(LUCID_SPRING_CONFIG.stiffness)
  .mass(LUCID_SPRING_CONFIG.mass);

export const CardExitTransition = FadeOutUp.springify()
  .damping(LUCID_SPRING_CONFIG.damping)
  .stiffness(LUCID_SPRING_CONFIG.stiffness)
  .mass(LUCID_SPRING_CONFIG.mass);

export const BubbleEntryTransition = FadeInUp.springify()
  .damping(SNAPPY_SPRING_CONFIG.damping)
  .stiffness(SNAPPY_SPRING_CONFIG.stiffness)
  .mass(SNAPPY_SPRING_CONFIG.mass);

export const FadeQuick = FadeIn.duration(200);
export const FadeOutQuick = FadeOut.duration(200);
