/**
 * trace.ts — lightweight runtime tracing for viewer debugging.
 *
 * Enabled by URL query param: ?trace or ?trace=1
 */

const TRACE_ENABLED = (() => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  const v = params.get('trace');
  return params.has('trace') && v !== '0';
})();

const TRACE_BUFFER_MAX = 4000;

type TraceEntry = {
  ts: number;
  scope: string;
  message: string;
  data?: unknown;
};

function pushTraceEntry(entry: TraceEntry): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as {
    __arm3dViewerTraceLog?: TraceEntry[];
    __arm3dDumpViewerTrace?: () => TraceEntry[];
  };
  const store = Array.isArray(w.__arm3dViewerTraceLog) ? w.__arm3dViewerTraceLog : [];
  store.push(entry);
  if (store.length > TRACE_BUFFER_MAX) {
    store.splice(0, store.length - TRACE_BUFFER_MAX);
  }
  w.__arm3dViewerTraceLog = store;
  if (!w.__arm3dDumpViewerTrace) {
    w.__arm3dDumpViewerTrace = () => {
      const list = Array.isArray(w.__arm3dViewerTraceLog) ? w.__arm3dViewerTraceLog : [];
      console.info('[arm3d:trace] viewer trace dump', list);
      return list;
    };
  }
}

export function isTraceEnabled(): boolean {
  return TRACE_ENABLED;
}

export function trace(scope: string, message: string, data?: unknown): void {
  pushTraceEntry({ ts: Date.now(), scope, message, data });
  if (!TRACE_ENABLED) return;
  if (data !== undefined) {
    console.info(`[arm3d:trace][${scope}] ${message}`, data);
  } else {
    console.info(`[arm3d:trace][${scope}] ${message}`);
  }
}
