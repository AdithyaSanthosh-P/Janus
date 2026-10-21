import React, { useEffect } from 'react';
import { View, Text } from 'react-native';
import Animated, { useSharedValue, useAnimatedStyle, withSpring, withSequence, withTiming } from 'react-native-reanimated';
import { Lock, LockOpen, Warehouse, DoorOpen, Thermometer, Lightbulb, LightbulbOff, WashingMachine, Cpu } from 'lucide-react-native';
import { DeviceStateData } from '../types/janus';
import { tokens } from '../theme/colors';
import { type } from '../theme/type';
import { SPRING } from '../theme/motion';

interface DeviceCardProps {
  device: DeviceStateData;
  isDark: boolean;
}

/**
 * One device: its name, then its state as the figure. The state is in ink
 * unless it needs attention (unlocked, open, paused), which is amber; green
 * stays reserved for actions the device confirmed.
 */
export const DeviceCard: React.FC<DeviceCardProps> = ({ device, isDark }) => {
  const t = tokens(isDark);
  const scale = useSharedValue(1);

  useEffect(() => {
    if (device.updatedAt > 0) {
      scale.value = withSequence(withTiming(1.03, { duration: 110 }), withSpring(1, SPRING));
    }
  }, [device.value, device.updatedAt]);

  const animatedStyle = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }));

  let value = String(device.value);
  let unit: string | undefined;
  let ink = t.ink;
  let Glyph = Cpu;

  const cap = device.capability || '';
  const devId = device.device || '';

  if (cap === 'lock' || devId === 'dev-lock' || devId.startsWith('lock-')) {
    const locked = device.value === 'locked';
    value = locked ? 'Locked' : 'Unlocked';
    ink = locked ? t.ink : t.caution.ink;
    Glyph = locked ? Lock : LockOpen;
  } else if (cap === 'garageDoorControl' || cap === 'doorControl' || devId === 'dev-garage' || devId.startsWith('garage-')) {
    const closed = device.value === 'closed';
    value = closed ? 'Closed' : 'Open';
    ink = closed ? t.ink : t.caution.ink;
    Glyph = closed ? Warehouse : DoorOpen;
  } else if (cap === 'thermostatCoolingSetpoint' || cap.includes('thermostat') || devId === 'dev-ac' || devId.startsWith('thermo-')) {
    value = String(device.value);
    unit = typeof device.value === 'number' ? '°C' : undefined;
    Glyph = Thermometer;
  } else if (cap === 'switch' || devId === 'dev-light' || devId.startsWith('switch-')) {
    const on = device.value === 'on';
    value = on ? 'On' : 'Off';
    ink = on ? t.ink : t.ink2;
    Glyph = on ? Lightbulb : LightbulbOff;
  } else if (cap === 'washerOperatingState' || cap.includes('washer') || devId === 'dev-washer' || devId.startsWith('washer-')) {
    // No error code here: a user can pause with no fault; the agent says the code.
    if (device.value === 'pause') {
      value = 'Paused';
      ink = t.caution.ink;
    } else if (device.value === 'run') {
      value = 'Running';
    } else if (device.value === 'stop') {
      value = 'Stopped';
      ink = t.ink2;
    }
    Glyph = WashingMachine;
  }

  return (
    <Animated.View
      style={[{ flexGrow: 1, flexBasis: '40%', backgroundColor: t.surface, borderRadius: 18, padding: 16 }, animatedStyle]}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <Glyph size={15} color={t.ink3} />
        <Text style={[type.footnote, { color: t.ink2, flex: 1 }]} numberOfLines={1}>
          {device.label || device.device}
        </Text>
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 3, marginTop: 10 }}>
        <Text style={[type.figure, { color: ink }]} numberOfLines={1}>
          {value}
        </Text>
        {unit && <Text style={[type.subhead, { color: t.ink2 }]}>{unit}</Text>}
      </View>
    </Animated.View>
  );
};
