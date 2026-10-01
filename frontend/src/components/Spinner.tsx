export function Spinner({ light = false }: { light?: boolean }) {
  return <span aria-hidden="true" className={`spinner${light ? ' spinner--light' : ''}`} />;
}
