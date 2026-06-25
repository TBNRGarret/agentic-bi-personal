declare module "plotly.js-dist-min" {
  const Plotly: {
    newPlot: (node: HTMLElement, data: unknown[], layout: Record<string, unknown>, config?: Record<string, unknown>) => Promise<unknown>;
    purge: (node: HTMLElement) => void;
    Plots: {
      resize: (node: HTMLElement) => void;
    };
  };
  export default Plotly;
}
