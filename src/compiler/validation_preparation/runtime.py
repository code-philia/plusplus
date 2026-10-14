"""Validation-only Node and browser clock injection, installed before application code."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

RUNTIME_PATH = 'backend/src/validation/runtime.ts'
HTML_TAG = '<script src="/__arc/validation-runtime.js" data-arc-validation-runtime></script>'


def normalize_environment(environment: dict[str, Any]) -> dict[str, Any]:
    # Validation preparation controls time only; fixture/environment policy lives elsewhere.
    result = {key: value for key, value in environment.items()
              if key in {'timezone', 'benchmark_now', 'clock', 'clock_mode'}}
    timezone = result.get('timezone')
    if timezone:
        ZoneInfo(timezone)
    benchmark = result.get('benchmark_now')
    if benchmark:
        date = datetime.fromisoformat(benchmark.replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=ZoneInfo(timezone or 'UTC'))
        result['benchmark_now_ms'] = int(date.timestamp() * 1000)
    else:
        result['benchmark_now_ms'] = None
    mode = result.get('clock_mode')
    if mode is None:
        description = str(result.get('clock', '')).lower()
        mode = 'frozen' if any(word in description for word in ('frozen', 'freeze', 'fixed', '冻结', '固定')) else 'elapsed'
    if mode not in {'elapsed', 'frozen'}:
        raise ValueError('environment.clock_mode must be elapsed or frozen')
    result['clock_mode'] = mode
    return result


# Self-contained JavaScript: the same adapter runs in Node and before browser modules.
CLOCK_JAVASCRIPT = r'''(function installValidationClock(config) {
  if (globalThis.__arcValidationRuntime) return;
  const NativeDate = globalThis.Date;
  const NativeFormat = Intl.DateTimeFormat;
  const monotonicStart = performance.now();
  const timezone = config.timezone;
  const locale = config.locale;
  const clockNow = () => config.nowMs + (config.clock_mode === 'frozen' ? 0 : performance.now() - monotonicStart);
  const formatter = timezone ? new NativeFormat('en-US-u-ca-gregory-nu-latn', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  }) : null;
  const nativeGetTime = NativeDate.prototype.getTime;
  const parts = date => {
    if (!Number.isFinite(nativeGetTime.call(date))) return null;
    const values = {};
    for (const part of formatter.formatToParts(date)) if (part.type !== 'literal') values[part.type] = Number(part.value);
    return [values.year, values.month - 1, values.day, values.hour % 24, values.minute, values.second, date.getUTCMilliseconds()];
  };
  const wallMs = fields => {
    const date = new NativeDate(0);
    date.setUTCFullYear(fields[0], fields[1], fields[2]);
    date.setUTCHours(fields[3], fields[4], fields[5], fields[6]);
    return nativeGetTime.call(date);
  };
  const offset = date => {
    const values = parts(date);
    return values ? nativeGetTime.call(date) - wallMs(values) : NaN;
  };
  const wallToEpoch = fields => {
    const wall = wallMs(fields);
    let epoch = wall;
    // Iteration handles timezone offsets, including daylight-saving transitions.
    for (let i = 0; i < 4; i++) {
      const next = wall + offset(new NativeDate(epoch));
      if (next === epoch) break;
      epoch = next;
    }
    return epoch;
  };
  const parse = value => {
    if (timezone && typeof value === 'string') {
      const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(value);
      if (match) return wallToEpoch([Number(match[1]), Number(match[2]) - 1, Number(match[3]),
        Number(match[4]), Number(match[5]), Number(match[6] || 0), Number((match[7] || '').padEnd(3, '0'))]);
    }
    return NativeDate.parse(value);
  };
  if (timezone) {
    const names = ['FullYear', 'Month', 'Date', 'Hours', 'Minutes', 'Seconds', 'Milliseconds'];
    names.forEach((name, index) => {
      NativeDate.prototype['get' + name] = function () { const value = parts(this); return value ? value[index] : NaN; };
    });
    NativeDate.prototype.getDay = function () { const value = parts(this); return value ? new NativeDate(wallMs(value)).getUTCDay() : NaN; };
    NativeDate.prototype.getYear = function () { return this.getFullYear() - 1900; };
    NativeDate.prototype.getTimezoneOffset = function () { return offset(this) / 60000; };
    const setters = { FullYear: [0, 1, 2], Month: [1, 2], Date: [2], Hours: [3, 4, 5, 6],
      Minutes: [4, 5, 6], Seconds: [5, 6], Milliseconds: [6] };
    for (const [name, indexes] of Object.entries(setters)) {
      NativeDate.prototype['set' + name] = function (...values) {
        const fields = parts(this) || (name === 'FullYear' ? parts(new NativeDate(0)) : null);
        if (!fields) return this.setTime(NaN);
        indexes.forEach((index, position) => { if (position < values.length) fields[index] = Number(values[position]); });
        if (!values.length) return this.setTime(NaN);
        return this.setTime(wallToEpoch(fields));
      };
    }
    NativeDate.prototype.setYear = function (year) {
      year = Number(year);
      return this.setFullYear(year >= 0 && year <= 99 ? year + 1900 : year);
    };
    for (const method of ['toLocaleString', 'toLocaleDateString', 'toLocaleTimeString']) {
      const nativeMethod = NativeDate.prototype[method];
      NativeDate.prototype[method] = function (locales, options) {
        return nativeMethod.call(this, locales === undefined ? locale : locales,
          { ...options, timeZone: options?.timeZone ?? timezone });
      };
    }
    const dateText = date => {
      const f = parts(date);
      if (!f) return 'Invalid Date';
      return new NativeFormat('en-US', { timeZone: timezone, weekday: 'short', year: 'numeric', month: 'short', day: '2-digit' }).format(date);
    };
    const timeText = date => {
      const f = parts(date);
      if (!f) return 'Invalid Date';
      const minutes = -date.getTimezoneOffset();
      const sign = minutes >= 0 ? '+' : '-';
      const pad = n => String(Math.trunc(n)).padStart(2, '0');
      return `${pad(f[3])}:${pad(f[4])}:${pad(f[5])} GMT${sign}${pad(Math.abs(minutes)/60)}${pad(Math.abs(minutes)%60)} (${timezone})`;
    };
    NativeDate.prototype.toDateString = function () { return dateText(this); };
    NativeDate.prototype.toTimeString = function () { return timeText(this); };
    NativeDate.prototype.toString = function () { return parts(this) ? dateText(this) + ' ' + timeText(this) : 'Invalid Date'; };
  }
  const WrappedDate = new Proxy(NativeDate, {
    construct(target, args, newTarget) {
      if (!args.length) args = [clockNow()];
      else if (timezone && args.length >= 2) {
        const fields = [Number(args[0]), Number(args[1]), args.length > 2 ? Number(args[2]) : 1,
          ...[3,4,5,6].map(index => args.length > index ? Number(args[index]) : 0)];
        if (fields[0] >= 0 && fields[0] <= 99) fields[0] += 1900;
        args = [wallToEpoch(fields)];
      } else if (typeof args[0] === 'string') args = [parse(args[0])];
      return Reflect.construct(target, args, newTarget);
    },
    apply() { return new NativeDate(clockNow()).toString(); },
    get(target, key, receiver) {
      if (key === 'now') return clockNow;
      if (key === 'parse') return parse;
      return Reflect.get(target, key, receiver);
    }
  });
  globalThis.Date = WrappedDate;
  NativeDate.prototype.constructor = WrappedDate;
  const makeFormatter = args => {
    const formatter = Reflect.construct(NativeFormat, [args[0] === undefined ? locale : args[0],
      timezone ? { ...args[1], timeZone: args[1]?.timeZone ?? timezone } : args[1]]);
    const format = formatter.format;
    Object.defineProperty(formatter, 'format', { value: value => format(value === undefined ? clockNow() : value) });
    const parts = formatter.formatToParts.bind(formatter);
    formatter.formatToParts = value => parts(value === undefined ? clockNow() : value);
    return formatter;
  };
  // Intl.DateTimeFormat can be called with or without new.
  Intl.DateTimeFormat = new Proxy(NativeFormat, {
    construct(target, args) { return makeFormatter(args); },
    apply(target, self, args) { return makeFormatter(args); }
  });
  if (locale) {
    const NativeNumber = Intl.NumberFormat;
    Intl.NumberFormat = new Proxy(NativeNumber, {
      construct(target, args) { return Reflect.construct(target, [args[0] === undefined ? locale : args[0], args[1]]); },
      apply(target, self, args) { return Reflect.construct(target, [args[0] === undefined ? locale : args[0], args[1]]); }
    });
  }
  globalThis.__arcValidationRuntime = Object.freeze({ ...config, now: clockNow });
  if (typeof document !== 'undefined' && locale) document.documentElement.lang = locale;
})(CONFIGURATION);
'''


def render_runtime_source() -> str:
    return r'''// Validation-only clock and browser bootstrap. Imported before the application's server.
import { Server } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { runInThisContext } from "node:vm";

const clockJavaScript = JAVASCRIPT;
interface RuntimeEnvironment {
  benchmark_now_ms: number | null;
  clock_mode: "elapsed" | "frozen";
  timezone?: string;
  locale?: string;
}
export function installValidationRuntime(environment: RuntimeEnvironment): void {
  const realNow = Date.now.bind(Date);
  // Each application start establishes a fresh clock origin; no database state is used.
  const epoch = environment.benchmark_now_ms ?? realNow();
  const monotonicStart = performance.now();
  const now = () => epoch + (environment.clock_mode === "frozen" ? 0 : performance.now() - monotonicStart);
  const configuration = () => ({ ...environment, nowMs: now() });
  const script = () => clockJavaScript.replace("CONFIGURATION", JSON.stringify(configuration()).replace(/</g, "\\u003c"));
  runInThisContext(script(), { filename: "arc-validation-clock.js" });
  // Intercept only compiler-owned runtime URLs before application request listeners.
  const originalEmit = Server.prototype.emit;
  Server.prototype.emit = function (event: string | symbol, ...args: unknown[]): boolean {
    if (event === "request") {
      const request = args[0] as IncomingMessage;
      const response = args[1] as ServerResponse;
      const path = request.url?.split("?")[0];
      if (path === "/__arc/validation-runtime.js" || path === "/__arc/runtime") {
        response.setHeader("Cache-Control", "no-store");
        if (request.method !== "GET") { response.statusCode = 405; response.end(); return true; }
        response.setHeader("Content-Type", path.endsWith(".js") ? "application/javascript; charset=utf-8" : "application/json; charset=utf-8");
        response.end(path.endsWith(".js") ? script() : JSON.stringify(configuration()));
        return true;
      }
    }
    return Reflect.apply(originalEmit, this, [event, ...args]) as boolean;
  };
}
'''.replace('JAVASCRIPT', json.dumps(CLOCK_JAVASCRIPT, ensure_ascii=False))
