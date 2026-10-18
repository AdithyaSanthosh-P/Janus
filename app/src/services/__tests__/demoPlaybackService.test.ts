import test from 'node:test';
import assert from 'node:assert';
import { demoPlaybackService } from '../demoPlaybackService';
import { janusStore } from '../../store/janusStore';

test('DemoPlaybackService handles play, pause, speed toggle, and reset', async () => {
  demoPlaybackService.reset();
  const initialStatus = demoPlaybackService.getStatus();
  assert.strictEqual(initialStatus.isPlaying, false);
  assert.strictEqual(initialStatus.currentIndex, 0);

  // Speed adjustments
  demoPlaybackService.setSpeed(2.0);
  assert.strictEqual(demoPlaybackService.getStatus().speed, 2.0);
  demoPlaybackService.setSpeed(1.0);
  assert.strictEqual(demoPlaybackService.getStatus().speed, 1.0);

  // Trigger play
  demoPlaybackService.play();
  assert.strictEqual(demoPlaybackService.getStatus().isPlaying, true);

  // Let it process initial zero-delay events
  await new Promise(resolve => setTimeout(resolve, 80));

  assert.ok(demoPlaybackService.getStatus().currentIndex > 0);
  const state = janusStore.getState();
  assert.strictEqual(state.connectionStatus, 'demo');
  assert.ok(state.backend);

  // Pause
  demoPlaybackService.pause();
  assert.strictEqual(demoPlaybackService.getStatus().isPlaying, false);

  // Reset
  demoPlaybackService.reset();
  assert.strictEqual(demoPlaybackService.getStatus().currentIndex, 0);
  assert.strictEqual(demoPlaybackService.getStatus().isPlaying, false);
});
