import React from 'react';
import { View, Text } from 'react-native';
import Animated from 'react-native-reanimated';
import { DeviceCard } from './DeviceCard';
import { DeviceStateData } from '../types/janus';
import { AppLayoutTransition } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: 2-column flex-wrap responsive grid layout.
 * - Animation: Reanimated 3 continuous layout transition when device states update.
 * - Theming: Cohesive dark & light palette.
 */

interface DeviceTilesProps {
  devices: Record<string, DeviceStateData>;
  isDark: boolean;
}

export const DeviceTiles: React.FC<DeviceTilesProps> = ({ devices, isDark }) => {
  const deviceList: DeviceStateData[] = Object.values(devices);

  return (
    <Animated.View layout={AppLayoutTransition} style={{ marginBottom: 16 }}>
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 10,
        }}
      >
        <Text
          style={{
            fontSize: 12,
            fontWeight: '700',
            textTransform: 'uppercase',
            letterSpacing: 0.8,
            color: isDark ? '#a1a1aa' : '#64748b',
          }}
        >
          Smart Home Devices
        </Text>
        <Text
          style={{
            fontSize: 11,
            fontWeight: '600',
            color: isDark ? '#71717a' : '#94a3b8',
          }}
        >
          SmartThings Connected
        </Text>
      </View>

      <View
        style={{
          flexDirection: 'row',
          flexWrap: 'wrap',
          gap: 10,
          justifyContent: 'space-between',
        }}
      >
        {deviceList.map(dev => (
          <DeviceCard key={dev.device} device={dev} isDark={isDark} />
        ))}
      </View>
    </Animated.View>
  );
};
