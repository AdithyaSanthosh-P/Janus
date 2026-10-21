import React, { useState, useEffect } from 'react';
import {
  SafeAreaView,
  StatusBar,
  ScrollView,
  View,
  useColorScheme,
} from 'react-native';
import { Header } from './src/components/Header';
import { CaptionsCard } from './src/components/CaptionsCard';
import { ActionTimeline } from './src/components/ActionTimeline';
import { DeviceTiles } from './src/components/DeviceTiles';
import { LedgerFooter } from './src/components/LedgerFooter';
import { ConnectModal } from './src/components/ConnectModal';
import { DrillDrawer } from './src/components/DrillDrawer';
import { janusStore, JanusState } from './src/store/janusStore';
import { demoPlaybackService } from './src/services/demoPlaybackService';
import { livekitService } from './src/services/livekitService';
import { drillService } from './src/services/drillService';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: SafeAreaView phone portrait container with Header, Captions, Timeline, Tiles, and Ledger Footer.
 * - Animation: Reanimated 3 with continuous layout transitions and Lucid spring physics.
 * - Theming: Full dynamic dark and light mode theme support (Galaxy clean minimalist aesthetic).
 */

export default function App() {
  const systemColorScheme = useColorScheme();
  const [isDark, setIsDark] = useState(true); // Default dark mode for tech demo aesthetic
  const [state, setState] = useState<JanusState>(janusStore.getState());
  const [connectModalVisible, setConnectModalVisible] = useState(false);
  const [drillDrawerVisible, setDrillDrawerVisible] = useState(false);
  const [isDrillActive, setIsDrillActive] = useState<boolean>(drillService.isAnyActive());
  const [demoStatus, setDemoStatus] = useState(demoPlaybackService.getStatus());
  const [isCameraOn, setIsCameraOn] = useState(false);

  // Camera on/off follows the LiveKit service (it resets on disconnect).
  useEffect(() => livekitService.subscribe(s => setIsCameraOn(s.isCameraOn)), []);

  // Subscribe to central Janus store
  useEffect(() => {
    const unsubscribe = janusStore.subscribe(nextState => {
      setState(nextState);
    });
    return unsubscribe;
  }, []);

  // Subscribe to demo playback status
  useEffect(() => {
    const unsubscribe = demoPlaybackService.subscribe(status => {
      setDemoStatus(demoPlaybackService.getStatus());
    });
    return unsubscribe;
  }, []);

  // Subscribe to failure drill status
  useEffect(() => {
    const unsubscribe = drillService.subscribe((_drillState, anyActive) => {
      setIsDrillActive(anyActive);
    });
    return unsubscribe;
  }, []);

  const handleToggleTheme = () => {
    setIsDark(prev => !prev);
  };

  const handleToggleDemoPlay = () => {
    if (demoStatus.isPlaying) {
      demoPlaybackService.pause();
    } else {
      demoPlaybackService.play();
    }
  };

  const handleToggleDemoSpeed = () => {
    const nextSpeed = demoStatus.speed === 1.0 ? 2.0 : 1.0;
    demoPlaybackService.setSpeed(nextSpeed);
  };

  const handleResetDemo = () => {
    demoPlaybackService.reset();
  };

  const handleConnectLiveKit = async (url: string, token: string, enableMic: boolean) => {
    demoPlaybackService.pause();
    await livekitService.connect({ url, token, enableMic });
  };

  const handleDisconnectLiveKit = async () => {
    await livekitService.disconnect();
  };

  return (
    <SafeAreaView
      style={{
        flex: 1,
        backgroundColor: isDark ? '#09090b' : '#f8fafc',
      }}
    >
      <StatusBar
        barStyle={isDark ? 'light-content' : 'dark-content'}
        backgroundColor={isDark ? '#09090b' : '#ffffff'}
      />

      {/* Top Header: Branding, Backend Badge, Mic indicator, Controls, Drill On Badge */}
      <Header
        backend={state.backend}
        connectionStatus={state.connectionStatus}
        userSpeaking={state.userSpeaking}
        isDark={isDark}
        demoPlaying={demoStatus.isPlaying}
        demoSpeed={demoStatus.speed}
        isDrillActive={isDrillActive}
        isCameraOn={isCameraOn}
        onToggleCamera={() => {
          livekitService.toggleCamera();
        }}
        onToggleTheme={handleToggleTheme}
        onOpenConnectModal={() => setConnectModalVisible(true)}
        onToggleDemoPlay={handleToggleDemoPlay}
        onToggleDemoSpeed={handleToggleDemoSpeed}
        onResetDemo={handleResetDemo}
        onOpenDrillDrawer={() => setDrillDrawerVisible(true)}
      />

      {/* Main Portrait Scroll Content */}
      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{
          padding: 16,
          gap: 16,
          paddingBottom: 24,
        }}
        style={{ flex: 1 }}
      >
        {/* Area 1: Live Captions */}
        <CaptionsCard
          captions={state.captions}
          isDark={isDark}
          userSpeaking={state.userSpeaking}
        />

        {/* Area 2: Action Timeline (Planned -> Held -> Waiting for Yes -> Sent -> Confirmed / Never Sent) */}
        <ActionTimeline actions={state.actions} isDark={isDark} />

        {/* Area 3: Home Device Tiles (Lock, Garage, AC, Light, Washer) */}
        <DeviceTiles devices={state.devices} isDark={isDark} backend={state.backend} />
      </ScrollView>

      {/* Bottom Pinned Area: Safety Ledger Counters */}
      <LedgerFooter ledger={state.ledger} isDark={isDark} />

      {/* Connect Modal for LiveKit URL & Token */}
      <ConnectModal
        visible={connectModalVisible}
        isDark={isDark}
        isConnected={state.connectionStatus === 'connected'}
        onClose={() => setConnectModalVisible(false)}
        onConnect={handleConnectLiveKit}
        onDisconnect={handleDisconnectLiveKit}
        onLaunchDemoMode={() => {
          demoPlaybackService.reset();
          demoPlaybackService.play();
        }}
      />

      {/* Hidden Failure Drills Drawer (opened by long-press on REAL/MOCK badge in live mode) */}
      <DrillDrawer
        visible={drillDrawerVisible}
        isDark={isDark}
        onClose={() => setDrillDrawerVisible(false)}
      />
    </SafeAreaView>
  );
}
