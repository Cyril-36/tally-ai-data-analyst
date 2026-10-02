// Partial Plotly bundle with the traces the app draws: bar, scatter (line/area), pie, histogram.
declare module 'plotly.js-cartesian-dist-min' {
  interface PlotlyStatic {
    react(el: HTMLElement, data: unknown[], layout?: unknown, config?: unknown): Promise<unknown>;
    purge(el: HTMLElement): void;
    Plots: { resize(el: HTMLElement): void };
  }
  const Plotly: PlotlyStatic;
  export default Plotly;
}
