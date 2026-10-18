import { SAMPLE_EVENTS } from '../data/sampleEvents';
import { janusStore } from '../store/janusStore';

export class DemoPlaybackService {
  private currentIndex = 0;
  private isPlaying = false;
  private speed = 1.0;
  private timer: NodeJS.Timeout | null = null;
  private listeners: Array<(status: { isPlaying: boolean; speed: number; progress: number }) => void> = [];

  public subscribe(listener: (status: { isPlaying: boolean; speed: number; progress: number }) => void) {
    this.listeners.push(listener);
    this.notify();
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  private notify() {
    const progress = SAMPLE_EVENTS.length > 0 ? this.currentIndex / SAMPLE_EVENTS.length : 0;
    for (const listener of this.listeners) {
      listener({
        isPlaying: this.isPlaying,
        speed: this.speed,
        progress,
      });
    }
  }

  public getStatus() {
    return {
      isPlaying: this.isPlaying,
      speed: this.speed,
      progress: SAMPLE_EVENTS.length > 0 ? this.currentIndex / SAMPLE_EVENTS.length : 0,
      currentIndex: this.currentIndex,
      totalEvents: SAMPLE_EVENTS.length,
    };
  }

  public setSpeed(newSpeed: number) {
    this.speed = Math.max(0.5, Math.min(newSpeed, 4.0));
    this.notify();
  }

  public play() {
    if (this.isPlaying) return;
    this.isPlaying = true;
    janusStore.setConnectionStatus('demo');
    this.scheduleNext();
    this.notify();
  }

  public pause() {
    if (!this.isPlaying) return;
    this.isPlaying = false;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.notify();
  }

  public reset() {
    this.pause();
    this.currentIndex = 0;
    janusStore.reset(false);
    janusStore.setConnectionStatus('demo');
    this.notify();
  }

  private scheduleNext() {
    if (!this.isPlaying) return;

    if (this.currentIndex >= SAMPLE_EVENTS.length) {
      this.isPlaying = false;
      this.notify();
      return;
    }

    const currentEvent = SAMPLE_EVENTS[this.currentIndex];
    const prevEvent = this.currentIndex > 0 ? SAMPLE_EVENTS[this.currentIndex - 1] : null;

    let delayMs = 0;
    if (prevEvent) {
      const delta = Math.max(0, currentEvent.t_ms - prevEvent.t_ms);
      delayMs = delta / this.speed;
    }

    this.timer = setTimeout(() => {
      if (!this.isPlaying) return;
      janusStore.processEvent(currentEvent);
      this.currentIndex++;
      this.notify();
      this.scheduleNext();
    }, delayMs);
  }
}

export const demoPlaybackService = new DemoPlaybackService();
