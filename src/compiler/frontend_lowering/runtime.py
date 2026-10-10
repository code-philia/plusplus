"""React runtime sources; scopes and UI instances are implementation details."""

STATE_RUNTIME = r'''
import * as React from "react";
import type { APIContracts, DataValues, FunctionContracts } from "../types";

export type Value = { kind: "literal"; value: unknown } | {
    kind: "data" | "input" | "result" | "item"; id: string; path?: (string | number)[];
};
export type DataDefinition = { id: string; scope_ui_id: string | null; initial?: unknown };
type Scope = { parent?: Scope; values: Map<string, unknown>; listeners: Set<() => void>; version: number };
export interface Environment { scope: Scope; items: Record<string, unknown> }
type Handler = (environment: Environment, input: Record<string, unknown>) => Promise<unknown>;
const functions: Record<string, Handler> = {};
const apis: Record<string, (input: Record<string, unknown>) => Promise<unknown>> = {};
let definitions: DataDefinition[] = [];
let uiRoutes: Record<string, string> = {};
export function configure(data: DataDefinition[], routes: Record<string, string>) {
    definitions = data; uiRoutes = routes;
}
function makeScope(owner: string | null, parent?: Scope): Scope {
    const values = new Map<string, unknown>();
    for (const row of definitions) if (row.scope_ui_id === owner) {
        const value = Object.prototype.hasOwnProperty.call(row, "initial")
            ? structuredClone(row.initial) : undefined;
        values.set(row.id, value);
    }
    return { parent, values, listeners: new Set(), version: 0 };
}
export function createEnvironment(): Environment { return { scope: makeScope(null), items: {} }; }
export function childEnvironment(parent: Environment, ui: string): Environment {
    return { scope: makeScope(ui, parent.scope), items: parent.items };
}
function owner(environment: Environment, id: string): Scope {
    let current: Scope | undefined = environment.scope;
    while (current) {
        if (current.values.has(id)) return current;
        current = current.parent;
    }
    throw new Error(`Data ${id} is outside this UI instance scope`);
}
export function read(environment: Environment, id: string): unknown { return owner(environment, id).values.get(id); }
export function write(environment: Environment, id: string, value: unknown) {
    const scope = owner(environment, id);
    scope.values.set(id, value); scope.version++;
    scope.listeners.forEach(listener => listener());
}
export function useSnapshot(environment: Environment) {
    const subscribe = React.useCallback((listener: () => void) => {
        const scopes: Scope[] = [];
        let scope: Scope | undefined = environment.scope;
        while (scope) { scopes.push(scope); scope.listeners.add(listener); scope = scope.parent; }
        return () => scopes.forEach(row => row.listeners.delete(listener));
    }, [environment.scope]);
    const snapshot = React.useCallback(() => {
        let version = 0; let scope: Scope | undefined = environment.scope;
        while (scope) { version += scope.version; scope = scope.parent; }
        return version;
    }, [environment.scope]);
    React.useSyncExternalStore(subscribe, snapshot, snapshot);
}
export function resolve(value: Value, environment: Environment,
    input: Record<string, unknown> = {}, results: Record<string, unknown> = {}): unknown {
    if (value.kind === "literal") return value.value;
    let current: unknown = value.kind === "data" ? read(environment, value.id)
        : value.kind === "input" ? input[value.id]
        : value.kind === "result" ? results[value.id] : environment.items[value.id];
    for (const part of value.path ?? []) {
        if (current == null) return undefined;
        current = (current as Record<string | number, unknown>)[part];
    }
    return current;
}
export function argumentsFor(values: Record<string, Value>, environment: Environment,
    input: Record<string, unknown>, results: Record<string, unknown>) {
    return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, resolve(value, environment, input, results)]));
}
export function registerFunction(id: string, handler: Handler) { functions[id] = handler; }
export function registerAPI(id: string, handler: (input: Record<string, unknown>) => Promise<unknown>) { apis[id] = handler; }
export async function invokeFunction(environment: Environment, id: string, input: Record<string, unknown>): Promise<unknown> {
    const handler = functions[id]; if (!handler) throw new Error(`Unknown frontend function ${id}`);
    return handler(environment, input);
}
export function unimplemented(id: string): never { throw new Error(`Not implemented: ${id}`); }
export function reportError(error: unknown) { console.error(error); }
export interface FunctionContext {
    read<K extends keyof DataValues>(id: K): DataValues[K];
    write<K extends keyof DataValues>(id: K, value: DataValues[K]): void;
    callFunction<K extends keyof FunctionContracts>(id: K, input: FunctionContracts[K]["input"]): Promise<FunctionContracts[K]["output"]>;
    callAPI<K extends keyof APIContracts>(id: K, input: APIContracts[K]["input"]): Promise<APIContracts[K]["output"]>;
    navigate(uiId: string, parameters?: Record<string, string | number>): void;
    item(uiId: string): unknown;
}
export function context(environment: Environment): FunctionContext {
    return {
        read: id => read(environment, id) as never,
        write: (id, value) => write(environment, id, value),
        callFunction: async (id, input) => await invokeFunction(environment, id, input) as never,
        callAPI: async (id, input) => {
            const handler = apis[id]; if (!handler) throw new Error(`Unknown API ${id}`);
            return await handler(input) as never;
        },
        navigate: (uiId, parameters = {}) => {
            let route = uiRoutes[uiId]; if (!route) throw new Error(`UI ${uiId} has no route`);
            route = route.replace(/:([A-Za-z0-9_]+)/g, (_, key: string) => {
                if (!(key in parameters)) throw new Error(`Missing route parameter ${key}`);
                return encodeURIComponent(String(parameters[key]));
            });
            window.history.pushState(null, "", route); window.dispatchEvent(new PopStateEvent("popstate"));
        },
        item: uiId => environment.items[uiId],
    };
}
'''

UI_RUNTIME = r'''
import * as React from "react";
import { childEnvironment, resolve, useSnapshot, reportError } from "./state";
import type { Environment, Value } from "./state";
export interface UIProps { environment: Environment }
export interface UINode {
    id: string; kind: string; route?: string; bindings: Record<string, Value>;
    descendantRoutes?: string[]; valueType?: string;
    repeat?: { source: Value; key: Value };
}
export interface EventBinding {
    id: string; name: string;
    payload: (event?: React.SyntheticEvent<HTMLElement>) => Record<string, unknown>;
    execute: (environment: Environment, input: Record<string, unknown>) => Promise<unknown>;
}
interface Props extends UIProps {
    node: UINode; events: EventBinding[];
    renderChildren: (environment: Environment) => React.ReactNode;
}
function pathSnapshot() { return window.location.pathname; }
function subscribePath(listener: () => void) {
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
}
function matches(route: string, path: string) {
    const pattern = route.split("/").map(part => part.startsWith(":") ? "[^/]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("/");
    return new RegExp("^" + pattern + "/?$").test(path);
}
export function UIBoundary(props: Props) {
    useSnapshot(props.environment);
    const path = React.useSyncExternalStore(subscribePath, pathSnapshot, () => "/");
    if (props.node.kind === "screen" && props.node.route &&
        ![props.node.route, ...(props.node.descendantRoutes ?? [])].some(route => matches(route, path))) return null;
    if (!props.node.repeat) return <MountedUI {...props} />;
    const values = resolve(props.node.repeat.source, props.environment);
    if (!Array.isArray(values)) return null;
    return <>{values.map(item => {
        const environment = { ...props.environment, items: { ...props.environment.items, [props.node.id]: item } };
        const key = resolve(props.node.repeat!.key, environment);
        if (typeof key !== "string" && typeof key !== "number") throw new Error(`Invalid repeat key for ${props.node.id}`);
        return <MountedUI key={key} {...props} environment={environment} />;
    })}</>;
}
function MountedUI(props: Props) {
    const [scope] = React.useState(() => childEnvironment(props.environment, props.node.id).scope);
    // Item values must follow keyed instances when their source list is refreshed.
    const environment = { scope, items: props.environment.items };
    useSnapshot(environment);
    const bindings = Object.fromEntries(Object.entries(props.node.bindings).map(([key, value]) => [key, resolve(value, environment)]));
    const visible = bindings.visible !== false;
    const entered = React.useRef(false);
    React.useEffect(() => {
        if (!visible) { entered.current = false; return; }
        if (entered.current) return;
        entered.current = true;
        for (const event of props.events) if (event.name === "enter") {
            try { void event.execute(environment, event.payload()).catch(reportError); }
            catch (error) { reportError(error); }
        }
    });
    if (!visible) return null;
    const fire = (name: string, raw: React.SyntheticEvent<HTMLElement>) => {
        const events = props.events.filter(event => event.name === name);
        if (name === "submit" && events.length) raw.preventDefault();
        // Capture payload synchronously while currentTarget is available.
        let tasks: { event: EventBinding; input: Record<string, unknown> }[];
        try { tasks = events.map(event => ({ event, input: event.payload(raw) })); }
        catch (error) { reportError(error); return; }
        if (tasks.length) raw.stopPropagation();
        void (async () => {
            for (const task of tasks) await task.event.execute(environment, task.input);
        })().catch(reportError);
    };
    const common = {
        "data-ui-id": props.node.id,
        "aria-label": bindings.label == null ? undefined : String(bindings.label),
        onClick: (event: React.MouseEvent<HTMLElement>) => fire("activate", event),
        onChange: (event: React.SyntheticEvent<HTMLElement>) => fire("change", event),
        onSubmit: (event: React.FormEvent<HTMLElement>) => fire("submit", event),
        onFocus: (event: React.FocusEvent<HTMLElement>) => fire("focus", event),
        onBlur: (event: React.FocusEvent<HTMLElement>) => fire("blur", event),
    };
    const children = props.renderChildren(environment);
    const label = bindings.text ?? bindings.label;
    const text = label == null ? undefined : String(label);
    if (props.node.kind === "text") return <span {...common}>{text}{children}</span>;
    if (props.node.kind === "feedback") return <div {...common} role="status">{text}{children}</div>;
    if (props.node.kind === "image") return <img {...common} src={bindings.src == null ? undefined : String(bindings.src)} alt={String(bindings.alt ?? bindings.label ?? "")} />;
    if (props.node.kind === "action") return <button {...common} type={bindings.submits_form === true ? "submit" : "button"} disabled={bindings.enabled === false}>{text}{children}</button>;
    if (props.node.kind === "input") {
        const value = bindings.value;
        return <label>{text}<input {...common}
            type={props.node.valueType === "boolean" || typeof value === "boolean" ? "checkbox" : String(bindings.input_type ?? "text")}
            disabled={bindings.enabled === false}
            placeholder={bindings.placeholder == null ? undefined : String(bindings.placeholder)}
            value={props.node.valueType === "boolean" || typeof value === "boolean" ? undefined : String(value ?? "")}
            checked={props.node.valueType === "boolean" || typeof value === "boolean" ? value === true : undefined}
            readOnly={!props.events.some(event => event.name === "change")} />{children}</label>;
    }
    if (props.node.kind === "choice") {
        const options = Array.isArray(bindings.options) ? bindings.options : [];
        return <label>{text}<select {...common} value={String(bindings.value ?? "")} disabled={bindings.enabled === false}>
            {options.map((option, index) => {
                const record = option != null && typeof option === "object" ? option as Record<string, unknown> : { value: option, label: option };
                return <option key={index} value={String(record.value ?? "")}>{String(record.label ?? record.value ?? "")}</option>;
            })}
        </select>{children}</label>;
    }
    if (props.node.kind === "heading") {
        const level = Math.max(1, Math.min(6, Number(bindings.level ?? 2)));
        return React.createElement(`h${level}`, common, text, children);
    }
    if (props.node.kind === "link") return <a {...common} href={bindings.destination == null ? undefined : String(bindings.destination)}>{text}{children}</a>;
    if (props.node.kind === "navigation") return <nav {...common}>{text}{children}</nav>;
    if (props.node.kind === "section") return <section {...common}>{text}{children}</section>;
    if (props.node.kind === "field") return <div {...common} role="group">{text}{children}</div>;
    if (props.node.kind === "dialog") return <div {...common} role="dialog" aria-modal={true}>{text}{children}</div>;
    if (props.node.kind === "tabs") return <div {...common} role="tablist">{children}</div>;
    if (props.node.kind === "tab") return <button {...common} type="button" role="tab" aria-selected={bindings.selected === true} disabled={bindings.enabled === false}>{text}{children}</button>;
    if (props.node.kind === "table") return <table {...common}><tbody>{children}</tbody></table>;
    if (props.node.kind === "row") return <tr {...common}>{children}</tr>;
    if (props.node.kind === "cell") return bindings.header === true ? <th {...common}>{text}{children}</th> : <td {...common}>{text}{children}</td>;
    if (props.node.kind === "progress") return <progress {...common} max={Number(bindings.maximum ?? 100)} value={bindings.value == null ? undefined : Number(bindings.value)}>{text}</progress>;
    if (props.node.kind === "form" || props.events.some(event => event.name === "submit")) return <form {...common}>{children}</form>;
    if (props.node.kind === "screen") return <main {...common}>{children}</main>;
    // Lists preserve their explicit repeat/child structure; no fabricated item schema.
    return <div {...common}>{text}{children}</div>;
}
'''
