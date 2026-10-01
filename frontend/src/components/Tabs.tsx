import { useRef, type KeyboardEvent, type ReactNode } from 'react';

export interface TabItem {
  id: string;
  label: ReactNode;
}

interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
  /** prefix used to link each tab to its panel (`${idPrefix}-tab-${id}` / `${idPrefix}-panel`) */
  idPrefix: string;
  className?: string;
  tabClassName?: string;
  renderTab?: (item: TabItem, selected: boolean) => ReactNode;
}

/** WAI-ARIA tabs with automatic activation: Left/Right/Home/End move and select; only the selected tab is tabbable. */
export function Tabs({ items, value, onChange, label, idPrefix, className, tabClassName, renderTab }: TabsProps) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const index = items.findIndex((i) => i.id === value);
    let next = index;
    if (e.key === 'ArrowRight') next = (index + 1) % items.length;
    else if (e.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    else return;
    e.preventDefault();
    const target = items[next];
    onChange(target.id);
    refs.current[target.id]?.focus();
  };

  return (
    <div role="tablist" aria-label={label} className={className} onKeyDown={onKeyDown}>
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            ref={(el) => {
              refs.current[item.id] = el;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${item.id}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={selected ? 0 : -1}
            className={tabClassName}
            onClick={() => onChange(item.id)}
          >
            {renderTab ? renderTab(item, selected) : item.label}
          </button>
        );
      })}
    </div>
  );
}
