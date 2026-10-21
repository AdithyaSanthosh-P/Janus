import React from 'react';
import { View, Text } from 'react-native';
import Animated from 'react-native-reanimated';
import { DeviceCard } from './DeviceCard';
import { BackendType, DeviceStateData } from '../types/janus';
import { AppLayoutTransition } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: 2-column flex-wrap responsive grid layout with empty state placeholder.
 * - Animation: Reanimated 3 continuous layout transition when device states update.
 * - Theming: Cohesive dark & light palette.
 */

interface DeviceTilesProps {
  devices: Record<string, DeviceStateData>;
  isDark: boolean;
  backend?: BackendType;
}

export const DeviceTiles: React.FC<DeviceTilesProps> = ({ devices, isDark, backend = 'unknown' }) => {
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
          {backend === 'real' ? 'SmartThings cloud' : backend === 'mock' ? 'Simulated devices' : 'Not connected'}
        </Text>
      </View>

      {deviceList.length === 0 ? (
        <View
          style={{
            padding: 18,
            borderRadius: 14,
            borderWidth: 1,
            borderColor: isDark ? '#27272a' : '#e2e8f0',
            backgroundColor: isDark ? '#18181b' : '#ffffff',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text
            style={{
              fontSize: 13,
              fontWeight: '500',
              color: isDark ? '#71717a' : '#94a3b8',
            }}
          >
            Awaiting device states from agent...
          </Text>
        </View>
      ) : (
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
      )}
    </Animated.View>
  );
};
