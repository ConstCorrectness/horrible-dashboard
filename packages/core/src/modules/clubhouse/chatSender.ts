/**
 * Posting to a room's chat without tripping Clubhouse's two refusals.
 *
 * Both were seen in a live room (2026-10-04) and both come back as an error the user
 * never sees, because the agent's posts swallow it:
 *
 * - **429 "Less is more! Please wait a bit"** — a rate limit. It fired between sends
 *   3 s and 7 s apart, so the old 300 ms gap between a long reply's chunks was never
 *   going to clear it, and a rejected message was simply lost.
 * - **400 "Looks like that's been said already!"** — an exact repeat. The silence nudge
 *   asks the model to "say something" every interval and a low-temperature model says
 *   the same 48 characters every time, so the room got one refusal a minute, forever.
 *
 * Everything is serialized through one chain, so a reply and a typed message cannot
 * overlap and the spacing holds across callers. Pure and clock-injected so the
 * timing is tested without waiting.
 */

export type PostResult = 'sent' | 'duplicate' | 'dropped';

export interface ChatSenderOptions {
  /** Minimum time between the end of one send attempt and the start of the next. */
  minGapMs?: number;
  /** Waits before each retry of a 429. One retry per entry; then the error is thrown. */
  retryDelaysMs?: number[];
  /** How long a posted line counts as "already said". */
  repeatWindowMs?: number;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}

type Send = (channel: string, text: string) => Promise<unknown>;

function statusOf(err: unknown): number | undefined {
  return typeof err === 'object' && err !== null ? (err as { status?: number }).status : undefined;
}

function isRateLimited(err: unknown): boolean {
  return statusOf(err) === 429;
}

/** Clubhouse's refusal of an exact repeat — a 400, not a 429, so never retried. */
function isRepeatRefusal(err: unknown): boolean {
  return (
    statusOf(err) === 400 && /said already/i.test(err instanceof Error ? err.message : String(err))
  );
}

export class ChatSender {
  private chain: Promise<unknown> = Promise.resolve();
  private lastAttemptAt = Number.NEGATIVE_INFINITY;
  private readonly posted = new Map<string, number>();
  /** Bumped by `reset`, so a post queued for a room we left is dropped, not sent. */
  private epoch = 0;

  private readonly minGapMs: number;
  private readonly retryDelaysMs: number[];
  private readonly repeatWindowMs: number;
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(
    private readonly send: Send,
    opts: ChatSenderOptions = {},
  ) {
    this.minGapMs = opts.minGapMs ?? 3000;
    this.retryDelaysMs = opts.retryDelaysMs ?? [5000, 10000];
    this.repeatWindowMs = opts.repeatWindowMs ?? 10 * 60_000;
    this.now = opts.now ?? Date.now;
    this.sleep = opts.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  private key(channel: string, text: string): string {
    return `${channel}\n${text.trim()}`;
  }

  /** Whether this exact line went out to this room recently. */
  wasPosted(channel: string, text: string): boolean {
    const at = this.posted.get(this.key(channel, text));
    return at !== undefined && this.now() - at < this.repeatWindowMs;
  }

  /** Record a line as said — for a whole reply whose chunks were sent separately. */
  remember(channel: string, text: string): void {
    this.posted.set(this.key(channel, text), this.now());
    // Bounded: a long-running room must not grow this without limit.
    if (this.posted.size > 200) {
      const oldest = this.posted.keys().next().value;
      if (oldest !== undefined) this.posted.delete(oldest);
    }
  }

  /** Forget everything and drop whatever is still queued. For leaving a room. */
  reset(): void {
    this.epoch++;
    this.posted.clear();
  }

  /**
   * Send one message. `dedupe` is for the agent: its repeats are skipped (`'duplicate'`).
   * A person's own message is never second-guessed — if they typed it again, Clubhouse
   * decides, and its answer reaches them as an error.
   *
   * Rejects with the original error once retries are spent, or on any refusal that
   * retrying cannot fix.
   */
  post(channel: string, text: string, opts: { dedupe?: boolean } = {}): Promise<PostResult> {
    const epoch = this.epoch;
    const run = async (): Promise<PostResult> => {
      if (epoch !== this.epoch) return 'dropped';
      if (opts.dedupe && this.wasPosted(channel, text)) return 'duplicate';

      for (let attempt = 0; ; attempt++) {
        const wait = this.lastAttemptAt + this.minGapMs - this.now();
        if (wait > 0) await this.sleep(wait);
        if (epoch !== this.epoch) return 'dropped';

        try {
          await this.send(channel, text);
          this.lastAttemptAt = this.now();
          this.remember(channel, text);
          return 'sent';
        } catch (err) {
          // A rejected attempt still counts toward their window.
          this.lastAttemptAt = this.now();
          if (isRepeatRefusal(err)) {
            // Someone — possibly us, from before a reload — already said it.
            this.remember(channel, text);
            throw err;
          }
          if (!isRateLimited(err) || attempt >= this.retryDelaysMs.length) throw err;
          await this.sleep(this.retryDelaysMs[attempt]);
        }
      }
    };

    const next = this.chain.then(run, run);
    // A failed post must not wedge the ones behind it.
    this.chain = next.catch(() => undefined);
    return next;
  }
}
