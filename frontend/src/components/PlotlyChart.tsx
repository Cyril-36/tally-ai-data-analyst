import { useEffect, useRef, useState } from 'react';
import type { Figure } from '../lib/figure';

interface Props {
  figure: Figure;
  ariaLabel: string;
  height: number;
  /** Called when Plotly cannot draw the figure, so the caller can fall back to the table. */
  onError?: (message: string) => void;
}

type PlotNode = HTMLDivElement & { _fullLayout?: unknown };

/** Thin Plotly wrapper. The library is loaded on first use so it stays out of the main bundle. */
export function PlotlyChart({ figure, ariaLabel, height, onError }: Props) {
  const el = useRef<PlotNode>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    let live = true;
    let observer: ResizeObserver | undefined;
    import('plotly.js-dist-min')
      .then(async (mod) => {
        if (!live) return;
        const Plotly = mod.default ?? (mod as unknown as typeof mod.default);
        await Plotly.react(node, figure.data, figure.layout, figure.config);
        if (!live) return;
        setReady(true);
        if (typeof ResizeObserver !== 'undefined') {
          observer = new ResizeObserver(() => {
            // Plotly throws on detached / not-yet-drawn / hidden nodes.
            if (live && node.isConnected && node._fullLayout && node.offsetParent !== null) {
              try {
                Plotly.Plots.resize(node);
              } catch {
                /* hidden while resizing */
              }
            }
          });
          observer.observe(node);
        }
      })
      .catch((e: unknown) => live && onError?.(e instanceof Error ? e.message : 'The chart could not be drawn.'));
    return () => {
      live = false;
      observer?.disconnect();
    };
  }, [figure, onError]);

  useEffect(() => {
    const node = el.current;
    return () => {
      void import('plotly.js-dist-min').then((mod) => {
        // The import resolves later; by then a remount (StrictMode, fast re-render) may have
        // redrawn this same node, so only purge a node that is really gone.
        if (node && !node.isConnected && node._fullLayout) mod.default?.purge(node);
      });
    };
  }, []);

  return <div ref={el} role="img" aria-label={ariaLabel} aria-busy={!ready} className="plot" style={{ width: '100%', height }} />;
}
