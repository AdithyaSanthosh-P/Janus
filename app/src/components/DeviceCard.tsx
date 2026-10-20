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
 * - Primitives: SmartThings tile widget styled by capability with prominent vector icons and state badges.
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

  // Device display formatting & vector icon selection - styled by capability
  let formattedValue = String(device.value);
  let statusColor = '#10b981'; // default emerald
  let iconBg = isDark ? 'rgba(16, 185, 129, 0.15)' : '#ecfdf5';

  let iconNode = <Cpu size={20} color={statusColor} />;

  const cap = device.capability || '';
  const devId = device.device || '';

  // 1. Lock capability (match on capability 'lock', or fallback to id 'dev-lock' / 'lock-*')
  if (cap === 'lock' || devId === 'dev-lock' || devId.startsWith('lock-')) {
    const isLocked = device.value === 'locked';
    formattedValue = isLocked ? 'Locked' : 'Unlocked';
    statusColor = isLocked ? '#10b981' : '#f59e0b';
    iconBg = isLocked
      ? isDark
        ? 'rgba(16, 185, 129, 0.15)'
        : '#ecfdf5'
      : isDark
      ? 'rgba(245, 158, 11, 0.15)'
      : '#fffbeb';
    iconNode = isLocked ? (
      <Lock size={20} color={statusColor} />
    ) : (
      <LockOpen size={20} color={statusColor} />
    );
  }
  // 2. Garage door capability (match on 'garageDoorControl' or 'doorControl', or id 'dev-garage' / 'garage-*')
  else if (
    cap === 'garageDoorControl' ||
    cap === 'doorControl' ||
    devId === 'dev-garage' ||
    devId.startsWith('garage-')
  ) {
    const isClosed = device.value === 'closed';
    formattedValue = isClosed ? 'Closed' : 'Open';
    statusColor = isClosed ? '#10b981' : '#3b82f6';
    iconBg = isClosed
      ? isDark
        ? 'rgba(16, 185, 129, 0.15)'
        : '#ecfdf5'
      : isDark
      ? 'rgba(59, 130, 246, 0.15)'
      : '#eff6ff';
    iconNode = isClosed ? (
      <Warehouse size={20} color={statusColor} />
    ) : (
      <DoorOpen size={20} color={statusColor} />
    );
  }
  // 3. Thermostat / AC cooling setpoint (match on 'thermostatCoolingSetpoint', or id 'dev-ac' / 'thermo-*')
  else if (
    cap === 'thermostatCoolingSetpoint' ||
    cap.includes('thermostat') ||
    devId === 'dev-ac' ||
    devId.startsWith('thermo-')
  ) {
    formattedValue = typeof device.value === 'number' ? `${device.value} °C` : `${device.value}`;
    statusColor = '#06b6d4'; // cyan
    iconBg = isDark ? 'rgba(6, 182, 212, 0.15)' : '#ecfeff';
    iconNode = <Thermometer size={20} color={statusColor} />;
  }
  // 4. Switch / Light capability (match on 'switch', or id 'dev-light' / 'switch-*')
  else if (cap === 'switch' || devId === 'dev-light' || devId.startsWith('switch-')) {
    const isOn = device.value === 'on';
    formattedValue = isOn ? 'On' : 'Off';
    statusColor = isOn ? '#f59e0b' : '#71717a';
    iconBg = isOn
      ? isDark
        ? 'rgba(245, 158, 11, 0.15)'
        : '#fffbeb'
      : isDark
      ? '#27272a'
      : '#f1f5f9';
    iconNode = isOn ? (
      <Lightbulb size={20} color={statusColor} />
    ) : (
      <LightbulbOff size={20} color={statusColor} />
    );
  }
  // 5. Washer capability (match on 'washerOperatingState', or id 'dev-washer' / 'washer-*')
  else if (
    cap === 'washerOperatingState' ||
    cap.includes('washer') ||
    devId === 'dev-washer' ||
    devId.startsWith('washer-')
  ) {
    if (device.value === 'pause') {
      // No error code here: a user can pause with no fault; the agent says the code.
      formattedValue = 'Paused';
      statusColor = '#f59e0b';
      iconBg = isDark ? 'rgba(245, 158, 11, 0.15)' : '#fffbeb';
    } else if (device.value === 'run') {
      formattedValue = 'Running';
      statusColor = '#3b82f6';
      iconBg = isDark ? 'rgba(59, 130, 246, 0.15)' : '#eff6ff';
    } else if (device.value === 'stop') {
      // Contract: Show stop as "Stopped"
      formattedValue = 'Stopped';
      statusColor = isDark ? '#a1a1aa' : '#64748b';
      iconBg = isDark ? '#27272a' : '#f1f5f9';
    } else {
      formattedValue = String(device.value);
      statusColor = '#10b981';
      iconBg = isDark ? 'rgba(16, 185, 129, 0.15)' : '#ecfdf5';
    }
    iconNode = <WashingMachine size={20} color={statusColor} />;
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
        numberOfLines={1}
      >
        {device.label || device.device}
      </Text>

      <Text
        style={{
          fontSize: 15,
          fontWeight: '700',
          color: isDark ? '#fafafa' : '#0f172a',
        }}
        numberOfLines={1}
      >
        {formattedValue}
      </Text>
    </Animated.View>
  );
};
