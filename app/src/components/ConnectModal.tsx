import React, { useState } from 'react';
import { View, Text, TextInput, Switch } from 'react-native';
import { tokens } from '../theme/colors';
import { type } from '../theme/type';
import { Press } from './primitives';
import { Group, Sheet } from './Sheet';

interface ConnectModalProps {
  visible: boolean;
  isDark: boolean;
  isConnected: boolean;
  onClose: () => void;
  onConnect: (url: string, token: string, enableMic: boolean) => Promise<void>;
  onDisconnect: () => Promise<void>;
  onLaunchDemoMode: () => void;
}

export const ConnectModal: React.FC<ConnectModalProps> = ({
  visible,
  isDark,
  isConnected,
  onClose,
  onConnect,
  onDisconnect,
  onLaunchDemoMode,
}) => {
  const t = tokens(isDark);
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [enableMic, setEnableMic] = useState(true);
  const [isConnecting, setIsConnecting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleConnect = async () => {
    if (!url.trim() || !token.trim()) {
      setErrorMessage('Enter both the LiveKit URL and the token.');
      return;
    }
    setErrorMessage(null);
    setIsConnecting(true);
    try {
      await onConnect(url, token, enableMic);
      onClose();
    } catch (err: any) {
      setErrorMessage(err?.message || 'Could not connect.');
    } finally {
      setIsConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    await onDisconnect();
    onClose();
  };

  const field = { ...type.body, color: t.ink, paddingHorizontal: 16, paddingVertical: 12 };

  return (
    <Sheet visible={visible} t={t} title="Connect to Janus" subtitle="Join the LiveKit room the agent is in." onClose={onClose}>
      {errorMessage && (
        <View style={{ backgroundColor: t.negative.bg, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10 }}>
          <Text style={[type.subhead, { color: t.negative.ink }]}>{errorMessage}</Text>
        </View>
      )}

      <Group t={t} title="Room">
        <TextInput
          value={url}
          onChangeText={setUrl}
          placeholder="wss://your-project.livekit.cloud"
          placeholderTextColor={t.ink3}
          autoCapitalize="none"
          autoCorrect={false}
          style={field}
        />
        <TextInput
          value={token}
          onChangeText={setToken}
          placeholder="Access token"
          placeholderTextColor={t.ink3}
          multiline
          autoCapitalize="none"
          autoCorrect={false}
          style={[field, { ...type.footnote, minHeight: 72, textAlignVertical: 'top' }]}
        />
      </Group>

      <Group t={t} footer="Your phone's microphone is sent to the agent while connected.">
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 10 }}>
          <Text style={[type.body, { color: t.ink }]}>Microphone</Text>
          <Switch
            value={enableMic}
            onValueChange={setEnableMic}
            trackColor={{ false: t.pressed, true: t.positive.mark }}
            thumbColor="#FFFFFF"
          />
        </View>
      </Group>

      <View style={{ gap: 10 }}>
        {isConnected ? (
          <Press onPress={handleDisconnect} style={{ height: 50, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: t.negative.bg }}>
            <Text style={[type.body, { color: t.negative.ink, fontWeight: '600' }]}>Leave the room</Text>
          </Press>
        ) : (
          <Press
            onPress={handleConnect}
            disabled={isConnecting}
            style={{ height: 50, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: t.inverseFill, opacity: isConnecting ? 0.6 : 1 }}
          >
            <Text style={[type.body, { color: t.inverseInk, fontWeight: '600' }]}>{isConnecting ? 'Connecting…' : 'Join room'}</Text>
          </Press>
        )}
        <Press
          onPress={() => {
            onLaunchDemoMode();
            onClose();
          }}
          style={{ height: 46, borderRadius: 14, alignItems: 'center', justifyContent: 'center' }}
        >
          <Text style={[type.body, { color: t.ink2, fontWeight: '500' }]}>Play the recorded session instead</Text>
        </Press>
      </View>
    </Sheet>
  );
};
