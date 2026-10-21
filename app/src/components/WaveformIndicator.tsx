import React, { useEffect } from 'react';
import { View } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withSpring,
  withDelay,
} from 'react-native-reanimated';
import { SPRING } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: Gluestack-inspired visualizer bars embedded in a flex row container.
 * - Animation: Reanimated 3 with 4 shared values oscillating via custom Lucid spring physics.
 * - Theming: Dynamic accent color (blue-500 in dark mode, blue-600 in light mode).
 */

interface WaveformIndicatorProps {
  active: boolean;
  color?: string;
  size?: 'sm' | 'md';
}

export const WaveformIndicator: React.FC<WaveformIndicatorProps> = ({
  active,
  color = '#3b82f6',
  size = 'md',
}) => {
  const barHeights = [
    useSharedValue(0.3),
    useSharedValue(0.5),
    useSharedValue(0.8),
    useSharedValue(0.4),
  ];

  const maxHeight = size === 'sm' ? 14 : 20;
  const barWidth = size === 'sm' ? 2.5 : 3.5;

  useEffect(() => {
    if (active) {
      const targets = [0.9, 0.4, 1.0, 0.6];
      barHeights.forEach((height, i) => {
        height.value = withDelay(
          i * 70,
          withRepeat(
            withSpring(targets[i], {
              damping: 10,
              stiffness: 140,
              mass: 0.4,
            }),
            -1,
            true
          )
        );
      });
    } else {
      barHeights.forEach(height => {
        height.value = withSpring(0.2, SPRING);
      });
    }
  }, [active]);

  const animatedStyles = barHeights.map(height =>
    useAnimatedStyle(() => ({
      height: height.value * maxHeight,
    }))
  );

  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: size === 'sm' ? 2 : 3,
        height: maxHeight,
      }}
    >
      {animatedStyles.map((style, idx) => (
        <Animated.View
          key={idx}
          style={[
            {
              width: barWidth,
              backgroundColor: color, opacity: active ? 1 : 0.4,
              borderRadius: barWidth / 2,
            },
            style,
          ]}
        />
      ))}
    </View>
  );
};
