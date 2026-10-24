import React from 'react';
import { Pressable, PressableProps, StyleProp, Text, View, ViewStyle } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import Svg, { Path } from 'react-native-svg';
import { Tokens } from '../theme/colors';
import { SPRING_QUICK } from '../theme/motion';
import { type } from '../theme/type';

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

interface PressProps extends Omit<PressableProps, 'style'> {
  style?: StyleProp<ViewStyle>;
  pressedStyle?: StyleProp<ViewStyle>;
  children?: React.ReactNode;
}

/** A pressable that answers on touch-down: it settles to 97% while held. */
export const Press: React.FC<PressProps> = ({ style, pressedStyle, children, onPressIn, onPressOut, ...rest }) => {
  const scale = useSharedValue(1);
  const [down, setDown] = React.useState(false);
  const animated = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }));
  return (
    <AnimatedPressable
      {...rest}
      onPressIn={e => {
        scale.value = withSpring(0.97, SPRING_QUICK);
        setDown(true);
        onPressIn?.(e);
      }}
      onPressOut={e => {
        scale.value = withSpring(1, SPRING_QUICK);
        setDown(false);
        onPressOut?.(e);
      }}
      style={[style, down && pressedStyle, animated]}
    >
      {children}
    </AnimatedPressable>
  );
};

/** The Janus mark: two arcs back to back, one facing each way. */
export const JanusMark: React.FC<{ size?: number; color: string }> = ({ size = 24, color }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <Path d="M10.6 3.5 A8.5 8.5 0 0 1 10.6 20.5" stroke={color} strokeWidth={2.4} strokeLinecap="round" />
    <Path d="M13.4 3.5 A8.5 8.5 0 0 0 13.4 20.5" stroke={color} strokeWidth={2.4} strokeLinecap="round" />
  </Svg>
);

/** A section heading: sentence case, grey, with an optional note on the right. */
export const SectionHeader: React.FC<{ t: Tokens; title: string; note?: string }> = ({ t, title, note }) => (
  <View style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', paddingHorizontal: 4, marginBottom: 8 }}>
    <Text style={[type.section, { color: t.ink2 }]}>{title}</Text>
    {note ? <Text style={[type.footnote, { color: t.ink3 }]}>{note}</Text> : null}
  </View>
);

/** A small status pill: a pale tint with text of the same hue and a dot. */
export const Pill: React.FC<{ text: string; ink: string; bg: string; dot?: string }> = ({ text, ink, bg, dot }) => (
  <View
    style={{
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      height: 24,
      paddingHorizontal: 10,
      borderRadius: 12,
      backgroundColor: bg,
      flexShrink: 0,
      maxWidth: '100%',
    }}
  >
    {dot ? <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: dot }} /> : null}
    <Text style={[type.caption, { color: ink, fontWeight: '600', flexShrink: 1 }]} numberOfLines={1}>
      {text}
    </Text>
  </View>
);
