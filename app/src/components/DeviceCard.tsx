import React, { useEffect } from 'react';
import { View, Text } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  withSequence,
} from 'react-native-reanimated';
import { DeviceStateData } from '../types/janus';
import { LUCID_SPRING_CONFIG, SNAPPY_SPRING_CONFIG } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: React Native Reusables Card tile widget with status badges.
 * - Animation: Reanimated 3 scale spring bounce when device value updates.
 * - Theming: Dynamic state colors (Emerald for locked/closed/normal, Amber/Cyan for active, Rose for error).
 */

interface DeviceCardProps {
  device: DeviceStateData;
  isDark: boolean;
}

export const DeviceCard: React.FC<DeviceCardProps> = ({ device, isDark }) => {
  const scale = useSharedValue(1);

  useEffect(() => {
    if (device.updatedAt > 0) {
      scale.value = withSequence(
        withSpring(1.06, SNAPPY_SPRING_CONFIG),
        withSpring(1.0, LUCID_SPRING_CONFIG)
      );
    }
  }, [device.value, device.updatedAt]);

  const animatedStyle = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
  }));

  // Device display formatting
  let tag = 'DEVICE';
  let formattedValue = String(device.value);
  let statusColor = '#10b981'; // default emerald

  switch (device.device) {
    case 'dev-lock':
      tag = 'LOCK';
      formattedValue = device.value === 'locked' ? 'Locked' : 'Unlocked';
      statusColor = device.value === 'locked' ? '#10b981' : '#f59e0b';
      break;

    case 'dev-garage':
      tag = 'GARAGE';
      formattedValue = device.value === 'closed' ? 'Closed' : 'Open';
      statusColor = device.value === 'closed' ? '#10b981' : '#3b82f6';
      break;

    case 'dev-ac':
      tag = 'CLIMATE';
      formattedValue = `${device.value} °C`;
      statusColor = '#06b6d4'; // cyan
      break;

    case 'dev-light':
      tag = 'LIGHT';
      formattedValue = device.value === 'on' ? 'On' : 'Off';
      statusColor = device.value === 'on' ? '#f59e0b' : '#71717a';
      break;

    case 'dev-washer':
      tag = 'WASHER';
      if (device.value === 'pause') {
        formattedValue = 'Paused · 4C';
        statusColor = '#ef4444'; // red error code
      } else if (device.value === 'run') {
        formattedValue = 'Running';
        statusColor = '#3b82f6';
      } else {
        formattedValue = String(device.value);
        statusColor = '#10b981';
      }
      break;
  }

  return (
    <Animated.View
      style={[
        {
          width: '48%',
          backgroundColor: isDark ? '#18181b' : '#ffffff',
          borderRadius: 14,
          borderWidth: 1.5,
          borderColor: isDark ? '#27272a' : '#e2e8f0',
          padding: 12,
        },
        animatedStyle,
      ]}
    >
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 8,
        }}
      >
        <View
          style={{
            paddingHorizontal: 7,
            paddingVertical: 2.5,
            borderRadius: 6,
            backgroundColor: isDark ? '#27272a' : '#f1f5f9',
          }}
        >
          <Text
            style={{
              fontSize: 10,
              fontWeight: '800',
              letterSpacing: 0.8,
              color: isDark ? '#d4d4d8' : '#475569',
            }}
          >
            {tag}
          </Text>
        </View>
        <View
          style={{
            width: 8,
            height: 8,
            borderRadius: 4,
            backgroundColor: statusColor,
          }}
        />
      </View>

      <Text
        style={{
          fontSize: 12,
          fontWeight: '600',
          color: isDark ? '#a1a1aa' : '#64748b',
          marginBottom: 2,
        }}
      >
        {device.label}
      </Text>

      <Text
        style={{
          fontSize: 15,
          fontWeight: '700',
          color: isDark ? '#fafafa' : '#0f172a',
        }}
      >
        {formattedValue}
      </Text>
    </Animated.View>
  );
};
