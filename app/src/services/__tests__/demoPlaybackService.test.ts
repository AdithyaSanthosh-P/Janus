import { demoPlaybackService } from '../demoPlaybackService';
import { janusStore } from '../../store/janusStore';

describe('DemoPlaybackService', () => {
  beforeEach(() => {
    demoPlaybackService.reset();
  });

  afterEach(() => {
    demoPlaybackService.reset();
  });

  it('handles play, pause, speed toggle, and reset', async () => {
    const initialStatus = demoPlaybackService.getStatus();
    expect(initialStatus.isPlaying).toBe(false);
    expect(initialStatus.currentIndex).toBe(0);

    // Speed adjustments
    demoPlaybackService.setSpeed(2.0);
    expect(demoPlaybackService.getStatus().speed).toBe(2.0);
    demoPlaybackService.setSpeed(1.0);
    expect(demoPlaybackService.getStatus().speed).toBe(1.0);

    // Trigger play
    demoPlaybackService.play();
    expect(demoPlaybackService.getStatus().isPlaying).toBe(true);

    // Let it process initial zero-delay events
    await new Promise((resolve) => setTimeout(resolve, 80));

    expect(demoPlaybackService.getStatus().currentIndex).toBeGreaterThan(0);
    const state = janusStore.getState();
    expect(state.connectionStatus).toBe('demo');
    expect(state.backend).toBeDefined();

    // Pause
    demoPlaybackService.pause();
    expect(demoPlaybackService.getStatus().isPlaying).toBe(false);

    // Reset
    demoPlaybackService.reset();
    expect(demoPlaybackService.getStatus().currentIndex).toBe(0);
    expect(demoPlaybackService.getStatus().isPlaying).toBe(false);
  });
});

