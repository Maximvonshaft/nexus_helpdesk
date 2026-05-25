export type StageName =
  | "queue"
  | "client_lookup"
  | "appserver_start"
  | "initialize"
  | "login"
  | "thread_start"
  | "turn_start"
  | "terminal_wait"
  | "parse"
  | "total";

export type StageMetrics = Record<StageName, number>;

export class StageTimer {
  private readonly started = Date.now();
  private readonly values: Partial<StageMetrics> = {};

  async measure<T>(stage: StageName, fn: () => Promise<T>): Promise<T> {
    const start = Date.now();
    try {
      return await fn();
    } finally {
      this.values[stage] = Date.now() - start;
    }
  }

  set(stage: StageName, value: number): void {
    this.values[stage] = value;
  }

  snapshot(): StageMetrics {
    return {
      queue: this.values.queue ?? 0,
      client_lookup: this.values.client_lookup ?? 0,
      appserver_start: this.values.appserver_start ?? 0,
      initialize: this.values.initialize ?? 0,
      login: this.values.login ?? 0,
      thread_start: this.values.thread_start ?? 0,
      turn_start: this.values.turn_start ?? 0,
      terminal_wait: this.values.terminal_wait ?? 0,
      parse: this.values.parse ?? 0,
      total: Date.now() - this.started,
    };
  }
}
