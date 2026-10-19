// Compiler-owned navigation and explicit page-state lifetime. Authorization is enforced separately.
import { createContext, useContext, useEffect, useState } from 'react';
import type { ComponentType, ReactNode, MouseEvent } from 'react';
export interface PageStateDefinition { id: string; parent?: string | null; }
export interface RoutePage {
  id: string; title: string; route: string | null; component: ComponentType;
  initial_state: string | null; states: PageStateDefinition[];
}
export interface MenuEntry { label: string; kind?: string; page?: string; children?: MenuEntry[]; }
interface PageStateContext {
  activeState: string | null; parameters: Readonly<Record<string, unknown>>;
  openState: (id: string, parameters?: Record<string, unknown>) => void;
  closeState: () => void; visible: (id: string) => boolean;
}
const StateContext = createContext<PageStateContext | null>(null);
export function usePageState(): PageStateContext {
  const context = useContext(StateContext);
  if (!context) throw new Error('usePageState requires a routed page');
  return context;
}
function PageStateProvider({ page, children }: { page: RoutePage; children: ReactNode }) {
  const [selection, setSelection] = useState<{ id: string | null; parameters: Record<string, unknown> }>({ id: page.initial_state, parameters: {} });
  const definitions = new Map(page.states.map(state => [state.id, state]));
  const visible = (id: string) => {
    let current = selection.id;
    const visited = new Set<string>();
    while (current && !visited.has(current)) {
      if (current === id) return true;
      visited.add(current); current = definitions.get(current)?.parent ?? null;
    }
    return false;
  };
  return <StateContext.Provider value={{ activeState: selection.id, parameters: selection.parameters, visible,
    openState: (id, parameters = {}) => {
      if (!definitions.has(id)) throw new Error('Unknown page state: ' + id);
      setSelection({ id, parameters: { ...parameters } });
    },
    closeState: () => setSelection(previous => ({ id: (previous.id && definitions.get(previous.id)?.parent) || page.initial_state, parameters: previous.parameters }))
  }}>{children}</StateContext.Provider>;
}
export function PageStateView({ stateId, children }: { stateId: string; children: ReactNode }) {
  const state = usePageState();
  return state.visible(stateId) ? <>{children}</> : null;
}
const locationKey = () => window.location.pathname + window.location.search;
export function ApplicationShell({ pages, menu, title }: { pages: RoutePage[]; menu: MenuEntry[]; title: string }) {
  const [location, setLocation] = useState(locationKey);
  useEffect(() => {
    const update = () => setLocation(locationKey());
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  const selected = pages.find(page => page.route === location)
    ?? (location === '/' ? pages.find(page => page.route) : undefined);
  const navigate = (event: MouseEvent<HTMLAnchorElement>, route: string) => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.history.pushState(null, '', route); setLocation(locationKey());
  };
  const entries = (items: MenuEntry[], prefix = 'menu'): ReactNode => <ul>{items.map((item, index) => {
    const key = prefix + ':' + index;
    const page = pages.find(row => row.id === item.page);
    return <li key={key}>{page?.route
      ? <a href={page.route} aria-current={selected?.id === page.id ? 'page' : undefined} onClick={event => navigate(event, page.route!)}>{item.label}</a>
      : <span aria-disabled={item.page ? true : undefined}>{item.label}</span>}
      {item.children?.length ? entries(item.children, key) : null}</li>;
  })}</ul>;
  const Page = selected?.component;
  return <div className="arc-application-shell">
    <header className="arc-application-header">{title}</header>
    <aside className="arc-application-sidebar"><nav aria-label="主菜单">{entries(menu)}</nav></aside>
    <main className="arc-application-content">{selected && Page
      ? <PageStateProvider key={selected.id} page={selected}><Page /></PageStateProvider>
      : <p role="status">页面不存在</p>}</main>
  </div>;
}
