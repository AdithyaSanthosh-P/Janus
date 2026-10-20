import 'react-native-reanimated';
import { registerGlobals } from '@livekit/react-native';
import { AppRegistry } from 'react-native';
import App from './App';
import { name as appName } from './app.json';

// WebRTC globals for livekit-client; must run once, before any Room is made.
registerGlobals();

AppRegistry.registerComponent(appName, () => App);
