import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { uploadDataset, replaceDataset } from '../api/upload';
import { Dialog } from '../components/Dialog';
import { bytes } from '../lib/format';
import { useToast } from './toast';
import { useWorkspace } from './workspace';

interface UploadItem {
  id: string;
  key: string;
  name: string;
  size: number;
  progress: number;
  state: 'uploading' | 'done' | 'error';
  datasetId?: string;
  summary?: string;
  message?: string;
}

interface UploadsApi {
  openDialog: () => void;
  pickFiles: () => void;
  handleFiles: (files: FileList | File[] | null) => void;
  /** Choose a replacement file for an existing dataset (one request, progress in a toast). */
  replaceFile: (datasetId: string, filename: string) => void;
}

const Ctx = createContext<UploadsApi | null>(null);

export function useUploads(): UploadsApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useUploads outside UploadsProvider');
  return ctx;
}

export function UploadsProvider({ children }: { children: ReactNode }) {
  const ws = useWorkspace();
  const { toast, patch } = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<{ id: string; filename: string } | null>(null);
  const seq = useRef(0);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const patchItem = useCallback((id: string, p: Partial<UploadItem>) => {
    setItems((s) => s.map((u) => (u.id === id ? { ...u, ...p } : u)));
  }, []);

  const handleFiles = useCallback(
    (files: FileList | File[] | null) => {
      const list = Array.from(files ?? []);
      if (!list.length) return;
      setOpen(true);
      setOver(false);
      for (const file of list) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (itemsRef.current.some((u) => u.key === key && u.state === 'uploading')) {
          toast(`${file.name} is already uploading.`);
          continue;
        }
        const id = `u${++seq.current}`;
        setItems((s) => [{ id, key, name: file.name, size: file.size, progress: 0, state: 'uploading' }, ...s]);
        uploadDataset(file, (p) => patchItem(id, { progress: p }))
          .then((meta) => {
            ws.addDataset(meta);
            patchItem(id, {
              state: 'done',
              progress: 100,
              datasetId: meta.id,
              summary: `${meta.row_count.toLocaleString('en-GB')} rows · selected for analysis`,
            });
          })
          .catch((e: unknown) => patchItem(id, { state: 'error', message: e instanceof ApiError ? e.message : 'Upload failed.' }));
      }
    },
    [patchItem, toast, ws],
  );

  const replaceFile = useCallback((datasetId: string, filename: string) => {
    replaceTarget.current = { id: datasetId, filename };
    replaceInput.current?.click();
  }, []);

  const onReplaceChosen = (file: File | undefined) => {
    const target = replaceTarget.current;
    if (!file || !target) return;
    const tid = toast(`Replacing ${target.filename}… 0%`, { sticky: true, busy: true });
    replaceDataset(target.id, file, (p) => patch(tid, { text: `Replacing ${target.filename}… ${p}%`, sticky: true }))
      .then(async (meta) => {
        ws.patchDataset(meta);
        await ws.refreshDatasets();
        void ws.refreshPins();
        patch(tid, {
          text: `${target.filename} replaced with ${file.name} (${meta.row_count.toLocaleString('en-GB')} rows). Results built on the old version are marked out of date.`,
          busy: false,
        });
      })
      .catch((e: unknown) =>
        patch(tid, {
          text: `Replacement rejected: ${e instanceof ApiError ? e.message : 'upload failed'} ${target.filename} is unchanged.`,
          tone: 'error',
          busy: false,
          sticky: true,
        }),
      );
  };

  const api = useMemo<UploadsApi>(
    () => ({ openDialog: () => setOpen(true), pickFiles: () => fileInput.current?.click(), handleFiles, replaceFile }),
    [handleFiles, replaceFile],
  );

  return (
    <Ctx.Provider value={api}>
      {children}
      <input
        ref={fileInput}
        type="file"
        multiple
        accept=".csv,text/csv"
        aria-hidden="true"
        tabIndex={-1}
        data-testid="file-input"
        style={{ display: 'none' }}
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = '';
        }}
      />
      <input
        ref={replaceInput}
        type="file"
        accept=".csv,text/csv"
        aria-hidden="true"
        tabIndex={-1}
        data-testid="replace-input"
        style={{ display: 'none' }}
        onChange={(e) => {
          onReplaceChosen(e.target.files?.[0]);
          e.target.value = '';
        }}
      />
      {open && (
        <Dialog title="Upload CSV files" wide showClose onClose={() => setOpen(false)}>
          <DropZone variant="dialog" over={over} setOver={setOver} onFiles={handleFiles} onPick={api.pickFiles} />
          {items.length > 0 && (
            <ul className="uploads">
              {items.map((u) => (
                <li key={u.id} className={`uploads__item${u.state === 'error' ? ' uploads__item--err' : ''}`}>
                  <div className="uploads__row">
                    <span className="mono uploads__name">{u.name}</span>
                    <span className={`mono uploads__status uploads__status--${u.state}`}>
                      {u.state === 'uploading' ? `${u.progress}% · ${bytes(u.size)}` : u.state === 'done' ? '✓ Uploaded' : '✕ Rejected'}
                    </span>
                  </div>
                  {u.state === 'uploading' && (
                    <div role="progressbar" aria-valuenow={u.progress} aria-valuemin={0} aria-valuemax={100} aria-label={u.name} className="uploads__bar">
                      <div style={{ width: `${u.progress}%` }} />
                    </div>
                  )}
                  {u.state === 'done' && (
                    <div className="uploads__row uploads__done">
                      <span>{u.summary}</span>
                      <button
                        type="button"
                        className="btn btn--link btn--xs"
                        onClick={() => {
                          setOpen(false);
                          navigate(`/data/${u.datasetId}`);
                        }}
                      >
                        Inspect →
                      </button>
                    </div>
                  )}
                  {u.state === 'error' && (
                    <div className="uploads__err">
                      <div role="alert" className="uploads__msg">
                        {u.message}
                      </div>
                      <button type="button" className="btn btn--sm" onClick={api.pickFiles}>
                        Choose another file
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="uploads__foot">
            <span>Each file is checked separately — a rejected file never removes one that succeeded.</span>
            <button type="button" className="btn btn--primary" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </Dialog>
      )}
    </Ctx.Provider>
  );
}

interface DropZoneProps {
  variant: 'tile' | 'band' | 'dialog';
  over: boolean;
  setOver: (v: boolean) => void;
  onFiles: (files: FileList | null) => void;
  onPick: () => void;
  label?: string;
  children?: ReactNode;
}

export function DropZone({ variant, over, setOver, onFiles, onPick, label, children }: DropZoneProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={label ?? 'Upload CSV files: drop files here or press Enter to choose'}
      className={`dropzone dropzone--${variant}${over ? ' dropzone--over' : ''}`}
      onClick={onPick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onPick();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!over) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        onFiles(e.dataTransfer.files);
      }}
    >
      {children ?? (
        <>
          <div className="serif" style={{ fontSize: 24 }}>
            Drop files here or click to choose
          </div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>.csv only · first row must be column names</div>
        </>
      )}
    </div>
  );
}

/** Drop zone wired to the shared upload flow (used by the onboarding tile and the empty Data screen). */
export function ConnectedDropZone({ variant, label, children }: { variant: 'tile' | 'band'; label?: string; children: ReactNode }) {
  const { handleFiles, pickFiles } = useUploads();
  const [over, setOver] = useState(false);
  return (
    <DropZone variant={variant} over={over} setOver={setOver} onFiles={handleFiles} onPick={pickFiles} label={label}>
      {children}
    </DropZone>
  );
}
