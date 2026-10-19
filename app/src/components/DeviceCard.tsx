import React, { useEffect } from 'react';
import { View, Text } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  withSequence,
} from 'react-native-reanimated';
import {
  Lock,
  LockOpen,
  Warehouse,
  DoorOpen,
  Thermometer,
  Lightbulb,
  LightbulbOff,
  WashingMachine,
  Cpu,
} from 'lucide-react-native';
import { DeviceStateData } from '../types/janus';
import { LUCID_SPRING_CONFIG, SNAPPY_SPRING_CONFIG } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: SmartThings tile widget with prominent vector icons and state badges.
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

  // Device display formatting & vector icon selection
  let formattedValue = String(device.value);
  let statusColor = '#10b981'; // default emerald
  let iconBg = isDark ? 'rgba(16, 185, 129, 0.15)' : '#ecfdf5';

  let iconNode = <Cpu size={20} color={statusColor} />;

  switch (device.device) {
    case 'dev-lock':
      formattedValue = device.value === 'locked' ? 'Locked' : 'Unlocked';
      statusColor = device.value === 'locked' ? '#10b981' : '#f59e0b';
      iconBg =
        device.value === 'locked'
          ? isDark
            ? 'rgba(16, 185, 129, 0.15)'
            : '#ecfdf5'
          : isDark
          ? 'rgba(245, 158, 11, 0.15)'
          : '#fffbeb';
      iconNode =
        device.value === 'locked' ? (
          <Lock size={20} color={statusColor} />
        ) : (
          <LockOpen size={20} color={statusColor} />
        );
      break;

    case 'dev-garage':
      formattedValue = device.value === 'closed' ? 'Closed' : 'Open';
      statusColor = device.value === 'closed' ? '#10b981' : '#3b82f6';
      iconBg =
        device.value === 'closed'
          ? isDark
            ? 'rgba(16, 185, 129, 0.15)'
            : '#ecfdf5'
          : isDark
          ? 'rgba(59, 130, 246, 0.15)'
          : '#eff6ff';
      iconNode =
        device.value === 'closed' ? (
          <Warehouse size={20} color={statusColor} />
        ) : (
          <DoorOpen size={20} color={statusColor} />
        );
      break;

    case 'dev-ac':
      formattedValue = `${device.value} °C`;
      statusColor = '#06b6d4'; // cyan
      iconBg = isDark ? 'rgba(6, 182, 212, 0.15)' : '#ecfeff';
      iconNode = <Thermometer size={20} color={statusColor} />;
      break;

    case 'dev-light':
      formattedValue = device.value === 'on' ? 'On' : 'Off';
      statusColor = device.value === 'on' ? '#f59e0b' : '#71717a';
      iconBg =
        device.value === 'on'
          ? isDark
            ? 'rgba(245, 158, 11, 0.15)'
            : '#fffbeb'
          : isDark
          ? '#27272a'
          : '#f1f5f9';
      iconNode =
        device.value === 'on' ? (
          <Lightbulb size={20} color={statusColor} />
        ) : (
          <LightbulbOff size={20} color={statusColor} />
        );
      break;

    case 'dev-washer':
      if (device.value === 'pause') {
        formattedValue = 'Paused · 4C';
        statusColor = '#ef4444'; // red error code
        iconBg = isDark ? 'rgba(239, 68, 68, 0.15)' : '#fee2e2';
      } else if (device.value === 'run') {
        formattedValue = 'Running';
        statusColor = '#3b82f6';
        iconBg = isDark ? 'rgba(59, 130, 246, 0.15)' : '#eff6ff';
      } else {
        formattedValue = String(device.value);
        statusColor = '#10b981';
        iconBg = isDark ? 'rgba(16, 185, 129, 0.15)' : '#ecfdf5';
      }
      iconNode = <WashingMachine size={20} color={statusColor} />;
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
          marginBottom: 10,
        }}
      >
        <View
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            backgroundColor: iconBg,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {iconNode}
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

